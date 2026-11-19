"""Final read-only Stage1 comparison: V6, pinned team B1 Hybrid, team's exact Merge.

Reuses the pinned AegisRAG V6 and team model workers. Reproduces team
feature/detect-merge starter/detect_merge.py (blob 52216a7911bd...) with
the pinned reference pipeline's _drop_overlaps. Importantly, team V6
integration uses *mask_intervals*, not detector entities, and converts
untyped mask segments to PERSON (v6_client.py). Both are measured.

No raw text, names, spans, model outputs, HMAC secrets or DB writes are
emitted. Gold is used for scoring ONLY, never candidate selection.

First:  PYTHONPATH=step2_privacy_rag/app python -m unified_v1.merge_validation --dataset B --limit 5
Then:   PYTHONPATH=step2_privacy_rag/app python -m unified_v1.merge_validation --dataset B
External: ... --data ~/aegis_external_eval/team4/synth_hard_eval.jsonl
Optional: ... --team4-preds ~/aegis_external_eval/team4/rules+atonlee_synth_hard_eval.jsonl
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
from .contracts import Candidate, DetectionEvidence, config_hash, fingerprint
from .external_validation import load_external
from .lean import Worker
from .replay import SnapshotDetector

PINNED_TEAM_REFERENCE = "242543bf0eae91aa3d655227a747aa128707368e"
TEAM_MERGE_BLOB = "52216a7911bd361df11ff82a8406a02460740d2e"
MODES = ("V6_TYPED", "V6_MASK_VIEW", "HYBRID_B1", "TEAM_MERGE",
         "MERGE_TYPED_DIAG", "TEAM4_V10")


def overlaps(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def intersection(a, b):
    return min(a["end"], b["end"]) - max(a["start"], b["start"])


def teammate_merge(hyb, v6):
    """Exact decision logic of 5-team feature/detect-merge detect_merge.merge."""
    out, review = [], []
    for v in v6:
        hits = [h for h in hyb if overlaps(v, h)]
        if hits:
            v = {**v, "label": max(hits, key=lambda h: intersection(v, h))["label"]}
        else:
            review.append({**v, "source": "v6_only"})
        out.append({"label": v["label"], "start": v["start"], "end": v["end"]})
    for h in hyb:
        if not any(overlaps(h, v) for v in v6):
            out.append({"label": h["label"], "start": h["start"], "end": h["end"]})
            review.append({**h, "source": "hybrid_only"})
    out.sort(key=lambda s: (s["start"], s["end"]))
    review.sort(key=lambda s: (s["start"], s["end"]))
    return out, review


def test_merge_contract():
    v = [{"label": "person", "start": 0, "end": 3},
         {"label": "card_number", "start": 6, "end": 19},
         {"label": "person", "start": 40, "end": 42}]
    h = [{"label": "person", "start": 0, "end": 4},
         {"label": "phone_number", "start": 6, "end": 19},
         {"label": "person", "start": 30, "end": 34}]
    m, review = teammate_merge(h, v)
    assert m == [{"label":"person","start":0,"end":3},
                 {"label":"phone_number","start":6,"end":19},
                 {"label":"person","start":30,"end":34},
                 {"label":"person","start":40,"end":42}]
    assert [(e["start"], e["source"]) for e in review] == [
        (30, "hybrid_only"), (40, "v6_only")
    ]
    assert overlaps(h[0], v[0]) and not overlaps(v[0], h[1])


def v6_detailed(text, worker_output):
    """Reconstruct the frozen V6 *detector* output using actual raw heads."""
    raw = worker_output["by_source"]
    fp = fingerprint(text)
    evidence = DetectionEvidence(
        "document", "stage1_merge_comparison",
        {source: tuple(Candidate(source, "frozen-v6", ent["label"],
                                 ent["start"], ent["end"], fp,
                                 evidence_codes=tuple(ent.get("evidence_codes", ())))
                       for ent in raw[source]) for source in ("ax", "veil")},
        {"ax": "OK", "veil": "OK"}, (),
        config_hash({"benchmark": "stage1-merge", "v": 1}), fp, "own2",
    )
    with SnapshotDetector(text, evidence) as detector:
        detail = detector.detect(text)
    if detail.get("status") not in (
        "READY_FOR_PSEUDONYMIZATION", "REVIEW_REQUIRED"
    ):
        raise RuntimeError("V6_DETECTOR_NOT_READY")
    return detail


def v6_mask_view(detail):
    """Identical to 5-team starter/v6_client.py::to_spans conversion."""
    entities = detail.get("entities", [])
    out = []
    for m in detail.get("mask_intervals", []):
        start, end = int(m["start"]), int(m["end"])
        label = next((x["label"] for x in entities
                      if x["start"] < end and start < x["end"]), "person")
        out.append({"label": label, "start": start, "end": end})
    return out


def rows_to_spans(rows):
    return [{"label": x["label"], "start": int(x["start"]),
             "end": int(x["end"])} for x in rows]


def score(stats, pred, gold, masks, length):
    p = span_keys(pred)
    g = span_keys(gold)
    for label in ("ALL", *LABELS):
        pl = p if label == "ALL" else {x for x in p if x[0] == label}
        gl = g if label == "ALL" else {x for x in g if x[0] == label}
        c = stats[label]
        c["tp"] += len(pl & gl)
        c["fp"] += len(pl - gl)
        c["fn"] += len(gl - pl)
    coverage = bytearray(length)
    for m in masks:
        a, b = int(m["start"]), int(m["end"])
        if not 0 <= a < b <= length:
            raise RuntimeError("INVALID_MASK_OFFSET")
        coverage[a:b] = b"\x01" * (b-a)
    all_c = stats["ALL"]
    for _, a, b in g:
        count = sum(coverage[a:b])
        all_c["gold_chars"] += b-a
        all_c["covered_chars"] += count
        all_c["mask_overlap_gold"] += count > 0
        all_c["fully_uncovered_gold"] += count == 0
        all_c["partly_uncovered_gold"] += count < b-a


def p95(seq):
    seq = sorted(seq)
    if not seq:
        return None
    return round(seq[min(len(seq)-1, int((len(seq)-1)*0.95 + 0.999))], 2)


def timings_line(times):
    return {k: {"mean_ms": round(statistics.mean(v), 2), "p50_ms": round(
        statistics.median(v), 2), "p95_ms": p95(v)}
        for k, v in times.items() if v}


def team4_predictions(path, ids):
    if path is None:
        return None
    data = {}
    for line in path.expanduser().read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        rid = str(r["id"])
        if rid in data:
            raise RuntimeError("TEAM4_DUPLICATE_ID")
        data[rid] = rows_to_spans(r["entities"])
    if set(data) != ids:
        raise RuntimeError("TEAM4_DOCUMENT_SET_MISMATCH")
    return data


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    group = cli.add_mutually_exclusive_group(required=True)
    group.add_argument("--dataset", choices=("A", "B"))
    group.add_argument("--data", type=Path)
    cli.add_argument("--team4-preds", type=Path)
    cli.add_argument("--limit", type=int, default=None)
    cli.add_argument("--check-only", action="store_true")
    args = cli.parse_args()
    if args.limit is not None and args.limit <= 0:
        cli.error("--limit must be positive")
    test_merge_contract()
    src = (S.A if args.dataset == "A" else S.B if args.dataset == "B"
           else args.data.expanduser())
    src = src.resolve()
    full = load_external(src)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    if args.dataset:
        known = {"A": "7ccf27589971ed0911756e527faf475144ab996f6d06416b4a167bda51fda822",
                 "B": "52bca604b38cf06d1445c57b9e48ad96a7654fed73dfcd0734a8eaa5df58b42f"}[args.dataset]
        if sha != known:
            raise RuntimeError("PINNED_CORPUS_SHA256_MISMATCH")
    ids = {str(r["id"]) for r in full}
    if len(ids) != len(full):
        raise RuntimeError("DUPLICATE_DOCUMENT_ID")
    extra = team4_predictions(args.team4_preds, ids)
    print(f"MERGE_EVAL_INPUT_SHA256={sha} full_docs={len(full)} "
          f"selected_docs={len(full[:args.limit] if args.limit else full)} "
          f"team4_v10={'yes' if extra else 'no'}", flush=True)
    print("SELECTION_DATA_EXPOSED_NO_BLIND_CLAIM=true", flush=True)
    if args.check_only:
        print("MERGE_SCHEMA_AND_SMOKE_PASS", flush=True)
        return 0

    head = subprocess.check_output(
        ["git", "-C", str(S.REFERENCE), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != PINNED_TEAM_REFERENCE:
        raise RuntimeError("REFERENCE_HEAD_CHANGED_REPRODUCIBILITY_BLOCKED")
    from .lean_worker import reference_modules
    P, _, _ = reference_modules(S.REFERENCE)
    data = full[:args.limit] if args.limit is not None else full
    scores = {m: defaultdict(Counter) for m in MODES if m != "TEAM4_V10" or extra}
    times = defaultdict(list)
    issues = Counter()
    comparison = Counter()
    with Worker("B0") as v6, Worker("B1") as team:
        for idx, doc in enumerate(data, 1):
            text = doc["text"]
            start = time.perf_counter()
            a = v6.call(text)
            times["V6_WORKER_WITH_PREPARE"].append(
                a.get("inference_ms", (time.perf_counter()-start)*1000)
            )
            start = time.perf_counter()
            b = team.call(text)
            times["HYBRID_B1_WORKER"].append(
                b.get("inference_ms", (time.perf_counter()-start)*1000)
            )
            if idx == 1:
                issues["v6_cold_ms"] = round(a.get("cold_load_ms", 0), 2)
                issues["hybrid_cold_ms"] = round(b.get("cold_load_ms", 0), 2)
            issues["v6_peak_rss_mb"] = max(
                issues["v6_peak_rss_mb"], a.get("peak_rss_mb", 0)
            )
            issues["hybrid_peak_rss_mb"] = max(
                issues["hybrid_peak_rss_mb"], b.get("peak_rss_mb", 0)
            )

            detail = v6_detailed(text, a)
            typed = rows_to_spans(detail["entities"])
            masks = rows_to_spans([
                {**mask, "label": "person"}
                for mask in detail["mask_intervals"]
            ])
            view = v6_mask_view(detail)
            hyb = rows_to_spans(b["spans"])
            started = time.perf_counter()
            merged_raw, review = teammate_merge(hyb, view)
            merged = P._drop_overlaps(merged_raw)
            times["MERGE_LOGIC_ONLY"].append((time.perf_counter()-started)*1000)
            merged_typed, review_typed = teammate_merge(hyb, typed)
            merged_typed = P._drop_overlaps(merged_typed)

            predicted = {
                "V6_TYPED": typed,
                "V6_MASK_VIEW": view,
                "HYBRID_B1": hyb,
                "TEAM_MERGE": merged,
                "MERGE_TYPED_DIAG": merged_typed,
            }
            if extra:
                predicted["TEAM4_V10"] = extra[str(doc["id"])]
            gold = doc["entities"]
            for name, spans in predicted.items():
                mask_for_eval = masks if name == "V6_TYPED" else spans
                score(scores[name], spans, gold, mask_for_eval, len(text))
            issues["docs_v6_review"] += detail["status"] == "REVIEW_REQUIRED"
            issues["docs_hybrid_review"] += bool(b.get("review"))
            issues["docs_merge_disagreement"] += bool(review)
            issues["merge_review_spans"] += len(review)
            issues["merge_typed_review_spans"] += len(review_typed)
            issues["merge_review_v6_only"] += sum(
                r["source"] == "v6_only" for r in review
            )
            issues["merge_review_hybrid_only"] += sum(
                r["source"] == "hybrid_only" for r in review
            )
            baseline = span_keys(typed)
            final = span_keys(merged)
            ground = span_keys(gold)
            comparison["merge_recovered_tp_vs_v6"] += len((final-baseline)&ground)
            comparison["merge_lost_tp_vs_v6"] += len((baseline-final)&ground)
            comparison["merge_new_fp_vs_v6"] += len((final-baseline)-ground)
            comparison["merge_removed_fp_vs_v6"] += len((baseline-final)-ground)

            if idx % 25 == 0 or idx == len(data):
                print(f"MERGE_EVAL_PROGRESS {idx}/{len(data)}", flush=True)

    if args.dataset in ("A", "B") and args.limit is None:
        expected = {"A": (910,10,2), "B": (1430,61,9)}[args.dataset]
        observed = tuple(scores["V6_TYPED"]["ALL"][k] for k in ("tp","fp","fn"))
        if expected != observed:
            raise RuntimeError(f"V6_TYPED_PARITY_FAILED expected={expected} actual={observed}")

    print("\nMODE                   TP    FP    FN      F1   MASK_CHAR_RECALL "
          "FULL_UNCOVERED PARTLY_UNCOVERED")
    for mode, by_label in scores.items():
        c = by_label["ALL"]
        char_rec = c["covered_chars"] / c["gold_chars"] if c["gold_chars"] else 1.0
        print(f"{mode:<22} {fmt(c)}       {char_rec:.6f} "
              f"      {c['fully_uncovered_gold']:>4}  {c['partly_uncovered_gold']:>4}")
    print("\nMODE             LABEL                  TP    FP    FN       F1")
    for mode, by_label in scores.items():
        for label in ("person", "address", "account_number",
                      "card_number", "phone_number", "email",
                      "resident_registration_number"):
            print(f"{mode:<19} {label:<25} {fmt(by_label[label])}")
    print("MERGE_DISAGREEMENT_ESTIMATE",
          json.dumps(dict(issues), sort_keys=True), flush=True)
    print("PAIRED_VS_V6",
          json.dumps(dict(comparison), sort_keys=True), flush=True)
    print("CPU_RESOURCE_DIAGNOSTICS",
          json.dumps(timings_line(times), sort_keys=True), flush=True)
    print("MERGE_EVAL_COMPLETE gold_only_for_scoring=true "
          "no_hmac_gate_rag_db_api=true "
          "worker_v6_timing_includes_frozen_prepare=true "
          "hybrid_config=pinned_B1_no_ensemble "
          "merge_input=team_v6_client_mask_view", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
