#!/usr/bin/env python3
"""Summarize a private Confusion Sensitivity run without new API or DB calls.

Default prints IDs, condition, decision and citation behavior only.
--show-answers prints locally redacted answer excerpts; inspect before sharing.
Neither operation can establish semantic/factual correctness by itself.
"""
from __future__ import annotations

import argparse
from collections import defaultdict,Counter
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=(ROOT/"step2_privacy_rag/local_eval").resolve()
TOKEN=re.compile(r"<([A-Z][A-Z0-9_]*)_[0-9a-f]{24}>")


def load(path:Path)->list[dict]:
    src=path.expanduser().resolve()
    if not src.is_relative_to(PRIVATE) or src.suffix!=".jsonl":
        raise ValueError("CONF_REVIEW_MUST_BE_PRIVATE_LOCAL_JSONL")
    records=[]
    for l in src.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        row=json.loads(l)
        if not isinstance(row,dict) or not isinstance(row.get("case_id"),str):
            raise ValueError("CONF_REVIEW_INVALID_ROW")
        records.append(row)
    if not records:
        raise ValueError("CONF_REVIEW_EMPTY")
    return records


def summary(records:list[dict])->dict:
    c=defaultdict(Counter)
    by_case=defaultdict(list)
    for row in records:
        if row.get("condition") is None:
            c["gate"]["skipped"]+=1
            continue
        group=c[row["condition"]]
        if row.get("skipped"):
            group["skipped"]+=1
            continue
        group["observations"]+=1
        if row.get("reused"):
            group["reused_no_new_api_call"]+=1
        else:
            group["api_calls"]+=1
        if row.get("error"):
            group["api_error"]+=1
        if row.get("decision")=="ANSWER":
            group["answer"]+=1
        if row.get("decision")=="CLARIFY":
            group["clarify"]+=1
        if row.get("decision")=="INSUFFICIENT_EVIDENCE":
            group["insufficient"]+=1
        if row.get("gold_citations"):
            group["gold_cited"]+=1
        if row.get("confusable_citations"):
            group["confusable_cited"]+=1
        if row.get("other_citations"):
            group["other_cited"]+=1
        by_case[row["case_id"]].append({
            "trial":row["trial"],"condition":row["condition"],
            "decision":row.get("decision"),"citations":row.get("citations",[]),
            "confusable_citations":row.get("confusable_citations",[]),
            "gold_citations":row.get("gold_citations",[]),
            "answer_length":len(row.get("answer") or ""),
            "reused":row.get("reused",False),
            "error":row.get("error")
        })
    return {"by_condition":{k:dict(v) for k,v in sorted(c.items())},
            "by_case":dict(sorted(by_case.items())),
            "warning":"Citation metrics are diagnostic, not a factual accuracy score."}


def preview(answer, max_chars:int=1600)->str:
    if not isinstance(answer,str):
        return ""
    # Common residual PII, plus HMAC digests omitted from shared previews.
    sys.path.insert(0,str(ROOT/"step2_privacy_rag/app"))
    from hcx import redact_residual_pii
    masked,_=redact_residual_pii(answer)
    return TOKEN.sub(lambda m:"<"+m.group(1)+"_TOKEN>",masked)[:max_chars]


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report",type=Path,required=True)
    ap.add_argument("--show-answers",action="store_true",
                    help="Print redacted answer excerpts; manually inspect for sensitive content")
    ap.add_argument("--qids",nargs="*",default=None)
    args=ap.parse_args()
    try:
        records=load(args.report)
        if args.qids is not None:
            known={x["case_id"] for x in records}
            if any(x not in known for x in args.qids):
                raise ValueError("CONF_REVIEW_UNKNOWN_QID")
            records=[x for x in records if x["case_id"] in args.qids]
        if not records:
            raise ValueError("CONF_REVIEW_EMPTY")
        print("CONF_SUMMARY",json.dumps(summary(records),ensure_ascii=False,sort_keys=True),
              flush=True)
        if args.show_answers:
            for row in records:
                if row.get("skipped") or row.get("condition") is None:
                    continue
                print("CONF_ANSWER",json.dumps({
                    "case_id":row["case_id"],"trial":row.get("trial"),
                    "condition":row["condition"],"decision":row.get("decision"),
                    "citations":row.get("citations"),
                    "answer":preview(row.get("answer")),
                    "reused":row.get("reused",False)
                },ensure_ascii=False,sort_keys=True),flush=True)
            print("CONF_REVIEW_NOTICE",
                  "Redaction is best effort. Review content manually before sharing.",
                  flush=True)
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch(
            r"CONF_[A-Z0-9_]{1,80}",str(exc)) else type(exc).__name__
        print("CONF_REVIEW_FAILED",code,file=sys.stderr,flush=True)
        raise SystemExit(2)


if __name__=="__main__":
    main()
