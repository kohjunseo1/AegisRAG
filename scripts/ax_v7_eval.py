"""Local A.X V7 vs frozen V6 evaluation on identical docs and V6 policy.

Prints aggregate strict/coverage/resource statistics only. No PII text,
Gold values, document IDs, model weights, API calls, or database writes.
The same AX token decoder and 350-char line windows are used as the frozen
Step1 backend; typed spans and actual protected mask intervals are distinct.

Example:
 PYTHONPATH=step2_privacy_rag/app python scripts/ax_v7_eval.py \
   --checkpoint step2_privacy_rag/local_eval/ax_v7_run1/best_model \
   --data "$HOME/aegis_external_eval/team4/synth_hard_eval.jsonl" --compare-v6
"""
from __future__ import annotations
import argparse
import hashlib
import inspect
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from ax_v7_train import ALIASES, read_labels
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"step2_privacy_rag"/"app"))


def chunks(text, max_chars=350):
    result=[];origin=0;buf=""
    for line in text.splitlines(keepends=True):
        if buf and len(buf)+len(line)>max_chars:
            result.append((origin,buf));origin+=len(buf);buf=""
        if len(line)>max_chars:
            if buf:
                result.append((origin,buf));origin+=len(buf);buf=""
            for i in range(0,len(line),max_chars):
                result.append((origin+i,line[i:i+max_chars]))
            origin+=len(line)
        else:
            buf+=line
    if buf:
        result.append((origin,buf))
    return result


def model_label(tag):
    text=str(tag).strip().upper().replace(" ","_").replace("-","_")
    base=re.sub(r"^(B|I|E|S)_","",text)
    matches=[name for name,aliases in ALIASES.items() if base in aliases]
    if len(matches)>1:
        raise ValueError("AMBIGUOUS_LABEL_MAPPING")
    return matches[0] if matches else None


def decode(text, offsets, tags, origin):
    # Frozen A.X Step1 decode_bio semantics: same span union and B/I/E/S.
    spans=[];cur=None
    for (s,e),tag in zip(offsets,tags):
        if int(s)>=int(e): continue
        tag=str(tag)
        if tag in ("O",""):
            if cur:spans.append(cur);cur=None
            continue
        m=re.match(r"^([BIES])[-_](.+)$",tag)
        if not m:
            if cur:spans.append(cur);cur=None
            continue
        part,lab=m.groups()
        label=model_label(tag)
        if label is None:
            if cur:spans.append(cur);cur=None
            continue
        a,b=origin+int(s),origin+int(e)
        if part=="S":
            if cur:spans.append(cur);cur=None
            spans.append({"label":label,"start":a,"end":b})
        elif part=="B" or not cur or cur["label"]!=label or a>cur["end"]+1:
            if cur:spans.append(cur)
            cur={"label":label,"start":a,"end":b}
            if part=="E":spans.append(cur);cur=None
        else:
            cur["end"]=b
            if part=="E":spans.append(cur);cur=None
    if cur:spans.append(cur)
    return [x for x in spans if 0<=x["start"]<x["end"]<=len(text)]


