#!/usr/bin/env python3
"""Local-only diagnosis of HOLDOUT query Gate failures without printing PII.

Prints only aggregate reason codes and character-class facts. Never prints
question text, original identifier, canonical HMAC input, or model spans.
No DB writes, no HCX; intended for investigating structural false blocks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"step2_privacy_rag/app"),str(ROOT/"step1_pii_detector/src")]
DEFAULT=ROOT/"step2_privacy_rag/local_eval/holdout_retrieval_queries.jsonl"
NUMERIC_LABELS=frozenset({"resident_registration_number","card_number",
                          "account_number","phone_number"})


def number_span_diagnostics(text:str,entities:list[dict])->list[dict]:
    """Static metadata only: no snippets, hashes, normalized values, or offsets."""
    from local_pseudonymizer import canonicalize, PseudonymizationError
    issues=[]
    for entity in entities:
        if not isinstance(entity,dict) or entity.get("label") not in NUMERIC_LABELS:
            continue
        start,end=entity.get("start"),entity.get("end")
        if not (type(start) is int and type(end) is int and 0<=start<end<=len(text)):
            issues.append({"label":entity.get("label"),"error":"INVALID_OFFSETS"})
            continue
        value=text[start:end]
        try:
            canonicalize(entity["label"],value)
        except PseudonymizationError as exc:
            code=str(exc)
            issues.append({
                "label":entity["label"],
                "error":code if code in {"invalid_number_value","invalid_phone_value",
                                         "invalid_account_value"} else "UNKNOWN",
                "span_char_count":len(value),
                "all_ascii_digits":value.isascii() and value.isdecimal(),
                "contains_letters":any(c.isalpha() for c in value),
                "contains_unexpected_punctuation":any(
                    not (c.isalnum() or c in "* -.()+") for c in value),
                "covers_entire_input":start==0 and end==len(text),
            })
    return issues


def inspect_case(question:str,qid:str,detector)->dict:
    from name_protection_handoff import _FrozenDetectorView,STEP1_READY
    from privacy_gate import check_privacy_gate
    result=detector.detect(question)
    if not isinstance(result,dict):
        return {"qid":qid,"step1_status":"INVALID"}
    item={"qid":qid,"step1_status":result.get("status"),
          "step1_issue_codes":sorted(set(
             x.get("code","") for x in result.get("issues",[])
             if isinstance(x,dict) and isinstance(x.get("code"),str) and
                x.get("code","").replace("_","").isalnum()
          ))[:20]}
    if result.get("status")!=STEP1_READY:
        return item
    gate=check_privacy_gate(_FrozenDetectorView(detector,result),question)
    item["gate_decision"]=gate.get("decision")
    item["gate_reason"]=gate.get("reason")
    item["gate_uncovered_count"]=gate.get("uncovered_count",0)
    item["veil_policy_counts"]=gate.get("veil_policy_counts",{})
    if gate.get("decision")=="READY_FOR_LOCAL_PSEUDONYMIZATION":
        item["numeric_normalization_failures"]=number_span_diagnostics(
            question,gate.get("entities_for_pseudonymization",[]))
    return item


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--qids",nargs="+",default=["Q001","Q015","Q033"])
    p.add_argument("--queries",type=Path,default=DEFAULT)
    args=p.parse_args()
    try:
        qs={}
        for line in args.queries.read_text(encoding="utf-8").splitlines():
            if line.strip():
                x=json.loads(line)
                qs[x["qid"]]=x["query"]
        if not all(q in qs and q.startswith("Q") and q[1:].isdigit()
                   for q in args.qids):
            raise ValueError("GATE_DIAG_UNKNOWN_ID")
        from pii_detector import create_detector
        detector=create_detector()
        try:
            for qid in args.qids:
                print("GATE_DIAGNOSTIC",json.dumps(
                    inspect_case(qs[qid],qid,detector),ensure_ascii=False,sort_keys=True),
                    flush=True)
        finally:
            detector.close()
        print("GATE_DIAGNOSTIC_COMPLETE local_only=true raw_text_logged=false",
              flush=True)
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and str(exc).startswith("GATE_DIAG_") else type(exc).__name__
        print("GATE_DIAGNOSTIC_FAILED",code,file=sys.stderr,flush=True)
        raise SystemExit(2)


if __name__=="__main__":
    main()
