"""S2-01 offline live regression. No DB, embedding, HCX or Gold in runtime.
Only aggregate metadata is written. All token/raw mappings remain in memory.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import socket
import statistics
import time

from starter.s2_pseudonymizer import (prepare_from_detection, READY, REVIEW, BLOCKED, V7_POLICY,
                                    canonicalize, make_hmac_token)
from starter.v7_model_store import read_manifest, provision, verify, DEFAULT_LOCAL
from tools.benchmark_step1 import COMMON, DATA, load_data, require, score, metrics

def mask(spans,n):
    b=bytearray(n)
    for s in spans:b[s["start"]:s["end"]]=b"\x01"*(s["end"]-s["start"])
    return b

def inspect_output(text,meta,result,key,seen):
    """Verify actual emitted replacements and all untouched text, not just a mask proxy."""
    out=result.sanitized_text
    src=dst=0
    records={(r["start"],r["end"]):r for r in result.safe_token_manifest}
    vault={(r["source_start"],r["source_end"]):r for r in result.vault_records or ()}
    used=set()
    for job in result.replacement_intervals:
        a,b=job["start"],job["end"]
        require(src<=a<b<=len(text),"INVALID_REPLACEMENT_TRACE")
        unchanged=text[src:a]
        require(out[dst:dst+len(unchanged)]==unchanged,"UNCHANGED_TEXT_MISMATCH")
        dst+=len(unchanged)
        if job["kind"]=="hmac":
            rec=records[(a,b)];v=vault[(a,b)]
            token,norm=make_hmac_token(key,rec["label"],text[a:b])
            require(rec["token"]==v["token"]==token and v["canonical"]==norm and v["original"]==text[a:b],"TOKEN_MAPPING_MISMATCH")
            identity=(rec["label"],norm)
            require(seen.setdefault(token,identity)==identity,"CROSS_DOCUMENT_TOKEN_COLLISION")
            used.add((a,b))
        elif job["kind"]=="residual":token="[REDACTED_UNCLASSIFIED]"
        else:raise RuntimeError("UNEXPECTED_REPLACEMENT_KIND")
        require(out[dst:dst+len(token)]==token,"OUTPUT_REPLACEMENT_MISMATCH")
        src=b;dst+=len(token)
    require(out[dst:]==text[src:],"OUTPUT_TAIL_MISMATCH")
    require(used==set(records)==set(vault),"TOKEN_RECORD_ACCOUNTING")
    actual=mask(result.replacement_intervals,len(text))
    require(actual==mask(meta["mask_intervals"],len(text)),"ACTUAL_PROTECTION_MISMATCH")
    require(sum(actual)==result.replaced_source_chars,"REPLACED_CHAR_ACCOUNTING")
    return actual

def self_test():
    key=b"x"*32
    text="김민준 고객, 010-0000-0000"
    typed=[dict(label="person",start=0,end=3)]
    masks=[dict(start=0,end=3),dict(start=8,end=len(text))]
    meta=dict(policy=V7_POLICY,source="v7_context_validated",deployment_approved=False,
              status=READY,review_events=[],review=[],audit_events=[],entities=typed,
              raw_spans=typed,core_mask_intervals=masks,mask_intervals=masks,
              uncertainty_candidates=[],unmapped_non_o_uncovered_tokens=0)
    def run(t=text,m=meta,k=key):
        return prepare_from_detection(t,typed,m,hmac_key=k,include_vault_records=True)
    p=run()
    require(p.decision==READY and p.external_transfer_approved is False,"READY_SELF_TEST")
    actual=inspect_output(text,meta,p.output,key,{})
    require(sum(actual)==3+len(text)-8 and p.output.residual_redaction_count==1,"RESIDUAL_SELF_TEST")
    require("김민준" not in p.output.sanitized_text and "010-0000-0000" not in p.output.sanitized_text,"OUTPUT_SELF_TEST")
    event=dict(source="test")
    p=run(m={**meta,"status":REVIEW,"review_events":[event],"review":[event]})
    require(p.decision==REVIEW and p.output is None,"REVIEW_SELF_TEST")
    for m in ({**meta,"mask_intervals":masks[:1]}, {**meta,"status":REVIEW},
              {**meta,"deployment_approved":True}, {**meta,"entities":[]},
              {**meta,"unmapped_non_o_uncovered_tokens":-1}):
        p=run(m=m);require(p.decision==BLOCKED and p.output is None,"FAIL_CLOSED_SELF_TEST")
    require(run(m={**meta,"unmapped_non_o_uncovered_tokens":1}).decision==REVIEW,"UNMAPPED_SELF_TEST")
    require(run(k=b"short").decision==BLOCKED,"KEY_SELF_TEST")
    require(run(t="<PERSON_deadbeef>").decision==BLOCKED,"MARKER_SELF_TEST")
    require(make_hmac_token(key,"phone_number","010-0000-0000")==make_hmac_token(key,"phone_number","+82 10 0000 0000"),"PHONE_PARITY")
    require(make_hmac_token(key,"person","김 민준")==make_hmac_token(key,"person","김민준"),"NAME_PARITY")
    require(make_hmac_token(key,"account_number","01000000000")[0]!=make_hmac_token(key,"phone_number","01000000000")[0],"LABEL_DOMAIN_SEPARATION")
    print("S2_PREPARATION_SELF_TEST_PASS",flush=True)

def run(name,rows,det,key,seen,has_gold,*,cache=None):
    counts=Counter();reasons=Counter();labels=Counter();c=Counter();times=[];prep_times=[];issues=[]
    for i,row in enumerate(rows,1):
        text=row["text"]
        t=time.perf_counter()
        typed,meta=det.detect(text)
        times.append((time.perf_counter()-t)*1000)
        t=time.perf_counter()
        p=prepare_from_detection(text,typed,meta,hmac_key=key,include_vault_records=True)
        prep_times.append((time.perf_counter()-t)*1000)
        require(p.external_transfer_approved is False,"UNAUTHORIZED_RELEASE")
        counts["docs"]+=1
        counts["detector_review"]+=bool(meta["review_events"])
        counts[p.decision]+=1
        reasons[p.reason]+=1
        if has_gold:score(c,typed,row["entities"],len(text))
        if cache is not None:
            # Persist only offset/label information, never values, tokens or raw text.
            cache.append(dict(qid=row.get("qid"),length=len(text),
                              entities=[{k:e[k] for k in ("label","start","end")} for e in typed],
                              mask_intervals=[{k:e[k] for k in ("start","end")} for e in meta["mask_intervals"]],
                              review_events=[{k:e[k] for k in ("source","start","end","label") if k in e} for e in meta["review_events"]],
                              decision=p.decision,reason=p.reason))
        if p.decision==READY:
            actual=inspect_output(text,meta,p.output,key,seen)
            counts["hmac_entities"]+=p.output.hmac_entity_count
            counts["residual_intervals"]+=p.output.residual_redaction_count
            counts["residual_chars"]+=p.output.residual_redacted_chars
            for rec in p.output.safe_token_manifest:labels[rec["label"]]+=1
            if has_gold:
                gold=mask(row["entities"],len(text))
                counts["ready_gold_chars"]+=sum(gold)
                counts["ready_unprotected_gold_chars"]+=sum(x and not y for x,y in zip(gold,actual))
                counts["ready_protected_outside_gold_chars"]+=sum(y and not x for x,y in zip(gold,actual))
        else:
            require(p.output is None,"NON_READY_OUTPUT")
            issues.append(dict(row_index=i,qid=row.get("qid"),decision=p.decision,reason=p.reason,
                               detector_sources=sorted({e["source"] for e in meta["review_events"]})))
        if i==1 or i%25==0 or i==len(rows):print("PROGRESS",name,f"{i}/{len(rows)}",flush=True)
    result=dict(counts=dict(counts),reasons=dict(reasons),hmac_labels=dict(labels),issues=issues,
                local_ready_rate=counts[READY]/len(rows),
                detect_mean_ms=statistics.mean(times),detect_p95_ms=sorted(times)[math.ceil(.95*len(times))-1],
                prepare_mean_ms=statistics.mean(prep_times),prepare_p95_ms=sorted(prep_times)[math.ceil(.95*len(prep_times))-1])
    if has_gold:
        result["typed"]=metrics(c)
        expected={"A187":(909,3,3,3),"B171":(1435,56,4,8)}[name]
        require((c["tp"],c["fp"],c["fn"],counts["detector_review"])==expected,"STEP1_PARITY_"+name)
        require(counts["ready_unprotected_gold_chars"]==0,"READY_OUTPUT_GOLD_RESIDUE_"+name)
    print("S2_PREPARATION_RESULT",name,json.dumps(result,ensure_ascii=False),flush=True)
    return result

def queries(path):
    rows=[json.loads(s) for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    require(len(rows)==88,"QUERY_COUNT_MISMATCH")
    texts=[]
    ids=set()
    for r in rows:
        vals=[r[k] for k in ("query","question","text") if isinstance(r.get(k),str)]
        require(vals and len(set(vals))==1 and vals[0].strip(),"QUERY_TEXT_FIELD_INVALID")
        qid=r.get("qid")
        require(isinstance(qid,str) and qid.startswith("Q") and qid[1:].isdigit() and qid not in ids,"QUERY_ID_INVALID")
        ids.add(qid)
        texts.append(dict(qid=qid,text=vals[0]))
    return texts

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--self-test",action="store_true")
    p.add_argument("--out",type=Path)
    p.add_argument("--queries",type=Path)
    p.add_argument("--model-dir",type=Path)
    p.add_argument("--queries-only",action="store_true")
    p.add_argument("--query-gold",type=Path)
    p.add_argument("--cache-query-offsets",action="store_true")
    a=p.parse_args();self_test()
    if a.self_test:return
    require(a.out is not None,"OUTPUT_REQUIRED")
    out=a.out.expanduser().resolve()
    require(not out.exists() and not any((d/".git").exists() for d in [out,*out.parents]),"OUTPUT_INVALID")
    home=Path.home()
    require(not a.queries_only or a.queries is not None,"QUERY_FILE_REQUIRED")
    require(not (a.query_gold or a.cache_query_offsets) or a.queries is not None,"QUERY_FILE_REQUIRED")
    documents_path=home/"AegisRAG/step2_privacy_rag/local_eval/holdout_labeled.jsonl"
    datasets={} if a.queries_only else {"A187":load_data("A187",documents_path),
              "B171":load_data("B171",home/"Downloads/blind_eval.jsonl")}
    gold_summary=None
    if a.queries:
        datasets["Q88"]=queries(a.queries.expanduser())
        print("QUERY_DATASET_OK",len(datasets["Q88"]),flush=True)
        if a.query_gold:
            docs=datasets.get("A187") or load_data("A187",documents_path)
            doc_ids={str(d["id"]) for d in docs}
            rows=[json.loads(s) for s in a.query_gold.expanduser().read_text(encoding="utf-8-sig").splitlines() if s.strip()]
            ids=[g.get("qid") for g in rows]
            require(len(rows)==88 and len(set(ids))==88 and set(ids)=={q["qid"] for q in datasets["Q88"]},"QUERY_GOLD_ID_MISMATCH")
            for g in rows:
                refs=g.get("relevant_doc_ids")
                require(isinstance(refs,list) and all(isinstance(x,str) for x in refs) and len(set(refs))==len(refs) and set(refs)<=doc_ids,"QUERY_GOLD_DOC_MISMATCH")
            gold_summary=dict(rows=len(rows),with_relevant=sum(bool(g["relevant_doc_ids"]) for g in rows),
                              without_relevant=sum(not g["relevant_doc_ids"] for g in rows),
                              sha256=hashlib.sha256(a.query_gold.expanduser().read_bytes()).hexdigest())
            print("QUERY_GOLD_OK",json.dumps(gold_summary),flush=True)
    else:print("QUERY_DATASET_NOT_REQUESTED",flush=True)
    m=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    os.environ.update(COMMON)
    if a.model_dir:
        model=verify(a.model_dir,m);source="verified_explicit_local"
    else:
        from huggingface_hub import snapshot_download
        def fetch(manifest):
            return snapshot_download(repo_id=manifest["repo_id"],revision=manifest["revision"],
                                     allow_patterns=sorted(manifest["sha256"]),local_files_only=True,token=False)
        model,source=provision(m,DEFAULT_LOCAL,fetch=fetch)
    print("MODEL_SOURCE",source,flush=True)
    def deny(*args,**kwargs):raise RuntimeError("NETWORK_FORBIDDEN")
    socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
    import torch
    from starter.v7_context_validated import ContextValidatedOnepass
    torch.set_num_threads(2);torch.set_num_interop_threads(2)
    det=ContextValidatedOnepass(model);key=secrets.token_bytes(32);seen={}
    query_cache=[] if a.cache_query_offsets else None
    results={name:run(name,rows,det,key,seen,name!="Q88",cache=query_cache if name=="Q88" else None) for name,rows in datasets.items()}
    report=dict(policy=V7_POLICY,model_revision=m["revision"],model_source=source,
                datasets=results,query_evaluated="Q88" in results,
                query_sha256=hashlib.sha256(a.queries.expanduser().read_bytes()).hexdigest() if a.queries else None,
                query_gold=gold_summary,external_transfer_approved=False)
    if query_cache is not None:
        report["private_query_offsets"]=query_cache
    os.umask(0o077);out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("S2_LOCAL_PREPARATION_REGRESSION_COMPLETE",out,flush=True)

if __name__=="__main__":main()
