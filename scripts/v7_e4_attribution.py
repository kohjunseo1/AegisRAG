#!/usr/bin/env python3
"""E4: local V7 uncovered-Gold root-cause attribution (read-only).

Uses pinned Hard403 audit, offline human decisions, raw A.X predictions and
frozen post-policy masks. Prints counts/indices only; never raw PII strings.
No Gold mutation, training, DB write, HF upload or external API use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
APP = ROOT / "step2_privacy_rag" / "app"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(APP))

from v7_mask_review_cli import cid, load_decisions, validate

# Describes overlap evidence, not proven underlying failure mechanism.
RAW_NO_OVERLAP = "RAW_NO_OVERLAP"
RAW_OTHER_LABEL_ONLY = "RAW_OTHER_LABEL_ONLY"
RAW_SAME_LABEL_PARTIAL = "RAW_SAME_LABEL_PARTIAL"
POSTPOLICY_GAP = "POSTPOLICY_GAP"
UNEXPLAINED = "UNEXPLAINED"


def covered_chars(gold, spans, text_len, label=None):
    a, b = gold["start"], gold["end"]
    if not (0 <= a < b <= text_len):
        raise ValueError("GOLD_OFFSET_INVALID")
    bitmap = bytearray(b - a)
    for span in spans:
        if label is not None and span["label"] != label:
            continue
        s, e = int(span["start"]), int(span["end"])
        if not (0 <= s < e <= text_len):
            raise ValueError("PRED_OR_MASK_OFFSET_INVALID")
        left, right = max(s, a), min(e, b)
        if left < right:
            bitmap[left-a:right-a] = b"\x01" * (right-left)
    return sum(bitmap)


def attribute(gold, raw, prepared, text_len):
    n = gold["end"] - gold["start"]
    raw_any = covered_chars(gold, raw, text_len)
    raw_same = covered_chars(gold, raw, text_len, gold["label"])
    final_mask = covered_chars(gold, prepared["mask_intervals"], text_len)
    if final_mask == n:
        raise ValueError("AUDIT_EXPECTED_UNCOVERED_GOLD")
    if raw_any == 0:
        category = RAW_NO_OVERLAP
    elif raw_same == 0:
        category = RAW_OTHER_LABEL_ONLY
    elif raw_same < n:
        category = RAW_SAME_LABEL_PARTIAL
    elif final_mask < n:
        category = POSTPOLICY_GAP
    else:
        category = UNEXPLAINED
    return {
        "category": category,
        "gold_chars": n,
        "raw_any_covered_chars": raw_any,
        "raw_same_label_covered_chars": raw_same,
        "final_mask_covered_chars": final_mask,
        "final_unprotected_chars": n-final_mask,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--audit", type=Path, required=True)
    p.add_argument("--decisions", type=Path, required=True)
    p.add_argument("--checkpoint", type=Path, required=True)
    args = p.parse_args()
    data, audit, decisions, checkpoint = (v.expanduser().resolve() for v in
                                        (args.data, args.audit, args.decisions, args.checkpoint))
    if not (checkpoint / "config.json").is_file():
        raise ValueError("V7_CHECKPOINT_CONFIG_NOT_FOUND")
    if not decisions.is_file():
        raise ValueError("DECISIONS_JSON_NOT_FOUND")
    digest, docs, cases = validate(data, audit)
    if len(cases) != 46:
        raise ValueError("EXPECTED_46_AUDIT_CASES")
    saved = load_decisions(decisions, digest, cases)
    if len(saved["decisions"]) != len(cases):
        raise ValueError("REVIEW_NOT_COMPLETE")
    expected_count = Counter(v.get("decision") for v in saved["decisions"].values())
    if expected_count != Counter({"VALID_PII": 28, "INVALID_GOLD": 15,
                                  "POLICY_DISPUTED": 3}):
        raise ValueError("ADJUDICATION_COUNTS_CHANGED")
    print("INPUT_VALIDATED", json.dumps({
        "dataset_sha256": digest, "audit_cases": len(cases),
        "human_decisions": dict(sorted(expected_count.items())),
        "checkpoint_config_sha256": hashlib.sha256(
            (checkpoint / "config.json").read_bytes()).hexdigest(),
        "data_unmodified": True}, sort_keys=True), flush=True)

    # Load model only after verifying the dataset and decisions.
    from ax_v7_eval import predictor
    from unified_v1.ax_veil_ablation import no_veil_counterfactual
    predict = predictor(checkpoint, "cpu")

    counts = Counter()
    category_by_label = Counter()
    raw_same_total = 0
    final_unprotected_total = 0
    cached = {}
    for index, case in enumerate(cases):
        key = cid(case)
        decision = saved["decisions"][key]["decision"]
        # Do not relabel invalid/disputed cases; original Gold stays intact.
        if decision != "VALID_PII":
            continue
        di = case["document_index"]
        if di not in cached:
            doc = docs[di]
            raw = predict(doc["text"])
            prepared = no_veil_counterfactual(
                doc["text"], {"by_source": {"ax": raw, "veil": []}})
            cached[di] = (raw, prepared)
        raw, prepared = cached[di]
        gold = docs[di]["entities"][case["gold_index"]]
        stats = attribute(gold, raw, prepared, len(docs[di]["text"]))
        if stats["final_mask_covered_chars"] != case["covered_chars"]:
            raise ValueError(f"MASK_DIFFERS_FROM_PINNED_AUDIT_CASE_{index}")
        raw_same_total += stats["raw_same_label_covered_chars"]
        final_unprotected_total += stats["final_unprotected_chars"]
        group = (case["coverage"], case["label"], stats["category"])
        counts[group] += 1
        category_by_label[(case["label"], stats["category"])] += 1
        print("VALID_CASE", json.dumps({
            "index": index, "coverage": case["coverage"],
            "label": case["label"], **stats}, sort_keys=True), flush=True)
    if sum(counts.values()) != 28:
        raise ValueError("EXPECTED_28_VALID_PII_CASES")
    print("\nERROR_ATTRIBUTION_BREAKDOWN")
    for (coverage, label, cause), n in sorted(counts.items()):
        print(f"  {coverage:7s} {label:32s} {cause:29s} {n}")
    print("\nBY_LABEL_AND_RAW_CAUSE")
    for (label, cause), n in sorted(category_by_label.items()):
        print(f"  {label:32s} {cause:29s} {n}")
    print("E4_ATTRIBUTION_COMPLETE", json.dumps({
        "valid_cases": sum(counts.values()), "distinct_docs_inferred": len(cached),
        "final_unprotected_gold_chars": final_unprotected_total,
        "raw_same_label_gold_chars_covered_sum": raw_same_total,
        "original_gold_unchanged": True, "model_unchanged": True,
        "no_private_text_printed": True,
        "disclaimer": "Cause groups describe overlap evidence, not proven root mechanisms",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
