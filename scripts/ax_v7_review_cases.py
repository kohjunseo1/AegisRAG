"""Sample raw PERSON/ADDRESS detector errors to an explicitly authorized LOCAL file.

This contains sensitive original snippets and MUST NEVER be committed, uploaded
or pasted into ChatGPT, a web tool, HF, or another external service. Its only
purpose is manually auditing gold label policies and proposed synthetic
template families. It must not be used verbatim as a training dataset.

Run:
 PYTHONPATH=step2_privacy_rag/app python scripts/ax_v7_review_cases.py \
   --data "$HOME/aegis_external_eval/team4/synth_hard_eval.jsonl" \
   --out step2_privacy_rag/local_eval/ax_v7/manual_case_review.jsonl \
   --confirm-local-sensitive-export
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"step2_privacy_rag"/"app"))
from unified_v1 import selection as S
from unified_v1.external_validation import load_external
from unified_v1.error_taxonomy import strict_status, mask_status, shape_tags, fp_status
from unified_v1.lean import Worker
from unified_v1.merge_validation import v6_detailed, overlaps

LABELS=("person","address")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument("--dataset",choices=("A","B"))
    group.add_argument("--data",type=Path)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--max-per-group",type=int,default=8)
    p.add_argument("--confirm-local-sensitive-export",action="store_true")
    args=p.parse_args()
    if not args.confirm_local_sensitive_export:
        p.error("Explicit --confirm-local-sensitive-export required because snippets contain raw PII")
    if not 1<=args.max_per_group<=50:
        p.error("--max-per-group must be 1..50")

    out=args.out.expanduser().resolve()
    permitted=(ROOT/"step2_privacy_rag"/"local_eval").resolve()
    if not out.is_relative_to(permitted):
        raise ValueError("LOCAL_EXPORT_PATH_MUST_BE_UNDER_GITIGNORED_LOCAL_EVAL")
    if out.exists():
        raise ValueError("SENSITIVE_LOCAL_EXPORT_ALREADY_EXISTS")
    path=(S.A if args.dataset=="A" else S.B if args.dataset=="B"
          else args.data.expanduser()).resolve()
    rows=load_external(path)
    quota=defaultdict(int)
    counters=Counter()
    report=[]

    with Worker("B0") as model:
        for doc in rows:
            fulltext=doc["text"]
            output=v6_detailed(fulltext,model.call(fulltext))
            preds=output["entities"]
            golds=doc["entities"]
            safe_id=hashlib.sha256(str(doc["id"]).encode()).hexdigest()[:16]
            for gold in golds:
                if gold["label"] not in LABELS:
                    continue
                status=strict_status(gold,preds)
                if status=="exact":
                    continue
                group_id=(gold["label"],status)
                counters[group_id]+=1
                if quota[group_id]>=args.max_per_group:
                    continue
                quota[group_id]+=1
                lo=max(0,gold["start"]-70)
                hi=min(len(fulltext),gold["end"]+70)
                nearby=[x for x in preds if x["start"]<hi and x["end"]>lo]
                report.append({
                    "doc_hash":safe_id,
                    "error_type":"FN_"+status,
                    "label":gold["label"],
                    "tags":shape_tags(fulltext,gold),
                    "mask_state":mask_status(gold,output["mask_intervals"]),
                    "window_start":lo,"window_text":fulltext[lo:hi],
                    "gold":gold,
                    "nearby_predictions":nearby,
                    "manual_gold_policy_review_required":True,
                    "not_training_data":True,
                })
            gold_keys={(x["label"],x["start"],x["end"]) for x in golds}
            for pred in preds:
                if pred["label"] not in LABELS:
                    continue
                if (pred["label"],pred["start"],pred["end"]) in gold_keys:
                    continue
                kind=fp_status(pred,golds)
                if kind!="unmatched":
                    continue
                group_id=(pred["label"],"FP_unmatched")
                counters[group_id]+=1
                if quota[group_id]>=args.max_per_group:
                    continue
                quota[group_id]+=1
                lo=max(0,pred["start"]-70)
                hi=min(len(fulltext),pred["end"]+70)
                report.append({
                    "doc_hash":safe_id,"error_type":"FP_unmatched",
                    "label":pred["label"],
                    "window_start":lo,"window_text":fulltext[lo:hi],
                    "prediction":pred,
                    "manual_gold_policy_review_required":True,
                    "not_training_data":True,
                })

    out.parent.mkdir(parents=True,exist_ok=True)
    # Local permission hardening; still requires disk encryption/access control.
    os.chmod(out.parent,0o700)
    fd=os.open(str(out),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        for x in report:
            f.write(json.dumps(x,ensure_ascii=False)+"\n")
    print("AX_V7_LOCAL_CASE_REVIEW_CREATED",json.dumps({
        "error_counts":{f"{lab}/{err}":v for (lab,err),v in sorted(counters.items())},
        "sampled":len(report),
        "file_is_local_and_sensitive":True,
        "contains_original_PII":True,
        "must_not_be_uploaded":True,
        "file_path":str(out),
    },ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
