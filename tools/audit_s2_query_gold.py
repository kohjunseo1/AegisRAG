"""Audit Q88 Gold against cached offsets and verified final mask parity.

No model, network, DB or raw values in output. Final typed spans for recovered
queries are NOT reconstructed from Gold; only unchanged queries are scored.
Gold entity schema is validated at runtime; unsupported/ambiguous rows are
reported as unevaluable, never silently treated as negative labels.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from starter.s2_pseudonymizer import LABELS,V7_POLICY
from starter.v7_model_store import read_manifest
from tools.benchmark_s2_prepare import queries,mask
from tools.benchmark_step1 import require

ALIASES={"name":"person","phone":"phone_number","rrn":"resident_registration_number",
         "account":"account_number","card":"card_number","resident_number":"resident_registration_number"}
def parse_gold(text,items):
    if not isinstance(items,list):return None,"unsupported_entity_container"
    spans=[]
    for item in items:
        if isinstance(item,str):
            # Value-only strings can support protection evaluation, not type scoring.
            value=item;label=None;a=b=None
        elif isinstance(item,dict):
            label=item.get("label",item.get("type",item.get("entity_type")))
            if isinstance(label,str):
                label=label.lower();label=ALIASES.get(label,label)
            if label not in LABELS:label=None
            value=next((item[k] for k in ("text","value","original","surface","entity") if isinstance(item.get(k),str)),None)
            a,b=item.get("start",item.get("start_offset")),item.get("end",item.get("end_offset"))
        else:return None,"unsupported_entity_item"
        if a is not None or b is not None:
            if type(a) is not int or type(b) is not int or not 0<=a<b<=len(text):return None,"invalid_gold_offsets"
            if value is not None and value!=text[a:b]:return None,"gold_value_offset_mismatch"
        else:
            if not value:return None,"missing_gold_value_or_offsets"
            hits=[m.start() for m in re.finditer("(?="+re.escape(value)+")",text)]
            if len(hits)!=1:return None,"ambiguous_or_missing_gold_value"
            a=hits[0];b=a+len(value)
        spans.append(dict(start=a,end=b,label=label))
    return spans,None

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query-cache",required=True,type=Path)
    p.add_argument("--controller-report",required=True,type=Path)
    a=p.parse_args()
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    qp=root/"holdout_retrieval_queries.jsonl";gp=root/"holdout_retrieval_queries_gold.jsonl"
    qs=queries(qp);gold=[json.loads(s) for s in gp.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    gm={g["qid"]:g for g in gold}
    cache=json.loads(a.query_cache.expanduser().read_text())
    final=json.loads(a.controller_report.expanduser().read_text())
    manifest=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    require(cache["policy"]==V7_POLICY and cache["model_revision"]==manifest["revision"]==final["model_revision"],"MODEL_POLICY_MISMATCH")
    require(cache["query_sha256"]==hashlib.sha256(qp.read_bytes()).hexdigest(),"QUERY_CACHE_HASH_MISMATCH")
    require(cache["query_gold"]["sha256"]==hashlib.sha256(gp.read_bytes()).hexdigest(),"GOLD_CACHE_HASH_MISMATCH")
    cm={r["qid"]:r for r in cache["private_query_offsets"]}
    require(len(cm)==len(cache["private_query_offsets"])==len(gm)==len(gold)==len(qs)==88 and set(cm)==set(gm)=={q["qid"] for q in qs},"QUERY_ID_MISMATCH")
    counts=final["counts"];changed={r["qid"]:r for r in final["details"]}
    require(counts["queries"]==counts["outputs_original_mask_preserved"]==88 and counts["SEARCH_READY"]==counts["normal_output_identical"] and counts["RECOVERED_LOCAL"]==len(changed) and counts["SEARCH_READY"]+len(changed)==88 and all(r["status"]=="RECOVERED_LOCAL" for r in changed.values()),"FINAL_PARITY_NOT_ESTABLISHED")
    require(final["external_transfer_approved"] is False,"UNEXPECTED_EGRESS_FLAG")
    # The preceding benchmark verifies fresh Q88 detector mask parity against
    # this cache and final actual replacement parity. Its report does not
    # include hashes or final per-query offsets, so this is a linked audit,
    # not an independently authenticated replay of the final output.
    c=Counter();by=Counter()
    for q in qs:
        qid=q["qid"];text=q["text"];g=gm[qid];r=cm[qid]
        require(r["length"]==len(text),"QUERY_LENGTH_MISMATCH")
        if "query" in g and g["query"]!=text:
            print("QUERY_GOLD_UNEVALUABLE",json.dumps(dict(qid=qid,reason="gold_query_text_mismatch")),flush=True)
            c["unevaluable"]+=1;continue
        spans,reason=parse_gold(text,g.get("query_entities"))
        if reason:
            print("QUERY_GOLD_UNEVALUABLE",json.dumps(dict(qid=qid,reason=reason)),flush=True)
            c["unevaluable"]+=1;continue
        require(all(type(e["start"]) is int and type(e["end"]) is int and 0<=e["start"]<e["end"]<=len(text) for e in r["mask_intervals"]+r["entities"]),"CACHE_OFFSET_INVALID")
        expected=mask(spans,len(text));actual=mask(r["mask_intervals"],len(text))
        miss=sum(x and not y for x,y in zip(expected,actual))
        extra=sum(y and not x for x,y in zip(expected,actual))
        c["evaluable"]+=1;c["gold_entities"]+=len(spans);c["gold_chars"]+=sum(expected)
        c["unprotected_gold_chars"]+=miss;c["protected_outside_gold_chars"]+=extra
        c["queries_with_unprotected_gold"]+=bool(miss);c["queries_with_extra_protection"]+=bool(extra)
        c["mask_exact_queries"]+=not (miss or extra)
        c["gold_negative_queries"]+=not spans
        c["gold_negative_with_protection"]+=not spans and bool(extra)
        typed_known=all(e["label"] in LABELS for e in spans)
        tp=fp=fn=None
        if typed_known and qid not in changed:
            gold_set={(e["start"],e["end"],e["label"]) for e in spans}
            pred_set={(e["start"],e["end"],e["label"]) for e in r["entities"]}
            tp=len(gold_set&pred_set);fp=len(pred_set-gold_set);fn=len(gold_set-pred_set)
            c["typed_evaluable_unchanged_queries"]+=1;c["typed_tp_unchanged"]+=tp
            c["typed_fp_unchanged"]+=fp;c["typed_fn_unchanged"]+=fn
        elif qid in changed:c["recovered_final_typed_not_recorded"]+=1
        else:c["typed_unevaluable"]+=1
        if miss or extra or fp or fn or qid in changed:
            print("QUERY_GOLD_DIFFERENCE",json.dumps(dict(qid=qid,gold_entities=len(spans),gold_chars=sum(expected),
                  unprotected_gold_chars=miss,protected_outside_gold_chars=extra,
                  gold_labels=dict(Counter(e["label"] or "unknown" for e in spans)),
                  final_typed_scored=tp is not None,typed_tp=tp,typed_fp=fp,typed_fn=fn,
                  recovered=qid in changed),ensure_ascii=False),flush=True)
    c["queries"]=88
    print("QUERY_GOLD_AUDIT_SUMMARY",json.dumps(dict(c),ensure_ascii=False),flush=True)
    print("QUERY_GOLD_AUDIT_SCOPE", "linked cache + final mask parity; recovered final typed offsets unavailable; Gold annotation completeness not guaranteed",flush=True)
if __name__=="__main__":main()
