#!/usr/bin/env python3
"""Q-001 LOCAL-ONLY, annotation-only human review prototype.

No DB, HCX, HMAC, automatic promotion, or outbound calls. Raw source is served
ONLY to a token-authenticated browser on 127.0.0.1 and never written by this tool.
The annotation journal is local, chmod 0600, and never authorizes release.
Use a trusted local browser. Not a production or multi-user review service.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Lock
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LOCAL = (ROOT / "step2_privacy_rag/local_eval").resolve()
sys.path[:0] = [str(ROOT / "step1_pii_detector/src"),
                str(ROOT / "step2_privacy_rag/app")]
IDS = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
CHOICES = {
    "name_boundary": {"LONG_NAME", "SHORT_NAME_PLUS_PARTICLE", "UNRESOLVED"},
    "veil_candidate": {"PERSON_PII", "NON_PERSON_EXPRESSION", "UNRESOLVED"},
}


def private_path(path):
    value = Path(path).resolve()
    if not value.is_relative_to(LOCAL):
        raise ValueError("NOT_IN_LOCAL_EVAL")
    return value


def read_sources(path, ids):
    if not ids or len(ids) != len(set(ids)) or any(not IDS.fullmatch(x) for x in ids):
        raise ValueError("INVALID_IDS")
    wanted, records = set(ids), {}
    with private_path(path).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("id") in wanted:
                did, raw = row["id"], row.get("text")
                if did in records or not isinstance(raw, str):
                    raise ValueError("INVALID_SOURCE")
                records[did] = raw
    if set(records) != wanted:
        raise ValueError("MISSING_ID")
    return records


def get_cases(sources, detector):
    from name_boundary_policy import propose
    from name_protection_handoff import _FrozenDetectorView
    from privacy_gate import check_privacy_gate
    from gate_validation import check_spans, merge_masks, covered, find_safe_name_merges
    from veil_person_policy import classify_veil_only_person
    result = {}
    for did, source in sorted(sources.items()):
        step = detector.detect(source)
        status = step.get("status")
        if status not in ("REVIEW_REQUIRED", "READY_FOR_PSEUDONYMIZATION"):
            raise ValueError("UNSUPPORTED_STATUS")
        candidates = []
        for p in propose(source, step):
            a, b = p.candidate_full_span
            candidates.append(dict(kind="name_boundary", start=a, end=b,
                                   reason=p.evidence, action=p.action))
        gate_reason = "detector_not_ready"
        if status == "READY_FOR_PSEUDONYMIZATION":
            gate = check_privacy_gate(_FrozenDetectorView(detector, step), source)
            if gate.get("decision") != "REVIEW_REQUIRED":
                raise ValueError("NOT_IN_REVIEW")
            gate_reason = gate.get("reason", "UNKNOWN")
            strict = check_spans(step["entities"], len(source), True)
            masks = merge_masks(check_spans(step["mask_intervals"], len(source), False))
            veil = check_spans(detector.veil_predict(source), len(source), True)
            people = [x for x in veil if x["label"] == "person"]
            merges = find_safe_name_merges(source, strict, people, masks)
            expanded = merge_masks(masks + [x["merged"] for x in merges])
            for item in people:
                if covered(item, expanded):
                    continue
                policy = classify_veil_only_person(source, item["start"], item["end"])
                labels = sorted(set(s["label"] for s in strict
                                    if item["start"] < s["end"] and s["start"] < item["end"]))
                candidates.append(dict(kind="veil_candidate", start=item["start"],
                                       end=item["end"], reason=policy.reason,
                                       action=policy.action, overlapping_labels=labels))
        if not candidates:
            raise ValueError("NO_REVIEW_CANDIDATES")
        result[did] = dict(id=did, source=source, step1_status=status,
                           gate_reason=gate_reason, candidates=candidates,
                           entities=[{k: x[k] for k in ("start", "end", "label")}
                                     for x in step["entities"]],
                           masks=[{k: x[k] for k in ("start", "end")}
                                  for x in step["mask_intervals"]])
    return result


def review_record(payload, cases):
    if not isinstance(payload, dict) or set(payload) != {"id", "candidate", "choice"}:
        raise ValueError("INVALID_REQUEST")
    did, ix, choice = payload["id"], payload["candidate"], payload["choice"]
    if not isinstance(did, str) or did not in cases or type(ix) is not int:
        raise ValueError("UNKNOWN_ID")
    if not 0 <= ix < len(cases[did]["candidates"]):
        raise ValueError("UNKNOWN_CANDIDATE")
    candidate = cases[did]["candidates"][ix]
    if type(choice) is not str or choice not in CHOICES[candidate["kind"]]:
        raise ValueError("INVALID_CHOICE")
    return dict(
        format="q001-review-draft-v1", document_id=did, candidate_index=ix,
        kind=candidate["kind"], source_span=[candidate["start"], candidate["end"]],
        source_digest=hashlib.sha256(cases[did]["source"].encode("utf-8")).hexdigest(),
        human_classification=choice, state="NEEDS_INDEPENDENT_REVALIDATION",
        reviewed_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def persist(path, entry):
    dest = private_path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_APPEND | os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(dest, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError("UNSAFE_FILE_PERMISSIONS")
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            fd = -1
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd != -1:
            os.close(fd)


HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Q-001 · 로컬 개인정보 검토</title><style>
:root{font:15px system-ui,-apple-system,sans-serif;color:#182330;background:#f5f7f8}
*{box-sizing:border-box}body{margin:0}main{max-width:1300px;margin:auto;padding:28px 24px}
header{margin-bottom:18px}h1{font-size:26px;margin:0 0 6px}h2{font-size:18px}
.sub{color:#657388;font-size:14px;margin:3px 0}.warning{background:#fff8e8;border:1px solid #e8cf99;border-radius:8px;padding:12px 16px;color:#654f28;margin:16px 0}
.auth{display:flex;gap:10px;flex-wrap:wrap;align-items:center;background:white;border:1px solid #d8e0e7;border-radius:12px;padding:12px 16px;margin-bottom:18px}
input,select,button{font:inherit;border-radius:7px;padding:9px 12px}
input,select{border:1px solid #b4c1cd;background:white}button{border:1px solid #b4c1cd;background:#fff;cursor:pointer}
button:hover{background:#eff6f4}button:focus-visible{outline:3px solid #13846a;outline-offset:2px}
button.primary{background:#116b57;color:#fff;border-color:#116b57}
#e2e-controls{margin:16px 0;border:1px solid #bbdace;background:#fff;padding:22px 24px;border-radius:14px}
#e2e-controls[hidden],#e2e-results[hidden],#e2e-error[hidden]{display:none}
.e2e-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.e2e-head h2{margin:0}.e2e-tag{font-size:12px;font-weight:700;border-radius:100px;padding:6px 12px;background:#f0f3f8;color:#485b6e}
.e2e-tag[data-state="RUNNING"]{background:#e9f3ff;color:#225abb}
.e2e-tag[data-state="SUCCEEDED"]{background:#ddf5e9;color:#0c6951}
.e2e-tag[data-state="FAILED"]{background:#fff0ec;color:#ad4430}
.e2e-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:14px 0}
.e2e-progress{border:1px solid #dce6eb;border-radius:11px;background:#f8fbfc;padding:16px;margin-top:14px}
.e2e-progress-top{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.e2e-progress-top strong{font-size:14px}
.e2e-progress progress{appearance:none;-webkit-appearance:none;width:100%;height:12px;border:0;border-radius:20px;overflow:hidden;background:#e3ebf0}
.e2e-progress progress::-webkit-progress-bar{background:#e3ebf0;border-radius:20px}
.e2e-progress progress::-webkit-progress-value{background:#168065;border-radius:20px;transition:width .25s ease}
.e2e-progress progress::-moz-progress-bar{background:#168065;border-radius:20px}
.e2e-steps{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;padding:0;margin:12px 0 4px;list-style:none}
.e2e-steps li{font-size:12px;color:#8091a0;padding:8px 3px;text-align:center;border-top:3px solid #dce6eb}
.e2e-steps li.done{color:#0d7057;border-color:#168065;font-weight:700}
.e2e-steps li.active{color:#254b7e;border-color:#4379c4;font-weight:700}
.e2e-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:15px 0}
.metric-tile{border:1px solid #dce5e9;border-radius:11px;padding:16px;background:#fff}
.metric-tile .metric-label{font-size:13px;color:#697c8d}
.metric-tile .metric-value{font-size:27px;font-weight:800;letter-spacing:-.04em;color:#0a6f57;margin:7px 0}
.metric-tile .metric-base{font-size:12px;color:#728294}
#e2e-results h3{margin:22px 0 4px;font-size:17px}
#e2e-table{overflow-wrap:anywhere;font-size:12px;color:#758596}
#e2e-error{border:1px solid #f1c7bf;background:#fff5f2;padding:12px;border-radius:8px;color:#a23b2b}
@media(max-width:850px){.e2e-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:480px){.e2e-metrics{grid-template-columns:1fr 1fr}.metric-tile{padding:10px}.metric-tile .metric-value{font-size:22px}.e2e-steps li{font-size:10px}}
#status{min-height:24px;color:#116b57;font-weight:600}
.layout{display:grid;grid-template-columns:270px minmax(0,1fr);gap:18px;align-items:start}
.panel{background:#fff;border:1px solid #d8e0e7;border-radius:12px;padding:16px;min-width:0}
#queue{display:flex;flex-direction:column;gap:8px;max-height:66vh;overflow:auto}
.queue-item{display:flex;justify-content:space-between;align-items:center;width:100%;text-align:left;background:#f8fafb;border:1px solid #e1e7ec;border-radius:8px;padding:11px}
.queue-item.active{background:#eaf6f1;border-color:#11755e;color:#084d3e;font-weight:700}
.queue-item small{font-size:11px;color:#687b8e}#source{white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid #d8e0e7;background:#fcfdfd;border-radius:8px;padding:16px;max-height:55vh;overflow:auto;line-height:1.9;min-height:110px}
mark.suspect{background:#ffd28a;border-radius:2px}mark.masked{background:#bbebcc;border-radius:2px}
#candidates{margin-top:15px}article{border:1px solid #d8e0e7;padding:15px;margin:12px 0;border-radius:8px;background:#fdfefe}
.actionline{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}
.notice{color:#64748b;font-size:13px}#heading{overflow-wrap:anywhere}
@media(max-width:700px){main{padding:16px}.layout{grid-template-columns:1fr}#queue{max-height:210px}}
</style></head><body><main>
<header><h1>Q-001 · 로컬 개인정보 검토</h1><p class="sub">문서 목록 → 후보 확인 → 판단 기록 · REVIEW 승격/적재는 별도 검증 후</p></header>
<p class="warning">로컬 실험 전용. 문서 분류 기록은 HMAC·적재 승인이 아닙니다. 별도 「실험 적재·평가」를 실행하고 Gate 재검증을 통과해야 새 DB 테이블이 생성됩니다.</p>
<div class="auth"><label for="secret">관리자 로컬 세션 토큰</label>
<input type="password" id="secret" autocomplete="off" placeholder="터미널 발급 토큰 붙여넣기 · 자동 확인" size="28">
<button id="login" class="primary">검토 문서 불러오기</button><span class="notice">토큰 붙여넣기 시 자동 조회 · 현 로컬 서버 실행 중에만 유효</span></div>
<p id="status" role="status">관리자 토큰을 붙여 넣으면 자동으로 검토 목록을 조회합니다. 필요하면 검토 문서 불러오기를 눌러주세요.</p>
<section id="e2e-controls" hidden>
 <div class="e2e-head"><h2>Human-assisted · E2E 검증</h2><span id="e2e-state" class="e2e-tag" data-state="IDLE">실행 전</span></div>
 <p class="sub">검토 10문서 재검증 → 가명처리 → 별도 187문서 DB 적재 → 88질의 검색 평가 · 기존 177문서 DB 유지</p>
 <div class="e2e-actions"><button class="primary" id="run-e2e">검토 반영 · 실험 적재 · 88질의 평가</button><span id="e2e-elapsed" class="sub"></span></div>
 <div class="e2e-progress" aria-label="실험 처리 단계">
   <div class="e2e-progress-top"><strong id="e2e-stage">실행 대기</strong><span id="e2e-counter" class="sub">—</span></div>
   <progress id="e2e-bar" max="100" value="0" aria-label="실제 작업 단계 진행률"></progress>
   <ol class="e2e-steps" id="e2e-steps">
     <li>① REVIEW 재검증</li><li>② 문서 임베딩</li><li>③ 실험 DB 적재</li><li>④ 88질의 평가</li>
   </ol>
   <p class="notice">실제 완료 단계와 처리 건수 기준 · 진행바는 남은 시간 추정치가 아닙니다.</p>
 </div>
 <div id="e2e-results" hidden>
   <h3>88질의 검색 평가 결과</h3>
   <p class="sub">전체 88질의 분모 · 보류 질의는 실패로 집계 · Human-assisted</p>
   <div class="e2e-metrics">
      <div class="metric-tile"><div class="metric-label">Hit@1</div><div id="e2e-hit1" class="metric-value">—</div><div id="e2e-base1" class="metric-base">—</div></div>
      <div class="metric-tile"><div class="metric-label">Hit@5</div><div id="e2e-hit5" class="metric-value">—</div><div id="e2e-base5" class="metric-base">—</div></div>
      <div class="metric-tile"><div class="metric-label">MRR</div><div id="e2e-mrr" class="metric-value">—</div><div id="e2e-basem" class="metric-base">—</div></div>
      <div class="metric-tile"><div class="metric-label">Recall@5</div><div id="e2e-recall5" class="metric-value">—</div><div class="metric-base">88질의 전체 정답 기준</div></div>
   </div>
   <p id="e2e-table"></p>
 </div>
 <p id="e2e-error" hidden role="alert"></p>
</section>
<div class="layout">
<section class="panel" aria-label="검토 문서 목록">
<h2>REVIEW 문서 <span id="count" class="sub"></span></h2>
<p class="sub" id="review-summary">인증 후 문서 목록을 불러옵니다.</p><div id="queue"></div>
</section>
<section class="panel" aria-label="선택한 문서">
<h2 id="heading">왼쪽에서 검토 문서를 선택하세요</h2>
<p class="notice">주황색 = REVIEW 후보 · 연두색 = 기존 보호 구간</p>
<div id="source"></div><div id="candidates"></div>
</section></div>
</main><script>
const elem=id=>document.getElementById(id);
const node=(tag,text)=>{let x=document.createElement(tag);x.textContent=text;return x};
let entries=[],selected=null;
async function api(url,payload){
const opts={headers:{"X-Local-Review-Token":elem("secret").value.trim()}};
if(payload){opts.method="POST";opts.headers["Content-Type"]="application/json";opts.body=JSON.stringify(payload)}
const r=await fetch(url,opts),data=await r.json();
if(!r.ok)throw Error(data.error||"request_failed");return data
}
async function loadCase(id){
 try{
 const doc=await api("/api/case/"+encodeURIComponent(id));
 selected=id;renderList();show(doc);
 }catch(e){elem("status").textContent="문서 조회 실패: "+e.message}
}
function renderList(){
const box=elem("queue");box.replaceChildren();elem("count").textContent="("+entries.length+"개)";
const completed=entries.filter(x=>x.review_complete).length;
elem("review-summary").textContent=entries.length===0
  ?"현재 확인할 REVIEW 문서가 없습니다."
  :"판단 기록: "+completed+"/"+entries.length+"건 · 미분류: "+(entries.length-completed)+"건. 기록 완료 문서도 재검증·적재 전에는 이 목록에 남습니다.";
if(entries.length===0){box.appendChild(node("p","현재 확인할 문서 없음 · REVIEW 대기열 0건"));return;}
for(const info of entries){
const button=node("button","");button.type="button";
button.className="queue-item"+(selected===info.id?" active":"");
button.appendChild(node("span",info.id));
button.appendChild(node("small",(info.review_complete?"판단 기록":"검토 필요")+" · "+(info.step1_status==="REVIEW_REQUIRED"?"Step1":"Gate")));
button.onclick=()=>loadCase(info.id);box.appendChild(button);
}
}
function show(doc){
elem("heading").textContent=doc.id+" · Step1 "+doc.step1_status+" · "+doc.gate_reason;
const all=[...doc.candidates,...doc.masks],positions=new Set([0,doc.source.length]);
for(const x of all){positions.add(x.start);positions.add(x.end)}
const cuts=[...positions].sort((a,b)=>a-b),area=elem("source");area.replaceChildren();
for(let i=0;i<cuts.length-1;i++){
const a=cuts[i],b=cuts[i+1];if(a===b)continue;
const hot=doc.candidates.some(x=>x.start<b&&x.end>a);
const masked=doc.masks.some(x=>x.start<b&&x.end>a);
const span=node(hot||masked?"mark":"span",doc.source.slice(a,b));
if(hot)span.className="suspect";else if(masked)span.className="masked";
area.appendChild(span);
}
const out=elem("candidates");out.replaceChildren();
const labels={LONG_NAME:"끝 '이'까지 실제 이름",SHORT_NAME_PLUS_PARTICLE:"짧은 이름 + 조사 '이'",
PERSON_PII:"실제 인명 개인정보",NON_PERSON_EXPRESSION:"인명이 아닌 일반 표현",
UNRESOLVED:"판단 불가 — REVIEW 유지"};
doc.candidates.forEach((item,i)=>{
const card=node("article","");card.appendChild(node("h3",(i+1)+". "+item.kind+" / "+item.reason));
card.appendChild(node("p","검토 후보: "+doc.source.slice(item.start,item.end)));
if(item.overlapping_labels)card.appendChild(node("p","Step1 겹침: "+item.overlapping_labels.join(",")));
const line=node("div","");line.className="actionline";
const select=document.createElement("select");
const choices=item.kind==="name_boundary"?["UNRESOLVED","LONG_NAME","SHORT_NAME_PLUS_PARTICLE"]:
["UNRESOLVED","PERSON_PII","NON_PERSON_EXPRESSION"];
for(const ch of choices){const option=document.createElement("option");option.value=ch;option.textContent=labels[ch];select.appendChild(option)}
const old=Array.isArray(doc.saved_choices)?doc.saved_choices[i]:null;
if(choices.includes(old))select.value=old;
if(old && old!=="UNRESOLVED")card.appendChild(node("p","저장된 판단: "+labels[old]+" · 독립 재검증 전"));
const button=node("button","판단 기록 (승인 아님)");button.type="button";
button.onclick=async()=>{
 if(!confirm("판단 기록만 수행합니다. 실제 가명처리·DB 적재는 이루어지지 않습니다."))return;
 try{const result=await api("/api/review",{id:doc.id,candidate:i,choice:select.value});
 elem("status").textContent=doc.id+" · 기록됨: "+result.state+" (재검증 전)";
 const summary=await api("/api/cases");entries=summary.cases;renderList();
 }catch(e){elem("status").textContent="기록 실패: "+e.message}
};
line.append(select,button);card.appendChild(line);out.appendChild(card);
});
}
let loginBusy=false;
elem("secret").addEventListener("input",()=>{
 const value=elem("secret").value.trim();
 if(!entries.length)elem("status").textContent="토큰 입력 중 · 붙여넣기가 완료되면 자동으로 목록을 불러옵니다.";
 // Local server issues token_urlsafe(32): exactly 43 URL-safe characters.
 // Auto-login only when a complete token has been pasted/entered.
 if(!loginBusy && /^[A-Za-z0-9_-]{43}$/.test(value))elem("login").click();
});
elem("secret").addEventListener("keydown",e=>{
 if(e.key==="Enter"){e.preventDefault();elem("login").click()}
});
elem("login").onclick=async()=>{
 if(loginBusy)return;
 loginBusy=true;
 elem("login").disabled=true;
 elem("status").textContent="관리자 인증 및 검토 목록 조회 중…";
 try{
  const data=await api("/api/cases");
  if(!Array.isArray(data.cases))throw Error("INVALID_CASES_RESPONSE");
  entries=data.cases;selected=null;renderList();
  elem("e2e-controls").hidden=!data.experimental_enabled;
  elem("status").textContent=entries.length
    ?"인증 완료 · 문서 "+entries.length+"건 로드 · 왼쪽 목록에서 선택하세요"
    :"인증 완료 · 현재 확인할 REVIEW 문서가 없습니다.";
  if(entries.length)await loadCase(entries[0].id);
  if(data.experimental_enabled)startProgressPolling();
 }catch(e){elem("status").textContent="인증/조회 실패: "+e.message}
 finally{loginBusy=false;elem("login").disabled=false}
};
let progressInterval=null;
const stageOrder=["REVALIDATING","EMBEDDING","INDEXING","EVALUATING"];
const stageLabels={
  REVALIDATING:"REVIEW 교정·Gate/HMAC 검증",EMBEDDING:"187문서 임베딩 계산",
  INDEXING:"별도 실험 DB에 안전하게 적재",EVALUATING:"전체 88질의 검색 평가",
  COMPLETE:"전체 실험 완료",IDLE:"실행 대기"
};
function showExperimentStatus(result){
 const state=result.state||"IDLE";
 const tag=elem("e2e-state");tag.dataset.state=state;
 tag.textContent=({IDLE:"실행 전",PREVIOUS_RUN:"이전 결과 있음",RUNNING:"진행 중",
                   SUCCEEDED:"완료",FAILED:"실패"})[state]||state;
 const p=result.progress||{stage:"IDLE",completed:0,total:0};
 const index=stageOrder.indexOf(p.stage);
 const fraction=Number.isInteger(p.completed)&&Number.isInteger(p.total)&&p.total>0
   ? Math.min(1,Math.max(0,p.completed/p.total)):0;
 const pct=state==="SUCCEEDED"?100:index<0?0:
   Math.min(99,Math.round((index+fraction)/stageOrder.length*100));
 elem("e2e-bar").value=pct;
 elem("e2e-stage").textContent=stageLabels[p.stage]||"실험 상태 확인 중";
 elem("e2e-counter").textContent=state==="RUNNING" && p.total
   ?p.completed+" / "+p.total+"건 완료":(state==="SUCCEEDED"?"4단계 완료":pct+"% · 단계 기준");
 const steps=elem("e2e-steps").children;
 for(let i=0;i<steps.length;i++){
   steps[i].className=(state==="SUCCEEDED"||index>i)?"done":index===i?"active":"";
 }
 const elapsed=result.elapsed_seconds;
 elem("e2e-elapsed").textContent=typeof elapsed==="number"?"실제 경과: "+elapsed.toFixed(1)+"초":"";
 elem("run-e2e").disabled=state==="RUNNING"||state==="SUCCEEDED";
 const error=elem("e2e-error");
 error.hidden=state!=="FAILED"&&state!=="PREVIOUS_RUN";
 error.textContent=state==="FAILED"?"실험 중단 · "+(result.error_type||"UNKNOWN_ERROR"):
   state==="PREVIOUS_RUN"?"이전 실행에서 187문서 적재는 완료됐습니다. 구버전 결과에는 지표가 저장되지 않아 재평가 시 표시할 수 있습니다.":"";
 const out=elem("e2e-results");
 const official=result.all88?.official;
 out.hidden=!official;
 if(official){
   const n=official.n||88;
   elem("e2e-hit1").textContent=official.hit1+"/"+n;
   elem("e2e-hit5").textContent=official.hit5+"/"+n;
   elem("e2e-mrr").textContent=Number(official.mrr).toFixed(4);
   elem("e2e-recall5").textContent=(100*Number(official.recall5)).toFixed(1)+"%";
   // Fixed 177-doc V6 baseline on the same 88-query set, measured 2026-11-12.
   const diff1=official.hit1-73,diff5=official.hit5-79,diffm=Number(official.mrr)-0.8596;
   elem("e2e-base1").textContent="177문서 기준 73/88 · 변화 "+(diff1>=0?"+":"")+diff1+"건";
   elem("e2e-base5").textContent="177문서 기준 79/88 · 변화 "+(diff5>=0?"+":"")+diff5+"건";
   elem("e2e-basem").textContent="177문서 기준 0.8596 · 변화 "+(diffm>=0?"+":"")+diffm.toFixed(4);
   elem("e2e-table").textContent="실험 DB: "+(result.table||"미확인");
 }
}
async function pollExperiment(){
 try{showExperimentStatus(await api("/api/e2e-status"))}
 catch(e){
   elem("e2e-stage").textContent="진행 상태를 불러오지 못했습니다";
   elem("e2e-counter").textContent="연결을 확인하세요";
 }
}
function startProgressPolling(){
 if(progressInterval!==null)return;
 pollExperiment();
 progressInterval=setInterval(pollExperiment,1500);
}
elem("run-e2e").onclick=async()=>{
 if(!confirm("검토 완료된 10건을 별도 실험 DB에 적재하고 88질의를 재평가합니다. 기존 테이블은 변경되지 않습니다. 실행할까요?"))return;
 try{
   const result=await api("/api/e2e-run",{confirm:"CREATE_ISOLATED_HUMAN_ASSISTED_INDEX"});
   showExperimentStatus({state:result.state,progress:{stage:"REVALIDATING",completed:0,total:10}});
   startProgressPolling();
 }catch(e){elem("e2e-error").hidden=false;elem("e2e-error").textContent="시작 실패 · "+e.message}
};
</script></body></html>"""


