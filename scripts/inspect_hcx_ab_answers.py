#!/usr/bin/env python3
"""Private local review of paired HCX answers. No network, DB or model calls.

Reads only existing E2E JSON reports under current worktree local_eval.
Shows HCX-generated text after local residual PII redaction, NEVER raw questions,
Gold private expected answers, or document text. Review output before sharing.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"step2_privacy_rag/app"),str(ROOT/"scripts")]
from compare_hcx_variants import read_report
from hcx import redact_residual_pii

PRIVATE=(ROOT/"step2_privacy_rag/local_eval").resolve()
SAFE_ID=re.compile(r"^[A-Za-z0-9_-]{1,60}$")


def load_private(path:Path)->dict[str,dict]:
    fp=path.expanduser().resolve()
    if not fp.is_relative_to(PRIVATE) or fp.suffix!=".json":
        raise ValueError("REVIEW_FILE_NOT_LOCAL_PRIVATE_JSON")
    data=json.loads(fp.read_text(encoding="utf-8"))
    cases=data.get("cases")
    if not isinstance(cases,list):
        raise ValueError("REVIEW_CASES_INVALID")
    by_id={}
    for c in cases:
        if (not isinstance(c,dict) or not isinstance(c.get("case_id"),str)
                or SAFE_ID.fullmatch(c["case_id"]) is None or c["case_id"] in by_id):
            raise ValueError("REVIEW_CASE_INVALID")
        by_id[c["case_id"]]=c
    return by_id


def redact_answer(answer)->str:
    if not isinstance(answer,str):
        return "(none)"
    # Conservative: this does NOT certify full PII removal. Local inspection only.
    redacted,_=redact_residual_pii(answer)
    return redacted[:2400]


def compare_case(a:dict,b:dict)->dict:
    aa=a.get("observed",{})
    bb=b.get("observed",{})
    for x in (aa,bb):
        if not isinstance(x,dict):
            raise ValueError("REVIEW_OBSERVED_INVALID")
    return {
       "case_id":a["case_id"],
       "category":a.get("category"),
       "same_top5":aa.get("top5_doc_ids")==bb.get("top5_doc_ids"),
       "baseline":{"decision":aa.get("decision"),"citations":aa.get("citations"),
                   "answer":redact_answer(a.get("hcx_answer"))},
       "variant":{"decision":bb.get("decision"),"citations":bb.get("citations"),
                  "answer":redact_answer(b.get("hcx_answer"))},
       "manual_review_required":True,
    }


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline",type=Path,required=True)
    ap.add_argument("--variant",type=Path,required=True)
    ap.add_argument("--qids",nargs="+",help="Optional selected case IDs; default changed citations/decision")
    args=ap.parse_args()
    try:
        a,b=load_private(args.baseline),load_private(args.variant)
        if set(a)!=set(b):
            raise ValueError("REVIEW_CASE_SET_MISMATCH")
        selected=args.qids
        if selected is None:
            selected=[
              cid for cid in sorted(a) if
                a[cid].get("observed",{}).get("citations")!=b[cid].get("observed",{}).get("citations")
                or a[cid].get("observed",{}).get("decision")!=b[cid].get("observed",{}).get("decision")
            ]
        if any(cid not in a for cid in selected) or len(set(selected))!=len(selected):
            raise ValueError("REVIEW_UNKNOWN_OR_DUPLICATE_QID")
        for cid in selected:
            print("HCX_PAIRED_ANSWER",json.dumps(
              compare_case(a[cid],b[cid]),ensure_ascii=False,sort_keys=True),
              flush=True)
        print("HCX_PAIRED_COMPLETE",json.dumps({
          "cases":len(selected),"local_only":True,"no_new_hcx_calls":True,
          "note":"Review each line locally for residual private content before sharing. Citation change is not factual correctness."
        },sort_keys=True),flush=True)
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and str(exc).startswith("REVIEW_") else type(exc).__name__
        print("HCX_PAIRED_FAILED",code,file=sys.stderr,flush=True)
        raise SystemExit(2)


if __name__=="__main__":
    main()
