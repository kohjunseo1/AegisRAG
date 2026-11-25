"""Offline residual-protection and normalization ablation, using frozen offset caches.
No model, network, DB, HCX or runtime policy change. Gold only scores outputs.
Never log raw text, normalized values, token values or restored strings.
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

from starter.s2_pseudonymizer import (
    READY,V7_POLICY,canonicalize,pseudonymize_local,prepare_from_detection,
    PseudonymizationError,
)
from starter.v7_model_store import read_manifest
from tools.benchmark_step1 import load_data,require
from tools.benchmark_s2_prepare import queries,inspect_output,mask

# Exact normalization logic from team's feature/integration pii_pipeline.py
# blob 12087e71bd87749c2be9fae57c1649d8a29dc7f3 at retrieval time.
# Intentionally isolate normalization; do NOT reproduce team's 32-bit HMAC.
COMPOUND = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")
JOSA_TAIL = ("께서", "에게", "이랑", "님", "씨", "은", "는", "이", "가", "을", "를", "의", "랑", "도", "만", "과", "와")
SIDO_SHORT = [("서울특별시", "서울"), ("부산광역시", "부산"), ("대구광역시", "대구"), ("인천광역시", "인천"),
              ("광주광역시", "광주"), ("대전광역시", "대전"), ("울산광역시", "울산"), ("세종특별자치시", "세종"),
              ("경기도", "경기"), ("강원특별자치도", "강원"), ("강원도", "강원"), ("충청북도", "충북"), ("충청남도", "충남"),
              ("전북특별자치도", "전북"), ("전라북도", "전북"), ("전라남도", "전남"), ("경상북도", "경북"),
              ("경상남도", "경남"), ("제주특별자치도", "제주")]


def _digits(v):
    d = re.sub(r"\D", "", v)
    if re.match(r"\s*\+?82\D", v) and d.startswith("82"):   # +82 10-1234-5678 → 01012345678
        d = "0" + d[2:].lstrip("0")
    return d


def team_normalize(label, value):
    v = value.strip()
    if label == "person":
        v = re.sub(r"\s+", "", v)
        base = 4 if v[:2] in COMPOUND else 3
        for j in JOSA_TAIL:   # 탐지기가 조사까지 잡은 경우만 (이름 길이 보호)
            if v.endswith(j) and len(v) - len(j) >= base:
                return v[:-len(j)]
        return v
    if label in ("phone_number", "resident_registration_number", "account_number", "card_number"):
        return _digits(v)
    if label == "email":
        return v.lower()
    if label == "address":
        for full, short in SIDO_SHORT:
            if v.startswith(full):
                v = short + v[len(full):]
                break
        return re.sub(r"[\s,]", "", v)
    return re.sub(r"\s+", "", v)



SEMANTIC={"event_status_nominal","bereavement_kinship_role",
          "candidate_bereavement_body","candidate_bereavement_header"}
PROJECT_SCOPE=SEMANTIC|{"standalone_surname_title","candidate_surname_title",
                        "explicit_internal_extension"}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def spans_from_mask(bits):
    out=[];i=0
    while i<len(bits):
        if not bits[i]:i+=1;continue
        a=i
        while i<len(bits) and bits[i]:i+=1
        out.append(dict(start=a,end=i))
    return out

def release_plan(text,base,meta,allowed):
    """Experiment only: a whole residual interval must have eligible audit
    coverage; never release typed, pending, or overlapping disallowed evidence."""
    bits=mask(meta["mask_intervals"],len(text))
    released=[];reasons=Counter()
    for job in base.replacement_intervals:
        if job["kind"]!="residual":continue
        a,b=job["start"],job["end"]
        events=[e for e in meta["audit_events"] if type(e.get("start")) is int and
                type(e.get("end")) is int and a<e["end"] and e["start"]<b]
        if not events or any(e.get("source") not in allowed for e in events):continue
        if any(a<e["end"] and e["start"]<b for e in meta["entities"]+meta["review_events"]):continue
        supported=set()
        for e in events:
            supported.update(range(max(a,e["start"]),min(b,e["end"])))
        if len(supported)!=b-a:continue
        bits[a:b]=b"\0"*(b-a)
        released.append((a,b))
        for reason in {e["source"] for e in events}:reasons[reason]+=1
    return spans_from_mask(bits),released,reasons

def normalization_key(profile,label,value):
    try:
        norm=canonicalize(label,value) if profile=="ours" else team_normalize(label,value)
    except (ValueError,TypeError):return None
    return (label,norm) if norm else None

def synthetic():
    # Hypothetical values only. Equivalence expectations are explicit and
    # deliberately include conservative non-equivalence cases.
    cases=[
        ("person","가상민","가 상민",True,"name_spacing"),
        ("person","가상민이","가상민",False,"name_suffix_ambiguous"),
        ("person","가상민님","가상민",True,"name_honorific_boundary"),
        ("phone_number","010-0000-0000","+82 10 0000 0000",True,"phone_country"),
        ("phone_number","010.0000.0000","01000000000",True,"phone_separator"),
        ("account_number","82-12345678","012345678",False,"account_country_confusion"),
        ("account_number","1234-****-5678","12345678",False,"account_masked"),
        ("card_number","1234-****-5678","12345678",False,"card_masked"),
        ("resident_registration_number","000000-0000000","0000000000000",True,"rrn_separator"),
        ("email","User@EXAMPLE.invalid","User@example.invalid",True,"email_domain"),
        ("email","User@example.invalid","user@example.invalid",False,"email_local_case"),
        ("address","서울시 가상구 가상로 12-3","서울특별시 가상구 가상로12-3",True,"address_spacing"),
        ("address","서울시 가상구 가상로 1 23층","서울시 가상구 가상로 12 3층",False,"address_number_boundary"),
        ("address","경기 가상시 가상로 10 101동1203호","경기도 가상시 가상로 10 101동 1203호",True,"address_unit"),
        ("address","서울시 가상구 가상로 10","서울시 가상구 가상로 11",False,"address_distinct"),
    ]
    rows=[]
    for lab,a,b,expected,kind in cases:
        row=dict(case=kind,label=lab,expected_equal=expected)
        for profile in ("ours","team"):
            x=normalization_key(profile,lab,a);y=normalization_key(profile,lab,b)
            equal=x is not None and y is not None and x==y
            row[profile]=dict(equal=equal,pass_case=equal==expected)
        rows.append(row)
    return rows

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--document-cache",type=Path,required=True)
    p.add_argument("--query-cache",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    dp=root/"holdout_labeled.jsonl";qp=root/"holdout_retrieval_queries.jsonl"
    gp=root/"holdout_retrieval_queries_gold.jsonl"
    out=a.out.expanduser().resolve()
    require(not out.exists() and not any((d/".git").exists() for d in (out,*out.parents)),"OUTPUT_INVALID")
    docs=load_data("A187",dp);qs=queries(qp)
    gold=[json.loads(s) for s in gp.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    refs={g["qid"]:set(g["relevant_doc_ids"]) for g in gold}
    dc=json.loads(a.document_cache.expanduser().read_text())
    qc=json.loads(a.query_cache.expanduser().read_text())
    m=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    for cache,field,path in [(dc,"document_sha256",dp),(qc,"query_sha256",qp)]:
        require(cache["model_revision"]==m["revision"] and cache["policy"]==V7_POLICY and cache[field]==sha(path),"CACHE_MISMATCH")
    drows=dc["private_document_offsets"];qrows=qc["private_query_offsets"]
    dm={str(r["doc_id"]):r for r in drows};qm={r["qid"]:r for r in qrows}
    require(len(dm)==len(drows)==len(docs)==187 and set(dm)=={str(d["id"]) for d in docs},"DOC_CACHE_IDS")
    require(len(qm)==len(qrows)==len(refs)==len(gold)==len(qs)==88 and set(qm)==set(refs)=={q["qid"] for q in qs},"QUERY_CACHE_IDS")
    require(all(v<=set(dm) for v in refs.values()),"GOLD_DOC_IDS")
    key=secrets.token_bytes(32);seen={};ready=set()
    stats={k:Counter() for k in ("strict","semantic_only","project_scope")}
    reason_counts=defaultdict(Counter);index={p:defaultdict(set) for p in ("ours","team")}
    for d in docs:
        text=d["text"];did=str(d["id"]);r=dm[did]
        require(r["length"]==len(text),"DOC_LENGTH_MISMATCH")
        typed=r["entities"]
        meta={**r,"policy":V7_POLICY,"source":"v7_context_validated","deployment_approved":False,
              "status":"REVIEW_REQUIRED" if r["review_events"] else READY,"review":r["review_events"]}
        prep=prepare_from_detection(text,typed,meta,hmac_key=key,include_vault_records=True)
        require(prep.decision==r["decision"],"DOC_PREPARATION_PARITY")
        if prep.decision!=READY:continue
        ready.add(did);base=prep.output
        actual=inspect_output(text,meta,base,key,seen)
        gold_mask=mask(d["entities"],len(text))
        for profile in index:
            for e in typed:
                k=normalization_key(profile,e["label"],text[e["start"]:e["end"]])
                if k:index[profile][k].add(did)
        for policy,allowed in (("strict",set()),("semantic_only",SEMANTIC),("project_scope",PROJECT_SCOPE)):
            intervals,released,reasons=release_plan(text,base,meta,allowed)
            gate=dict(decision=READY,entities=typed,entities_for_pseudonymization=typed,mask_intervals=intervals,redaction_intervals=[])
            result=pseudonymize_local(text,gate,hmac_key=key,include_vault_records=True)
            changed=inspect_output(text,{**meta,"mask_intervals":intervals},result,key,seen)
            require(all(not x or y for x,y in zip(changed,actual)),"UNEXPECTED_PROTECTION_EXPANSION")
            require(result.safe_token_manifest==base.safe_token_manifest,"TYPED_TOKEN_CHANGED")
            c=stats[policy];c["ready_docs"]+=1;c["changed_docs"]+=bool(released)
            c["released_intervals"]+=len(released);c["released_chars"]+=sum(b-a for a,b in released)
            c["remaining_residual_intervals"]+=result.residual_redaction_count
            c["remaining_residual_chars"]+=result.residual_redacted_chars
            c["unprotected_gold_chars"]+=sum(x and not y for x,y in zip(gold_mask,changed))
            c["protected_outside_gold_chars"]+=sum(y and not x for x,y in zip(gold_mask,changed))
            reason_counts[policy].update(reasons)
    require(len(ready)==184 and stats["strict"]["remaining_residual_intervals"]==31 and
            stats["strict"]["remaining_residual_chars"]==91,"BASELINE_PARITY")
    require(stats["strict"]["unprotected_gold_chars"]==0,"BASELINE_GOLD_RESIDUE")
    protection={k:{**dict(v),"release_reasons":dict(reason_counts[k])} for k,v in stats.items()}
    print("PROTECTION_ABLATION",json.dumps(protection,ensure_ascii=False),flush=True)
    matches={p:defaultdict(Counter) for p in index};summary={p:Counter() for p in index};delta=[]
    for q in qs:
        qid=q["qid"];text=q["text"];r=qm[qid];g=refs[qid]&ready
        require(r["length"]==len(text),"QUERY_LENGTH_MISMATCH")
        all_hits={}
        for profile in index:
            hits=set()
            for e in r["entities"]:
                aa,bb=e["start"],e["end"];lab=e["label"]
                require(0<=aa<bb<=len(text),"QUERY_OFFSET_INVALID")
                k=normalization_key(profile,lab,text[aa:bb]);found=index[profile].get(k,set())
                c=matches[profile][lab];c["spans"]+=1;c["normalizable"]+=bool(k)
                c["any_match"]+=bool(found);c["known_gold_match"]+=bool(found&g)
                hits.update(found)
            all_hits[profile]=hits
            summary[profile]["queries_with_any_match"]+=bool(hits)
            summary[profile]["queries_with_known_gold_match"]+=bool(hits&g)
        x,y=all_hits["ours"],all_hits["team"]
        if x!=y:
            delta.append(dict(qid=qid,ours_hits=len(x),team_hits=len(y),
                ours_gold=len(x&g),team_gold=len(y&g),
                team_added_outside_known_gold=len(y-x-refs[qid]),ours_added_outside_known_gold=len(x-y-refs[qid])))
    norm=dict(summary={p:dict(v) for p,v in summary.items()},by_label={p:{l:dict(c) for l,c in v.items()} for p,v in matches.items()},differences=delta)
    print("NORMALIZATION_ABLATION",json.dumps(norm,ensure_ascii=False),flush=True)
    cases=synthetic()
    print("NORMALIZATION_UNIT_CASES",json.dumps(cases,ensure_ascii=False),flush=True)
    report=dict(protection=protection,normalization=norm,synthetic=cases,model_revision=m["revision"],
                document_sha256=sha(dp),query_sha256=sha(qp),query_gold_sha256=sha(gp),
                external_transfer_approved=False,scope="offline experimental policy; no production release")
    os.umask(0o077);out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("S2_POLICY_ABLATION_COMPLETE",out,flush=True)

if __name__=="__main__":main()
