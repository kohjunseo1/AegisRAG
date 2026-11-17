#!/usr/bin/env python3
"""Score human-reviewed private HCX E2E report (never print answers or raw PII).

Uses bool-or-null judgements. Correct decision and privacy are mandatory;
ANSWER also requires factual correctness, complete coverage, supported
citations and temporally accurate statements. No external judge or API.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
LOCAL=(ROOT/"step2_privacy_rag/local_eval").resolve()
ANSWER_FLAGS=("human_answer_correct","human_citation_supported",
              "human_answer_complete","human_temporal_correct")
COMMON_FLAGS=("human_decision_appropriate","human_privacy_safe")


def grade(items:list[dict])->dict:
    graded=[];pending=[];failed=[]
    by_decision={}
    for obj in items:
        cid=obj.get("case_id")
        observed=obj.get("observed",{})
        decision=observed.get("decision")
        required=COMMON_FLAGS+(ANSWER_FLAGS if decision=="ANSWER" else ())
        verdicts=[obj.get(flag) for flag in required]
        if not isinstance(cid,str) or not cid.replace("_","").replace("-","").isalnum():
            raise ValueError("E2E_SCORE_CASE_ID_INVALID")
        if decision not in ("ANSWER","CLARIFY","INSUFFICIENT_EVIDENCE",
                            "BLOCKED_OR_DEFERRED","REVIEW_REQUIRED"):
            raise ValueError("E2E_SCORE_UNKNOWN_DECISION")
        by_decision[decision]=by_decision.get(decision,0)+1
        if any(type(v) is not bool for v in verdicts):
            pending.append(cid)
        elif all(verdicts):
            graded.append(cid)
        else:
            failed.append(cid)
    total=len(items)
    complete=len(graded)+len(failed)
    return {
        "cases":total,"scored":complete,"pending":len(pending),
        "passes":len(graded),"failures":len(failed),
        "success_rate_scored":round(len(graded)/complete,4) if complete else None,
        "success_rate_all":round(len(graded)/total,4) if total else None,
        "decision_distribution":by_decision,
        "pending_qids":pending,"failed_qids":failed,
        "note":"Human review only; missing labels never counted as successes."
    }


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args()
    try:
        fp=args.report.expanduser().resolve()
        if fp.suffix!=".json" or not fp.is_relative_to(LOCAL):
            raise ValueError("E2E_SCORE_REPORT_MUST_BE_PRIVATE_LOCAL_JSON")
        report=json.loads(fp.read_text(encoding="utf-8"))
        cases=report.get("cases")
        if not isinstance(cases,list) or not cases:
            raise ValueError("E2E_SCORE_CASES_INVALID")
        print("E2E_HUMAN_SCORE",json.dumps(
            grade(cases),ensure_ascii=False,sort_keys=True),flush=True)
    except Exception as exc:
        safe=str(exc) if isinstance(exc,ValueError) and str(exc).startswith("E2E_SCORE_") else type(exc).__name__
        print("E2E_HUMAN_SCORE_FAILED",safe,file=sys.stderr,flush=True)
        raise SystemExit(2)


if __name__=="__main__":
    main()
