"""Inspect frozen Step1 review/audit events on A187 and B171 only.
No runtime changes, Gold edits, network, DB or new training.
Raw context stays in PRIVATE files outside Git; score cache has offsets only.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import socket
from collections import Counter
from pathlib import Path

from starter.v7_validated import capture, owner_rows, finalize, cover, key
from starter.v7_onepass import Onepass, apply_policy
from tools.benchmark_step1 import COMMON, DATA, ONEPASS_SHA, digest, load_data, require, score, metrics
from tools.benchmark_v7_validated import self_test
from tools.audit_v7_residuals import EXPECTED

TARGETS={"A187":(10,30,7,8,{"name_suffix:review_only":3,"o_pii_seed25_adjacent10":7}),
         "B171":(44,8,44,121,{"o_pii_seed25_adjacent10":82})}

def write_json(path,data):
    with path.open("x",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)

def shape(s):
    import unicodedata
    return "".join("H" if "\uac00"<=c<="\ud7a3" else
                   "D" if c.isdecimal() else
                   "L" if c.isalpha() else
                   " " if c.isspace() else
                   "P" if unicodedata.category(c).startswith("P") else "X" for c in s)

def csv_safe(s):
    return "'"+s if s.lstrip().startswith(("=","+","-","@")) else s

def run(name,docs,logs,det,out):
    typed_count=Counter(); raw_count=Counter(); counts=Counter(); reasons=Counter()
    summary=Counter(); records=[]; events=[]
    for idx,(doc,entry) in enumerate(zip(docs,logs),1):
        text=doc["text"]; gold=doc["entities"]
        raw,calls,offsets,windows,owners,model=capture(det,text)
        require(sorted(map(key,raw))==sorted(map(key,entry["spans"])),"RAW_PARITY_"+name)
        typed,info=apply_policy(text,raw)
        tokens=[]
        for (a,b),r in zip(offsets,owner_rows(offsets,windows,owners,model)):
            winner,po,pg,gi,pa=r
            tokens.append(dict(start=int(a),end=int(b),winner=str(model.tags[winner]),
                               p_o=po,p_pii=pa,p_best_type=pg,best_type=model.group_names[gi]))
        result,meta=finalize(text,typed,info,tokens)
        require(result==typed and meta["deployment_approved"] is False,"CONTRACT_CHANGED")
        score(raw_count,raw,gold,len(text));score(typed_count,typed,gold,len(text))
        counts["review_docs"]+=bool(meta["review_events"])
        counts["audit_docs"]+=bool(meta["audit_events"])
        counts["uncertainty_docs"]+=bool(meta["uncertainty_candidates"])
        gm=cover(gold,len(text));pm=cover(meta["core_mask_intervals"],len(text))
        cm=cover(meta["uncertainty_candidates"],len(text))
        counts["outside_gold_chars"]+=sum(x and not y and not z for x,y,z in zip(cm,gm,pm))
        records.append(dict(doc_index=idx,length=len(text),gold=[dict(label=g["label"],start=g["start"],end=g["end"]) for g in gold],
                            typed=typed,core_mask_intervals=meta["core_mask_intervals"],
                            tokens=tokens,review_events=meta["review_events"],audit_events=meta["audit_events"],
                            uncertainty_candidates=meta["uncertainty_candidates"]))
        for kind,rows in (("REVIEW",meta["review_events"]),("AUDIT",meta["audit_events"])):
            for ev in rows:
                source=ev["source"]
                if kind=="REVIEW":reasons[source]+=1
                a,b=ev.get("start"),ev.get("end")
                require(type(a) is int and type(b) is int and 0<=a<b<=len(text),"INVALID_EVENT_OFFSET")
                ts=[t for t in tokens if t["start"]<b and a<t["end"]]
                near=min((max(0,g["start"]-b,a-g["end"]) for g in typed),default=-1)
                label=ev.get("label","")
                if not label and source.startswith("postal"):label="address"
                val=text[a:b]
                row=dict(dataset=name,doc_index=idx,event_kind=kind,source=source,
                         action=ev.get("action",""),label=label,start=a,end=b,
                         shape=shape(val),chars=b-a,gold_overlap_chars=sum(gm[a:b]),
                         already_protected_chars=sum(pm[a:b]),nearest_typed_gap=near,
                         max_p_pii=round(max((t["p_pii"] for t in ts),default=0),6),
                         winning_tags=";".join(sorted({t["winner"] for t in ts})),
                         candidate=csv_safe(val),
                         context=csv_safe(text[max(0,a-65):a]+"⟦"+val+"⟧"+text[b:min(len(text),b+65)]))
                events.append(row)
                summary[(kind,source,label)]+=1
        if idx==1 or idx%50==0 or idx==len(docs):
            print("PROGRESS",name,f"{idx}/{len(docs)}",flush=True)
    def sig(m):return tuple(m[k] for k in ("tp","fp","fn","fully_uncovered","leak_chars"))
    er,et,_=EXPECTED[name]
    require(sig(metrics(raw_count))==er and sig(metrics(typed_count))==et,"SCORE_PARITY_"+name)
    r,a,u,c,rr=TARGETS[name]
    require(tuple(counts[k] for k in ("review_docs","audit_docs","uncertainty_docs","outside_gold_chars"))==(r,a,u,c)
            and dict(reasons)==rr,"REVIEW_PARITY_"+name)
    fields=list(events[0])
    for suffix,columns in (("PRIVATE_context",fields),("offsets_no_text",[k for k in fields if k not in ("candidate","context")])):
        with (out/f"{name}_{suffix}.csv").open("x",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=columns,extrasaction="ignore")
            w.writeheader();w.writerows(events)
    write_json(out/f"{name}_PRIVATE_offset_scores.json",records)
    report=dict(counts=counts,review_reasons=reasons,
                event_types=[dict(kind=k[0],source=k[1],label=k[2],count=v) for k,v in sorted(summary.items())])
    print("REVIEW_AUDIT_PARITY_PASS",name,json.dumps(report,ensure_ascii=False),flush=True)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--old-root",type=Path,default=Path.home()/"AegisRAG-unified-v1")
    a=p.parse_args()
    self_test()
    os.umask(0o077)
    out=a.out.expanduser().resolve()
    require(not out.exists() and not any((d/".git").exists() for d in [out,*out.parents]),"OUTPUT_PATH_INVALID")
    root=a.old_root.expanduser()
    ck=root/"step2_privacy_rag/local_eval/models/ax_v7_final_onepass/best_model"
    cache=root/"step2_privacy_rag/local_eval/v7_final_candidate_eval"
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
    det=Onepass(ck)
    out.mkdir(parents=True,mode=0o700)
    report={name:run(name,docs,logs,det,out) for name,(docs,logs) in datasets.items()}
    write_json(out/"aggregate.json",report)
    print("REVIEW_INSPECTION_COMPLETE",out,flush=True)

if __name__=="__main__":main()
