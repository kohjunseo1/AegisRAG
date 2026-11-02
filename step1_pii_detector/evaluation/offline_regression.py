"""Re-score exact saved predictions with the deployed postal validator and core policy.

Only reads the two explicitly supplied JSONL paths. It never searches Drive,
downloads models, runs inference, or writes source documents and spans to reports.
Latency covers local postprocessing only; it excludes model inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from pii_detector.structural_policy import mask_union, refine_spans


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def span_key(entity: dict) -> tuple[str, int, int]:
    return str(entity["label"]), int(entity["start"]), int(entity["end"])


def validate_inputs(gold: list[dict], predictions: list[dict]) -> dict[str, list[dict]]:
    gold_ids = [str(row["id"]) for row in gold]
    prediction_ids = [str(row["id"]) for row in predictions]
    if len(set(gold_ids)) != len(gold_ids) or len(set(prediction_ids)) != len(prediction_ids):
        raise ValueError("duplicate document IDs")
    if set(gold_ids) != set(prediction_ids):
        raise ValueError("gold and prediction document IDs differ")
    gold_by_id = {str(row["id"]): row for row in gold}
    pred_by_id = {str(row["id"]): row["entities"] for row in predictions}
    for doc_id, row in gold_by_id.items():
        text = row["text"]
        for entity in row["entities"]:
            _, start, end = span_key(entity)
            if not 0 <= start < end <= len(text):
                raise ValueError("gold span out of bounds")
        for entity in pred_by_id[doc_id]:
            _, start, end = span_key(entity)
            if not 0 <= start < end <= len(text):
                raise ValueError("prediction span out of bounds")
    return pred_by_id


def _metrics(gold: list[dict], predictions: dict[str, list[dict]],
             masks: dict[str, list[dict]]) -> dict:
    tp = fp = fn = total_gold_chars = unmasked_gold_chars = 0
    per_label: dict[str, Counter] = defaultdict(Counter)
    for row in gold:
        doc_id = str(row["id"])
        actual = {span_key(e) for e in row["entities"]}
        predicted = {span_key(e) for e in predictions[doc_id]}
        for label, _, _ in actual & predicted:
            per_label[label]["tp"] += 1
        for label, _, _ in predicted - actual:
            per_label[label]["fp"] += 1
        for label, _, _ in actual - predicted:
            per_label[label]["fn"] += 1
        tp += len(actual & predicted)
        fp += len(predicted - actual)
        fn += len(actual - predicted)

        gold_chars = {i for _, start, end in actual for i in range(start, end)}
        masked_chars = {i for interval in masks[doc_id]
                        for i in range(int(interval["start"]), int(interval["end"]))}
        total_gold_chars += len(gold_chars)
        unmasked_gold_chars += len(gold_chars - masked_chars)

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    result = {
        "documents": len(gold), "tp": tp, "fp": fp, "fn": fn,
        "strict_precision": precision, "strict_recall": recall, "strict_f1": f1,
        "mask_recall": ((total_gold_chars - unmasked_gold_chars) / total_gold_chars
                        if total_gold_chars else 1.0),
        "unmasked_gold_characters": unmasked_gold_chars,
        "per_label": {label: {"tp": stats["tp"], "fp": stats["fp"], "fn": stats["fn"]}
                      for label, stats in sorted(per_label.items())},
    }
    result["review_documents"] = None
    result["review_rate"] = None
    result["review_rate_note"] = "saved 05 predictions contain spans only, not detector status/issues"
    return result


def _percentile(samples: list[float], fraction: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def evaluate(gold: list[dict], raw: dict[str, list[dict]], *, include_postal: bool) -> dict:
    gold_by_id = {str(row["id"]): row for row in gold}
    postal: dict[str, list[dict]] = {}
    refined: dict[str, list[dict]] = {}
    masks: dict[str, list[dict]] = {}
    policy_counts: Counter = Counter()
    changed_doc_ids: set[str] = set()
    review_from_postal = set()
    latencies_ms: list[float] = []

    if include_postal:
        from pii_detector.detector import validate_boundaries

    for doc_id, row in gold_by_id.items():
        text = row["text"]
        started = time.perf_counter()
        initial = raw[doc_id]
        if include_postal:
            initial, postal_events = validate_boundaries(text, initial)
            if any(event["status"].startswith("review_") for event in postal_events):
                review_from_postal.add(doc_id)
        else:
            postal_events = []
        postal[doc_id] = initial
        revised, events = refine_spans(text, initial)
        refined[doc_id] = revised
        masks[doc_id] = mask_union(initial, revised, text)
        latencies_ms.append((time.perf_counter() - started) * 1000)
        if events:
            changed_doc_ids.add(doc_id)
            for event in events:
                policy_counts[event["rule"]] += 1

    raw_masks = {doc_id: [{"start": e["start"], "end": e["end"]} for e in spans]
                 for doc_id, spans in raw.items()}
    postal_masks = {doc_id: [{"start": e["start"], "end": e["end"]} for e in spans]
                    for doc_id, spans in postal.items()}

    # The union must protect every baseline/postal span, including strict spans removed by policy.
    for doc_id, spans in postal.items():
        for entity in spans:
            if not any(mask["start"] <= entity["start"] and mask["end"] >= entity["end"]
                       for mask in masks[doc_id]):
                raise AssertionError("mask interval does not cover a pre-policy span")

    reports = {
        "frozen_predictions": _metrics(gold, raw, raw_masks),
        "after_existing_postal_validator": _metrics(gold, postal, postal_masks),
        "after_two_core_rules": _metrics(gold, refined, masks),
    }
    gold_spans = {str(row["id"]): {span_key(e) for e in row["entities"]} for row in gold}
    removed = [(doc_id, span_key(e)) for doc_id, spans in postal.items()
               for e in spans if span_key(e) not in {span_key(x) for x in refined[doc_id]}]
    removed_true = sum(key in gold_spans[doc_id] for doc_id, key in removed)
    removed_false = len(removed) - removed_true
    reports["policy"] = {
        "version": "aegis_core_v1",
        "changed_documents": len(changed_doc_ids),
        "changed_spans_by_rule": dict(sorted(policy_counts.items())),
        "removed_false_positive_spans": removed_false,
        "removed_true_positive_spans_added_as_false_negatives": removed_true,
        "documents_with_existing_postal_review_events": len(review_from_postal),
        "postprocessing_latency_ms": {
            "scope": "postal boundary validation plus structural rules; excludes model inference",
            "p50": statistics.median(latencies_ms) if latencies_ms else None,
            "p95": _percentile(latencies_ms, 0.95),
        },
    }
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path,
                        help="exact path to the local Official Test/Holdout gold JSONL")
    parser.add_argument("--pred", required=True, type=Path,
                        help="exact path to the saved 05 labelwise prediction JSONL")
    parser.add_argument("--dataset", required=True, choices=("official_test", "official_holdout"))
    parser.add_argument("--include-existing-postal", action="store_true",
                        help="run the repository's existing conservative validate_boundaries")
    parser.add_argument("--report", type=Path,
                        help="optional output path for text-free summary metrics JSON")
    args = parser.parse_args()
    gold_path, pred_path = args.gold.resolve(strict=True), args.pred.resolve(strict=True)
    gold = read_jsonl(gold_path)
    prediction_rows = read_jsonl(pred_path)
    expected_documents = {"official_test": 200, "official_holdout": 187}[args.dataset]
    if len(gold) != expected_documents:
        raise ValueError(f"{args.dataset} requires {expected_documents} gold documents")
    predictions = validate_inputs(gold, prediction_rows)
    report = {
        "dataset": args.dataset,
        "gold_documents": len(gold),
        "gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        "predictions_sha256": hashlib.sha256(pred_path.read_bytes()).hexdigest(),
        "include_existing_postal": args.include_existing_postal,
        "results": evaluate(gold, predictions, include_postal=args.include_existing_postal),
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
