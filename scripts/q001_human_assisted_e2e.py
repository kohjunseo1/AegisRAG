#!/usr/bin/env python3
"""Q-001 Human-assisted 187-doc isolation + common 88-query evaluation.

Uses 177 immutable baseline masked documents and 10 locally human-annotated
source documents. Revalidates changes via original Step1 model heads + Gate
and applies HMAC locally. Writes ONLY a content-addressed NEW experiment table,
never changes original V6 table. No HCX. No Gold in preparation or ranking.
This prototype is NOT production authorization or proof of full PII recall.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"scripts"),str(ROOT/"step2_privacy_rag/app"),
              str(ROOT/"step1_pii_detector/src")]
from review_apply_q001 import prepare_approved_batch, load_journal
from review_dashboard_q001 import read_sources, private_path
from compare_88_retrieval import aggregate,safe_path

IDS=("H0002","H0057","H0072","H0091","H0103",
     "H0105","H0116","H0117","H0119","H0135")
BASELINE=ROOT/"step2_privacy_rag/local_eval/reproduction_20261111/prepared.json"
SOURCE=ROOT/"step2_privacy_rag/local_eval/holdout_labeled.jsonl"
JOURNAL=ROOT/"step2_privacy_rag/local_eval/q001_review_decisions.jsonl"
OUTPUT=ROOT/"step2_privacy_rag/local_eval/q001_human_assisted_187.json"


def build_in_memory(baseline:dict,sources:dict[str,str],detector:Any,
                    key:bytes,journal_entries:list[dict]) -> tuple[dict,dict]:
    from dataset_preparation import key_tag
    if (baseline.get("format")!="aegisrag-local-embedding-eval-v1"
        or baseline.get("hmac_key_tag")!=key_tag(key)
        or len(baseline.get("documents",[]))!=177
        or len(baseline.get("queries",[]))!=88
        or len(baseline.get("gold_for_evaluation_only",[]))!=88
        or len(sources)!=10 or set(sources)!=set(IDS)):
        raise ValueError("INCOMPATIBLE_BASELINE_OR_SOURCE_COUNT")
    existing={d["doc_id"] for d in baseline["documents"]}
    if len(existing)!=177 or existing&set(sources):
        raise ValueError("REVIEW_DOCUMENT_ALREADY_INDEXED")
    approved=prepare_approved_batch(sources,detector,key,journal_entries)
    if len(approved)!=len(sources):
        raise ValueError("NOT_ALL_REVIEWS_REVALIDATED")
    new=deepcopy(baseline)
    new["documents"].extend({
        "doc_id":r.doc_id,"text":r.sanitized_text,
        "region_keys":list(r.region_keys)}
        for r in approved)
    new["documents"].sort(key=lambda d:d["doc_id"])
    if len(new["documents"])!=187 or len({d["doc_id"] for d in new["documents"]})!=187:
        raise ValueError("INVALID_HUMAN_ASSISTED_DOCUMENT_COUNT")
    new["scope"]="local_synthetic_holdout_human_assisted_experiment"
    new["counts"]={
        "document_states":{"PREVIOUSLY_AUTO_PREPARED":177,"REVIEW_REVALIDATED":10},
        "query_states":baseline.get("counts",{}).get("query_states",{}),
        "docs_index_candidates":187,
        "queries_search_candidates":sum(q.get("text") is not None for q in new["queries"]),
    }
    safe_report={
        "baseline_documents_unchanged":all(
            a==b for a,b in zip(
                sorted(baseline["documents"],key=lambda d:d["doc_id"]),
                [d for d in new["documents"] if d["doc_id"] in existing])),
        "baseline_doc_count":177,"revalidated_review_count":len(approved),
        "review_kind_counts":{
            "name":sum(x.review_type=="review_name" for x in approved),
            "veil":sum(x.review_type=="review_veil" for x in approved)},
        "mask_coverage_non_decreasing":all(
            x.final_protected_characters>=x.original_protected_characters
            for x in approved),
        "all88_queries_unchanged":new["queries"]==baseline["queries"],
        "db_written":False,"hcx_called":False,
    }
    if not (safe_report["baseline_documents_unchanged"]
            and safe_report["mask_coverage_non_decreasing"]
            and safe_report["all88_queries_unchanged"]):
        raise ValueError("HUMAN_ASSISTED_REPRO_REGRESSION")
    return new,safe_report


def run_pipeline(*,mode:str="preview",env:Path=ROOT/".env",
                 baseline:Path=BASELINE,source:Path=SOURCE,journal:Path=JOURNAL,
                 output:Path=OUTPUT,device:str="cpu",confirm:str|None=None) -> dict:
    from dataset_preparation import fixed_key,private_write
    from reproduction import artifact, encode_docs, target, ingest, evaluate
    key=fixed_key(env)
    original=artifact(safe_path(baseline),env)
    docs=read_sources(source,list(IDS))
    events=load_journal(journal)
    from pii_detector import create_detector
    with create_detector() as detector:
        new,safe=build_in_memory(original,docs,detector,key,events)
    table=target(new)
    report={
        "state":"REVALIDATED_LOCAL",
        "planned_table":table,
        "documents":len(new["documents"]),
        "queries":len(new["queries"]),
        "review":safe,
        "hcx_called":False,"baseline_table_modified":False
    }
    if mode=="preview":
        return report
    if mode!="run" or confirm!="CREATE_ISOLATED_HUMAN_ASSISTED_INDEX":
        raise ValueError("EXPLICIT_EXPERIMENT_CONFIRMATION_REQUIRED")
    dest=private_path(output)
    if dest.exists():
        # Reuse only byte-identical experiment data; never overwrite.
        old=artifact(dest,env)
        if old!=new:
            raise ValueError("EXISTING_EXPERIMENT_ARTIFACT_DIFFERENT")
    else:
        private_write(dest,new)
    import psycopg
    from settings import db_config
    vectors=encode_docs(new,device)
    with psycopg.connect(**db_config(env)) as conn:
        status=ingest(conn,new,vectors,table,table)
    # These rankings exclude unresolved query text, which remains deferred.
    available,ranked=evaluate(new,table,env,device,expected_baseline=False)
    gold=new["gold_for_evaluation_only"]
    all_ids={g["qid"] for g in gold}
    score,missed=aggregate(gold,{q:[x["doc_id"] for x in ranked.get(q,[])][:20]
                                if ranked.get(q) and isinstance(ranked[q][0],dict)
                                else ranked.get(q,[])[:20]
                                for q in all_ids},all_ids)
    report.update({
        "state":"EXPERIMENT_INDEXED_AND_EVALUATED",
        "table_status":status,"table":table,
        "eligible":available,"all88":score,
        "all88_hit5_misses":missed,
        "review":{**safe,"db_written":True},
        "db_written":True,
    })
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode",choices=("preview","run"),default="preview")
    ap.add_argument("--env",type=Path,default=ROOT/".env")
    ap.add_argument("--baseline",type=Path,default=BASELINE)
    ap.add_argument("--source",type=Path,default=SOURCE)
    ap.add_argument("--journal",type=Path,default=JOURNAL)
    ap.add_argument("--out",type=Path,default=OUTPUT)
    ap.add_argument("--device",choices=("cpu","mps"),default="cpu")
    ap.add_argument("--confirm")
    a=ap.parse_args()
    try:
        out=run_pipeline(mode=a.mode,env=a.env,baseline=a.baseline,
                         source=a.source,journal=a.journal,output=a.out,
                         device=a.device,confirm=a.confirm)
        print("Q001_HUMAN_ASSISTED_E2E "+json.dumps(out,ensure_ascii=False,sort_keys=True))
        return 0
    except Exception as e:
        # Never print original PII or DSNs from library exceptions.
        print("Q001_HUMAN_ASSISTED_FAILED "+type(e).__name__,file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
