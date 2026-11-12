#!/usr/bin/env python3
"""Apples-to-apples 88-query retrieval evaluation, no LLM and no DB changes.

Score ALL 88 (deferred queries count as misses), plus a separate *indexable*
subset for diagnosis. Uses the team's documented convention of removing
neutral_doc_ids BEFORE scoring and also reports raw top-5 without this removal.
Never feeds Gold into retrieval. Optionally scores an independently supplied
team run (ranked_doc_ids) on the exact same local Gold.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=(ROOT/"step2_privacy_rag/local_eval").resolve()
sys.path.insert(0,str(ROOT/"step2_privacy_rag/app"))

def safe_path(path:Path):
    p=path.resolve()
    if not p.is_relative_to(PRIVATE):
        raise ValueError("ARTIFACT_MUST_REMAIN_LOCAL_EVAL")
    return p

def load_runs(path:Path, qids:set[str]) -> dict[str,list[str]]:
    runs={}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r=json.loads(line)
        qid=r.get("qid")
        rank=r.get("ranked_doc_ids")
        if (not isinstance(qid,str) or qid not in qids or qid in runs
            or not isinstance(rank,list) or any(not isinstance(x,str) for x in rank)
            or len(rank)!=len(set(rank))):
            raise ValueError("INVALID_RUN_DATA")
        runs[qid]=rank
    if set(runs)!=qids:
        raise ValueError("TEAM_RUN_MUST_CONTAIN_ALL_88_QIDS")
    return runs

def score_one(ranked:list[str],g:dict):
    rel=set(g["relevant_doc_ids"])
    neutral=set(g.get("neutral_doc_ids", []))
    confuse=set(g.get("confusable_doc_ids", []))
    if not rel:
        raise ValueError("EMPTY_GOLD_RELEVANT")
    clean=[did for did in ranked if did not in neutral]
    def sub(rows):
        top=set(rows[:5])
        first=next((1/i for i,d in enumerate(rows,1) if d in rel),0.0)
        return {"hit1":int(bool(rows) and rows[0] in rel),
                "hit5":int(bool(rel&top)),
                "recall5":len(rel&top)/len(rel),
                "mrr":first,
                "confuse10":len(confuse&set(rows[:10]))/max(len(rows[:10]),1) if confuse else None}
    return {"official":sub(clean),"raw":sub(ranked)}

def aggregate(gold:list, runs:dict, ids:set[str]):
    metrics={"official":[],"raw":[]}
    bad=[]
    for g in gold:
        if g["qid"] not in ids:
            continue
        ranked=runs.get(g["qid"],[])
        s=score_one(ranked,g)
        if not s["official"]["hit5"]:
            bad.append(g["qid"])
        for kind in metrics:
            metrics[kind].append(s[kind])
    def reduce(rows):
        if not rows:return {"n":0}
        n=len(rows)
        out={"n":n,
             "hit1":sum(r["hit1"] for r in rows),
             "hit5":sum(r["hit5"] for r in rows)}
        for metric in ("recall5","mrr"):
            out[metric]=round(sum(r[metric] for r in rows)/n,4)
        conf=[r["confuse10"] for r in rows if r["confuse10"] is not None]
        out["confuse10"]=round(sum(conf)/len(conf),4) if conf else None
        return out
    return {x:reduce(rows) for x,rows in metrics.items()},bad

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prepared",type=Path,default=PRIVATE/"reproduction_20261111/prepared.json")
    ap.add_argument("--table",default="rag.aegis_koe5_v6_exp1")
    ap.add_argument("--env",type=Path,default=ROOT/".env")
    ap.add_argument("--device",choices=("cpu","mps"),default="cpu")
    ap.add_argument("--team-run",type=Path,help="Optional team result JSONL with all 88 ranked_doc_ids")
    ap.add_argument("--ours-out",type=Path,help="Optional local_eval JSONL output of own ranked doc IDs")
    args=ap.parse_args()
    try:
        from reproduction import artifact
        from core import LivePipeline,PreparedQuery,PII
        from dataset_preparation import key_tag
        from settings import table_identifier
        data=artifact(safe_path(args.prepared),args.env)
        qs=data["queries"]
        gold=data["gold_for_evaluation_only"]
        qids={q["qid"] for q in qs}
        if len(qs)!=88 or len(gold)!=88 or qids!={g["qid"] for g in gold}:
            raise ValueError("EXPECTED_88_GOLD_AND_QUERIES")
        ids={d["doc_id"] for d in data["documents"]}
        pipeline=LivePipeline(ROOT,args.device,args.env)
        pipeline.table=table_identifier(args.table)
        pipeline.expected_rows=len(ids)
        if pipeline.tag!=key_tag(pipeline.secret):
            raise ValueError("INCONSISTENT_KEY")
        runs={}
        try:
            for q in qs:
                if q["text"] is None:
                    runs[q["qid"]]=[]
                    continue
                prep=PreparedQuery(q["text"],set(PII.findall(q["text"])),set(q["region_keys"]))
                results=pipeline.retrieve(prep,top_k=None)
                runs[q["qid"]]=[r["doc_id"] for r in results]
        finally:
            pipeline.close()
        indexable={q["qid"] for q in qs if q["text"] is not None and
                   bool(set(next(g["relevant_doc_ids"] for g in gold if g["qid"]==q["qid"]))&ids)}
        all_scores,all_misses=aggregate(gold,runs,qids)
        eligible_scores,_=aggregate(gold,runs,indexable)
        out={"documents":len(ids),"total_queries":len(qids),
             "queries_prepared":sum(q["text"] is not None for q in qs),
             "queries_with_indexable_gold":len(indexable),
             "ours":{"all88":all_scores,"indexable_only":eligible_scores,
                     "all88_official_hit5_misses":all_misses}}
        if args.team_run:
            tr=load_runs(args.team_run,qids)
            scores,misses=aggregate(gold,tr,qids)
            out["team"]={"all88":scores,"all88_official_hit5_misses":misses}
            out["same_gold_and_scoring_rules"]=True
            out["warning"]="Same scoring is NOT same corpus/model/config; verify team run provenance."
        if args.ours_out:
            dest=safe_path(args.ours_out)
            if dest.exists():raise ValueError("OUTPUT_ALREADY_EXISTS")
            dest.parent.mkdir(parents=True,exist_ok=True)
            with dest.open("x",encoding="utf-8") as f:
                for qid in sorted(runs):
                    f.write(json.dumps({"qid":qid,"ranked_doc_ids":runs[qid]})+"\n")
        out["db_written"]=False
        out["hcx_called"]=False
        print("RET001_FAIR_EVAL "+json.dumps(out,ensure_ascii=False,sort_keys=True))
        return 0
    except Exception as e:
        # Never print exception text: private data or DB credentials may be embedded.
        print("RET001_FAIR_EVAL_FAILED "+type(e).__name__,file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