def predictor(checkpoint, device):
    import torch
    from transformers import AutoModelForTokenClassification,AutoTokenizer
    model=AutoModelForTokenClassification.from_pretrained(str(checkpoint),local_files_only=True)
    tok=AutoTokenizer.from_pretrained(str(checkpoint),use_fast=True,local_files_only=True)
    if model.config.num_labels!=37 or not tok.is_fast:
        raise ValueError("INVALID_V7_MODEL_OR_TOKENIZER")
    model.eval().to(device)
    allowed=set(inspect.signature(model.forward).parameters)
    def predict(text):
        all_spans=[]
        with torch.inference_mode():
            for origin,piece in chunks(text):
                enc=tok(piece,return_offsets_mapping=True,return_tensors="pt",truncation=True,max_length=512)
                offsets=enc.pop("offset_mapping")[0].tolist()
                batch={k:v.to(device) for k,v in enc.items() if
                       k in ("input_ids","attention_mask") or (k=="token_type_ids" and k in allowed)}
                logits=model(**batch).logits[0]
                ids=logits.argmax(dim=-1).tolist()
                tags=[model.config.id2label[int(i)] for i in ids]
                all_spans.extend(decode(text,offsets,tags,origin))
        unique={(x["label"],x["start"],x["end"]):x for x in all_spans}
        return sorted(unique.values(),key=lambda x:(x["start"],x["end"],x["label"]))
    return predict


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint",type=Path,required=True)
    p.add_argument("--data",type=Path,required=True)
    p.add_argument("--compare-v6",action="store_true")
    p.add_argument("--limit",type=int,default=None)
    p.add_argument("--device",choices=("cpu","cuda"),default="cpu")
    args=p.parse_args()
    if args.limit is not None and args.limit<=0:
        p.error("--limit must be positive")
    checkpoint=args.checkpoint.expanduser().resolve()
    read_labels(checkpoint)
    from unified_v1.external_validation import load_external
    from unified_v1.merge_validation import score,fmt,p95,v6_detailed,rows_to_spans
    from unified_v1.ax_veil_ablation import no_veil_counterfactual
    from unified_v1.lean import Worker
    from contextlib import nullcontext

    input_path=args.data.expanduser().resolve()
    rows=load_external(input_path)
    print("V7_EVAL_INPUT",json.dumps({
        "sha256":hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "total_docs":len(rows),"selected":len(rows[:args.limit] if args.limit else rows),
        "checkpoint":str(checkpoint),"device":args.device,
        "training_not_triggered":True,"HF_upload_not_triggered":True}),flush=True)
    rows=rows[:args.limit] if args.limit else rows
    predict=predictor(checkpoint,args.device)
    stats={"AX_V7_SINGLE":defaultdict(Counter)}
    if args.compare_v6:
        stats["V6_FROZEN"]=defaultdict(Counter)
    times=defaultdict(list)

    with (Worker("B0") if args.compare_v6 else nullcontext()) as worker:
        for i,doc in enumerate(rows,1):
            text=doc["text"]
            started=time.perf_counter()
            raw=predict(text)
            # Only ADDRESS route differs from V6; inject locally into the
            # frozen policy's expected AX and ADDRESS source slots.
            prepared=no_veil_counterfactual(text,{"by_source":{"ax":raw,"veil":[]}})
            times["AX_V7_MODEL_AND_POLICY_MS"].append((time.perf_counter()-started)*1000)
            score(stats["AX_V7_SINGLE"],prepared["entities"],doc["entities"],
                  prepared["mask_intervals"],len(text))
            if worker is not None:
                started=time.perf_counter()
                old=worker.call(text)
                previous=v6_detailed(text,old)
                times["V6_FROZEN_WORKER_MS"].append((time.perf_counter()-started)*1000)
                score(stats["V6_FROZEN"],previous["entities"],doc["entities"],
                      previous["mask_intervals"],len(text))
            if i%25==0 or i==len(rows):
                print(f"V7_EVAL_PROGRESS {i}/{len(rows)}",flush=True)

    print("MODE                 LABEL                    TP FP FN F1")
    for mode,labels in stats.items():
        for label in ("ALL","person","address","account_number","card_number",
                      "phone_number","email","resident_registration_number"):
            print(f"{mode:<22} {label:<25} {fmt(labels[label])}")
        c=labels["ALL"]
        print("MASK_SAFETY",mode,json.dumps({
            "mask_character_recall":round(c["covered_chars"]/c["gold_chars"],6) if c["gold_chars"] else 1.0,
            "fully_uncovered_gold":c["fully_uncovered_gold"],
            "partially_uncovered_gold":c["partly_uncovered_gold"],
            "detector_only":True}))
    for key,val in times.items():
        print("CPU_TIMING",key,json.dumps({
            "mean_ms":round(statistics.mean(val),2),
            "p50_ms":round(statistics.median(val),2),
            "p95_ms":p95(val)},sort_keys=True))
    print("V7_EVAL_COMPLETE no_pii_export=true gate_hmac_rag_not_measured=true",flush=True)


if __name__=="__main__":
    main()
