#!/usr/bin/env python3
"""Local-only CrossEncoder rerank diagnostic; NOT a production ranker.

Frozen 187-document index, real Gate, pseudonymized 88 questions, no HCX,
read-only SQL. The CrossEncoder is downloaded from Hugging Face on first use;
questions and documents are scored locally, never sent to a hosted rerank API.
Gold labels are read for AFTER-THE-FACT scoring only, never for model inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"step2_privacy_rag/app"),str(ROOT/"step1_pii_detector/src")]
PRIVATE=ROOT/"step2_privacy_rag/local_eval"

TOKEN=re.compile(r"<([A-Z][A-Z0-9_]*)_[0-9a-f]{24}>|\[REDACTED[A-Z0-9_]*\]")
REDUCED={"PERSON":"인물","ADDRESS":"주소","PHONE_NUMBER":"전화번호","EMAIL":"이메일",
         "ACCOUNT_NUMBER":"계좌","CARD_NUMBER":"카드번호"}


def semantic_surface(masked:str)->str:
    """Only coarse local role labels go into CE; opaque HMACs are not lexical clues."""
    return TOKEN.sub(lambda m:" "+REDUCED.get(m.group(1),"비식별정보")+" " if m.group(1)
                     else " 비식별정보 ",masked)


def sort_reranked(pool:list[dict], raw_scores:list[float], strength:float)->list[dict]:
    """Rank within unchanged HMAC/region tiers; combine CE and original rank by RRF."""
    if not 0 <= strength <= 1 or len(pool)!=len(raw_scores):
        raise ValueError("CROSSENCODER_INVALID_SCORES")
    import math
    if any(not math.isfinite(float(s)) for s in raw_scores):
        raise ValueError("CROSSENCODER_NONFINITE_SCORES")
    ce_order=sorted(range(len(pool)),key=lambda i:(-raw_scores[i],pool[i]["doc_id"]))
    ce_rank={pool[idx]["doc_id"]:rank+1 for rank,idx in enumerate(ce_order)}
    def score(r):
        return ((1-strength)/(60+r["dense_rank"])
                +strength/(60+ce_rank[r["doc_id"]]))
    # HMAC and region priority are security-sensitive and must never be weakened.
    return sorted(pool,key=lambda r:(-r["hmac_matches"],-r["region_matches"],
                                      -score(r),r["dense_rank"],r["doc_id"]))


def load(path:Path)->list[dict]:
    if not path.is_file():
        raise ValueError("CROSSENCODER_INPUT_MISSING")
    data=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
          if line.strip()]
    if len(data)!=88:
        raise ValueError("CROSSENCODER_EXPECTED_88")
    return data


def score(runs:dict[str,list[str]],labels:dict[str,dict])->dict:
    n=len(runs)
    hits1=hits5=confuse1=0
    recall=mrr=0.0
    per={}
    for qid,ids in runs.items():
        g=labels[qid]
        gold=set(g["relevant_doc_ids"])
        if not gold:
            raise ValueError("CROSSENCODER_GOLD_EMPTY")
        h1=bool(ids and ids[0] in gold)
        h5=bool(gold.intersection(ids[:5]))
        hits1+=h1; hits5+=h5
        recall+=len(gold.intersection(ids[:5]))/len(gold)
        mrr+=next((1/(j+1) for j,d in enumerate(ids[:5]) if d in gold),0)
        confuse1+=bool(ids and ids[0] in set(g.get("confusable_doc_ids",[])))
        per[qid]=(h1,h5)
    return dict(hit1=hits1,hit5=hits5,
                hit1_rate=round(hits1/n,4),hit5_rate=round(hits5/n,4),
                recall5=round(recall/n,4),mrr5=round(mrr/n,4),
                confusable_top1=confuse1,per_query=per)


def run(args)->None:
    import numpy as np
    from sentence_transformers import CrossEncoder
    from core import LivePipeline,PreparedQuery
    os.environ.update(AEGISRAG_TABLE="rag.aegis_live_docs",
                      AEGISRAG_INDEX_MODE="live",AEGISRAG_LIVE_MIN_ROWS="187",
                      AEGISRAG_TOPIC_BOOST="0")
    queries=load(args.queries)
    gold_rows=load(args.gold)
    labels={g["qid"]:g for g in gold_rows}
    if (len(labels)!=88 or len({q.get("qid") for q in queries})!=88
            or {q["qid"] for q in queries}!=set(labels)
            or any(not isinstance(q.get("query"),str) or not q["query"].strip()
                   for q in queries)
            or any(not isinstance(g.get("relevant_doc_ids"),list)
                   or not g["relevant_doc_ids"] for g in gold_rows)):
        raise ValueError("CROSSENCODER_SCHEMA_INVALID")
    if args.qids:
        wanted=set(args.qids)
        if not wanted.issubset(labels):
            raise ValueError("CROSSENCODER_UNKNOWN_CASE_ID")
        queries=[q for q in queries if q["qid"] in wanted]
        if len(queries)!=len(wanted):
            raise ValueError("CROSSENCODER_DUPLICATE_ID")
    labels={qid:labels[qid] for qid in [q["qid"] for q in queries]}
    names=("baseline","ce_only","ce_25","ce_50")
    runs={name:{} for name in names}
    states=Counter();ce_pairs=0;ce_runtime=0.0
    pipeline=LivePipeline(ROOT,device=args.device,reuse_step1=True)
    try:
        status=pipeline.index_status()
        if (status.get("status")!="READY" or status.get("rows")!=187
                or status.get("table")!="rag.aegis_live_docs"
                or status.get("key_matches") is not True):
            raise ValueError("CROSSENCODER_INDEX_INVALID")
        pipeline.preload_models()
        print("CROSSENCODER_MODEL_LOADING model="+args.model,flush=True)
        started=time.perf_counter()
        reranker=CrossEncoder(args.model,device=args.device,max_length=384,
                              trust_remote_code=False)
        print("CROSSENCODER_MODEL_READY load_ms="+str(round((time.perf_counter()-started)*1000,2)),
              flush=True)
        for n,item in enumerate(queries,1):
            qid=item["qid"]
            prepared=pipeline.prepare(item["query"])
            if not isinstance(prepared,PreparedQuery):
                states[str(prepared)]+=1
                for name in names:runs[name][qid]=[]
                continue
            states["READY"]+=1
            ranking=pipeline.retrieve(prepared,top_k=None)
            if len(ranking)!=187:
                raise ValueError("CROSSENCODER_RANKING_INCOMPLETE")
            runs["baseline"][qid]=[r["doc_id"] for r in ranking]
            # For masked-entity queries, compare matching candidates first;
            # for general intent queries, allow expansion to rank 50.
            k=args.entity_k if prepared.tokens else args.general_k
            pool=ranking[:k]
            pairs=[(semantic_surface(prepared.text),
                    semantic_surface(r["text"][:args.max_doc_chars])) for r in pool]
            started=time.perf_counter()
            scores=np.asarray(reranker.predict(
                pairs,batch_size=args.batch_size,show_progress_bar=False,
                convert_to_numpy=True)).reshape(-1).tolist()
            ce_runtime+=(time.perf_counter()-started)*1000
            ce_pairs+=len(pool)
            if len(scores)!=len(pool):
                raise ValueError("CROSSENCODER_OUTPUT_SHAPE")
            selected={r["doc_id"] for r in pool}
            tail=[r["doc_id"] for r in ranking if r["doc_id"] not in selected]
            for name,strength in (("ce_only",1.0),("ce_25",.25),("ce_50",.5)):
                reranked=sort_reranked(pool,scores,strength)
                runs[name][qid]=[r["doc_id"] for r in reranked]+tail
            print("CROSSENCODER_PROGRESS",n,"/",len(queries),
                  "case_id="+qid,"candidate_k="+str(k),flush=True)
        if pipeline.index_status()!=status:
            raise ValueError("CROSSENCODER_INDEX_CHANGED")
    finally:
        pipeline.close()
    results={name:score(runs[name],labels) for name in names}
    base=results["baseline"]
    if len(queries)==88 and (base["hit1"]!=77 or base["hit5"]!=83):
        raise ValueError("CROSSENCODER_BASELINE_MISMATCH")
    print("CROSSENCODER_GATES",json.dumps(states,sort_keys=True),flush=True)
    for name in names:
        metric=results[name]
        improved1=[qid for qid in labels if not base["per_query"][qid][0] and metric["per_query"][qid][0]]
        regressed1=[qid for qid in labels if base["per_query"][qid][0] and not metric["per_query"][qid][0]]
        improved5=[qid for qid in labels if not base["per_query"][qid][1] and metric["per_query"][qid][1]]
        regressed5=[qid for qid in labels if base["per_query"][qid][1] and not metric["per_query"][qid][1]]
        print("CROSSENCODER_RESULT",json.dumps({
            "model":args.model,"variant":name,"cases":len(queries),
            "hit1":metric["hit1"],"hit5":metric["hit5"],
            "recall5":metric["recall5"],"mrr5":metric["mrr5"],
            "confusable_top1":metric["confusable_top1"],
            "delta_hit1":metric["hit1"]-base["hit1"],
            "delta_hit5":metric["hit5"]-base["hit5"],
            "hit1_improved_qids":improved1,"hit1_regressed_qids":regressed1,
            "hit5_improved_qids":improved5,"hit5_regressed_qids":regressed5
        },sort_keys=True,ensure_ascii=False),flush=True)
    print("CROSSENCODER_COST",json.dumps({
        "candidate_pairs":ce_pairs,"ce_inference_total_ms":round(ce_runtime,2),
        "mean_ce_ms_per_ready_query":round(ce_runtime/max(1,states["READY"]),2),
        "general_k":args.general_k,"entity_k":args.entity_k,
        "hcx_calls":0,"db_writes":0
    },sort_keys=True),flush=True)
    print("CROSSENCODER_COMPLETE production_unchanged=true gold_used_for_scoring_only=true",
          flush=True)


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device",choices=("cpu","mps"),default="cpu")
    p.add_argument("--model",default="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
    p.add_argument("--entity-k",type=int,default=20)
    p.add_argument("--general-k",type=int,default=50)
    p.add_argument("--batch-size",type=int,default=8)
    p.add_argument("--max-doc-chars",type=int,default=1500)
    p.add_argument("--qids",nargs="+",default=[])
    p.add_argument("--queries",type=Path,default=PRIVATE/"holdout_retrieval_queries.jsonl")
    p.add_argument("--gold",type=Path,default=PRIVATE/"holdout_retrieval_queries_gold.jsonl")
    args=p.parse_args()
    if not (5<=args.entity_k<=187 and 5<=args.general_k<=187
            and 1<=args.batch_size<=32 and 100<=args.max_doc_chars<=3000
            and re.fullmatch(r"[a-zA-Z0-9_./-]+",args.model)):
        p.error("invalid evaluation parameters")
    try:
        run(args)
        return 0
    except Exception as exc:
        safe=str(exc) if isinstance(exc,ValueError) and str(exc).startswith("CROSSENCODER_") else type(exc).__name__
        print("CROSSENCODER_FAILED",safe,file=sys.stderr,flush=True)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
