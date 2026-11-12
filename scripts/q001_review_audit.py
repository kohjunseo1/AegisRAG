#!/usr/bin/env python3
"""Audit Q-001 draft review decisions without changing Gate, HMAC or DB.

Requires raw private local source and a locally recorded annotation journal.
Uses actual detector proposals to verify case/offset/hash/choice consistency.
Outputs ONLY document IDs and safe state counts. REVIEW remains REVIEW.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import review_dashboard_q001 as ui

DEFAULT_IDS=["H0002","H0057","H0072","H0091","H0103",
             "H0105","H0116","H0117","H0119","H0135"]


def audit(cases:dict, events:list) -> dict:
    by_id=defaultdict(dict)
    errors=Counter()
    for entry in events:
        if not isinstance(entry,dict) or entry.get("format")!="q001-review-draft-v1":
            errors["invalid_record"]+=1
            continue
        did=entry.get("document_id")
        if did not in cases:
            errors["unknown_document"]+=1
            continue
        candidate=entry.get("candidate_index")
        arr=cases[did]["candidates"]
        if type(candidate) is not int or not 0<=candidate<len(arr):
            errors["invalid_candidate_index"]+=1
            continue
        expected=arr[candidate]
        if (entry.get("source_span")!=[expected["start"],expected["end"]]
                or entry.get("kind")!=expected["kind"]
                or entry.get("source_digest")!=hashlib.sha256(
                    cases[did]["source"].encode("utf-8")).hexdigest()):
            errors["stale_or_invalid_source"]+=1
            continue
        choice=entry.get("human_classification")
        if choice not in ui.CHOICES[expected["kind"]] or entry.get("state")!="NEEDS_INDEPENDENT_REVALIDATION":
            errors["invalid_choice_or_state"]+=1
            continue
        by_id[did][candidate]=choice  # final valid annotation wins
    summaries=[]
    for did in sorted(cases):
        n=len(cases[did]["candidates"])
        chosen=by_id.get(did,{})
        unresolved=sum(chosen.get(i,"UNRESOLVED")=="UNRESOLVED" for i in range(n))
        summaries.append({"id":did,"candidates":n,"reviewed_nonambiguous":n-unresolved,
                          "remaining":unresolved,
                          "review_complete":unresolved==0,
                          "still_blocked_from_index":True})
    complete=sum(item["review_complete"] for item in summaries)
    return {"documents":len(summaries),"record_count":len(events),
            "review_complete":complete,"review_incomplete":len(summaries)-complete,
            "invalid_event_counts":dict(errors),
            "cases":summaries,
            "ready_for_manual_correction_design":complete==len(summaries) and not errors,
            "db_written":False,"hcx_called":False,"automatic_promotion":False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ids",nargs="+",default=DEFAULT_IDS)
    p.add_argument("--input",type=Path,default=ui.LOCAL/"holdout_labeled.jsonl")
    p.add_argument("--journal",type=Path,default=ui.LOCAL/"q001_review_decisions.jsonl")
    args=p.parse_args()
    try:
        src=ui.read_sources(args.input,args.ids)
        if args.journal.exists():
            entries=[json.loads(l) for l in ui.private_path(args.journal).read_text(
                encoding="utf-8").splitlines() if l.strip()]
        else:
            entries=[]
        from pii_detector import create_detector
        with create_detector() as det:
            cases=ui.get_cases(src,det)
        out=audit(cases,entries)
        print("Q001_REVIEW_AUDIT "+json.dumps(out,ensure_ascii=False,sort_keys=True))
        return 2 if out["invalid_event_counts"] else 0
    except Exception as e:
        # A loader/model exception may include original personal data.
        print("Q001_REVIEW_AUDIT_FAILED "+type(e).__name__,file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
