#!/usr/bin/env python3
"""Real Step1 -> Gate -> HMAC -> DB -> HTTP E2E, strictly localhost/no HCX.

This does NOT rebuild indexes, re-stage 187 docs, approve REVIEW, or evaluate
factual HCX answers. Uses the existing pinned 187-row index and original 88
private questions for a bounded HTTP route smoke. Prints aggregate codes only.
"""
from __future__ import annotations

import argparse
from http.server import HTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "step2_privacy_rag/app"),
                str(ROOT / "step1_pii_detector/src")]


def request_json(url: str, question: str | None = None) -> dict:
    if question is None:
        request = urllib.request.Request(url, method="GET")
    else:
        request = urllib.request.Request(
            url, data=json.dumps({"question": question}, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     "Origin": url.split("/api/")[0]})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError("HTTP_STATUS_NOT_200")
        data = json.load(response)
    if not isinstance(data, dict):
        raise RuntimeError("HTTP_JSON_NOT_OBJECT")
    return data


def _valid_reply(reply: dict, *, expected: str) -> None:
    if reply.get("decision") != expected or reply.get("mode") != "local":
        raise RuntimeError("LOCAL_HTTP_DECISION_MISMATCH")
    if reply.get("citations") != [] or reply.get("sources") != []:
        raise RuntimeError("LOCAL_HTTP_CITATION_LEAK")
    if reply.get("diagnostics", {}).get("gate_state") not in {
        "READY", "REVIEW", "REVIEW_REQUIRED", "BLOCKED"
    }:
        raise RuntimeError("LOCAL_HTTP_GATE_INVALID")
    diagnostics = reply.get("diagnostics", {})
    if expected == "LOCAL_SEARCH_ONLY":
        rows = diagnostics.get("retrieval_candidates", [])
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("LOCAL_HTTP_CANDIDATES_MISSING")
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "doc_id", "dense_rank", "hmac_matches", "region_matches"
            }:
                raise RuntimeError("LOCAL_HTTP_UNEXPECTED_CANDIDATE_FIELD")
    else:
        if diagnostics.get("retrieved_count") != 0:
            raise RuntimeError("LOCAL_HTTP_DEFERRED_RETRIEVAL")


def execute(args) -> None:
    from app import Handler
    from core import LivePipeline
    from dataset_preparation import load_jsonl

    # Prevent accidental verification on an unrelated table; only DB SELECTs.
    os.environ.update({
        "AEGISRAG_TABLE": "rag.aegis_live_docs",
        "AEGISRAG_INDEX_MODE": "live",
        "AEGISRAG_LIVE_MIN_ROWS": "187",
        "AEGISRAG_TOPIC_BOOST": "0",
    })
    os.environ.pop("AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED", None)

    questions = load_jsonl(args.queries)
    if len(questions) != 88 or len({q.get("qid") for q in questions}) != 88:
        raise RuntimeError("RELEASE_QUERY_SET_INVALID")
    if any(not isinstance(q.get("query"), str) or not q["query"].strip()
           for q in questions):
        raise RuntimeError("RELEASE_QUERY_TEXT_INVALID")

    pipeline = LivePipeline(ROOT, args.device, reuse_step1=True)
    server = None
    thread = None
    try:
        index_before = pipeline.index_status()
        if (index_before.get("status") != "READY" or
                index_before.get("rows") != 187 or
                index_before.get("key_matches") is not True or
                index_before.get("table") != "rag.aegis_live_docs"):
            raise RuntimeError("RELEASE_INDEX_NOT_READY")
        print("RELEASE_INDEX_READY rows=187 key_matches=true", flush=True)

        # One real model preload, shared by the HTTP server and local checks.
        timings = pipeline.preload_models()
        print("RELEASE_MODELS_PRELOADED_MS", round(timings["total_ms"], 2), flush=True)

        # One READY question and one deferred question are sufficient to
        # exercise both HTTP routes without repeating the full 88-query eval.
        ready = None
        deferred = None
        for item in questions:
            state, prepared, evidence = pipeline.ask_local(item["query"])
            if prepared is not None and evidence and ready is None:
                ready = item["query"]
            if prepared is None and deferred is None:
                deferred = (item["query"], state)
            if ready is not None and deferred is not None:
                break
        if ready is None or deferred is None:
            raise RuntimeError("RELEASE_TEST_QUESTIONS_NOT_FOUND")

        class LocalOnlyHandler(Handler):
            pass
        LocalOnlyHandler.pipeline = pipeline
        LocalOnlyHandler.allow_hcx = False
        server = HTTPServer(("127.0.0.1", 0), LocalOnlyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)

        with patch("app.call_hcx", side_effect=RuntimeError("FORBIDDEN_HCX")) as mock_hcx:
            health = request_json(base + "/api/health")
            if health.get("external_hcx_enabled") is not False or health.get("db_write") is not False:
                raise RuntimeError("RELEASE_HEALTH_NOT_LOCAL_ONLY")
            index = request_json(base + "/api/index-status")
            if index.get("rows") != 187 or index.get("status") != "READY":
                raise RuntimeError("RELEASE_HTTP_INDEX_INVALID")

            answer = request_json(base + "/api/chat", ready)
            _valid_reply(answer, expected="LOCAL_SEARCH_ONLY")
            deferred_answer = request_json(base + "/api/chat", deferred[0])
            target = "CLARIFY" if deferred[1] in {"REVIEW", "REVIEW_REQUIRED"} else "BLOCKED_OR_DEFERRED"
            _valid_reply(deferred_answer, expected=target)
            mock_hcx.assert_not_called()
        index_after = pipeline.index_status()
        if index_after != index_before:
            raise RuntimeError("RELEASE_INDEX_CHANGED")

        print("RELEASE_HTTP_E2E_PASS", json.dumps({
            "model": "real", "database": "existing_187_read_only",
            "requests": 4, "ready": answer["decision"],
            "deferred": deferred_answer["decision"],
            "http": "loopback_only", "hcx_calls": 0, "db_writes": 0,
            "factual_answer_accuracy": "NOT_TESTED",
        }, sort_keys=True), flush=True)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join()
        pipeline.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    parser.add_argument("--queries", type=Path, default=(
        ROOT / "step2_privacy_rag/local_eval/holdout_retrieval_queries.jsonl"))
    args = parser.parse_args()
    try:
        execute(args)
        return 0
    except Exception as exc:
        # Avoid logging private question, DSN, model/HF errors, or stack traces.
        reason = str(exc) if (
            isinstance(exc, RuntimeError) and str(exc).startswith("RELEASE_")
        ) else type(exc).__name__
        print("RELEASE_HTTP_E2E_FAILED", reason, file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
