#!/usr/bin/env python3
"""Read-only Candidate-K diagnostic on the fixed 187-doc / 88-query Holdout.

Measures retrieval ceiling at each K, not answer correctness. Uses unchanged
V6 ranker. Gold is used only after ranking for metrics. Never runs HCX, writes
to DB, or prints questions/documents/PII/HMAC tokens.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"step2_privacy_rag/app"),str(ROOT/"step1_pii_detector/src")]
PRIVATE=ROOT/"step2_privacy_rag/local_eval"
SIZES=(1,3,5,8,10,20,50,100,187)


def load(path:Path)->list[dict]:
    if not path.is_file():
        raise ValueError("KCURVE_FILE_MISSING")
    values=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if len(values)!=88:
        raise ValueError("KCURVE_EXPECTED_88")
    return values


def summarize(ranked:dict[str,list[str]], gold:dict[str,dict],
              ready:set[str])->dict:
    n=len(gold)
    if n!=88 or set(ranked)!=set(gold):
        raise ValueError("KCURVE_INVALID_QUERY_SET")
    rows=[]
    for k in SIZES:
        h=0
        rec=0.0
        confusable=0
        for qid,label in gold.items():
            docs=ranked[qid][:k]
            relevant=set(label["relevant_doc_ids"])
            if not relevant:
                raise ValueError("KCURVE_GOLD_EMPTY")
            h+=bool(relevant.intersection(docs))
            rec+=len(relevant.intersection(docs))/len(relevant)
            confusable+=bool(set(label.get("confusable_doc_ids",[])).intersection(docs))
        rows.append({"k":k,"hit":h,"hit_total":round(h/n,4),
                     "hit_ready":round(h/len(ready),4),
                     "macro_recall":round(rec/n,4),
                     "queries_with_confusable":confusable})
    first_gold={}
    for qid,label in gold.items():
        relevant=set(label["relevant_doc_ids"])
        first_gold[qid]=next((j+1 for j,d in enumerate(ranked[qid]) if d in relevant),None)
    return {"curve":rows, "first_gold":first_gold,
            "remaining_after_k5":[
                {"qid":qid,"first_gold_rank":first_gold[qid]}
                for qid in sorted(gold)
                if first_gold[qid] is None or first_gold[qid]>5
            ]}


def execute(args)->None:
    from core import LivePipeline,PreparedQuery
    os.environ.update(AEGISRAG_TABLE="rag.aegis_live_docs",
                      AEGISRAG_INDEX_MODE="live",AEGISRAG_LIVE_MIN_ROWS="187",
                      AEGISRAG_TOPIC_BOOST="0")
    questions=load(args.queries)
    refs=load(args.gold)
    if (len({x.get("qid") for x in questions})!=88
            or len({x.get("qid") for x in refs})!=88
            or {x["qid"] for x in questions}!={x["qid"] for x in refs}
            or any(not isinstance(x.get("query"),str) or not x["query"].strip()
                   for x in questions)
            or any(not isinstance(x.get("relevant_doc_ids"),list)
                   or not x["relevant_doc_ids"] for x in refs)):
        raise ValueError("KCURVE_SCHEMA_INVALID")
    gold={x["qid"]:x for x in refs}
    ranked={}
    ready=set()
    gate=Counter()
    pipeline=LivePipeline(ROOT, args.device, reuse_step1=True)
    start=time.perf_counter()
    try:
        initial=pipeline.index_status()
        if (initial.get("status")!="READY"
                or initial.get("rows")!=187
                or initial.get("table")!="rag.aegis_live_docs"
                or initial.get("key_matches") is not True):
            raise ValueError("KCURVE_INDEX_INVALID")
        pipeline.preload_models()
        print("KCURVE_START docs=187 queries=88 no_hcx=true db_read_only=true",
              flush=True)
        for i,q in enumerate(questions,1):
            prepared=pipeline.prepare(q["query"])
            if not isinstance(prepared,PreparedQuery):
                gate[str(prepared)]+=1
                ranked[q["qid"]]=[]
            else:
                gate["READY"]+=1
                ready.add(q["qid"])
                results=pipeline.retrieve(prepared,top_k=None)
                if len(results)!=187:
                    raise ValueError("KCURVE_INCOMPLETE_RANK")
                ranked[q["qid"]]=[r["doc_id"] for r in results]
            if i%20==0 or i==88:
                print("KCURVE_PROGRESS",i,"/88",flush=True)
        if pipeline.index_status()!=initial:
            raise ValueError("KCURVE_INDEX_CHANGED")
    finally:
        pipeline.close()
    result=summarize(ranked,gold,ready)
    if len(ready)!=85 or result["curve"][0]["hit"]!=77 or result["curve"][2]["hit"]!=83:
        raise ValueError("KCURVE_BASELINE_MISMATCH")
    print("KCURVE_GATES",json.dumps(gate,sort_keys=True),flush=True)
    for row in result["curve"]:
        print("KCURVE_RESULT",json.dumps(row,sort_keys=True),flush=True)
    print("KCURVE_MISSES_AFTER_5",json.dumps(result["remaining_after_k5"],
                                           sort_keys=True),flush=True)
    print("KCURVE_ELAPSED_MS",round((time.perf_counter()-start)*1000,2),
          flush=True)
    print("KCURVE_COMPLETE no_gold_in_ranking=true production_unchanged=true",
          flush=True)


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device",choices=("cpu","mps"),default="cpu")
    parser.add_argument("--queries",type=Path,
                        default=PRIVATE/"holdout_retrieval_queries.jsonl")
    parser.add_argument("--gold",type=Path,
                        default=PRIVATE/"holdout_retrieval_queries_gold.jsonl")
    args=parser.parse_args()
    try:
        execute(args)
        return 0
    except Exception as exc:
        msg=str(exc) if isinstance(exc,ValueError) and str(exc).startswith("KCURVE_") else type(exc).__name__
        print("KCURVE_FAILED",msg,file=sys.stderr,flush=True)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
