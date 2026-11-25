"""Private local inspection of every protected span in A187 and Q88.
Contains original evaluation data. NEVER commit or upload the generated HTML.
Uses cached detector offsets and reconstructs local replacements with a fresh key;
does not replay model inference, prove egress safety, or change runtime policy.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from html import escape
import hashlib
import json
import os
from pathlib import Path
import secrets
from starter.s2_pseudonymizer import READY,V7_POLICY,prepare_from_detection,pseudonymize_local
from starter.s2_numeric_recovery import plans
from starter.s2_query_controller import document_region_keys
from starter.v7_model_store import read_manifest
from tools.benchmark_step1 import load_data,require
from tools.benchmark_s2_prepare import queries,mask,inspect_output
from tools.audit_s2_query_gold import parse_gold
from tools.ablate_s2_policy import release_plan,PROJECT_SCOPE

def h(s):return escape(str(s),quote=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.expanduser().read_text())
def overlap(a,b,e):return a<e["end"] and e["start"]<b
def rebuild(text,typed,masks,key):
    return pseudonymize_local(text,dict(decision=READY,entities=typed,
        entities_for_pseudonymization=typed,mask_intervals=masks,redaction_intervals=[]),
        hmac_key=key,include_vault_records=True)

def section(ident,text,gold,result,record,status,note="",release=()):
    gm=mask(gold,len(text));jobs=result.replacement_intervals if result else ()
    am=mask(jobs,len(text));extra=sum(y and not x for x,y in zip(gm,am))
    miss=sum(x and not y for x,y in zip(gm,am)) if result else 0
    flags=(" / Gold 외부 보호 "+str(extra)+"자" if extra else "")+(" / Gold 미보호 "+str(miss)+"자" if miss else "")
    if not result:flags+=" / 출력 없음"
    out=[f'<details{" open" if extra or miss else ""}><summary>{h(ident)} · {h(status)} · HMAC {result.hmac_entity_count if result else 0} · 잔여 {result.residual_redaction_count if result else 0}{h(flags)}</summary>']
    if note:out.append(f"<p>Gold note: {h(note)}</p>")
    out.append("<p>원문 (로컬 전용)</p><pre>")
    cursor=0
    for j in jobs:
        a,b=j["start"],j["end"];out.append(h(text[cursor:a]))
        outside=not all(gm[a:b]);cl="extra" if outside else ("residual" if j["kind"]=="residual" else "pii")
        out.append(f'<mark class="{cl}" title="{h(j.get("label",j["kind"]))}">{h(text[a:b])}</mark>')
        cursor=b
    out.append(h(text[cursor:])+"</pre>")
    if result:
        out.append("<p>가명처리 재구성 (새 임시 키, 운영 토큰 아님)</p><pre>"+h(result.sanitized_text)+"</pre>")
        out.append("<table><thead><tr><th>원문 값</th><th>처리 유형</th><th>위치</th><th>Gold 외부 문자</th><th>탐지 근거 / 완화 후보</th></tr></thead><tbody>")
        for j in jobs:
            a,b=j["start"],j["end"]
            n=sum(not x for x in gm[a:b])
            events=sorted({str(e.get("source","unknown")) for e in record.get("audit_events",[])+record.get("review_events",[]) if type(e.get("start")) is int and type(e.get("end")) is int and overlap(a,b,e)})
            desc=", ".join(events)
            if (a,b) in release:desc+=" | Project scope 완화 후보"
            out.append(f'<tr class="{"warn" if n else ""}"><td>{h(text[a:b])}</td><td>{h(j.get("label",j["kind"]))} ({h(j["kind"])})</td><td>{a}:{b}</td><td>{n}</td><td>{h(desc)}</td></tr>')
        out.append("</tbody></table>")
    else:
        out.append("<p>REVIEW/BLOCKED: 가명처리 출력 없음. 아래는 탐지 후보이지 실제 전송 결과가 아님.</p>")
    out.append("<details><summary>Gold / 최초 탐지 / REVIEW / Audit 근거</summary>")
    for title,items in (("Gold",gold),("최초 확정 탐지",record["entities"]),("REVIEW",record.get("review_events",[])),("Audit",record.get("audit_events",[]))):
        out.append(f"<p>{h(title)}</p><ul>")
        for e in items:
            a,b=e.get("start"),e.get("end")
            if type(a) is int and type(b) is int and 0<=a<b<=len(text):
                out.append(f"<li>{h(text[a:b])} · {h(e.get('label',''))} · {a}:{b} · {h(e.get('source',''))}</li>")
        out.append("</ul>")
    out.append("</details></details>")
    return extra,miss,"".join(out)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("document-cache","query-cache","controller-report","out"):p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args()
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    dp=root/"holdout_labeled.jsonl";qp=root/"holdout_retrieval_queries.jsonl";gp=root/"holdout_retrieval_queries_gold.jsonl"
    out=a.out.expanduser().resolve()
    require(not out.exists() and not any((x/".git").exists() for x in (out,*out.parents)),"OUTPUT_INVALID")
    require(out.is_relative_to((Path.home()/"aegis_private_audit").resolve()),"PRIVATE_DIRECTORY_REQUIRED")
    docs=load_data("A187",dp);qs=queries(qp);gold=[json.loads(s) for s in gp.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    gm={g["qid"]:g for g in gold};dc=read(a.document_cache);qc=read(a.query_cache);final=read(a.controller_report)
    m=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    for cache,field,path in ((dc,"document_sha256",dp),(qc,"query_sha256",qp)):
        require(cache["model_revision"]==m["revision"]==final["model_revision"] and cache["policy"]==V7_POLICY and cache[field]==sha(path),"CACHE_MISMATCH")
    require(qc["query_gold"]["sha256"]==sha(gp),"GOLD_CACHE_MISMATCH")
    dm={str(r["doc_id"]):r for r in dc["private_document_offsets"]};qm={r["qid"]:r for r in qc["private_query_offsets"]}
    require(len(dm)==len(dc["private_document_offsets"])==len(docs)==187 and set(dm)=={str(d["id"]) for d in docs},"DOCUMENT_IDS")
    require(len(qm)==len(qc["private_query_offsets"])==len(gm)==len(gold)==len(qs)==88 and set(qm)==set(gm)=={q["qid"] for q in qs},"QUERY_IDS")
    counts=final["counts"];changes={x["qid"]:x for x in final["details"]}
    require(counts["outputs_original_mask_preserved"]==88 and counts["normal_output_identical"]==counts["SEARCH_READY"] and counts["SEARCH_READY"]+len(changes)==88 and final["external_transfer_approved"] is False,"FINAL_PARITY_MISMATCH")
    key=secrets.token_bytes(32);seen={};index=set();regions=set();ds=[];qr=[];c=Counter()
    for d in docs:
        did=str(d["id"]);text=d["text"];r=dm[did];typed=r["entities"]
        require(r["length"]==len(text),"DOC_LENGTH")
        meta={**r,"policy":V7_POLICY,"source":"v7_context_validated","deployment_approved":False,
              "status":"REVIEW_REQUIRED" if r["review_events"] else READY,"review":r["review_events"]}
        prep=prepare_from_detection(text,typed,meta,hmac_key=key,include_vault_records=True)
        require(prep.decision==r["decision"],"DOC_PARITY")
        release=[]
        if prep.output:
            inspect_output(text,meta,prep.output,key,seen)
            index.update(e["token"] for e in prep.output.safe_token_manifest)
            regions.update(document_region_keys(text,prep.output.safe_token_manifest,key))
            _,release,_=release_plan(text,prep.output,meta,PROJECT_SCOPE)
            c["document_ready"]+=1;c["document_residual_intervals"]+=prep.output.residual_redaction_count
        extra,miss,html=section(did,text,d["entities"],prep.output,r,prep.decision,release=release)
        c["document_extra_chars"]+=extra;c["document_missing_ready_chars"]+=miss
        ds.append((extra or miss or not prep.output,html))
    for q in qs:
        qid=q["qid"];text=q["text"];r=qm[qid];g=gm[qid]
        require(r["length"]==len(text) and g.get("query",text)==text,"QUERY_LENGTH_OR_TEXT")
        gs,err=parse_gold(text,g.get("query_entities"));require(err is None,"GOLD_PARSE")
        typed=r["entities"];change=changes.get(qid)
        if change:
            require(change["status"]=="RECOVERED_LOCAL","RECOVERY_STATUS")
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
        result=rebuild(text,typed,r["mask_intervals"],key)
        inspect_output(text,r,result,key,seen)
        extra,miss,html=section(qid,text,gs,result,r,change["status"]+" / "+change["reason"] if change else "SEARCH_READY",note=g.get("note",""))
        c["query_extra_chars"]+=extra;c["query_missing_chars"]+=miss
        c["query_extra_rows"]+=bool(extra);qr.append((extra or miss,html))
    require(c["document_ready"]==184 and c["document_residual_intervals"]==31 and c["document_extra_chars"]==91 and c["document_missing_ready_chars"]==0,"DOC_SUMMARY_PARITY")
    require(c["query_extra_chars"]==26 and c["query_extra_rows"]==5 and c["query_missing_chars"]==0,"QUERY_SUMMARY_PARITY")
    header='''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>Private protection audit</title><style>
    body{font:15px/1.65 -apple-system,BlinkMacSystemFont,sans-serif;max-width:1150px;margin:28px auto;padding:0 18px;color:#172033;background:#f8fafc}
    details{background:white;border:1px solid #dbe2ea;border-radius:8px;margin:8px 0;padding:12px}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:12px;border-radius:6px;font:inherit}mark{color:inherit}.pii{background:#d1e7ff}.residual{background:#fef3c7}.extra{background:#fecaca}table{width:100%;border-collapse:collapse}th,td{padding:7px;text-align:left;border-bottom:1px solid #e2e8f0;overflow-wrap:anywhere}tr.warn{background:#fff1f2}h1,h2{line-height:1.2}</style></head><body><h1>개인정보 보호 구간 로컬 감사</h1><p><strong>민감한 원문 포함. GitHub, 공유 드라이브, 외부 API, 채팅에 업로드하지 마세요.</strong></p><p>파란색: Gold 내 HMAC / 노란색: Gold 내 잔여 보호 / 빨간색: Gold 외부 보호 포함. Gold 외부라는 사실만으로 안전한 보호 해제를 확정하지 않습니다.</p><p>캐시와 기존 최종 보고서에 기반한 재구성입니다. 새 임시 HMAC 키를 사용하며, 복구 질의는 기록된 복구 사유에 따라 재구성합니다. 실제 운영 출력이나 새 모델 실행이 아닙니다.</p>'''
    body=header+"<h2>질의 88개 — 차이 우선</h2>"+"".join(x[1] for x in sorted(qr,key=lambda x:not x[0]))+"<h2>문서 187개 — 차이 및 REVIEW 우선</h2>"+"".join(x[1] for x in sorted(ds,key=lambda x:not x[0]))+"</body></html>"
    os.umask(0o077);out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:f.write(body)
    print("PRIVATE_PROTECTION_REPORT",json.dumps(dict(c),ensure_ascii=False),flush=True)
    print("PRIVATE_PROTECTION_REPORT_PATH",out,flush=True)
if __name__=="__main__":main()
