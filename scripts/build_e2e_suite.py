#!/usr/bin/env python3
"""Prepare an E2E question suite from local labeled Holdout files.

Selection balances documented query types, never uses retrieved rankings or
model predictions. Files contain private raw questions: owner-only local_eval,
exclusive creation, never commit. Gold IDs are evaluation labels only.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/"step2_privacy_rag/local_eval"
QS=LOCAL/"holdout_retrieval_queries.jsonl"
GOLD=LOCAL/"holdout_retrieval_queries_gold.jsonl"


def read(path:Path)->list[dict]:
    items=[json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(items)!=88 or len({x.get("qid") for x in items})!=88:
        raise ValueError("E2E_EXPECTED_88")
    return items


def choose(queries:list[dict],gold:list[dict],n:int)->list[dict]:
    if not 1<=n<=88:
        raise ValueError("E2E_INVALID_SIZE")
    labels={x["qid"]:x for x in gold}
    if {q["qid"] for q in queries}!=set(labels):
        raise ValueError("E2E_ID_MISMATCH")
    pools={}
    for q in queries:
        g=labels[q["qid"]]
        key=g.get("type","unspecified")
        if not isinstance(key,str):
            raise ValueError("E2E_TYPE_INVALID")
        if not isinstance(q.get("query"),str) or not q["query"].strip():
            raise ValueError("E2E_QUERY_INVALID")
        if not isinstance(g.get("relevant_doc_ids"),list):
            raise ValueError("E2E_GOLD_INVALID")
        pools.setdefault(key,[]).append({
            "case_id":q["qid"],"split":"holdout",
            "category":key,"question":q["query"],
            "relevant_doc_ids":g["relevant_doc_ids"]
        })
    for v in pools.values():
        v.sort(key=lambda item:item["case_id"])
    chosen=[]
    kinds=sorted(pools)
    while len(chosen)<n:
        progress=False
        for kind in kinds:
            if pools[kind]:
                chosen.append(pools[kind].pop(0))
                progress=True
                if len(chosen)==n:
                    break
        if not progress:
            raise ValueError("E2E_NO_CASES")
    return chosen


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size",type=int,default=16,help="type-balanced pilot, 1..88")
    parser.add_argument("--out",type=Path,default=LOCAL/"e2e_pilot_16.jsonl")
    args=parser.parse_args()
    try:
        dest=args.out.expanduser().resolve()
        if (not dest.is_relative_to(LOCAL.resolve())
                or dest.suffix!=".jsonl" or dest.exists()):
            raise ValueError("E2E_DEST_MUST_BE_NEW_LOCAL_JSONL")
        chosen=choose(read(QS),read(GOLD),args.size)
        dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            for x in chosen:
                f.write(json.dumps(x,ensure_ascii=False)+"\n")
        print("E2E_SUITE_CREATED",json.dumps({
            "cases":len(chosen),
            "qids":[x["case_id"] for x in chosen],
            "types":sorted(set(x["category"] for x in chosen)),
            "file":str(dest),"private":True,
            "gold_used_for_sampling":False,
            "gold_used_for_scoring_only":True
        },ensure_ascii=False,sort_keys=True),flush=True)
    except Exception as exc:
        err=str(exc) if isinstance(exc,ValueError) and str(exc).startswith("E2E_") else type(exc).__name__
        print("E2E_SUITE_FAILED",err,file=sys.stderr,flush=True)
        raise SystemExit(2)


if __name__=="__main__":
    main()
