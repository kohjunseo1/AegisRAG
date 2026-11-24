"""Final Step1 candidate regression; local only, no further threshold search."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import socket
import statistics
import time

from starter.v7_validated import ValidatedOnepass, finalize, cover, key, POLICY
from starter.v7_onepass import apply_policy
from tools.audit_v7_residuals import EXPECTED
from tools.benchmark_step1 import COMMON, DATA, ONEPASS_SHA, digest, load_data, require, score, metrics

REVIEW={"A187":10,"B171":44,"Hard403":89}

def self_test():
    # Simulated strict exclusion remains protected, but is an audit not human review.
    t="가나다라"
    raw=[{"label":"person","start":0,"end":1}]
    info={"raw_spans":raw,"mask_intervals":[{"start":0,"end":1}],
          "review":[{"source":"standalone_surname_title",**raw[0]}],
          "boundary_events":[],"join_events":[],"guard_events":[],"structural_events":[]}
    tokens=[dict(start=a,end=b,winner="O",p_pii=p,best_type=l)
            for a,b,p,l in [(1,2,.3,"person"),(2,3,.12,"person"),(3,4,.12,"address")]]
    typed,meta=finalize(t,[],info,tokens)
    assert typed==[]
    assert meta["mask_intervals"]==[{"start":0,"end":3}]
    assert len(meta["audit_events"])==1 and len(meta["review_events"])==1
    assert meta["status"]=="REVIEW_REQUIRED" and meta["deployment_approved"] is False
    assert meta["review_events"][0]["label_confirmed"] is False
    _,m=finalize(t,[],info,[])
    assert not m["review_events"] and m["status"]=="READY_FOR_LOCAL_PSEUDONYMIZATION"
    assert m["mask_intervals"]==info["mask_intervals"] and m["deployment_approved"] is False
    # Unresolved old review is retained; resolved postal overlap is audit-only.
    t="12345 서울"
    typed=[{"label":"address","start":0,"end":8}]
    ev={"source":"postal:review_overlap","label":"address","start":0,"end":6}
    inf={"raw_spans":typed,"mask_intervals":[{"start":0,"end":8}],
         "review":[ev],"boundary_events":[],"join_events":[{"rule":"postal_join","status":"merged","start":0,"end":8}],
         "guard_events":[],"structural_events":[]}
    _,m=finalize(t,typed,inf,[])
    assert not m["review_events"] and any(e["action"]=="resolved_by_postal_join" for e in m["audit_events"])
    inf["join_events"]=[]
    _,m=finalize(t,typed,inf,[])
    assert m["review_events"]==[ev]
    print("FINAL_POLICY_SELF_TEST_PASS",flush=True)

def run(name,docs,logs,det):
    c=Counter(); rawc=Counter(); checks=Counter(); reasons=Counter(); audits=Counter(); times=[]
    for idx,(doc,entry) in enumerate(zip(docs,logs),1):
        t=doc["text"];gold=doc["entities"]
        start=time.perf_counter()
        typed,info=det.detect(t)
        times.append((time.perf_counter()-start)*1000)
        require(sorted(map(key,info["raw_spans"]))==sorted(map(key,entry["spans"])),"RAW_DOCUMENT_PARITY_MISMATCH_"+name)
        base,original=apply_policy(t,entry["spans"])
        require(typed==base,"TYPED_DOCUMENT_PARITY_MISMATCH_"+name)
        require(info["deployment_approved"] is False,"UNEXPECTED_RELEASE_APPROVAL")
        require(info["status"]==("REVIEW_REQUIRED" if info["review_events"] else "READY_FOR_LOCAL_PSEUDONYMIZATION"),"STATUS_MISMATCH")
        score(c,typed,gold,len(t));score(rawc,info["raw_spans"],gold,len(t))
        old=cover(original["mask_intervals"],len(t));new=cover(info["mask_intervals"],len(t))
        bm=cover(typed,len(t));gm=cover(gold,len(t));cand=cover(info["uncertainty_candidates"],len(t))
        checks["docs"]+=1
        checks["review_docs"]+=bool(info["review_events"])
        checks["audit_docs"]+=bool(info["audit_events"])
        checks["uncertainty_docs"]+=bool(info["uncertainty_candidates"])
        checks["unmapped_non_o_uncovered_tokens"]+=info["unmapped_non_o_uncovered_tokens"]
        checks["newly_uncovered_chars"]+=sum(x and not y for x,y in zip(old,new))
        checks["candidate_chars_outside_gold"]+=sum(x and not y and not z for x,y,z in zip(cand,gm,old))
        checks["new_gold_chars_candidate_covered"]+=sum(x and y and not z for x,y,z in zip(cand,gm,old))
        checks["protected_gold_chars_proxy"]+=sum(x and y for x,y in zip(new,gm))
        leaky=any(not all(bm[g["start"]:g["end"]]) for g in gold)
        checks["baseline_leaky_docs_reviewed"]+=leaky and bool(info["review_events"])
        checks["baseline_leaky_docs_unreviewed"]+=leaky and not info["review_events"]
        for g in gold:
            a,b=g["start"],g["end"]
            if not any(bm[a:b]):
                checks["complete_miss_pending_overlap"]+=any(cand[a:b])
                checks["complete_miss_pending_full"]+=all(cand[a:b])
        for e in info["review_events"]:reasons[e["source"]]+=1
        for e in info["audit_events"]:audits[e["source"]]+=1
        if idx==1 or idx%50==0 or idx==len(docs):
            print("PROGRESS",name,f"{idx}/{len(docs)}",flush=True)
    def sig(m):return tuple(m[k] for k in ("tp","fp","fn","fully_uncovered","leak_chars"))
    er,et,_=EXPECTED[name]
    require(sig(metrics(rawc))==er and sig(metrics(c))==et,"SCORE_PARITY_MISMATCH_"+name)
    require(checks["review_docs"]==REVIEW[name] and checks["newly_uncovered_chars"]==0,"REVIEW_OR_PROTECTION_PARITY_MISMATCH_"+name)
    if name=="Hard403":
        require(tuple(checks[k] for k in ("complete_miss_pending_overlap","complete_miss_pending_full","new_gold_chars_candidate_covered","candidate_chars_outside_gold","baseline_leaky_docs_unreviewed"))==(16,14,73,106,19),"HARD_BOUNDARY_PARITY_MISMATCH")
    result={"typed":metrics(c),"checks":dict(checks),"review_reasons":dict(reasons),"audit_reasons":dict(audits),
            "mask_union_recall_proxy":checks["protected_gold_chars_proxy"]/c["gold_chars"],
            "mean_ms":statistics.mean(times),"p95_ms":sorted(times)[max(0,(__import__("math").ceil(.95*len(times))-1))]}
    print("FINAL_RESULT",name,json.dumps(result,sort_keys=True),flush=True)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-root",type=Path,default=Path.home()/"AegisRAG-unified-v1")
    p.add_argument("--out",type=Path)
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    self_test()
    if a.self_test:return
    require(a.out is not None,"OUTPUT_REQUIRED")
    out=a.out.expanduser().resolve()
    require(not out.exists(),"OUTPUT_EXISTS")
    require(not any((d/".git").exists() for d in [out,*out.parents]),"OUTSIDE_GIT_REQUIRED")
    root=a.old_root.expanduser()
    ck=root/"step2_privacy_rag/local_eval/models/ax_v7_final_onepass/best_model"
    cache=root/"step2_privacy_rag/local_eval/v7_final_candidate_eval"
    require(digest(ck/"model.safetensors")==ONEPASS_SHA,"MODEL_HASH_MISMATCH")
    manifest=json.loads((cache/"manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("model_sha256")==ONEPASS_SHA and manifest.get("pilot")==0
            and manifest.get("mode")=="token510_only_no_post","CACHE_MANIFEST_MISMATCH")
    hashes={x["role"]:x["sha256"] for x in manifest["data"]}
    paths={"A187":Path.home()/"AegisRAG/step2_privacy_rag/local_eval/holdout_labeled.jsonl",
           "B171":Path.home()/"Downloads/blind_eval.jsonl",
           "Hard403":Path.home()/"aegis_external_eval/team4/synth_hard_eval.jsonl"}
    datasets={}
    for name,path in paths.items():
        require(hashes.get(name)==DATA[name][2],"CACHE_DATA_HASH_MISMATCH_"+name)
        docs=load_data(name,path)
        logs=json.loads((cache/f"PRIVATE_{name}_token510_candidate_offset_predictions.json").read_text(encoding="utf-8"))
        require(isinstance(logs,list) and len(logs)==len(docs),"CACHE_COUNT_MISMATCH_"+name)
        datasets[name]=(docs,logs)
    os.environ.update(COMMON)
    def deny(*args,**kwargs):raise RuntimeError("NETWORK_FORBIDDEN")
    socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
    import torch
    torch.set_num_threads(2);torch.set_num_interop_threads(2)
    start=time.perf_counter();det=ValidatedOnepass(ck);cold=(time.perf_counter()-start)*1000
    results={name:run(name,docs,logs,det) for name,(docs,logs) in datasets.items()}
    report={"policy":POLICY,"model_sha256":ONEPASS_SHA,
            "note":"Local Step1 regression only. No deployment, actual pseudonymization, DB or network. Review counts are not actual transfer safety.",
            "cold_model_load_ms":cold,"datasets":results}
    out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("STEP1_FINAL_REGRESSION_PASS",out,flush=True)

if __name__=="__main__":main()
