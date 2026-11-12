#!/usr/bin/env python3
"""Read-only per-QID diagnostic for RET-001 missed Hit@5 cases.

Inputs are previously prepared V6 eval artifact and saved ranked_doc_ids output.
NEVER reads source text or requests Gold during retrieval; no DB, models, HCX,
or writes. Reports metadata only (QID, query type, candidate counts and rank).
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=(ROOT/"step2_privacy_rag/local_eval").resolve()
sys.path.insert(0,str(ROOT/"step2_privacy_rag/app"))
from compare_88_retrieval import safe_path,score_one

def breakdown(data:dict,run:dict) -> dict:
    qs=data["queries"]
    gs=data["gold_for_evaluation_only"]
    ids={d["doc_id"] for d in data["documents"]}
    qids={q["qid"] for q in qs}
    if len(qs)!=88 or len(qids)!=88 or len(gs)!=88 or qids!={x["qid"] for x in gs}:
        raise ValueError("NOT_88_QUERY_ARTIFACT")
    if set(run)!=qids:
        raise ValueError("MISSING_QUERY_RANKING_ROWS")
    gold={g["qid"]:g for g in gs}
    failed=[]
    status_counts={"DEFERRED_QUERY":0,"READY_NO_INDEXABLE_GOLD":0,"RANKING_MISS":0}
    cases_by_type={}
    for q in sorted(qs,key=lambda x:x["qid"]):
        g=gold[q["qid"]]
        ranked=run[q["qid"]]
        if (not isinstance(ranked,list) or len(ranked)!=len(set(ranked))
                or any(type(r) is not str for r in ranked)):
            raise ValueError("INVALID_RANKED_IDS")
        evaluation=score_one(ranked,g)
        if evaluation["official"]["hit5"]:
            continue
        rel=set(g["relevant_doc_ids"])
        indexable=rel&ids
        if q.get("text") is None:
            reason="DEFERRED_QUERY"
        elif not indexable:
            reason="READY_NO_INDEXABLE_GOLD"
        else:
            reason="RANKING_MISS"
        status_counts[reason]+=1
        cases_by_type[g.get("type","unknown")]=cases_by_type.get(g.get("type","unknown"),0)+1
        neutral=set(g.get("neutral_doc_ids",[]))
        after_neutral=[d for d in ranked if d not in neutral]
        rank=next((i for i,d in enumerate(after_neutral,1) if d in rel),None)
        failed.append({
            "qid":q["qid"],"type":g.get("type","unknown"),
            "failure_reason":reason,"query_deferred":q.get("text") is None,
            "gold_total":len(rel),"gold_in_index":len(indexable),
            "ranked_returned":len(ranked),
            "first_gold_rank_within_saved_run":rank,
            "raw_hit5":evaluation["raw"]["hit5"],
            "neutral_removed_in_saved_run":len(ranked)-len(after_neutral),
        })
    return {"documents":len(ids),"queries":len(qs),
            "failed_hit5":len(failed),"reason_counts":status_counts,
            "failed_query_types":dict(sorted(cases_by_type.items())),
            "cases":failed,"db_written":False,"hcx_called":False,
            "gold_used_only_for_scoring":True}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prepared",type=Path,
                    default=PRIVATE/"reproduction_20261111/prepared.json")
    ap.add_argument("--runs",type=Path,
                    default=PRIVATE/"ret001_ours_88.jsonl")
    args=ap.parse_args()
    try:
        data=json.loads(safe_path(args.prepared).read_text(encoding="utf-8"))
        if data.get("format")!="aegisrag-local-embedding-eval-v1":
            raise ValueError("INVALID_PREPARED_ARTIFACT")
        run={}
        for line in safe_path(args.runs).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            x=json.loads(line)
            if x.get("qid") in run or type(x.get("qid")) is not str:
                raise ValueError("DUPLICATE_OR_INVALID_RUN_ID")
            run[x["qid"]]=x.get("ranked_doc_ids")
        report=breakdown(data,run)
        print("RET001_FAILURE_DIAG "+json.dumps(report,ensure_ascii=False,sort_keys=True))
        return 0
    except Exception as exc:
        # Do not print exception values (could include source or credentials).
        print("RET001_FAILURE_DIAG_FAILED "+type(exc).__name__,file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
