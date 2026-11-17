#!/usr/bin/env python3
"""Frozen 187/88 retrieval ablations. Strictly READ-ONLY, no HCX, no DB writes.

Variants do NOT alter production retrieval, use no Gold at ranking time, and
never output private questions/documents/tokens. Only aggregate metrics + QIDs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"step2_privacy_rag/app"),
                str(ROOT/"step1_pii_detector/src")]
PRIVATE = ROOT/"step2_privacy_rag/local_eval"


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError("ABLATION_INPUT_MISSING")
    with path.open(encoding="utf-8") as handle:
        data = [json.loads(line) for line in handle if line.strip()]
    if not data or any(not isinstance(obj, dict) for obj in data):
        raise ValueError("ABLATION_INPUT_INVALID")
    return data


def report(ranked: dict[str, list[str]], gold: dict[str, dict],
           eligible: set[str]) -> dict:
    n = len(gold)
    h1 = h5 = 0
    recall5 = mrr5 = 0.0
    confusable_top1 = confusable_top5 = 0
    wins1 = wins5 = set()
    hits1 = {}
    hits5 = {}
    for qid, g in gold.items():
        docs = ranked.get(qid, [])
        relevant = set(g["relevant_doc_ids"])
        if not relevant:
            raise ValueError("ABLATION_GOLD_EMPTY")
        top5 = docs[:5]
        got1 = int(bool(docs and docs[0] in relevant))
        got5 = int(bool(relevant.intersection(top5)))
        hits1[qid] = got1
        hits5[qid] = got5
        h1 += got1
        h5 += got5
        recall5 += len(relevant.intersection(top5)) / len(relevant)
        mrr5 += next((1/(i+1) for i, d in enumerate(top5) if d in relevant), 0)
        confusable = set(g.get("confusable_doc_ids", []))
        if qid in eligible:
            confusable_top1 += int(bool(docs and docs[0] in confusable))
            confusable_top5 += int(bool(confusable.intersection(top5)))
    return dict(hit1=h1, hit5=h5, hit1_total=round(h1/n, 4),
                hit5_total=round(h5/n, 4), recall5_total=round(recall5/n, 4),
                mrr5_total=round(mrr5/n, 4), confusable_top1=confusable_top1,
                confusable_top5=confusable_top5, hits1=hits1, hits5=hits5)


def execute(args: argparse.Namespace) -> None:
    from core import LivePipeline, PreparedQuery
    from retrieval_ablation import make_lexical_index, lexical_rerank, coverage_select

    # Hard-pin original serving index and prevent opt-in experimental topic boosts.
    os.environ.update(AEGISRAG_TABLE="rag.aegis_live_docs",
                      AEGISRAG_INDEX_MODE="live", AEGISRAG_LIVE_MIN_ROWS="187",
                      AEGISRAG_TOPIC_BOOST="0")
    queries = read_jsonl(args.queries)
    gold_rows = read_jsonl(args.gold)
    if (len(queries) != 88 or len(gold_rows) != 88
            or len({q.get("qid") for q in queries}) != 88
            or len({g.get("qid") for g in gold_rows}) != 88):
        raise ValueError("ABLATION_EXPECTED_88")
    gold = {g["qid"]: g for g in gold_rows}
    if (set(q["qid"] for q in queries) != set(gold)
            or any(not isinstance(q.get("query"), str) or not q["query"].strip()
                   for q in queries)
            or any(not isinstance(g.get("relevant_doc_ids"), list)
                   or not g["relevant_doc_ids"] for g in gold_rows)):
        raise ValueError("ABLATION_SCHEMA_INVALID")
    variants = ("baseline", "lexical_015", "lexical_030", "coverage_015")
    ranked = {variant: {} for variant in variants}
    states = Counter()
    eligible = set()
    offline_ms = {variant: 0.0 for variant in variants if variant != "baseline"}
    pipeline = LivePipeline(ROOT, args.device, reuse_step1=True)
    try:
        index_before = pipeline.index_status()
        if (index_before.get("status") != "READY"
                or index_before.get("rows") != 187
                or index_before.get("key_matches") is not True):
            raise ValueError("ABLATION_INDEX_INVALID")
        pipeline.preload_models()
        print("ABLATION_INDEX_READY rows=187 queries=88 no_hcx=true no_db_writes=true",
              flush=True)
        lex_index = None
        for number, query in enumerate(queries, 1):
            prepared = pipeline.prepare(query["query"])
            if not isinstance(prepared, PreparedQuery):
                states[str(prepared)] += 1
                for variant in variants:
                    ranked[variant][query["qid"]] = []
                continue
            states["READY"] += 1
            eligible.add(query["qid"])
            rows = pipeline.retrieve(prepared, top_k=None)
            if len(rows) != 187:
                raise ValueError("ABLATION_RETRIEVAL_INCOMPLETE")
            if lex_index is None:
                lex_index = make_lexical_index(rows)
            candidates = {
                "baseline": rows,
            }
            for name, weight in (("lexical_015", .15), ("lexical_030", .30)):
                start = time.perf_counter()
                candidates[name] = lexical_rerank(rows, prepared.text, weight, lex_index)
                offline_ms[name] += (time.perf_counter()-start)*1000
            start = time.perf_counter()
            candidates["coverage_015"] = coverage_select(
                rows, k=5, pool_size=20, penalty=.15,
                doc_grams=lex_index[0])
            offline_ms["coverage_015"] += (time.perf_counter()-start)*1000
            for name, docs in candidates.items():
                ranked[name][query["qid"]] = [d["doc_id"] for d in docs]
            if number % 20 == 0 or number == 88:
                print("ABLATION_PROGRESS", number, "/88", flush=True)
        if pipeline.index_status() != index_before:
            raise ValueError("ABLATION_INDEX_CHANGED")
    finally:
        pipeline.close()
    summaries = {v: report(ranked[v], gold, eligible) for v in variants}
    baseline = summaries["baseline"]
    if baseline["hit1"] != 77 or baseline["hit5"] != 83:
        raise ValueError("ABLATION_BASELINE_MISMATCH")
    print("ABLATION_GATE_STATES", json.dumps(states, sort_keys=True), flush=True)
    for name in variants:
        result = summaries[name]
        improved1 = sorted(q for q in gold
                           if result["hits1"][q] and not baseline["hits1"][q])
        regressed1 = sorted(q for q in gold
                            if baseline["hits1"][q] and not result["hits1"][q])
        improved5 = sorted(q for q in gold
                           if result["hits5"][q] and not baseline["hits5"][q])
        regressed5 = sorted(q for q in gold
                            if baseline["hits5"][q] and not result["hits5"][q])
        print("ABLATION_RESULT", json.dumps({
            "name": name, "hit1": result["hit1"], "hit5": result["hit5"],
            "recall5": result["recall5_total"], "mrr5": result["mrr5_total"],
            "confusable_top1": result["confusable_top1"],
            "confusable_top5": result["confusable_top5"],
            "delta_hit1": result["hit1"]-baseline["hit1"],
            "delta_hit5": result["hit5"]-baseline["hit5"],
            "hit1_improved_qids": improved1, "hit1_regressed_qids": regressed1,
            "hit5_improved_qids": improved5, "hit5_regressed_qids": regressed5,
            "offline_rerank_mean_ms": round(offline_ms.get(name, 0) /
                                             max(1, len(eligible)), 2),
        }, ensure_ascii=False, sort_keys=True), flush=True)
    print("ABLATION_COMPLETE read_only=true gold_never_used_for_ranking=true "
          "production_code_unchanged=true", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    parser.add_argument("--queries", type=Path,
                        default=PRIVATE/"holdout_retrieval_queries.jsonl")
    parser.add_argument("--gold", type=Path,
                        default=PRIVATE/"holdout_retrieval_queries_gold.jsonl")
    args = parser.parse_args()
    try:
        execute(args)
        return 0
    except Exception as exc:
        safe = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("ABLATION_") else type(exc).__name__
        print("ABLATION_FAILED", safe, file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
