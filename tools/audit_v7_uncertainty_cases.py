"""Hard403-only case-level uncertainty audit. Evaluation only, no new training.
Outputs offset/score metadata outside Git; never outputs text or PII values.
Reuses the exact EXP4/5 predictor and verifies per-document raw-cache parity.
"""
from __future__ import annotations
import argparse
import json
import os
import socket
from pathlib import Path
from collections import Counter

from starter import v7_frozen as F
from starter.v7_onepass import Onepass, apply_policy
from tools.ablate_v7_uncertainty import capture, signals, self_test
from tools.audit_v7_residuals import cover, key, review_scenarios
from tools.benchmark_step1 import COMMON, DATA, ONEPASS_SHA, digest, load_data, require

MODES=("o_pii_10","o_pii_25","o_pii_40","o_pii_60","o_margin_15")
EXPECTED={"o_pii_10":(130,28,24,20),"o_pii_25":(89,22,16,13),
          "o_pii_40":(50,15,7,5),"o_pii_60":(20,1,0,0),
          "o_margin_15":(44,14,7,5)}

def owner_rows(offsets,windows,owners,model):
    selected=[None]*len(offsets)
    for (a,b),(ka,kb),rows in zip(windows,owners,model.rows):
        require(len(rows)==b-a,"BAD_CAPTURE_LENGTH")
        for i in range(ka,kb):
            require(selected[i] is None,"DUP_OWNER")
            selected[i]=rows[i-a]
    require(all(r is not None for r in selected),"MISSING_OWNER")
    return selected

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-root",type=Path,default=Path.home()/"AegisRAG-unified-v1")
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    self_test()
    out=a.out.expanduser().resolve()
    require(not out.exists(),"OUTPUT_EXISTS")
    require(not any((x/".git").exists() for x in [out,*out.parents]),"OUTSIDE_GIT_REQUIRED")
    root=a.old_root.expanduser()
    ck=root/"step2_privacy_rag/local_eval/models/ax_v7_final_onepass/best_model"
    cache=root/"step2_privacy_rag/local_eval/v7_final_candidate_eval"
    require(digest(ck/"model.safetensors")==ONEPASS_SHA,"MODEL_HASH_MISMATCH")
    manifest=json.loads((cache/"manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("model_sha256")==ONEPASS_SHA and manifest.get("pilot")==0
            and manifest.get("mode")=="token510_only_no_post","CACHE_MANIFEST_MISMATCH")
    hashes={x["role"]:x["sha256"] for x in manifest["data"]}
    require(hashes.get("Hard403")==DATA["Hard403"][2],"GOLD_HASH_MISMATCH")
    docs=load_data("Hard403",Path.home()/"aegis_external_eval/team4/synth_hard_eval.jsonl")
    logs=json.loads((cache/"PRIVATE_Hard403_token510_candidate_offset_predictions.json").read_text(encoding="utf-8"))
    require(len(docs)==len(logs),"CACHE_COUNT_MISMATCH")
    os.environ.update(COMMON)
    def deny(*args,**kwargs): raise RuntimeError("NETWORK_FORBIDDEN")
    socket.socket.connect=deny
    socket.socket.connect_ex=deny
    socket.create_connection=deny
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(2)
    det=Onepass(ck)
    stats={m:Counter() for m in MODES}
    cases=[]; records=[]
    for idx,(doc,entry) in enumerate(zip(docs,logs),1):
        text=doc["text"]; gold=doc["entities"]
        raw,_,offsets,windows,owners,model=capture(det,text)
        require(sorted(map(key,raw))==sorted(map(key,entry["spans"])),"RAW_CACHE_PARITY_MISMATCH")
        typed,info=apply_policy(text,raw)
        bm=cover(typed,len(text))
        pm=cover(info["mask_intervals"],len(text))
        gm=cover(gold,len(text))
        found,_=signals(text,offsets,windows,owners,model.rows,model.oi,model.types,model.group_names,pm)
        baseline=review_scenarios(text,typed,info)["audit_and_resolved_postal"]
        misses=[g for g in gold if not any(bm[g["start"]:g["end"]])]
        leaky=any(not all(bm[g["start"]:g["end"]]) for g in gold)
        masks={}
        for mode in MODES:
            candidates=found.get(mode,[])
            m=cover(candidates,len(text));masks[mode]=m
            c=stats[mode]
            c["review_docs"]+=bool(baseline or candidates)
            c["leaky_docs_reviewed"]+=leaky and bool(baseline or candidates)
            c["miss_overlap"]+=sum(any(m[g["start"]:g["end"]]) for g in misses)
            c["miss_full"]+=sum(all(m[g["start"]:g["end"]]) for g in misses)
            c["candidate_chars_outside_gold"]+=sum(x and not y and not z for x,y,z in zip(m,gm,pm))
            c["new_gold_chars_candidate_covered"]+=sum(x and y and not z for x,y,z in zip(m,gm,pm))
        selected=owner_rows(offsets,windows,owners,model)
        token_rows=[]
        for (aa,bb),r in zip(offsets,selected):
            s,e=int(aa),int(bb)
            winner,po,pg,gi,pa=r
            token_rows.append({"start":s,"end":e,"winner":str(model.tags[winner]),
                               "p_o":po,"p_pii":pa,"p_best_type":pg,
                               "best_type":model.group_names[gi]})
        records.append({"doc_index":idx,"length":len(text),"gold":[dict(zip(("label","start","end"),key(g))) for g in gold],
                        "typed":typed,"mask_intervals":info["mask_intervals"],
                        "baseline_review":bool(baseline),"tokens":token_rows,
                        "mode_candidates":{m:found.get(m,[]) for m in MODES}})
        for g in misses:
            lab,s,e=key(g)
            ts=[t for t in token_rows if t["start"]<e and s<t["end"]]
            cases.append({"doc_index":idx,"label":lab,"start":s,"end":e,
                          "chars":e-s,"max_p_pii":round(max((t["p_pii"] for t in ts),default=0),6),
                          "winning_tags":sorted({t["winner"] for t in ts}),
                          "coverage":{mode:sum(m[s:e]) for mode,m in masks.items()}})
        if idx==1 or idx%50==0 or idx==len(docs):
            print("PROGRESS Hard403",f"{idx}/{len(docs)}",flush=True)
    require(len(cases)==33,"COMPLETE_MISS_COUNT_MISMATCH")
    for mode,ex in EXPECTED.items():
        c=stats[mode]
        require(tuple(c[k] for k in ("review_docs","leaky_docs_reviewed","miss_overlap","miss_full"))==ex,
                "EXP4_RESULT_PARITY_MISMATCH_"+mode)
    out.mkdir(parents=True,mode=0o700)
    for filename,data in (("PRIVATE_offset_scores.json",records),("case_summary_no_text.json",cases),
                          ("aggregate.json",dict(stats))):
        fd=os.open(out/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)
    print("CASE_AUDIT_PARITY_PASS",flush=True)
    print("CASE_SUMMARY",json.dumps(cases,ensure_ascii=False,separators=(",",":")),flush=True)
    print("CANDIDATE_COST",json.dumps(stats,ensure_ascii=False),flush=True)
    print("CASE_AUDIT_COMPLETE",out,flush=True)

if __name__=="__main__":
    main()
