"""S2-02 diagnostics: local only, no runtime changes or Gold-assisted retrieval.
A187 detected once; reuse pinned Q88 offset cache; synthetic context probes.
Report counts and span shapes only; no raw text, PII, HMAC tokens or keys.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket

from starter.s2_pseudonymizer import (
    READY, V7_POLICY, canonicalize, make_hmac_token, prepare_from_detection,
    PseudonymizationError,
)
from starter.v7_model_store import read_manifest, provision, DEFAULT_LOCAL
from tools.benchmark_step1 import COMMON, load_data, require
from tools.benchmark_s2_prepare import queries, inspect_output

NUM = ("phone_number","resident_registration_number","card_number","account_number")
# Diagnostic full numeric runs, not production detection or automatic merging.
RUN = re.compile(r"(?<![0-9A-Za-z*])\+?[0-9]+(?:[ .-]+[0-9]+)*(?![0-9A-Za-z*])")
CUES = {"phone_number":r"전화|연락처|휴대폰|휴대전화",
        "resident_registration_number":r"주민등록|주민번호",
        "card_number":r"카드번호|카드 번호",
        "account_number":r"계좌|통장|입금번호"}
def overlap(x,y):
    return x["start"] < y["end"] and y["start"] < x["end"]

def shape(s):
    return "".join("D" if c.isdecimal() else "H" if "가"<=c<="힣" else
                   "A" if c.isalpha() else c if c in " +-.*()@_" else "?" for c in s)

def token(key,label,value):
    try:return make_hmac_token(key,label,value)[0]
    except PseudonymizationError:return None

def numeric_alternatives(key,value):
    # Candidate lookup only. No label inference or release from a match.
    if not re.fullmatch(r"\+?[0-9]+(?:[ .-]+[0-9]+)*",value):return set()
    n=sum(c.isdecimal() for c in value)
    if not 7<=n<=24:return set()
    out=set()
    for lab in NUM:
        if lab=="resident_registration_number" and n!=13:continue
        if lab=="card_number" and not 13<=n<=19:continue
        t=token(key,lab,value)
        if t:out.add(t)
    return out

def read_jsonl(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]

def local_model(m):
    os.environ.update(COMMON)
    from huggingface_hub import snapshot_download
    def fetch(manifest):
        return snapshot_download(repo_id=manifest["repo_id"],revision=manifest["revision"],
            allow_patterns=sorted(manifest["sha256"]),local_files_only=True,token=False)
    path,src=provision(m,DEFAULT_LOCAL,fetch=fetch)
    print("MODEL_SOURCE",src,flush=True)
    def deny(*args,**kwargs):raise RuntimeError("NETWORK_FORBIDDEN")
    socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
    import torch
    from starter.v7_context_validated import ContextValidatedOnepass
    torch.set_num_threads(2);torch.set_num_interop_threads(2)
    return ContextValidatedOnepass(path)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query-cache",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    a=p.parse_args()
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    out=a.out.expanduser().resolve()
    require(not out.exists() and not any((d/".git").exists() for d in (out,*out.parents)),"OUTPUT_INVALID")
    docs=load_data("A187",root/"holdout_labeled.jsonl")
    qpath=root/"holdout_retrieval_queries.jsonl"
    qs=queries(qpath)
    gold=read_jsonl(root/"holdout_retrieval_queries_gold.jsonl")
    refs={g["qid"]:set(g["relevant_doc_ids"]) for g in gold}
    require(len(gold)==len(refs)==88 and set(refs)=={q["qid"] for q in qs},"GOLD_ID_MISMATCH")
    doc_ids={str(d["id"]) for d in docs}
    require(all(v<=doc_ids for v in refs.values()),"GOLD_DOC_MISMATCH")
    m=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    cache=json.loads(a.query_cache.expanduser().read_text())
    require(cache["model_revision"]==m["revision"] and cache["policy"]==V7_POLICY
            and cache["query_sha256"]==hashlib.sha256(qpath.read_bytes()).hexdigest(),"QUERY_CACHE_MISMATCH")
    rows=cache["private_query_offsets"]
    qcache={r["qid"]:r for r in rows}
    require(len(rows)==len(qcache)==88 and set(qcache)==set(refs),"QUERY_CACHE_ID_MISMATCH")
    require(all(qcache[q["qid"]]["length"]==len(q["text"]) for q in qs),"QUERY_CACHE_LENGTH_MISMATCH")
    det=local_model(m);key=secrets.token_bytes(32)
    index=defaultdict(set);ready=set();seen={}
    residual=Counter();sources=Counter();probe_values=defaultdict(list)
    for i,d in enumerate(docs,1):
        text=d["text"];did=str(d["id"])
        typed,meta=det.detect(text)
        p=prepare_from_detection(text,typed,meta,hmac_key=key,include_vault_records=True)
        if p.decision==READY:
            ready.add(did)
            inspect_output(text,meta,p.output,key,seen)
            for rec in p.output.safe_token_manifest:index[rec["token"]].add(did)
            for job in p.output.replacement_intervals:
                if job["kind"]!="residual":continue
                residual["intervals"]+=1;residual["chars"]+=job["end"]-job["start"]
                residual["gold_overlap_chars"]+=sum(max(0,min(job["end"],g["end"])-max(job["start"],g["start"])) for g in d["entities"])
                ev=[e for e in meta["audit_events"] if type(e.get("start")) is int and type(e.get("end")) is int and overlap(job,e)]
                ss={e["source"] for e in ev if isinstance(e.get("source"),str)}
                for s in ss or {"no_overlapping_audit"}:sources[s]+=1
            # Gold only selects test inputs for independent context probes.
            # It never creates index entries or changes runtime detection.
            for g in d["entities"]:
                lab=g["label"];v=text[g["start"]:g["end"]]
                if lab in NUM and len(probe_values[lab])<3 and re.fullmatch(r"[0-9]+(?:[ -][0-9]+)*",v) and sum(x.isdecimal() for x in v)>=9 and v not in probe_values[lab]:
                    if any(e["label"]==lab and e["start"]==g["start"] and e["end"]==g["end"] for e in typed):
                        probe_values[lab].append(v)
        if i==1 or i%25==0 or i==187:print("DOC_PROGRESS",i,"/187",flush=True)
    require(len(ready)==184,"DOC_READY_REGRESSION")
    require(residual["intervals"]==31 and residual["chars"]==91,"RESIDUAL_REGRESSION")
    print("RESIDUAL_SUMMARY",json.dumps(dict(residual),ensure_ascii=False),flush=True)
    print("RESIDUAL_AUDIT_OVERLAP",json.dumps(dict(sources),ensure_ascii=False),flush=True)
    aggregate=Counter();detail=[];kinds=defaultdict(Counter)
    for q in qs:
        qid=q["qid"];text=q["text"];r=qcache[qid];g=refs[qid]&ready
        typed=r["entities"];review=r["review_events"]
        baseline=set();alt=set();candidate_runs=[]
        observed=[];aggregate["queries"]+=1
        for e in typed:
            a0,b=e["start"],e["end"];lab=e["label"]
            require(0<=a0<b<=len(text),"CACHE_SPAN_INVALID")
            val=text[a0:b];t=token(key,lab,val);hit=index.get(t,set())
            baseline.update(hit)
            k=kinds[lab];k["spans"]+=1;k["normalizable"]+=bool(t)
            k["any_index_match"]+=bool(hit);k["known_gold_ready_match"]+=bool(hit&g)
            observed.append(dict(label=lab,start=a0,end=b,shape=shape(val),normalizable=bool(t),
                                 index_docs=len(hit),known_gold_ready_docs=len(hit&g)))
        # Numeric candidates must overlap an existing numeric signal.
        signals=[e for e in typed+review if e.get("label") in NUM]
        for x in RUN.finditer(text):
            s=dict(start=x.start(),end=x.end())
            matched=[e for e in signals if overlap(s,e)]
            if not matched:continue
            ts=numeric_alternatives(key,x.group())
            if not ts:continue
            found=set().union(*(index.get(t,set()) for t in ts))
            alt.update(found)
            candidate_runs.append(dict(start=s["start"],end=s["end"],shape=shape(x.group()),
                overlaps=[e["label"] for e in matched],index_docs=len(found),known_gold_ready_docs=len(found&g),
                new_index_docs=len(found-baseline),
                matched_type_count=sum(bool(index.get(t)) for t in ts)))
        aggregate["queries_with_typed"]+=bool(typed)
        aggregate["queries_with_numeric_signal"]+=bool(signals)
        aggregate["queries_with_ready_gold"]+=bool(g)
        aggregate["queries_typed_any_known_gold_ready"]+=bool(baseline&g)
        aggregate["queries_alternative_any_known_gold_ready"]+=bool((baseline|alt)&g)
        aggregate["queries_new_known_gold_ready_match"]+=bool((alt-baseline)&g)
        aggregate["queries_new_known_gold_ready_match_from_zero"]+=bool((alt&g) and not (baseline&g))
        aggregate["queries_additional_outside_known_gold"]+=bool(alt-baseline-refs[qid])
        aggregate["queries_numeric_multiple_type_hits"]+=any(x["matched_type_count"]>1 for x in candidate_runs)
        if r["decision"]!=READY or candidate_runs or (typed and not baseline&g):
            detail.append(dict(qid=qid,decision=r["decision"],reason=r["reason"],
                ready_gold_docs=len(g),typed=observed,numeric_candidates=candidate_runs,
                review=[dict(label=e.get("label"),start=e.get("start"),end=e.get("end"),
                    shape=shape(text[e["start"]:e["end"]])) for e in review],
                context_cues=[lab for lab,rx in CUES.items() if re.search(rx,text)]))
    print("TOKEN_MATCH_SUMMARY",json.dumps(dict(aggregate),ensure_ascii=False),flush=True)
    print("MATCH_BY_LABEL",json.dumps({k:dict(v) for k,v in kinds.items()},ensure_ascii=False),flush=True)
    for row in detail:
        if row["qid"] in {"Q033","Q048"} or row["numeric_candidates"]:
            print("QUERY_DIAGNOSTIC",json.dumps(row,ensure_ascii=False),flush=True)
    # Same exact value, different surrounding text, same pinned detector.
    # Labels from Gold are used exclusively for evaluation.
    names={"phone_number":"전화번호","resident_registration_number":"주민등록번호","card_number":"카드번호","account_number":"계좌번호"}
    probes=defaultdict(Counter)
    for lab,values in probe_values.items():
        for value in values:
            for mode,pre,suf in [("explicit",names[lab]+" "," 관련 문서 찾아줘"),("neutral",""," 관련 문서 찾아줘"),("bare","","")]:
                text=pre+value+suf
                typed,meta=det.detect(text)
                target=dict(start=len(pre),end=len(pre)+len(value))
                hits=[e for e in typed if overlap(e,target)]
                exact=any(e["label"]==lab and e["start"]==target["start"] and e["end"]==target["end"] for e in hits)
                c=probes[lab+"/"+mode];c["n"]+=1;c["exact_label_boundary"]+=exact
                c["wrong_label_overlap"]+=any(e["label"]!=lab for e in hits)
                c["no_typed_overlap"]+=not hits
                c["boundary_mismatch"]+=bool(hits) and not any(e["start"]==target["start"] and e["end"]==target["end"] for e in hits)
                c["review"]+=bool(meta["review_events"])
    probe_summary={k:dict(v) for k,v in probes.items()}
    print("CONTEXT_PROBE_SUMMARY",json.dumps(probe_summary,ensure_ascii=False),flush=True)
    report=dict(residual=dict(residual),residual_audit_overlap=dict(sources),
                token_match=dict(aggregate),match_by_label={k:dict(v) for k,v in kinds.items()},
                query_details=detail,context_probes=probe_summary,model_revision=m["revision"],
                notes="Local diagnostic only. Known gold references are not exhaustive; token hits are not retrieval accuracy or identity confirmation.")
    os.umask(0o077);out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("S2_DIAGNOSTIC_COMPLETE",out,flush=True)

if __name__=="__main__":main()
