"""S2 query controller integration regression. A187 cache + fresh Q88 detection.
No database, HCX, embedding or external authorization. Gold only scores.
Outputs contain aggregate counts, QIDs, reason codes and static messages.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import statistics

from starter.s2_query_controller import prepare_query,document_region_keys,self_test
from starter.s2_pseudonymizer import prepare_from_detection,READY,V7_POLICY
from starter.v7_model_store import read_manifest
from tools.benchmark_step1 import load_data,require
from tools.benchmark_s2_prepare import queries,inspect_output,mask
from tools.diagnose_s2_token_matching import local_model

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--document-cache",required=True,type=Path)
    p.add_argument("--query-cache",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    a=p.parse_args();self_test()
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    dp=root/"holdout_labeled.jsonl";qp=root/"holdout_retrieval_queries.jsonl"
    gp=root/"holdout_retrieval_queries_gold.jsonl";out=a.out.expanduser().resolve()
    require(not out.exists() and not any((x/".git").exists() for x in (out,*out.parents)),"OUTPUT_INVALID")
    docs=load_data("A187",dp);qs=queries(qp)
    gold=[json.loads(s) for s in gp.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    refs={g["qid"]:set(g["relevant_doc_ids"]) for g in gold}
    dc=json.loads(a.document_cache.expanduser().read_text())
    qc=json.loads(a.query_cache.expanduser().read_text())
    m=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    for cache,field,path in [(dc,"document_sha256",dp),(qc,"query_sha256",qp)]:
        require(cache["model_revision"]==m["revision"] and cache["policy"]==V7_POLICY and cache[field]==sha(path),"CACHE_MISMATCH")
    dm={str(r["doc_id"]):r for r in dc["private_document_offsets"]}
    qm={r["qid"]:r for r in qc["private_query_offsets"]}
    require(len(dm)==len(dc["private_document_offsets"])==len(docs)==187 and set(dm)=={str(d["id"]) for d in docs},"DOC_CACHE_IDS")
    require(len(qm)==len(qc["private_query_offsets"])==len(refs)==len(gold)==len(qs)==88 and set(qm)==set(refs)=={q["qid"] for q in qs},"QUERY_CACHE_IDS")
    require(all(v<=set(dm) for v in refs.values()),"GOLD_DOC_IDS")
    key=secrets.token_bytes(32);seen={};ready=set()
    index=defaultdict(set);region_index=defaultdict(set)
    for d in docs:
        text=d["text"];did=str(d["id"]);r=dm[did]
        require(r["length"]==len(text),"DOC_LENGTH_MISMATCH")
        typed=r["entities"]
        meta={**r,"policy":V7_POLICY,"source":"v7_context_validated","deployment_approved":False,
              "status":"REVIEW_REQUIRED" if r["review_events"] else READY,"review":r["review_events"]}
        prep=prepare_from_detection(text,typed,meta,hmac_key=key,include_vault_records=True)
        require(prep.decision==r["decision"],"DOC_PREPARATION_PARITY")
        if prep.decision!=READY:continue
        ready.add(did)
        actual=inspect_output(text,meta,prep.output,key,seen)
        require(all(not x or y for x,y in zip(mask(d["entities"],len(text)),actual)),"DOC_GOLD_RESIDUE")
        for e in prep.output.safe_token_manifest:index[e["token"]].add(did)
        for k in document_region_keys(text,prep.output.safe_token_manifest,key):region_index[k].add(did)
    require(len(ready)==184,"DOC_READY_REGRESSION")
    print("LOCAL_INDEX_READY",json.dumps(dict(docs=len(ready),token_keys=len(index),region_keys=len(region_index))),flush=True)
    det=local_model(m)
    counts=Counter();transitions=Counter();details=[];times=[];base_times=[]
    for i,q in enumerate(qs,1):
        qid=q["qid"];text=q["text"];old=qm[qid]
        typed,meta=det.detect(text)
        baseline_start=time.perf_counter()
        base=prepare_from_detection(text,typed,meta,hmac_key=key,include_vault_records=True)
        base_times.append((time.perf_counter()-baseline_start)*1000)
        require(base.decision==old["decision"] and base.reason==old["reason"],"QUERY_BASELINE_DRIFT")
        require(mask(meta["mask_intervals"],len(text))==mask(old["mask_intervals"],len(text)),"QUERY_MASK_DRIFT")
        require([{k:e[k] for k in ("label","start","end")} for e in typed]==old["entities"],"QUERY_TYPED_DRIFT")
        start=time.perf_counter()
        # All READY docs simulate one evaluation user's scope; not a live ACL test.
        d=prepare_query(text,typed,meta,key=key,lookup=lambda t:bool(index.get(t)),
                        region_lookup=lambda k:bool(region_index.get(k)))
        times.append((time.perf_counter()-start)*1000)
        require(not d.external_transfer_approved,"UNAUTHORIZED_EGRESS")
        counts[d.status]+=1;transitions[base.decision+" -> "+d.status]+=1
        hits=set();old_hits=set()
        if base.output:
            for e in base.output.safe_token_manifest:old_hits.update(index.get(e["token"],set()))
        if d.output:
            require(d.status in {"SEARCH_READY","RECOVERED_LOCAL"} and d.answer is None,"OUTPUT_STATE_MISMATCH")
            inspect_output(text,meta,d.output,key,seen)
            for e in d.output.safe_token_manifest:hits.update(index.get(e["token"],set()))
            for k in d.region_keys:hits.update(region_index.get(k,set()))
            counts["outputs_original_mask_preserved"]+=1
            if d.status=="SEARCH_READY":
                require(base.output is not None and d.output.sanitized_text==base.output.sanitized_text,"NORMAL_OUTPUT_CHANGED")
                counts["normal_output_identical"]+=1
        else:
            require(d.status in {"CLARIFY","ERROR","INVALID_INPUT"} and bool(d.answer),"MISSING_LOCAL_RESPONSE")
            counts["local_response_no_hcx"]+=1
        g=refs[qid]&ready
        counts["queries_known_gold_token_or_region_match"]+=bool(hits&g)
        if d.status=="RECOVERED_LOCAL":
            counts["recovered_with_known_gold_match"]+=bool(hits&g)
            counts["recovered_with_additional_outside_known_gold"]+=bool(hits-old_hits-refs[qid])
        if d.status!="SEARCH_READY":
            row=dict(qid=qid,baseline=base.decision,status=d.status,reason=d.reason,
                     known_gold_matches=len(hits&g),candidate_docs=len(hits),local_answer=d.answer)
            details.append(row);print("QUERY_CONTROLLER_CASE",json.dumps(row,ensure_ascii=False),flush=True)
        if i%25==0 or i==88:print("QUERY_PROGRESS",i,"/88",flush=True)
    counts["queries"]=88
    report=dict(counts=dict(counts),transitions=dict(transitions),details=details,
                base_prepare_mean_ms=statistics.mean(base_times),
                controller_mean_ms=statistics.mean(times),
                controller_p95_ms=sorted(times)[int(.95*(len(times)-1))],
                model_revision=m["revision"],external_transfer_approved=False,
                scope="Local preparation only; simulated scope of 184 READY documents. Not retrieval, identity, live ACL, or egress evaluation.")
    print("QUERY_CONTROLLER_SUMMARY",json.dumps(report,ensure_ascii=False),flush=True)
    os.umask(0o077);out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("QUERY_CONTROLLER_COMPLETE",out,flush=True)

if __name__=="__main__":main()
