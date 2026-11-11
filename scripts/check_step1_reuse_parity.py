#!/usr/bin/env python3
"""Compare baseline vs reused Step1 processing on private queries without exposing text.

Each mode runs in a SEPARATE Python invocation so both heavy model sets need not
coexist. Stores only keyed digests of prepared outputs and safe status codes
under git-ignored step2_privacy_rag/local_eval/. Does not access DB or HCX.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCAL_EVAL = (ROOT / "step2_privacy_rag" / "local_eval").resolve()
sys.path.insert(0, str(ROOT / "step2_privacy_rag" / "app"))
sys.path.insert(0, str(ROOT / "step1_pii_detector" / "src"))

RAW_QUERIES = LOCAL_EVAL / "holdout_retrieval_queries.jsonl"
PREPARED = LOCAL_EVAL / "reproduction_20261111" / "prepared.json"
DOMAIN = b"aegisrag-l001-privacy-parity-v1\x00"


def safe_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(LOCAL_EVAL):
        raise ValueError("OUTPUT_MUST_REMAIN_IN_GIT_IGNORED_LOCAL_EVAL")
    return resolved


def choose_queries(raw_path: Path, prepared_path: Path, limit: int):
    raw = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()
           if line.strip()]
    prep = json.loads(prepared_path.read_text(encoding="utf-8"))
    texts = {}
    for q in raw:
        qid, value = q.get("qid"), q.get("query")
        if not isinstance(qid, str) or not isinstance(value, str) or qid in texts:
            raise ValueError("INVALID_RAW_QUERY_RECORD")
        texts[qid] = value
    if not texts:
        raise ValueError("NO_QUERIES")
    prep_qs = prep["queries"]
    if set(texts) != {q["qid"] for q in prep_qs}:
        raise ValueError("PREPARED_QUERY_ID_MISMATCH")
    gold_type = {g["qid"]: str(g.get("type", "unknown"))
                 for g in prep["gold_for_evaluation_only"]}
    # Include all current review/blocked questions first, then one per query type.
    selected = []
    for q in sorted(prep_qs, key=lambda x: x["qid"]):
        if q["text"] is None:
            selected.append(q["qid"])
    if len(selected) > limit:
        raise ValueError("LIMIT_BELOW_NONREADY_QUERY_COUNT")
    seen_types = set()
    for qid in sorted(texts):
        typ = gold_type.get(qid, "unknown")
        if typ not in seen_types:
            seen_types.add(typ)
            if qid not in selected and len(selected) < limit:
                selected.append(qid)
    for qid in sorted(texts):
        if len(selected) >= limit:
            break
        if qid not in selected:
            selected.append(qid)
    return [(qid, texts[qid]) for qid in selected]


def fingerprint(secret: bytes, state: str, prepared) -> str:
    if prepared is None:
        payload = {"state": state}
    else:
        payload = {
            "state": state,
            "masked_query": prepared.text,
            "tokens": sorted(prepared.tokens),
            "regions": sorted(prepared.regions),
        }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hmac.new(secret, DOMAIN + encoded, hashlib.sha256).hexdigest()


def run_mode(args) -> int:
    from core import LivePipeline

    selected = choose_queries(args.queries, args.prepared, args.limit)
    pipe = LivePipeline(ROOT, device=args.device, reuse_step1=(args.mode == "reuse"))
    records = []
    try:
        for qid, raw_query in selected:
            result = pipe.prepare(raw_query)
            if isinstance(result, str):
                state = result
                digest = fingerprint(pipe.secret, state, None)
            else:
                state = "READY"
                digest = fingerprint(pipe.secret, state, result)
            records.append({"qid": qid, "state": state, "fingerprint": digest})
    finally:
        pipe.close()
    # No raw query, model output, HMAC token, or key is written.
    dest = safe_path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = {"format": "aegisrag-l001-parity-v1", "mode": args.mode,
            "records": records}
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(body, f, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    print("L001_PARITY_CAPTURED", json.dumps({
        "mode": args.mode, "n": len(records),
        "states": dict(Counter(x["state"] for x in records)),
        "output": str(dest.relative_to(ROOT)),
    }, ensure_ascii=False))
    return 0


def compare(args) -> int:
    a = json.loads(safe_path(args.baseline).read_text(encoding="utf-8"))
    b = json.loads(safe_path(args.reuse).read_text(encoding="utf-8"))
    if (a.get("format") != "aegisrag-l001-parity-v1" or a.get("mode") != "baseline"
            or b.get("format") != "aegisrag-l001-parity-v1" or b.get("mode") != "reuse"):
        raise ValueError("INVALID_PARITY_CAPTURE")
    baseline = {x["qid"]: x for x in a["records"]}
    candidate = {x["qid"]: x for x in b["records"]}
    if not baseline or set(baseline) != set(candidate):
        raise ValueError("MISMATCHED_PARITY_QUERY_SET")
    mismatches = [qid for qid in sorted(baseline)
                  if baseline[qid]["state"] != candidate[qid]["state"]
                  or baseline[qid]["fingerprint"] != candidate[qid]["fingerprint"]]
    print("L001_PARITY_RESULT", json.dumps({
        "checked": len(baseline), "equal": len(baseline)-len(mismatches),
        "mismatch_qids": mismatches, "states": dict(Counter(
            x["state"] for x in baseline.values())),
        "db_written": False, "hcx_called": False,
    }, ensure_ascii=False))
    return 2 if mismatches else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--mode", choices=("baseline", "reuse"))
    g.add_argument("--compare", action="store_true")
    p.add_argument("--queries", type=Path, default=RAW_QUERIES)
    p.add_argument("--prepared", type=Path, default=PREPARED)
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--out", type=Path)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--reuse", type=Path)
    p.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    args = p.parse_args()
    if not 1 <= args.limit <= 88:
        p.error("--limit must be 1..88")
    try:
        if args.compare:
            if not args.baseline or not args.reuse:
                p.error("--compare requires --baseline and --reuse")
            return compare(args)
        if not args.out:
            p.error("--mode requires --out")
        return run_mode(args)
    except Exception as exc:
        # Never print exception messages: raw source/PII may be embedded in them.
        print("L001_PARITY_FAILED", type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
