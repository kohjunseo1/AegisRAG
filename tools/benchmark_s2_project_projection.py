"""Cached local policy comparison; Gold is used only AFTER projection.
No model, DB, API, raw values, or external transfer. Q88 recovery replay uses
the previous verified report; query cache does not contain all audit events.
"""
from collections import Counter,defaultdict
import argparse,json,secrets
from pathlib import Path
from starter.s2_project_projection import project
from starter.s2_pseudonymizer import READY,V7_POLICY,prepare_from_detection
from starter.s2_query_controller import document_region_keys
from starter.s2_numeric_recovery import plans
from starter.v7_model_store import read_manifest
from tools.benchmark_step1 import load_data,require
from tools.benchmark_s2_prepare import queries,mask,inspect_output
from tools.audit_s2_query_gold import parse_gold
from tools.inspect_s2_protection_private import rebuild,sha,read,overlap

def assess(name,text,gs,old,new,c):
    g=mask(gs,len(text));a=mask(old.replacement_intervals,len(text));b=mask(new.search.replacement_intervals,len(text))
    missing=sum(x and not y for x,y in zip(g,b));extra=sum(y and not x for x,y in zip(g,b))
    c["rows"]+=1;c["gold_chars"]+=sum(g)
    c["strict_extra_chars"]+=sum(y and not x for x,y in zip(g,a))
    c["project_extra_chars"]+=extra;c["project_unprotected_gold_chars"]+=missing
    c["project_exact_mask_rows"]+=not (missing or extra)
    c["released_intervals"]+=len(new.released);c["released_chars"]+=sum(x["end"]-x["start"] for x in new.released)
    c["changed_rows"]+=bool(new.released)
    require(new.external_transfer_approved is False and new.strict is old,"PROJECTION_CONTRACT")
    if missing or extra:
        print("REMAINING_PROTECTION",json.dumps(dict(id=name,extra_chars=extra,missing_chars=missing,
             extra_labels=[e.get("label",e["kind"]) for e in new.search.replacement_intervals if not all(g[e["start"]:e["end"]])]),ensure_ascii=False),flush=True)
    if new.released:
        print("PROJECT_RELEASE",json.dumps(dict(id=name,reasons=dict(Counter(x["reason"] for x in new.released)),released_chars=sum(x["end"]-x["start"] for x in new.released),remaining_extra=extra,missing=missing),ensure_ascii=False),flush=True)
    if all(e.get("label") for e in gs):
        truth={(e["start"],e["end"],e["label"]) for e in gs}
        pred={(e["start"],e["end"],e["label"]) for e in new.search.safe_token_manifest}
        c["typed_tp"]+=len(truth&pred);c["typed_fp"]+=len(pred-truth);c["typed_fn"]+=len(truth-pred)
        if truth!=pred:
            print("REMAINING_TYPED",json.dumps(dict(id=name,fp_labels=dict(Counter(x[2] for x in pred-truth)),fn_labels=dict(Counter(x[2] for x in truth-pred))),ensure_ascii=False),flush=True)
    return b

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ("document-cache","query-cache","controller-report"):p.add_argument("--"+n,type=Path,required=True)
    a=p.parse_args()
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    dp=root/"holdout_labeled.jsonl";qp=root/"holdout_retrieval_queries.jsonl";gp=root/"holdout_retrieval_queries_gold.jsonl"
    docs=load_data("A187",dp);qs=queries(qp)
    gold=[json.loads(s) for s in gp.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    gm={g["qid"]:g for g in gold};dc=read(a.document_cache);qc=read(a.query_cache);final=read(a.controller_report)
    m=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    for cache,field,path in ((dc,"document_sha256",dp),(qc,"query_sha256",qp)):
        require(cache["model_revision"]==m["revision"]==final["model_revision"] and cache["policy"]==V7_POLICY and cache[field]==sha(path),"CACHE_MISMATCH")
    require(qc["query_gold"]["sha256"]==sha(gp),"GOLD_CACHE_MISMATCH")
    dm={str(r["doc_id"]):r for r in dc["private_document_offsets"]};qm={r["qid"]:r for r in qc["private_query_offsets"]}
    require(len(dm)==len(dc["private_document_offsets"])==len(docs)==187 and set(dm)=={str(d["id"]) for d in docs},"DOCUMENT_IDS")
    require(len(qm)==len(qc["private_query_offsets"])==len(gm)==len(gold)==len(qs)==88 and set(qm)==set(gm)=={q["qid"] for q in qs},"QUERY_IDS")
    changes={x["qid"]:x for x in final["details"]};counts=final["counts"]
    require(counts["outputs_original_mask_preserved"]==88 and counts["normal_output_identical"]==counts["SEARCH_READY"] and counts["SEARCH_READY"]+len(changes)==88 and final["external_transfer_approved"] is False,"FINAL_PARITY")
    key=secrets.token_bytes(32);seen={};index=defaultdict(set);regions=defaultdict(set);ready=set();dstat=Counter();qstat=Counter()
    for d in docs:
        text=d["text"];did=str(d["id"]);r=dm[did];typed=r["entities"]
        require(r["length"]==len(text),"DOC_LENGTH")
        meta={**r,"policy":V7_POLICY,"source":"v7_context_validated","deployment_approved":False,"status":"REVIEW_REQUIRED" if r["review_events"] else READY,"review":r["review_events"]}
        prep=prepare_from_detection(text,typed,meta,hmac_key=key,include_vault_records=True)
        require(prep.decision==r["decision"],"DOC_PARITY")
        if not prep.output:
            dstat["deferred"]+=1
            print("DOCUMENT_DEFERRED",json.dumps(dict(id=did,reason=prep.reason,review_sources=dict(Counter(e.get("source") for e in r["review_events"]))),ensure_ascii=False),flush=True);continue
        old=prep.output;inspect_output(text,meta,old,key,seen)
        new=project(text,old,meta,key=key)
        require(new.search.safe_token_manifest==old.safe_token_manifest,"DOCUMENT_TOKEN_CHANGED")
        assess(did,text,d["entities"],old,new,dstat)
        inspect_output(text,{"mask_intervals":list(new.search.replacement_intervals)},new.search,key,seen)
        ready.add(did)
        for e in new.search.safe_token_manifest:index[e["token"]].add(did)
        for k in document_region_keys(text,new.search.safe_token_manifest,key):regions[k].add(did)
    for q in qs:
        qid=q["qid"];text=q["text"];r=qm[qid];g=gm[qid];typed=r["entities"]
        require(r["length"]==len(text) and g.get("query",text)==text,"QUERY_LENGTH_OR_TEXT")
        gs,err=parse_gold(text,g.get("query_entities"));require(err is None,"GOLD_PARSE")
        change=changes.get(qid)
        if change:
            if change["reason"]=="numeric_candidate":
                ps=plans(text,typed,r["review_events"],key=key,lookup=index.__contains__)
                chosen=[x for x in ps if x.status=="CANDIDATE"]
                require(bool(chosen) and not any(x.status=="CLARIFY" for x in ps),"NUMERIC_REPLAY")
                typed=[e for e in typed if not any(overlap(x.start,x.end,e) for x in chosen)]
                typed=sorted(typed+[dict(start=x.start,end=x.end,label=x.label) for x in chosen],key=lambda e:e["start"])
            elif change["reason"]=="region_candidate":
                require(not typed and bool(r["review_events"]),"REGION_REPLAY")
            else:raise RuntimeError("UNKNOWN_RECOVERY")
        else:require(r["decision"]==READY,"QUERY_BASELINE")
        old=rebuild(text,typed,r["mask_intervals"],key);inspect_output(text,r,old,key,seen)
        new=project(text,old,r,key=key,query=True,region_lookup=regions.__contains__)
        assess(qid,text,gs,old,new,qstat)
        inspect_output(text,{"mask_intervals":list(new.search.replacement_intervals)},new.search,key,seen)
        hits=set();prior=set()
        for e in old.safe_token_manifest:prior.update(index.get(e["token"],set()))
        for e in new.search.safe_token_manifest:hits.update(index.get(e["token"],set()))
        for k in new.region_keys:hits.update(regions[k])
        refs=set(g["relevant_doc_ids"])&ready
        qstat["known_gold_candidate_match"]+=bool(hits&refs)
        qstat["lost_existing_gold_candidate"]+=bool(prior&refs) and not bool(hits&refs)
        qstat["region_query_rows"]+=bool(new.region_keys)
    require(dstat["rows"]==184 and dstat["strict_extra_chars"]==91 and qstat["rows"]==88 and qstat["strict_extra_chars"]==26,"BASELINE_PARITY")
    print("PROJECT_SCOPE_SUMMARY",json.dumps(dict(documents=dict(dstat),queries=dict(qstat),scope="local-only projection, cached reconstruction; no external approval"),ensure_ascii=False),flush=True)
    if dstat["project_unprotected_gold_chars"] or qstat["project_unprotected_gold_chars"] or qstat["lost_existing_gold_candidate"]:raise SystemExit("PROJECT_SCOPE_REGRESSION")
if __name__=="__main__":main()
