"""EXP4/5: one frozen-model pass per document, shadow REVIEW ablation only.

Capture scores from the *actual* frozen window predictor, with exact per-document
raw cache parity. No training, model changes, auto-additions, network or Gold edits.
Thresholds are exploratory, not calibrated operating points. Aggregate-only output.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import time
from collections import Counter, defaultdict

from starter import v7_frozen as F
from starter.v7_onepass import Onepass, apply_policy
from tools.audit_v7_residuals import EXPECTED, cover, key, review_scenarios
from tools.ablate_v7_validators import generate, choose, measure
from tools.benchmark_step1 import COMMON, DATA, ONEPASS_SHA, digest, load_data, metrics, require, score

THRESHOLDS=(0.10,0.25,0.40,0.60)
MODES=("baseline_audit",*(f"o_pii_{int(t*100):02d}" for t in THRESHOLDS),
       "o_margin_15","window_any","window_p50",
       "o25_plus_window","o25_plus_repeat","o25_plus_format_repeat")

class CaptureTokenizer:
    def __init__(self,tok):
        self.tok=tok
        self.enc=None
    def __call__(self,*args,**kwargs):
        self.enc=self.tok(*args,**kwargs)
        return self.enc

class CaptureModel:
    def __init__(self,model,torch):
        self.model=model
        self.config=model.config
        self.torch=torch
        self.rows=[]
        self.tags=[self.config.id2label[i] for i in range(self.config.num_labels)]
        self.types=[F.model_label(t) for t in self.tags]
        oi=[i for i,t in enumerate(self.tags) if str(t).upper()=="O"]
        require(len(oi)==1,"O_LABEL_NOT_UNIQUE")
        self.oi=oi[0]
        self.groups={lab:[i for i,v in enumerate(self.types) if v==lab] for lab in F.LABELS}
        require(all(self.groups.values()),"MISSING_PII_LABEL_GROUP")
        self.group_names=list(self.groups)
    def __call__(self,**kwargs):
        result=self.model(**kwargs)
        logits=result.logits[0,1:-1]
        require(bool(self.torch.isfinite(logits).all().item()),"NONFINITE_LOGITS")
        prob=logits.float().softmax(dim=-1)
        grouped=self.torch.stack([prob[:,ids].sum(dim=-1) for ids in self.groups.values()],dim=-1)
        best_p,best_group=grouped.max(dim=-1)
        self.rows.append(list(zip(logits.argmax(dim=-1).tolist(),prob[:,self.oi].tolist(),
                                  best_p.tolist(),best_group.tolist(),grouped.sum(dim=-1).tolist())))
        return result

def unique(rows):
    return sorted({key(e):e for e in rows}.values(),key=key)

def signals(text,offsets,windows,owners,rows,oi,types,group_names,protected):
    """Select evidence using owner logits, compare discarded overlapping logits.
    Only investigate token offsets not already fully protected by raw+typed union.
    """
    n=len(offsets)
    require(len(windows)==len(owners)==len(rows),"WINDOW_COUNT_MISMATCH")
    selected=[None]*n
    alternatives=defaultdict(list)
    for (a,b),(ka,kb),rr in zip(windows,owners,rows):
        require(len(rr)==b-a,"WINDOW_ROW_MISMATCH")
        for i,row in enumerate(rr,a):
            if ka<=i<kb:
                require(selected[i] is None,"DUPLICATE_OWNER")
                selected[i]=row
            else:
                alternatives[i].append(row)
    require(all(x is not None for x in selected),"MISSING_OWNER")
    found=defaultdict(list)
    stats=Counter(tokens=n,overlap_tokens=len(alternatives),windows=len(windows))
    for i,(offset,row) in enumerate(zip(offsets,selected)):
        a,b=map(int,offset)
        require(0<=a<=b<=len(text),"INVALID_TOKEN_OFFSET")
        if a==b or not text[a:b].strip() or all(protected[a:b]):
            continue
        winner,po,pg,gi,pa=row
        if winner!=oi:
            if types[winner] is None:
                stats["unmapped_non_o_uncovered_tokens"]+=1
            continue
        stats["uncovered_o_tokens"]+=1
        label=group_names[gi]
        e={"label":label,"start":a,"end":b}
        for t in THRESHOLDS:
            if pa>=t:
                found[f"o_pii_{int(t*100):02d}"].append(e)
        if po-pg<=0.15:
            found["o_margin_15"].append(e)
        for alt in alternatives.get(i,[]):
            other,_,other_p,other_group,_=alt
            lab=types[other]
            if lab is None:
                continue
            ev={"label":lab,"start":a,"end":b}
            found["window_any"].append(ev)
            # Group confidence for the winning label's type, not unrelated group.
            if group_names[other_group]==lab and other_p>=0.5:
                found["window_p50"].append(ev)
    return {mode:unique(rr) for mode,rr in found.items()},stats

def capture(det,text):
    tok=CaptureTokenizer(det.tokenizer)
    model=CaptureModel(det.model,det.torch)
    with det.torch.inference_mode():
        raw,calls=F.token_window_predict(text,tok,model,det.torch,F.decode,"cpu",510,128,
                                         special_pair=det.special_pair)
    enc=tok.enc
    offsets=enc["offset_mapping"] if enc is not None else []
    require(len(model.rows)==calls,"CAPTURE_CALL_COUNT_MISMATCH")
    windows=F.window_bounds(len(offsets),510,128)
    owners=F.winning_ranges(windows,len(offsets))
    return raw,calls,offsets,windows,owners,model

def self_test():
    # O vs grouped PII, overlap disagreement, protected-offset filter.
    text="가나다"
    offsets=[(0,1),(1,2),(2,3)]
    rr=[[(0,.95,.04,0,.05),(0,.51,.45,0,.49)],
        [(1,.10,.80,0,.90),(0,.60,.35,0,.40)]]
    found,s=signals(text,offsets,[(0,2),(1,3)],[(0,2),(2,3)],rr,
                    0,[None,"person"],["person"],bytearray([1,0,0]))
    assert len(found["o_pii_25"])==2
    assert len(found["o_pii_40"])==2
    assert len(found["o_margin_15"])==1
    assert found["window_any"]==[{"label":"person","start":1,"end":2}]
    assert found["window_p50"]==found["window_any"]
    assert s["overlap_tokens"]==1
    print("UNCERTAINTY_SELF_TEST_PASS",flush=True)

def run(name,docs,logs,det):
    counts={m:Counter() for m in MODES}
    raw_count,typed_count,stats=Counter(),Counter(),Counter()
    current_review=0
    elapsed=0.0
    capture_times=[]
    for idx,(doc,entry) in enumerate(zip(docs,logs),1):
        t=time.perf_counter()
        text,gold=doc["text"],doc["entities"]
        raw,calls,offsets,windows,owners,model=capture(det,text)
        require(sorted(map(key,raw))==sorted(map(key,entry["spans"])),
                "RAW_CACHE_DOCUMENT_PARITY_MISMATCH_"+name)
        base,info=apply_policy(text,raw)
        protected=cover(info["mask_intervals"],len(text))
        found,s=signals(text,offsets,windows,owners,model.rows,model.oi,model.types,
                        model.group_names,protected)
        stats.update(s)
        stats["docs_with_overlap"]+=bool(s["overlap_tokens"])
        capture_times.append((time.perf_counter()-t)*1000)
        score(raw_count,raw,gold,len(text))
        score(typed_count,base,gold,len(text))
        current_review+=bool(info["review"])
        audit_review=review_scenarios(text,base,info)["audit_and_resolved_postal"]
        proposals=generate(text,base)
        _,_,repeat=choose(text,base,info,proposals,("repeat",),False)
        _,_,format_repeat=choose(text,base,info,proposals,("format","repeat"),False)
        o25=found.get("o_pii_25",[])
        found["o25_plus_window"]=unique([*o25,*found.get("window_any",[])])
        found["o25_plus_repeat"]=unique([*o25,*repeat])
        found["o25_plus_format_repeat"]=unique([*o25,*format_repeat])
        for mode,c in counts.items():
            pending=found.get(mode,[])
            measure(c,text,gold,base,base,info,[],pending,[*audit_review,*pending])
        elapsed+=time.perf_counter()-t
        if idx==1 or idx%20==0 or idx==len(docs):
            print("PROGRESS",name,f"{idx}/{len(docs)}",f"elapsed_s={elapsed:.1f}",flush=True)
    def sig(m):
        return tuple(m[k] for k in ("tp","fp","fn","fully_uncovered","leak_chars"))
    er,et,ev=EXPECTED[name]
    require(sig(metrics(raw_count))==er and sig(metrics(typed_count))==et and current_review==ev,
            "CORPUS_PARITY_MISMATCH_"+name)
    print("PARITY_PASS",name,flush=True)
    scenarios={}
    for mode,c in counts.items():
        m=metrics(c)
        # Offset-only potential protection; REVIEW is not an actual mask.
        m["proposal_protection_recall_proxy"]=c["proposal_protected_chars"]/c["gold_chars"] if c["gold_chars"] else 1.0
        scenarios[mode]=m
        fields=("review_docs","covered_docs_reviewed","residual_leaky_docs_reviewed",
                "residual_leaky_docs_unreviewed","complete_miss_pending_overlap",
                "complete_miss_pending_full","complete_miss_remaining_unreviewed",
                "proposal_protection_recall_proxy","strict_f1","mask_recall")
        print("RESULT",name,mode,json.dumps({k:m.get(k,0) for k in fields},sort_keys=True),flush=True)
    report={"scenarios":scenarios,"token_stats":dict(stats),
            "mean_capture_plus_policy_ms":sum(capture_times)/len(capture_times)}
    print("TOKEN_STATS",name,json.dumps(report["token_stats"],sort_keys=True),flush=True)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-root",type=Path,default=Path.home()/"AegisRAG-unified-v1")
    p.add_argument("--checkpoint",type=Path)
    p.add_argument("--out",type=Path)
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    self_test()
    if a.self_test:
        return
    require(a.checkpoint is not None and a.out is not None,"MISSING_ARGUMENTS")
    out=a.out.expanduser().resolve()
    require(not out.exists(),"OUTPUT_EXISTS")
    require(not any((d/".git").exists() for d in [out,*out.parents]),"OUTPUT_MUST_BE_OUTSIDE_GIT")
    ck=a.checkpoint.expanduser().resolve()
    require(digest(ck/"model.safetensors")==ONEPASS_SHA,"MODEL_HASH_MISMATCH")
    cache=a.old_root.expanduser()/"step2_privacy_rag/local_eval/v7_final_candidate_eval"
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
    def deny(*args,**kwargs):
        raise RuntimeError("NETWORK_FORBIDDEN")
    socket.socket.connect=deny
    socket.socket.connect_ex=deny
    socket.create_connection=deny
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(2)
    det=Onepass(ck)
    print("MODEL_READY_FOR_SHADOW_ABLATION",flush=True)
    results={name:run(name,docs,logs,det) for name,(docs,logs) in datasets.items()}
    report={"note":"Exploratory thresholds on exposed regression sets, no auto-add or release approval; no calibrated probabilities. No Gold or model changed.",
            "model_sha256":ONEPASS_SHA,"source_sha256":F.SOURCE_SHA256,"thresholds":THRESHOLDS,
            "datasets":results}
    out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write("\n")
    print("UNCERTAINTY_ABLATION_COMPLETE",out,flush=True)

if __name__=="__main__":
    main()
