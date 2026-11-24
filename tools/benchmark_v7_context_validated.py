"""Final A187/B171 live regression of context review resolution.
Hard403 is frozen historical evidence, not a tuning target. No model updates.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import socket
import time
import statistics
import math
from starter.v7_context_validated import ContextValidatedOnepass, reason, resolve, POLICY, SOURCE
from starter.v7_validated import cover, key
from starter.v7_onepass import apply_policy
from tools.audit_v7_residuals import EXPECTED
from tools.benchmark_step1 import COMMON, DATA, ONEPASS_SHA, digest, load_data, require, score, metrics
from tools.benchmark_v7_validated import self_test as baseline_self_test

def self_test():
    baseline_self_test()
    def candidate(t,needle,label="person",start=0):
        a=t.index(needle,start)
        return dict(label=label,start=a,end=a+len(needle),source=SOURCE)
    def check(t,needle,expected,label="person",typed=(),start=0):
        e=candidate(t,needle,label,start)
        assert reason(t,e,list(typed))==expected,(expected,reason(t,e,list(typed)))
    check("상담원 박 대리: 안내합니다.","박","candidate_surname_title")
    check("박 대리님께서 연락했다.","박","candidate_surname_title")
    check("고객 박지훈 대리님","박",None)
    check("김대리: 안녕하세요","리",None)
    check("고객 김 씨","김",None)
    check("직원 A: 안녕하세요","A","candidate_dialogue_label")
    check("고객명: A","A",None)
    t="[부고] 김민수 과장 조모상\n김민수 과장의 조모 故 박영희님께서 별세"
    a=t.index("박영희")
    check(t,"조","candidate_bereavement_header",start=0)
    check(t,"조","candidate_bereavement_body",typed=[dict(label="person",start=a,end=a+3)],start=t.index("\n"))
    check("조모 고객님","조",None)
    check("홈페이지 https://www.example.com/path",".com","candidate_url_host","email")
    check("메일 abc@example.com",".com",None,"email")
    check("https://abc.example@other.com/",".example",None,"email")
    check("문의: 1588-1234","1234","candidate_service_contact","phone_number")
    check("개인 전화번호: 010-1234-5678","1234",None,"phone_number")
    check("운송장 번호: 1588-1234","1234",None,"phone_number")
    check("미납 금액 **원","*","candidate_amount_placeholder")
    check("비밀번호 **","*",None)
    t="박 대리: 안내";e=candidate(t,"박")
    meta=dict(review_events=[e],audit_events=[],mask_intervals=[dict(start=0,end=1)])
    typed,after=resolve(t,[],meta)
    assert not typed and not after["review_events"] and len(after["audit_events"])==1
    assert after["mask_intervals"]==meta["mask_intervals"] and after["deployment_approved"] is False
    print("CONTEXT_SELF_TEST_PASS",flush=True)

def run(name,docs,logs,det):
    c=Counter();rc=Counter();checks=Counter(); reasons=Counter();resolved=Counter();times=[];remaining=[]
    for idx,(doc,entry) in enumerate(zip(docs,logs),1):
        text=doc["text"];gold=doc["entities"]
        start=time.perf_counter();typed,info=det.detect(text);times.append((time.perf_counter()-start)*1000)
        require(sorted(map(key,info["raw_spans"]))==sorted(map(key,entry["spans"])),"RAW_PARITY_"+name)
        base,original=apply_policy(text,entry["spans"])
        require(typed==base,"TYPED_PARITY_"+name)
        require(info["deployment_approved"] is False,"RELEASE_NOT_APPROVED")
        require(info["status"]==("REVIEW_REQUIRED" if info["review_events"] else "READY_FOR_LOCAL_PSEUDONYMIZATION"),"STATUS")
        score(c,typed,gold,len(text));score(rc,info["raw_spans"],gold,len(text))
        gm=cover(gold,len(text));old=cover(original["mask_intervals"],len(text))
        cand=cover(info["uncertainty_candidates"],len(text));new=cover(info["mask_intervals"],len(text))
        require(all(bool(x or y)==bool(z) for x,y,z in zip(old,cand,new)),"MASK_PARITY")
        checks["docs"]+=1
        checks["before_review_docs"]+=bool(info["review_before_context"])
        checks["review_docs"]+=bool(info["review_events"])
        checks["before_review_events"]+=len(info["review_before_context"])
        checks["review_events"]+=len(info["review_events"])
        checks["protected_gold_chars"]+=sum(x and y for x,y in zip(gm,new))
        checks["protected_chars_outside_gold"]+=sum(y and not x for x,y in zip(gm,new))
        for e in info["review_events"]:
            reasons[e["source"]]+=1
            remaining.append(dict(doc_index=idx,**e))
        for e in info["audit_events"]:
            if e.get("action")=="review_resolved_protection_retained":
                resolved[e["source"]]+=1
                checks["resolved_events"]+=1
                checks["resolved_gold_overlap_chars"]+=sum(gm[e["start"]:e["end"]])
        if idx==1 or idx%50==0 or idx==len(docs):
            print("PROGRESS",name,f"{idx}/{len(docs)}",flush=True)
    def sig(m):return tuple(m[k] for k in ("tp","fp","fn","fully_uncovered","leak_chars"))
    er,et,_=EXPECTED[name]
    require(sig(metrics(rc))==er and sig(metrics(c))==et,"SCORE_PARITY_"+name)
    require((checks["before_review_docs"],checks["before_review_events"])=={"A187":(10,10),"B171":(44,82)}[name],"BASE_REVIEW_PARITY_"+name)
    require(checks["resolved_events"]+checks["review_events"]==checks["before_review_events"],"EVENT_LOSS")
    require(checks["protected_gold_chars"]==c["gold_chars"],"GOLD_PROTECTION_REGRESSION")
    require(checks["resolved_gold_overlap_chars"]==0,"UNEXPECTED_GOLD_RESOLUTION")
    result=dict(typed=metrics(c),checks=dict(checks),resolved_reasons=dict(resolved),remaining_reasons=dict(reasons),
                remaining=remaining,mean_ms=statistics.mean(times),p95_ms=sorted(times)[math.ceil(.95*len(times))-1])
    print("CONTEXT_FINAL_RESULT",name,json.dumps(result,ensure_ascii=False),flush=True)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--self-test",action="store_true")
    p.add_argument("--out",type=Path)
    p.add_argument("--old-root",type=Path,default=Path.home()/"AegisRAG-unified-v1")
    a=p.parse_args();self_test()
    if a.self_test:return
    require(a.out is not None,"OUTPUT_REQUIRED")
    out=a.out.expanduser().resolve()
    require(not out.exists() and not any((d/".git").exists() for d in [out,*out.parents]),"OUTPUT_INVALID")
    ck=a.old_root/"step2_privacy_rag/local_eval/models/ax_v7_final_onepass/best_model"
    cache=a.old_root/"step2_privacy_rag/local_eval/v7_final_candidate_eval"
    require(digest(ck/"model.safetensors")==ONEPASS_SHA,"MODEL_HASH_MISMATCH")
    manifest=json.loads((cache/"manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("model_sha256")==ONEPASS_SHA and manifest.get("pilot")==0
            and manifest.get("mode")=="token510_only_no_post","CACHE_MANIFEST_MISMATCH")
    hashes={x["role"]:x["sha256"] for x in manifest["data"]}
    paths={"A187":Path.home()/"AegisRAG/step2_privacy_rag/local_eval/holdout_labeled.jsonl",
           "B171":Path.home()/"Downloads/blind_eval.jsonl"}
    datasets={}
    for name,path in paths.items():
        require(hashes.get(name)==DATA[name][2],"CACHE_HASH_"+name)
        docs=load_data(name,path)
        logs=json.loads((cache/f"PRIVATE_{name}_token510_candidate_offset_predictions.json").read_text(encoding="utf-8"))
        require(isinstance(logs,list) and len(logs)==len(docs),"CACHE_COUNT_"+name)
        datasets[name]=(docs,logs)
    os.environ.update(COMMON)
    def deny(*args,**kwargs):raise RuntimeError("NETWORK_FORBIDDEN")
    socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
    import torch
    torch.set_num_threads(2);torch.set_num_interop_threads(2)
    det=ContextValidatedOnepass(ck)
    results={name:run(name,docs,logs,det) for name,(docs,logs) in datasets.items()}
    out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(dict(policy=POLICY,model_sha256=ONEPASS_SHA,datasets=results,deployment_approved=False),f,ensure_ascii=False,indent=2)
    print("STEP1_CONTEXT_REGRESSION_PASS",out,flush=True)

if __name__=="__main__":main()
