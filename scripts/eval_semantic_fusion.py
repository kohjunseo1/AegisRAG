#!/usr/bin/env python3
"""Read-only frozen 187/88 semantic-fusion evaluation.

Compares existing dense/HMAC/region ranking with general-purpose variants:
(1) first-line/title semantic embedding, (2) semantic search intent with
pseudonym tokens stripped. Uses the EXISTING pinned KoE5 model only.
No HCX calls, raw documents, new embedding DB tables, or production changes.
Gold affects metrics ONLY, never ranking or vector construction.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"step2_privacy_rag"/"app"),
                str(ROOT/"step1_pii_detector"/"src")]
LOCAL = ROOT/"step2_privacy_rag"/"local_eval"
TOKEN = re.compile(r"<[A-Z][A-Z0-9_]*_[0-9a-f]{24}>|\[REDACTED[A-Z0-9_]*\]")
SPACE = re.compile(r"\s+")


def clean_for_semantic(text: str) -> str:
    """Exclude protected identifiers; never try to reverse them."""
    return SPACE.sub(" ", TOKEN.sub(" ", text)).strip()


def document_heading(masked_text: str) -> str:
    """Use only the first nonempty line; no Holdout-specific title heuristics."""
    for line in masked_text.splitlines():
        if line.strip():
            return clean_for_semantic(line[:260])
    return ""


def dense_ranks(scores, identifiers: list[str]) -> dict[str, int]:
    """Descending cosine similarity, stable doc ID tie-breaker."""
    values = [float(s) for s in scores]
    return {identifiers[idx]: rank+1 for rank, idx in enumerate(
        sorted(range(len(identifiers)), key=lambda i: (-values[i], identifiers[i]))
    )}


def fuse(rows: list[dict], *, method: str, title_ranks: dict[str, int],
         intent_ranks: dict[str, int] | None) -> list[dict]:
    """RRF over semantic views, within unchanged HMAC/region tiers.

    For intent-less (name-only) queries, fall back to baseline, not guessing.
    """
    if method not in {"baseline", "title25", "title50", "intent50", "title_intent"}:
        raise ValueError("INVALID_FUSION_METHOD")
    if method == "baseline" or not rows:
        return list(rows)
    def rrf(rank: int) -> float:
        return 1.0 / (60.0 + rank)
    def score(row: dict) -> float:
        did = row["doc_id"]
        body = rrf(row["dense_rank"])
        title = rrf(title_ranks[did])
        intent = rrf(intent_ranks[did]) if intent_ranks else body
        if method == "title25":
            return 0.75*body + 0.25*title
        if method == "title50":
            return 0.5*body + 0.5*title
        if method == "intent50":
            return 0.5*body + 0.5*intent
        return 0.5*body + 0.25*title + 0.25*intent

    return sorted(rows, key=lambda row: (
        -row["hmac_matches"], -row["region_matches"],
        -score(row), row["dense_rank"], row["doc_id"]
    ))


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError("FUSION_INPUT_MISSING")
    with path.open(encoding="utf-8") as inp:
        data = [json.loads(line) for line in inp if line.strip()]
    if not data or any(not isinstance(x, dict) for x in data):
        raise ValueError("FUSION_INPUT_INVALID")
    return data


def metrics(ranked: dict[str, list[str]], gold: dict[str, dict]) -> dict:
    n = len(gold)
    h1 = h5 = 0
    recall = rr = 0.0
    confusable1 = 0
    per_query = {}
    for qid, g in gold.items():
        ids = ranked.get(qid, [])
        relevant = set(g["relevant_doc_ids"])
        hit1 = bool(ids and ids[0] in relevant)
        hit5 = bool(relevant.intersection(ids[:5]))
        h1 += hit1
        h5 += hit5
        recall += len(relevant.intersection(ids[:5])) / len(relevant)
        rr += next((1/(i+1) for i,did in enumerate(ids[:5]) if did in relevant), 0)
        confusable1 += bool(ids and ids[0] in set(g.get("confusable_doc_ids", [])))
        per_query[qid] = (hit1, hit5)
    return dict(hit1=h1, hit5=h5,
                hit1_rate=round(h1/n, 4), hit5_rate=round(h5/n, 4),
                recall5=round(recall/n, 4), mrr5=round(rr/n, 4),
                confusable_top1=confusable1, per_query=per_query)


def execute(device: str, query_file: Path, gold_file: Path) -> None:
    import numpy as np
    import psycopg
    from psycopg import sql
    from core import LivePipeline, PreparedQuery

    # Force frozen 187-row Live index; no accidental snapshot usage.
    os.environ.update(AEGISRAG_TABLE="rag.aegis_live_docs",
                      AEGISRAG_INDEX_MODE="live", AEGISRAG_LIVE_MIN_ROWS="187",
                      AEGISRAG_TOPIC_BOOST="0")
    queries, labels = read_jsonl(query_file), read_jsonl(gold_file)
    if (len(queries) != 88 or len(labels) != 88
            or len({q.get("qid") for q in queries}) != 88
            or len({g.get("qid") for g in labels}) != 88):
        raise ValueError("FUSION_EXPECTED_88")
    gold = {g["qid"]: g for g in labels}
    if (set(q["qid"] for q in queries) != set(gold)
            or any(not isinstance(q.get("query"), str) or not q["query"].strip() for q in queries)
            or any(not isinstance(g.get("relevant_doc_ids"), list)
                   or not g["relevant_doc_ids"] for g in labels)):
        raise ValueError("FUSION_SCHEMA_INVALID")
    methods = ("baseline", "title25", "title50", "intent50", "title_intent")
    ranked: dict[str, dict[str, list[str]]] = {name: {} for name in methods}
    states = Counter()
    times_ms = Counter()
    pipeline = LivePipeline(ROOT, device=device, reuse_step1=True)
    try:
        initial = pipeline.index_status()
        if (initial.get("status") != "READY" or initial.get("rows") != 187
                or initial.get("table") != "rag.aegis_live_docs"
                or initial.get("key_matches") is not True):
            raise ValueError("FUSION_INDEX_INVALID")
        pipeline.preload_models()

        # The existing stored passage embeddings already encode full documents.
        # Keep them unchanged; create title vectors only in local memory.
        with psycopg.connect(**pipeline.db) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(sql.SQL("""
                    SELECT doc_id, masked_text, embedding::text
                    FROM {} WHERE key_tag=%s ORDER BY doc_id
                """).format(pipeline.table), (pipeline.tag,))
                db_rows = cur.fetchall()
        if len(db_rows) != 187:
            raise ValueError("FUSION_DOCUMENT_COUNT_INVALID")
        doc_ids = [doc_id for doc_id,_,_ in db_rows]
        full_vectors = np.asarray(
            [np.fromstring(v[1:-1], sep=",", dtype=np.float32)
             for _,_,v in db_rows], dtype=np.float32)
        if full_vectors.shape != (187, 1024) or not np.isfinite(full_vectors).all():
            raise ValueError("FUSION_STORED_VECTORS_INVALID")
        # Full vectors were normalized during ingestion, but normalize again
        # defensively to avoid any inconsistent metadata from an older index.
        full_vectors /= np.maximum(np.linalg.norm(full_vectors,axis=1,keepdims=True),1e-12)
        headings = [document_heading(t) for _,t,_ in db_rows]
        encode_start = time.perf_counter()
        title_vectors = np.asarray(pipeline._model.encode(
            ["passage: " + (h or "문서") for h in headings],
            normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False, batch_size=8), dtype=np.float32)
        times_ms["title_index_once"] = round((time.perf_counter()-encode_start)*1000,2)
        if title_vectors.shape != (187,1024):
            raise ValueError("FUSION_TITLE_VECTORS_INVALID")

        print("FUSION_INDEX_READY rows=187 queries=88 db_writes=0 hcx_calls=0",
              flush=True)
        for number, question in enumerate(queries, 1):
            qid = question["qid"]
            prepared = pipeline.prepare(question["query"])
            if not isinstance(prepared, PreparedQuery):
                states[str(prepared)] += 1
                for name in methods:
                    ranked[name][qid] = []
                continue
            states["READY"] += 1
            rows = pipeline.retrieve(prepared, top_k=None)
            if len(rows) != 187 or {r["doc_id"] for r in rows} != set(doc_ids):
                raise ValueError("FUSION_RETRIEVAL_INCOMPLETE")

            # This additional semantic query excludes opaque HMAC identifiers,
            # but the independently verified exact-match tier is preserved.
            semantic_text = clean_for_semantic(prepared.text)
            if not semantic_text:
                variants = {name: rows for name in methods}
            else:
                started = time.perf_counter()
                qvec = np.asarray(pipeline._model.encode(
                    ["query: " + semantic_text], normalize_embeddings=True,
                    convert_to_numpy=True, show_progress_bar=False),
                    dtype=np.float32)[0]
                times_ms["query_semantic_extra"] += (time.perf_counter()-started)*1000
                title_rank = dense_ranks(title_vectors @ qvec, doc_ids)
                # Only use the intent-only view if the protected query actually
                # contained a masked identifier. Otherwise it is the baseline.
                intent_rank = dense_ranks(full_vectors @ qvec, doc_ids) if prepared.tokens else None
                variants = {"baseline":rows}
                started = time.perf_counter()
                for name in methods[1:]:
                    variants[name] = fuse(rows,method=name,
                                          title_ranks=title_rank, intent_ranks=intent_rank)
                times_ms["fusion_rerank"] += (time.perf_counter()-started)*1000
            for name, result in variants.items():
                ranked[name][qid] = [r["doc_id"] for r in result]
            if number%20 == 0 or number == 88:
                print("FUSION_PROGRESS",number,"/88",flush=True)

        if pipeline.index_status() != initial:
            raise ValueError("FUSION_INDEX_CHANGED")
    finally:
        pipeline.close()
    scored = {name: metrics(ranked[name], gold) for name in methods}
    base = scored["baseline"]
    if base["hit1"] != 77 or base["hit5"] != 83:
        raise ValueError("FUSION_BASELINE_MISMATCH")
    print("FUSION_GATE_STATES",json.dumps(states,sort_keys=True),flush=True)
    for name in methods:
        result = scored[name]
        bq, rq = base["per_query"], result["per_query"]
        improved1 = [q for q in gold if not bq[q][0] and rq[q][0]]
        regressed1 = [q for q in gold if bq[q][0] and not rq[q][0]]
        improved5 = [q for q in gold if not bq[q][1] and rq[q][1]]
        regressed5 = [q for q in gold if bq[q][1] and not rq[q][1]]
        print("FUSION_RESULT",json.dumps({
            "name":name,"hit1":result["hit1"],"hit5":result["hit5"],
            "recall5":result["recall5"],"mrr5":result["mrr5"],
            "confusable_top1":result["confusable_top1"],
            "delta_hit1":result["hit1"]-base["hit1"],
            "delta_hit5":result["hit5"]-base["hit5"],
            "hit1_improved_qids":improved1,"hit1_regressed_qids":regressed1,
            "hit5_improved_qids":improved5,"hit5_regressed_qids":regressed5
        },ensure_ascii=False,sort_keys=True),flush=True)
    print("FUSION_OFFLINE_EXTRA_MS",json.dumps(dict(times_ms),sort_keys=True),flush=True)
    print("FUSION_COMPLETE production_ranking_unchanged=true no_db_writes=true",
          flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device",choices=["cpu","mps"],default="cpu")
    parser.add_argument("--queries",type=Path,
                        default=LOCAL/"holdout_retrieval_queries.jsonl")
    parser.add_argument("--gold",type=Path,
                        default=LOCAL/"holdout_retrieval_queries_gold.jsonl")
    args=parser.parse_args()
    try:
        execute(args.device,args.queries,args.gold)
        return 0
    except Exception as exc:
        safe=str(exc) if isinstance(exc,ValueError) and str(exc).startswith("FUSION_") else type(exc).__name__
        print("FUSION_FAILED",safe,file=sys.stderr,flush=True)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
