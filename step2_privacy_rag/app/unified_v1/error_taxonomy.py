"""Aggregate PERSON/ADDRESS error taxonomy for frozen V6 vs AX-only.

Offline local inference and scoring only. No model downloads/updates, no
training, DB, HMAC, external calls, or raw-text/span/document-ID logging.
Diagnostic feature tags may overlap and are *not* training labels.

Run:
 PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python \
   -m unified_v1.error_taxonomy --dataset B --limit 5
 PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python \
   -m unified_v1.error_taxonomy --data ~/aegis_external_eval/team4/synth_hard_eval.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import selection as S
from .ax_veil_ablation import no_veil_counterfactual
from .external_validation import load_external
from .lean import Worker
from .merge_validation import overlaps, v6_detailed

JOSA = re.compile(r"^(?:께서|에게서|에게|한테|이랑|랑|으로|은|는|이|가|을|를|의|도|만|과|와)(?![가-힣]{2})")
TITLE = re.compile(r"^\s*(?:님|씨|대리|과장|차장|부장|팀장|담당자|고객)(?![가-힣])")
ROAD = re.compile(r"(?:로|길)\s*\d")
LOT = re.compile(r"(?:동|리|읍|면)\s*\d")
POSTCODE = re.compile(r"\(?\d{5}\)?")
HANGUL = re.compile(r"^[가-힣]+$")
LABELS = ("person", "address")


def shape_tags(text, entity):
    a, b = int(entity["start"]), int(entity["end"])
    value = text[a:b]
    next_text = text[b:b+22]
    if entity["label"] == "person":
        compact = re.sub(r"\s+", "", value)
        tags = ["person_all"]
        if HANGUL.fullmatch(compact):
            n = len(compact)
            tags.append("hangul_len_" + (str(n) if n <= 5 else "6plus"))
            if n == 2:
                # Two characters may be surname+one-given OR given-name only:
                # length alone cannot identify which.
                tags.append("two_chars_ambiguous")
            if compact.endswith("이"):
                tags.append("last_char_i_ambiguous")
        else:
            tags.append("non_hangul_name")
        if any(ch.isspace() for ch in value):
            tags.append("spaced_name")
        if JOSA.match(next_text):
            tags.append("josa_on_right")
        if TITLE.match(next_text):
            tags.append("title_on_right")
        if text.count(value) > 1 and len(value) >= 2:
            tags.append("repeated_string_in_document")
        return tags
    tags = ["address_all"]
    tags.append("road_style" if ROAD.search(value)
                else "lot_style" if LOT.search(value)
                else "other_address_style")
    if POSTCODE.search(value):
        tags.append("postcode_inside_gold")
    if "(" in value or ")" in value:
        tags.append("parenthesized")
    if re.search(r"\s", value):
        tags.append("has_spaces")
    if not re.search(r"\d", value):
        tags.append("no_digit_in_gold")
    if re.search(r"(?:번길|호|동)\s*[\d\-]+", value):
        tags.append("number_suffix")
    return tags


def strict_status(gold, predictions):
    if any((p["label"], p["start"], p["end"]) ==
           (gold["label"], gold["start"], gold["end"]) for p in predictions):
        return "exact"
    if any(p["label"] == gold["label"] and overlaps(gold, p)
           for p in predictions):
        return "boundary_only"
    if any(overlaps(gold, p) for p in predictions):
        return "wrong_label_overlap"
    return "no_overlap"


def mask_status(gold, intervals):
    a, b = int(gold["start"]), int(gold["end"])
    covered = bytearray(b-a)
    for m in intervals:
        s = max(a, int(m["start"]))
        e = min(b, int(m["end"]))
        if s < e:
            covered[s-a:e-a] = b"\x01" * (e-s)
    if not any(covered):
        return "uncovered"
    if not all(covered):
        return "partial"
    return "covered"


def fp_status(pred, golds):
    if any(p["label"] == pred["label"] and overlaps(pred, p)
           for p in golds):
        return "boundary_error"
    if any(overlaps(pred, p) for p in golds):
        return "label_conflict"
    return "unmatched"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dataset", choices=("A", "B"))
    g.add_argument("--data", type=Path)
    p.add_argument("--limit", type=int)
    args = p.parse_args()
    if args.limit is not None and args.limit <= 0:
        p.error("--limit must be positive")
    file_path = (S.A if args.dataset == "A" else S.B
                 if args.dataset == "B" else args.data.expanduser()).resolve()
    rows = load_external(file_path)
    selected = rows[:args.limit] if args.limit else rows
    sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
    print(f"TAXONOMY_DATA_SHA256={sha} selected={len(selected)}/{len(rows)}",
          flush=True)

    summary = {mode: defaultdict(Counter) for mode in ("V6", "AX_ALL_V6_POLICY")}
    groups = {mode: defaultdict(Counter) for mode in summary}
    fp_groups = {mode: defaultdict(Counter) for mode in summary}
    paired = Counter()

    with Worker("B0") as v6:
        for idx, row in enumerate(selected, 1):
            text = row["text"]
            raw = v6.call(text)
            a = v6_detailed(text, raw)
            b = no_veil_counterfactual(text, raw)
            models = {"V6": a, "AX_ALL_V6_POLICY": b}
            golds = [e for e in row["entities"] if e["label"] in LABELS]
            for mode, result in models.items():
                predictions = result["entities"]
                masks = result["mask_intervals"]
                predicted_keys = {(x["label"], x["start"], x["end"])
                                  for x in predictions}
                gold_keys = {(x["label"], x["start"], x["end"])
                             for x in row["entities"]}
                for gold in golds:
                    status = strict_status(gold, predictions)
                    mask = mask_status(gold, masks)
                    key = gold["label"]
                    summary[mode][key][status] += 1
                    summary[mode][key]["mask_" + mask] += 1
                    for tag in shape_tags(text, gold):
                        groups[mode][(key, tag)]["gold"] += 1
                        groups[mode][(key, tag)][status] += 1
                        groups[mode][(key, tag)]["mask_" + mask] += 1
                for pred in predictions:
                    key = (pred["label"], pred["start"], pred["end"])
                    if pred["label"] not in LABELS or key in gold_keys:
                        continue
                    fp_groups[mode][pred["label"]][fp_status(pred, row["entities"])] += 1

            for gold in golds:
                v = strict_status(gold, a["entities"])
                x = strict_status(gold, b["entities"])
                if v == "exact" and x != "exact":
                    paired["v6_wins_" + gold["label"]] += 1
                if v != "exact" and x == "exact":
                    paired["ax_wins_" + gold["label"]] += 1

            if idx % 50 == 0 or idx == len(selected):
                print(f"TAXONOMY_PROGRESS {idx}/{len(selected)}", flush=True)

    print("\nMODE LABEL  GOLD EXACT BOUNDARY WRONG_LABEL NO_OVERLAP "
          "FULLY_MASKED PARTIAL UNCOVERED")
    for mode in summary:
        for label in LABELS:
            c = summary[mode][label]
            gold_total = sum(c[k] for k in (
                "exact", "boundary_only", "wrong_label_overlap", "no_overlap"))
            print(f"{mode:17} {label:8} {gold_total} "
                  + " ".join(str(c[k]) for k in (
                      "exact", "boundary_only", "wrong_label_overlap",
                      "no_overlap", "mask_covered", "mask_partial",
                      "mask_uncovered")))
        print("FP_ERROR_TYPES", mode,
              json.dumps({lab: dict(counts) for lab, counts in
                          fp_groups[mode].items()}, sort_keys=True))
    print("\nSHAPE_BREAKDOWN: overlapping tags are diagnostic; not independent samples.")
    print("MODE LABEL TAG GOLD EXACT BOUNDARY WRONG_LABEL NO_OVERLAP "
          "PARTIAL UNCOVERED")
    for mode in groups:
        for (label, tag), c in sorted(groups[mode].items()):
            print(f"{mode} {label} {tag} "
                  + " ".join(str(c[k]) for k in (
                      "gold", "exact", "boundary_only", "wrong_label_overlap",
                      "no_overlap", "mask_partial", "mask_uncovered")))
    print("PAIRED_EXACT_WINS", json.dumps(dict(paired), sort_keys=True))
    print("TAXONOMY_COMPLETE no_raw_text_or_spans_export=true "
          "shape_tags_diagnostic_not_gold_subtypes=true no_model_training=true",
          flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
