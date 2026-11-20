#!/usr/bin/env python3
"""E4-3 read-only V7 gap position + strict false-positive profile on Hard403.

Offline human decisions guide diagnostics, never inference. Original Gold is
unchanged; sensitive source spans are not printed or exported.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "step2_privacy_rag" / "app"))

from v7_mask_review_cli import cid, load_decisions, validate


def character_kind(c):
    if c.isdecimal():
        return "DIGIT"
    if "\uac00" <= c <= "\ud7a3":
        return "HANGUL"
    if c.isascii() and c.isalpha():
        return "LATIN"
    if c.isspace():
        return "SPACE"
    return "PUNCT_OR_OTHER"


def morphology(text):
    value = text.strip()
    if re.fullmatch(r"[\uac00-\ud7a3 ]+", value):
        if " " in value:
            return "HANGUL_WITH_SPACES"
        if len(value) == 1:
            return "HANGUL_1"
        if len(value) == 2:
            return "HANGUL_2"
        return "HANGUL_3PLUS"
    if re.fullmatch(r"[A-Za-z .'-]+", value):
        return "LATIN_NAME_LIKE"
    if re.search(r"[A-Za-z]", value) and re.search(r"[\uac00-\ud7a3]", value):
        return "MIXED_KOREAN_LATIN"
    if re.search(r"\d", value):
        return "HAS_DIGITS"
    return "OTHER"


def bitmap(gold, masks):
    a, b = gold["start"], gold["end"]
    protected = bytearray(b - a)
    for mask in masks:
        x, y = max(a, mask["start"]), min(b, mask["end"])
        if x < y:
            protected[x-a:y-a] = b"\x01" * (y-x)
    return protected


def gap_position(bits):
    uncovered = [i for i, x in enumerate(bits) if not x]
    if len(uncovered) == len(bits):
        return "FULL"
    blocks = 1 + sum(uncovered[i] != uncovered[i-1] + 1
                     for i in range(1, len(uncovered)))
    if blocks > 1:
        return "MULTIPLE_GAPS"
    if uncovered[0] == 0:
        return "PREFIX"
    if uncovered[-1] == len(bits)-1:
        return "SUFFIX"
    return "INTERIOR"


def key(span):
    return (span["label"], int(span["start"]), int(span["end"]))


def overlaps(a, b):
    return a[1] < b[2] and b[1] < a[2]


def fp_relation(prediction, golds):
    if any(overlaps(prediction, g) and prediction[0] == g[0] for g in golds):
        return "OVERLAPS_SAME_LABEL_GOLD"
    if any(overlaps(prediction, g) for g in golds):
        return "OVERLAPS_OTHER_LABEL_GOLD"
    return "NO_GOLD_OVERLAP_UNVERIFIED"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    data, audit, decisions, checkpoint = (p.expanduser().resolve() for p in
            (args.data, args.audit, args.decisions, args.checkpoint))
    if not (checkpoint / "config.json").is_file():
        raise ValueError("CHECKPOINT_CONFIG_MISSING")
    if not decisions.is_file():
        raise ValueError("DECISIONS_MISSING")
    digest, docs, cases = validate(data, audit)
    saved = load_decisions(decisions, digest, cases)
    if len(cases) != 46 or len(saved["decisions"]) != 46:
        raise ValueError("EXPECTED_46_COMPLETED_CASES")
    expected = Counter(x["decision"] for x in saved["decisions"].values())
    if expected != Counter(VALID_PII=28, INVALID_GOLD=15, POLICY_DISPUTED=3):
        raise ValueError("DECISIONS_CHANGED")

    cases_by_doc = defaultdict(list)
    for index, case in enumerate(cases):
        if saved["decisions"][cid(case)]["decision"] == "VALID_PII":
            cases_by_doc[case["document_index"]].append((index, case))

    from ax_v7_eval import predictor
    from unified_v1.ax_veil_ablation import no_veil_counterfactual
    predict = predictor(checkpoint, "cpu")

    strict = Counter()
    fp_by_label_relation = Counter()
    person_fp_shapes = Counter()
    gap_by_label_position = Counter()
    gap_char_types = Counter()
    full_person_shapes = Counter()
    covered_case_count = 0

    for di, doc in enumerate(docs):
        text = doc["text"]
        raw = predict(text)
        prepared = no_veil_counterfactual(
            text, {"by_source": {"ax": raw, "veil": []}})
        typed = {key(s) for s in prepared["entities"]}
        golds = {key(g) for g in doc["entities"]}
        strict["tp"] += len(typed & golds)
        strict["fp"] += len(typed - golds)
        strict["fn"] += len(golds - typed)

        for prediction in typed - golds:
            relation = fp_relation(prediction, golds)
            fp_by_label_relation[(prediction[0], relation)] += 1
            if prediction[0] == "person":
                person_fp_shapes[(relation, morphology(
                    text[prediction[1]:prediction[2]]))] += 1

        for index, case in cases_by_doc.get(di, []):
            gold = doc["entities"][case["gold_index"]]
            a, b = gold["start"], gold["end"]
            bits = bitmap(gold, prepared["mask_intervals"])
            if sum(bits) != case["covered_chars"]:
                raise ValueError(f"PINNED_MASK_MISMATCH_INDEX_{index}")
            position = gap_position(bits)
            label = gold["label"]
            gap_by_label_position[(label, position)] += 1
            for i, covered in enumerate(bits):
                if not covered:
                    gap_char_types[(label, character_kind(text[a+i]))] += 1
            if label == "person" and position == "FULL":
                full_person_shapes[morphology(text[a:b])] += 1
            covered_case_count += 1
            print("GAP_CASE", json.dumps({
                "index": index, "label": label,
                "position": position,
                "unprotected_chars": len(bits)-sum(bits),
                "gold_chars": len(bits),
            }, sort_keys=True), flush=True)
        if (di+1) % 50 == 0 or di+1 == len(docs):
            print(f"V7_PROFILE_PROGRESS {di+1}/{len(docs)}", flush=True)

    if covered_case_count != 28:
        raise ValueError("VALID_PII_CASE_COUNT_MISMATCH")
    if tuple(strict[x] for x in ("tp", "fp", "fn")) != (1833, 230, 125):
        raise ValueError("FROZEN_V7_STRICT_PARITY_MISMATCH_" + str(dict(strict)))
    print("\nVALID_GAP_BY_LABEL_AND_POSITION")
    for (label, position), count in sorted(gap_by_label_position.items()):
        print(f"  {label:32s} {position:20s} {count}")
    print("\nVALID_UNPROTECTED_CHAR_TYPES")
    for (label, kind), count in sorted(gap_char_types.items()):
        print(f"  {label:32s} {kind:20s} {count}")
    print("\nFULL_PERSON_MISS_SHAPES")
    for shape, count in sorted(full_person_shapes.items()):
        print(f"  {shape:32s} {count}")
    print("\nSTRICT_FP_BY_LABEL_AND_GOLD_OVERLAP")
    for (label, relation), count in sorted(fp_by_label_relation.items()):
        print(f"  {label:32s} {relation:33s} {count}")
    print("\nPERSON_STRICT_FP_SHAPES")
    for (relation, shape), count in sorted(person_fp_shapes.items()):
        print(f"  {relation:33s} {shape:25s} {count}")
    print("\nE4_3_PROFILE_COMPLETE", json.dumps({
        "dataset_sha256": digest, "reviewed_valid_pii_cases": covered_case_count,
        "strict": dict(strict),
        "strict_fp_not_verified_as_actual_false_positives": True,
        "gold_unchanged": True, "model_unchanged": True,
        "no_text_printed": True}, sort_keys=True))


if __name__ == "__main__":
    main()
