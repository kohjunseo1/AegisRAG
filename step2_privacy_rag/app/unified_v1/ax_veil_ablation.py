"""Read-only AX/Veil cost + routing ablation using pinned resident workers.

The original V6 routes six labels to AX and address to Veil. Compare
(1) frozen V6, (2) AX owning all seven labels under the SAME V6 policy,
(3) previously tested AX-only C2 policy. Score strict and character masking.
All labels/gold are scored only, never fed into inference or routing.

AX_ALL_V6_POLICY is a COUNTERFACTUAL experiment, NOT a production
fail-open substitution: it replays the AX ADDRESS spans through the frozen
Veil-owned ADDRESS slot. An actual Veil failure must still fail closed.

Examples:
 PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python -m unified_v1.ax_veil_ablation --dataset B --limit 5
 PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python -m unified_v1.ax_veil_ablation --dataset B
 PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python -m unified_v1.ax_veil_ablation --data ~/aegis_external_eval/team4/synth_hard_eval.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import selection as S
from .boost_probe import LABELS, fmt, span_keys
from .external_validation import load_external
from .lean import Worker
from .merge_validation import p95, rows_to_spans, score, timings_line, v6_detailed

MODES = ("V6_FROZEN", "AX_ALL_V6_POLICY", "AX_C2_CORRECTIONS", "AX_RAW")


def no_veil_counterfactual(text, snapshot):
    """Reuse the *same* frozen postprocessing, only change ADDRESS owner.

    The only changed source is ADDRESS. All six AX-owned types are unchanged.
    """
    raw = snapshot["by_source"]
    redirected = {
        "by_source": {
            "ax": raw["ax"],
            "veil": [dict(s) for s in raw["ax"] if s["label"] == "address"],
        }
    }
    return v6_detailed(text, redirected)


def check_semantics():
    from pii_detector.detector import FROZEN_OWNER
    expected = {lab: ("veil" if lab == "address" else "ax") for lab in LABELS}
    if FROZEN_OWNER != expected:
        raise RuntimeError("FROZEN_ROUTING_CHANGED")
    assert sum(source == "veil" for source in expected.values()) == 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dataset", choices=("A", "B"))
    group.add_argument("--data", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    check_semantics()

    path = (S.A if args.dataset == "A"
            else S.B if args.dataset == "B" else args.data.expanduser()).resolve()
    rows = load_external(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected_hash = {
        "A": "7ccf27589971ed0911756e527faf475144ab996f6d06416b4a167bda822",
        "B": "52bca604b38cf06d1445c57b9e48ad96a7654fed73dfcd0734a8eaa5df58b42f",
    }.get(args.dataset)
    if expected_hash and digest != expected_hash:
        raise RuntimeError("CORPUS_SHA256_MISMATCH")
    if len(set(str(row["id"]) for row in rows)) != len(rows):
        raise RuntimeError("DUPLICATE_DOCUMENT_ID")

    print(f"AX_ABLATION_INPUT_SHA256={digest} total_docs={len(rows)} "
          f"selected_docs={len(rows[:args.limit] if args.limit else rows)}", flush=True)
    if args.check_only:
        print("AX_ABLATION_INPUT_AND_ROUTING_PASS", flush=True)
        return 0

    head = subprocess.check_output(
        ["git", "-C", str(S.REFERENCE), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != "242543bf0eae91aa3d655227a747aa128707368e":
        raise RuntimeError("PINNED_REFERENCE_HEAD_MISMATCH")

    picked = rows[:args.limit] if args.limit is not None else rows
    totals = {mode: defaultdict(Counter) for mode in MODES}
    elapsed = defaultdict(list)
    resources = Counter()
    diagnostic = Counter()
    with Worker("B0") as v6, Worker("C2") as ax_only:
        for i, doc in enumerate(picked, 1):
            text = doc["text"]
            v6_worker = v6.call(text)
            ax_worker = ax_only.call(text)

            # Compare raw AX heads: same source, two runtime paths.
            lhs = span_keys(v6_worker["by_source"]["ax"])
            rhs = span_keys(ax_worker["raw"])
            diagnostic["ax_raw_parity_mismatch_docs"] += lhs != rhs
            diagnostic["ax_raw_symmetric_difference_spans"] += len(lhs ^ rhs)

            original = v6_detailed(text, v6_worker)
            ax_policy = no_veil_counterfactual(text, v6_worker)
            # The head owner is the only difference; never use Gold for policy.
            outputs = {
                "V6_FROZEN": (original["entities"], original["mask_intervals"]),
                "AX_ALL_V6_POLICY": (ax_policy["entities"], ax_policy["mask_intervals"]),
                "AX_C2_CORRECTIONS": (ax_worker["spans"], ax_worker["spans"]),
                "AX_RAW": (v6_worker["by_source"]["ax"], v6_worker["by_source"]["ax"]),
            }
            for name, (spans, masks) in outputs.items():
                score(totals[name], spans, doc["entities"], masks, len(text))

            elapsed["V6_WITH_PREPARE"].append(v6_worker["inference_ms"])
            elapsed["AX_ONLY_C2"].append(ax_worker["inference_ms"])
            if i == 1:
                resources["v6_cold_ms"] = round(v6_worker["cold_load_ms"], 2)
                resources["ax_cold_ms"] = round(ax_worker["cold_load_ms"], 2)
            resources["v6_peak_rss_mb"] = max(
                resources["v6_peak_rss_mb"], round(v6_worker["peak_rss_mb"], 2))
            resources["ax_peak_rss_mb"] = max(
                resources["ax_peak_rss_mb"], round(ax_worker["peak_rss_mb"], 2))
            diagnostic["v6_detector_review_docs"] += (
                original["status"] == "REVIEW_REQUIRED")
            diagnostic["ax_counterfactual_review_docs"] += (
                ax_policy["status"] == "REVIEW_REQUIRED")
            if i % 25 == 0 or i == len(picked):
                print(f"AX_ABLATION_PROGRESS {i}/{len(picked)}", flush=True)

    if args.dataset in ("A", "B") and args.limit is None:
        expected = {"A": (910,10,2), "B": (1430,61,9)}[args.dataset]
        observed = tuple(totals["V6_FROZEN"]["ALL"][k] for k in ("tp","fp","fn"))
        if expected != observed:
            raise RuntimeError("FROZEN_V6_PARITY_FAILED: " + repr(observed))

    print("\nMODE                   TP    FP    FN       F1  MASK_CHAR_RECALL "
          "FULL_UNCOVERED PARTLY_UNCOVERED")
    for name in MODES:
        c = totals[name]["ALL"]
        mask = c["covered_chars"] / c["gold_chars"] if c["gold_chars"] else 1.0
        print(f"{name:<22} {fmt(c)}      {mask:.6f} "
              f"         {c['fully_uncovered_gold']:>5} "
              f"            {c['partly_uncovered_gold']:>5}")
    print("\nMODE                   LABEL               TP    FP    FN      F1")
    for name in MODES:
        for label in ("address", "person", "account_number", "card_number",
                      "phone_number", "email", "resident_registration_number"):
            print(f"{name:<22} {label:<21} {fmt(totals[name][label])}")
    print("AX_ABLATION_DIAGNOSTICS",
          json.dumps(dict(diagnostic), sort_keys=True), flush=True)
    print("CPU_TIMINGS_CAUTION_V6_INCLUDES_PREPARE",
          json.dumps(timings_line(elapsed), sort_keys=True), flush=True)
    print("MODEL_RESOURCE_DIAGNOSTICS",
          json.dumps(dict(resources), sort_keys=True), flush=True)
    print("AX_ABLATION_COMPLETE model_failure_not_tested=true "
          "counterfactual_not_deployable=true no_gate_hmac_db_or_llm=true "
          "no_model_training_or_online_api=true", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