def saved_review_choices(cases, journal):
    """Return only current journal choices matching exact source and candidate."""
    latest={}
    try:
        records=[json.loads(line) for line in private_path(journal).read_text(
            encoding="utf-8").splitlines() if line.strip()] if journal.exists() else []
    except (OSError,ValueError):
        return {}
    for record in records:
        if not isinstance(record,dict) or record.get("format")!="q001-review-draft-v1":
            continue
        did=record.get("document_id")
        index=record.get("candidate_index")
        if did not in cases or type(index) is not int:
            continue
        case=cases[did]
        if not 0<=index<len(case["candidates"]):
            continue
        candidate=case["candidates"][index]
        if (record.get("source_digest")!=hashlib.sha256(
                case["source"].encode("utf-8")).hexdigest()
            or record.get("source_span")!=[candidate["start"],candidate["end"]]
            or record.get("kind")!=candidate["kind"]
            or record.get("state")!="NEEDS_INDEPENDENT_REVALIDATION"
            or record.get("human_classification") not in CHOICES[candidate["kind"]]):
            continue
        latest[(did,index)]=record["human_classification"]
    return latest


def create_handler(cases, token, port, journal, *,
                   allow_experiment=False, experiment_env=None,
                   experiment_baseline=None, experiment_source=None,
                   experiment_device="cpu", experiment_output=None,
                   experiment_state=None):
    task={"state":"IDLE","result":None,"error_type":None,
          "progress":{"stage":"IDLE","completed":0,"total":0},
          "started_monotonic":None,"elapsed_seconds":None}
    lock=Lock()
    stages={"REVALIDATING","EMBEDDING","INDEXING","EVALUATING","COMPLETE"}
    def update_progress(stage,completed,total):
        if (stage not in stages or type(completed) is not int
            or type(total) is not int or total<=0 or not 0<=completed<=total):
            raise ValueError("INVALID_EXPERIMENT_PROGRESS")
        with lock:
            task["progress"]={"stage":stage,"completed":completed,"total":total}

    def safe_status():
        with lock:
            status={"state":task["state"],"error_type":task["error_type"],
                    "progress":dict(task["progress"])}
            if task["started_monotonic"] is not None:
                status["elapsed_seconds"]=(round(time.monotonic()-task["started_monotonic"],1)
                    if task["state"]=="RUNNING" else task["elapsed_seconds"])
            if task["result"] is not None:
                status.update({
                    "documents":task["result"].get("documents"),
                    "table":task["result"].get("table"),
                    "all88":task["result"].get("all88"),
                })
            return status
    # Persisted summaries do not reveal document source or session secrets.
    # Older successful runs may contain no score (prior schema); keep that
    # honest and offer a fresh explicit evaluation.
    if experiment_state is not None:
        try:
            saved=json.loads(private_path(experiment_state).read_text(encoding="utf-8"))
            if (saved.get("format")=="q001-e2e-index-v1"
                and saved.get("documents")==187 and
                re.fullmatch(r"rag\.aegis_repro_[0-9a-f]{20}",str(saved.get("table","")))):
                metrics=saved.get("all88")
                if isinstance(metrics,dict) and isinstance(metrics.get("official"),dict):
                    task.update(state="SUCCEEDED",
                        result={"documents":187,"table":saved["table"],"all88":metrics},
                        progress={"stage":"COMPLETE","completed":1,"total":1},
                        elapsed_seconds=saved.get("elapsed_seconds"))
                else:
                    task.update(state="PREVIOUS_RUN",
                        result={"documents":187,"table":saved["table"],"all88":None})
        except (OSError,ValueError,TypeError,AttributeError):
            pass

    def job():
        from q001_human_assisted_e2e import run_pipeline
        from dataset_preparation import private_write
        try:
            report=run_pipeline(mode="run",env=experiment_env,
                                baseline=experiment_baseline,
                                source=experiment_source,
                                journal=journal,
                                output=experiment_output,
                                device=experiment_device,
                                confirm="CREATE_ISOLATED_HUMAN_ASSISTED_INDEX",
                                progress=update_progress)
            if report.get("state")!="EXPERIMENT_INDEXED_AND_EVALUATED":
                raise ValueError("E2E_UNEXPECTED_OUTCOME")
            with lock:
                elapsed=round(time.monotonic()-task["started_monotonic"],1)
            # Atomic private write; includes only evaluation aggregates.
            private_write(private_path(experiment_state),{
                "format":"q001-e2e-index-v1",
                "table":report["table"],"documents":report["documents"],
                "hcx_called":False,"all88":report["all88"],
                "elapsed_seconds":elapsed,
            })
            with lock:
                task.update(state="SUCCEEDED",result=report,error_type=None,
                            elapsed_seconds=elapsed,
                            progress={"stage":"COMPLETE","completed":1,"total":1})
        except Exception as exc:
            # Only our strict alphanumeric review reason is safe to show.
            from review_apply_q001 import ReviewValidationError
            safe_reason=(str(exc) if isinstance(exc,ReviewValidationError)
                         and re.fullmatch(r"[A-Z0-9_]{3,100}",str(exc))
                         else type(exc).__name__)
            with lock:
                elapsed=(round(time.monotonic()-task["started_monotonic"],1)
                         if task["started_monotonic"] is not None else None)
                task.update(state="FAILED",result=None,error_type=safe_reason,
                            elapsed_seconds=elapsed)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass  # No access logs containing sensitive data.

        def reply(self, code, payload, content_type="application/json; charset=utf-8"):
            data = (payload if isinstance(payload, bytes) else
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            self.send_response(code)
            for k, v in [
                ("Content-Type", content_type), ("Content-Length", str(len(data))),
                ("Cache-Control", "no-store"), ("Pragma", "no-cache"),
                ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                ("Referrer-Policy", "no-referrer"),
                ("Content-Security-Policy",
                 "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'"),
            ]: self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def valid_origin(self):
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            return (host in (f"127.0.0.1:{port}", f"localhost:{port}") and
                    (origin is None or origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")) and
                    self.headers.get("Sec-Fetch-Site") in (None, "none", "same-origin"))

        def authenticated(self):
            return secrets.compare_digest(self.headers.get("X-Local-Review-Token", ""), token)

        def do_GET(self):
            if not self.valid_origin():
                self.reply(403, {"error": "LOCAL_ONLY"}); return
            if self.path == "/":
                self.reply(200, HTML.encode("utf-8"), "text/html; charset=utf-8"); return
            if not self.authenticated():
                self.reply(403, {"error": "AUTH_REQUIRED"}); return
            if self.path == "/api/cases":
                current=saved_review_choices(cases,journal)
                self.reply(200, {"experimental_enabled":bool(allow_experiment),
                                 "cases":[{"id":x["id"],"step1_status":x["step1_status"],
                                           "review_complete":all(current.get((x["id"],i),"UNRESOLVED")!="UNRESOLVED"
                                               for i in range(len(x["candidates"])))}
                                          for x in cases.values()]}); return
            if self.path == "/api/e2e-status":
                if not allow_experiment:
                    self.reply(404,{"error":"NOT_ENABLED"});return
                self.reply(200,safe_status());return
            if self.path.startswith("/api/case/"):
                did = self.path[len("/api/case/"):]
                if did in cases:
                    current=saved_review_choices(cases,journal)
                    self.reply(200, {**cases[did],"saved_choices":[
                        current.get((did,i),"UNRESOLVED")
                        for i in range(len(cases[did]["candidates"]))]}); return
            self.reply(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            if not self.valid_origin() or not self.authenticated():
                self.reply(403, {"error": "AUTH_REQUIRED"}); return
            if self.path not in ("/api/review","/api/e2e-run"):
                self.reply(404, {"error": "NOT_FOUND"}); return
            if self.headers.get("Content-Type") != "application/json":
                self.reply(415, {"error": "JSON_REQUIRED"}); return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 < size <= 1024:
                    raise ValueError("BAD_SIZE")
                payload=json.loads(self.rfile.read(size))
                if self.path=="/api/e2e-run":
                    if not allow_experiment or payload!={"confirm":"CREATE_ISOLATED_HUMAN_ASSISTED_INDEX"}:
                        raise ValueError("EXPERIMENT_DISABLED_OR_UNCONFIRMED")
                    with lock:
                        if task["state"] not in ("IDLE","PREVIOUS_RUN"):
                            self.reply(409,{"error":"EXPERIMENT_ALREADY_STARTED"});return
                        task.update(state="RUNNING",result=None,error_type=None,
                                    progress={"stage":"REVALIDATING","completed":0,"total":10},
                                    started_monotonic=time.monotonic(),
                                    elapsed_seconds=None)
                    Thread(target=job,daemon=True,name="q001-experiment").start()
                    self.reply(202,{"state":"RUNNING"});return
                with lock:
                    if task["state"]!="IDLE":
                        self.reply(409,{"error":"REVIEW_LOCKED_DURING_EXPERIMENT"});return
                    record = review_record(payload, cases)
                    persist(journal, record)
                self.reply(200, {"state": "NEEDS_INDEPENDENT_REVALIDATION"})
            except (TypeError, ValueError, OSError):
                self.reply(400, {"error": "INVALID_REVIEW"})
    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=LOCAL/"holdout_labeled.jsonl")
    ap.add_argument("--journal", type=Path, default=LOCAL/"q001_review_decisions.jsonl")
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--enable-experimental-indexing",action="store_true",
                    help="admin-confirmed isolated 187-doc DB indexing + 88-query eval")
    ap.add_argument("--env",type=Path,default=ROOT/".env")
    ap.add_argument("--baseline",type=Path,
                    default=LOCAL/"reproduction_20261111/prepared.json")
    ap.add_argument("--experiment-out",type=Path,
                    default=LOCAL/"q001_human_assisted_187.json")
    ap.add_argument("--experiment-state",type=Path,
                    default=LOCAL/"q001_e2e_state.json")
    ap.add_argument("--device",choices=("cpu","mps"),default="cpu")
    args = ap.parse_args()
    try:
        if not 1024 <= args.port <= 65535 or private_path(args.input) == private_path(args.journal):
            raise ValueError("BAD_CONFIG")
        sources = read_sources(args.input, args.ids)
        from pii_detector import create_detector
        with create_detector() as detector:
            cases = get_cases(sources, detector)
        secret = secrets.token_urlsafe(32)
        if args.enable_experimental_indexing and set(args.ids)!=set((
            "H0002","H0057","H0072","H0091","H0103",
            "H0105","H0116","H0117","H0119","H0135")):
            raise ValueError("REQUIRE_FULL_Q001_REVIEW_SET")
        server = ThreadingHTTPServer(("127.0.0.1", args.port),
                            create_handler(cases, secret, args.port, args.journal,
                            allow_experiment=args.enable_experimental_indexing,
                            experiment_env=args.env,experiment_baseline=args.baseline,
                            experiment_source=args.input,experiment_device=args.device,
                            experiment_output=args.experiment_out,
                            experiment_state=args.experiment_state))
        print("Q001_REVIEW_LOCAL http://127.0.0.1:" + str(args.port), flush=True)
        print("Q001_REVIEW_SESSION_TOKEN " + secret, flush=True)
        print("REVIEW_HUMAN_CLASSIFICATION_REQUIRED", flush=True)
        if args.enable_experimental_indexing:
            print("Q001_EXPERIMENTAL_INGEST_ENABLED isolated_index_only=TRUE",flush=True)
        else:
            print("REVIEW_ONLY_NO_AUTOMATIC_RELEASE", flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
        return 0
    except Exception as exc:
        # Never leak underlying source in exception messages.
        print("Q001_REVIEW_START_FAILED " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
