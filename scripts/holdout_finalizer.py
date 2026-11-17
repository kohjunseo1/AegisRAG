#!/usr/bin/env python3
"""AegisRAG Holdout MVP local release gate.

  python scripts/holdout_finalizer.py all       # policy -> regression -> stage -> query
  python scripts/holdout_finalizer.py policy    # conservative local fix + 164 tests + smoke
  python scripts/holdout_finalizer.py stage     # real 187 docs -> signed private REVIEW queue
  python scripts/holdout_finalizer.py query     # real 88 queries -> read-only local retrieval

Never silently approves a REVIEW/BLOCKED document, writes to a DB, or calls HCX.
Output consists only of status codes, aggregate metrics and local-only doc IDs.
No files outside the worktree are overwritten except the opt-in policy edit,
which is rolled back automatically if tests fail.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "step2_privacy_rag" / "local_eval"
POLICY = ROOT / "step2_privacy_rag" / "app" / "veil_person_policy.py"
STAGE = PRIVATE / "holdout_e2e_stage.json"
CASES = PRIVATE / "holdout_e2e_cases.jsonl"
DOCUMENTS = PRIVATE / "holdout_labeled.jsonl"
QUERIES = PRIVATE / "holdout_retrieval_queries.jsonl"
GOLD = PRIVATE / "holdout_retrieval_queries_gold.jsonl"

ROLE_ANCHOR = "    if len(val) == 1:\n"
QUOTE_GUARD = r'''
    # A role expression modifying the following noun is ambiguous. Keep it in
    # REVIEW instead of treating it as a confirmed surname/job-title reference.
    # Predicative quotations such as "박 대리라고 합니다" remain eligible for
    # role redaction; their safety still depends on the original Gate checks.
    if len(val) == 1 and re.match(
        rf"^[ \t]*(?:{TITLE_ALTS})(?:님)?"
        rf"(?:이라는|이란|라는|란)(?=$|[^가-힣])",
        after,
    ):
        return Decision(REVIEW, "quoted_role_reference")

'''


def corrected_policy_source(original: str) -> str:
    """Avoid overbroad local guard that also blocked '...대리라고 합니다'."""
    broad = "(?:이라는|이란|이라고|라는|란|라고)"
    narrow = "(?:이라는|이란|라는|란)"
    if "quoted_role_reference" in original:
        if broad in original:
            return original.replace(broad, narrow, 1)
        if narrow in original:
            return original
        raise ValueError("UNKNOWN_EXISTING_ROLE_GUARD")
    if original.count(ROLE_ANCHOR) != 1:
        raise ValueError("UNEXPECTED_POLICY_STRUCTURE")
    return original.replace(ROLE_ANCHOR, QUOTE_GUARD + ROLE_ANCHOR)


def env_for_tests() -> dict[str, str]:
    env = os.environ.copy()
    paths = [ROOT / "step2_privacy_rag" / "app", ROOT / "step1_pii_detector" / "src"]
    env["PYTHONPATH"] = os.pathsep.join(map(str, paths))
    # No external HCX transport is ever used for local contract tests.
    env.pop("AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED", None)
    return env


def _run_static(command: list[str], label: str, *, env: dict | None = None) -> None:
    completed = subprocess.run(command, cwd=ROOT, env=env,
                               text=True, capture_output=True)
    if completed.returncode:
        # The contract suite uses synthetic fixtures; diagnostics are local-only.
        lines = (completed.stdout + completed.stderr).splitlines()
        print(label + "_FAILED", file=sys.stderr)
        for line in lines[-22:]:
            print(line, file=sys.stderr)
        raise RuntimeError(label + "_FAILED")
    match = re.search(r"Ran (\d+) tests", completed.stdout + completed.stderr)
    print(label + "_PASS", "tests=" + match.group(1) if match else "OK", flush=True)


def policy() -> None:
    original = POLICY.read_bytes()
    updated = corrected_policy_source(original.decode("utf-8")).encode("utf-8")
    changed = original != updated
    if changed:
        POLICY.write_bytes(updated)
    try:
        env = env_for_tests()
        _run_static([sys.executable, "-m", "unittest", "discover",
                     "-s", "step2_privacy_rag/tests", "-p", "test_*.py", "-q"],
                    "STEP2_CONTRACTS", env=env)
        _run_static([sys.executable, "scripts/aegisrag.py", "smoke"],
                    "LOCAL_SYNTHETIC_SMOKE", env=env)
    except Exception:
        if changed:
            POLICY.write_bytes(original)
            print("POLICY_EDIT_REVERTED", flush=True)
        raise
    print("POLICY_STABLE", "changed=" + str(changed).lower(), flush=True)


def preflight() -> None:
    command = [sys.executable, "scripts/prepare_holdout_e2e.py",
               "--documents", str(DOCUMENTS), "--queries", str(QUERIES),
               "--gold", str(GOLD), "--check-only"]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode:
        print("HOLDOUT_PREFLIGHT_FAILED", file=sys.stderr)
        raise RuntimeError("HOLDOUT_PREFLIGHT_FAILED")
    for line in completed.stdout.splitlines():
        if line.startswith("HOLDOUT_E2E_PREFLIGHT "):
            data = json.loads(line.partition(" ")[2])
            if (data.get("documents") != 187 or data.get("queries") != 88
                    or data.get("gold_rows") != 88 or not data.get("query_gold_id_match")):
                raise RuntimeError("HOLDOUT_PREFLIGHT_COUNTS_INVALID")
            print("HOLDOUT_PREFLIGHT_PASS documents=187 queries=88 gold=88")
            return
    raise RuntimeError("HOLDOUT_PREFLIGHT_OUTPUT_INVALID")


def _run_intake(args: list[str], label: str) -> None:
    cmd = [sys.executable, "scripts/dataset_intake.py", *args]
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode:
        # Error messages from dependencies may include source data. Only code emitted.
        raise RuntimeError(label + "_FAILED")
    found = False
    for line in completed.stdout.splitlines():
        if line.startswith("INTAKE_STAGE ") or line.startswith("INTAKE_STATUS "):
            data = json.loads(line.partition(" ")[2])
            print(label + "_PASS", json.dumps({
                "total": data.get("total"),
                "ready": data.get("ready"),
                "review_pending": data.get("review_pending"),
                "blocked": data.get("blocked"),
                "dataset": data.get("dataset"),
            }, ensure_ascii=False, sort_keys=True), flush=True)
            found = True
        elif line.startswith("INTAKE_STAGE_TIMING_MS "):
            metrics = json.loads(line.partition(" ")[2])
            print("DOCUMENT_PRIVACY_STAGE_MS", json.dumps({
                "cold_detector_load": metrics.get("cold_detector_load"),
                "privacy_stage_total": metrics.get("privacy_stage_total"),
            }, sort_keys=True), flush=True)
    if not found:
        raise RuntimeError(label + "_OUTPUT_INVALID")


def stage() -> None:
    preflight()
    if STAGE.exists():
        _run_intake(["status", "--stage", str(STAGE)], "DOCUMENT_STAGE_EXISTING")
        return
    # No DB writes. Unique namespaced IDs prevent accidental collision with
    # H0001..H0187 in the already indexed Holdout DB.
    _run_intake(["stage", "--input", str(DOCUMENTS),
                 "--stage", str(STAGE), "--dataset", "holdoute2e",
                 "--id-field", "id", "--text-field", "text"], "DOCUMENT_STAGE")
    print("DOCUMENT_REVIEW_NEXT: remaining records require human review and local Gate recheck.")
    print("DOCUMENT_DB_WRITES=0 HCX_CALLS=0", flush=True)


def _prepare_cases() -> None:
    if CASES.is_file():
        return
    cmd = [sys.executable, "scripts/prepare_holdout_e2e.py",
           "--documents", str(DOCUMENTS), "--queries", str(QUERIES),
           "--gold", str(GOLD), "--out", str(CASES)]
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError("PRIVATE_E2E_CASE_EXPORT_FAILED")
    print("PRIVATE_E2E_CASES_READY count=88", flush=True)


def query() -> None:
    preflight()
    _prepare_cases()
    env = os.environ.copy()
    env.update({
        "AEGISRAG_TABLE": "rag.aegis_live_docs",
        "AEGISRAG_INDEX_MODE": "live",
        "AEGISRAG_LIVE_MIN_ROWS": "187",
        "AEGISRAG_TOPIC_BOOST": "0",
    })
    env.pop("AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED", None)
    # The existing evaluator defaults to local-only (no HCX). Stream only
    # whitelisted aggregate progress/metrics, never private question content.
    cmd = [sys.executable, "scripts/eval_chat_mvp.py", "--cases", str(CASES)]
    n, report = 0, None
    with subprocess.Popen(cmd, cwd=ROOT, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("MVP_CASE "):
                n += 1
                if n % 20 == 0 or n == 88:
                    print("LOCAL_QUERY_PROGRESS", str(n) + "/88", flush=True)
            elif line.startswith("MVP_EVALUATION_SUMMARY "):
                report = json.loads(line.partition(" ")[2])
        status = proc.wait()
    if status != 0 or n != 88 or not isinstance(report, dict):
        raise RuntimeError("LOCAL_QUERY_EVAL_FAILED")
    print("LOCAL_QUERY_METRICS", json.dumps({
        k: report.get(k) for k in (
            "total_cases", "gate_states", "hit1_labeled", "hit5_labeled",
            "hit1_rate_labeled", "hit5_rate_labeled",
            "recall5_indexable_gold", "mrr_indexable_gold",
            "latency_ms")
    }, ensure_ascii=False, sort_keys=True), flush=True)
    print("LOCAL_QUERY_HCX_CALLS=0 DB_WRITES=0 ANSWER_CORRECTNESS_NOT_TESTED")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("policy", "stage", "query", "all"))
    args = parser.parse_args(argv)
    try:
        if args.phase in ("policy", "all"):
            policy()
        if args.phase in ("stage", "all"):
            stage()
        if args.phase in ("query", "all"):
            query()
    except Exception as exc:
        code = str(exc) if type(exc) in (RuntimeError, ValueError) and re.fullmatch(
            r"[A-Z][A-Z0-9_]+", str(exc)) else type(exc).__name__
        print("HOLDOUT_FINALIZER_STOP", code, file=sys.stderr)
        return 2
    print("HOLDOUT_PHASE_COMPLETE", args.phase, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
