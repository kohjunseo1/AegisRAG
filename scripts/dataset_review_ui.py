#!/usr/bin/env python3
"""Loopback-only local review UI: revised-source Gate recheck and confirmed live upsert.

The random token never enters URLs/cookies. Only authenticated, same-origin
requests can read pending raw source text or modify stage/DB. No raw logging.
"""
from __future__ import annotations
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'step2_privacy_rag/app'))
sys.path.insert(0,str(ROOT/'step1_pii_detector/src'))

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AegisRAG · 문서 검토</title>
<style>
:root{color-scheme:light;--bg:#f5f7fb;--ink:#182338;--soft:#62748a;--line:#dfe5ed;--blue:#20538b;--sub:#f0f4f9;--warn:#fff5df}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,'Apple SD Gothic Neo',sans-serif;color:var(--ink);background:var(--bg);margin:0;font-size:14px}
button,input,textarea{font:inherit}
button{border:1px solid #cdd7e3;border-radius:8px;padding:10px 12px;cursor:pointer;background:white;color:var(--ink)}
button.primary{color:#fff;background:var(--blue);border-color:var(--blue);font-weight:600}
button:disabled{opacity:.48;cursor:not-allowed}
input,textarea{border:1px solid #cdd7e3;border-radius:8px;padding:11px;background:white;min-width:0;color:var(--ink)}
input:focus,textarea:focus,button:focus-visible{outline:2px solid #80aadd;outline-offset:1px}
header{background:white;border-bottom:1px solid var(--line);padding:15px max(20px,calc((100vw - 1620px)/2));display:flex;justify-content:space-between;align-items:center;gap:18px;flex-wrap:wrap}
h1{font-size:21px;margin:0 0 3px}.sub{color:var(--soft);font-size:12px;margin:0}.login{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.login input{width:290px}
.shell{max-width:1620px;margin:auto;padding:18px 22px 28px}.stats{color:#445a75;padding:0 0 14px;min-height:27px}
.workspace{display:grid;grid-template-columns:minmax(270px,330px) minmax(0,1fr);gap:16px;align-items:start}
.panel{background:white;border:1px solid var(--line);border-radius:12px;min-width:0}
.side{position:sticky;top:15px;display:flex;flex-direction:column;height:calc(100vh - 145px);min-height:490px;max-height:850px;overflow:hidden}
.sideHead{padding:16px;border-bottom:1px solid var(--line)}h2{font-size:17px;margin:0 0 8px}
.counter{font-size:12px;color:var(--soft)}#search{width:100%;margin-top:12px}
.filters{display:flex;gap:7px;margin-top:10px}.filters button{padding:7px 10px;font-size:12px}.filters button.active{background:#e8f1fc;border-color:#8eb2df;color:#173d70}
#queue{padding:6px;list-style:none;margin:0;overflow-y:auto;flex:1;min-height:0}
#queue li{margin:3px 0}#queue button{width:100%;text-align:left;border-color:transparent;padding:12px;background:transparent;display:block;overflow-wrap:anywhere}
#queue button:hover{background:#f3f6fb}#queue button.selected{border-color:#8fb5dd;background:#ebf3fc}
.itemTop{display:flex;justify-content:space-between;gap:5px;align-items:center;font-size:12px}
.itemId{font-weight:600}.itemReason{display:block;font-size:11px;color:var(--soft);margin-top:5px}
.badge{font-size:11px;border-radius:40px;background:#fff1dc;color:#915715;padding:3px 7px;white-space:nowrap}
.badge.blocked{background:#ffebeb;color:#a03d3d}
.detail{padding:20px;display:flex;flex-direction:column;gap:16px;min-height:calc(100vh - 145px)}
.detailHeader{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:start}
#selected{overflow-wrap:anywhere;color:#254c77;font-size:12px}
.help{padding:13px 15px;background:#f1f6fd;border:1px solid #d8e6f8;border-radius:9px;line-height:1.6}
.help strong{display:block;margin-bottom:3px}.help .minor{font-size:12px;color:#526982;margin-top:6px}
.editors{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;min-height:350px;flex:1}
.editor{display:flex;flex-direction:column;gap:8px;min-width:0}.editor h3{font-size:14px;margin:0}.editor small{color:var(--soft);line-height:1.5}
.editor textarea{width:100%;flex:1;min-height:350px;resize:vertical;line-height:1.7;white-space:pre-wrap}
.editor textarea[readonly]{background:#f6f8fb}
.reviewActions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:15px}
.reviewActions input{width:210px}.reviewActions .hint{color:var(--soft);font-size:12px;flex-basis:100%}
.commit{border-top:1px solid var(--line);padding-top:15px;display:flex;flex-wrap:wrap;align-items:center;gap:10px}
.commit input{width:240px}.commit .foot{color:var(--soft);font-size:12px;line-height:1.5;flex-basis:100%}
.notice{white-space:pre-wrap;min-height:20px;font-size:12px;color:#285a8b}
@media(max-width:940px){.workspace{grid-template-columns:1fr}.side{position:static;height:310px;min-height:310px}.detail{min-height:0}}
@media(max-width:660px){.editors{grid-template-columns:1fr}.editor textarea{min-height:220px}.login input{width:200px}.shell{padding:13px}.detail{padding:15px}}

/* Advisory markers are NOT confirmed privacy failures. */
.sourcePreview{width:100%;flex:1;min-height:350px;max-height:620px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7;padding:11px;border:1px solid #cdd7e3;border-radius:8px;background:#f6f8fb;user-select:text}
.piiMark{border-radius:2px;padding:1px 0}
.piiMark.entity{background:#cfe5ff;border-bottom:2px solid #3885c6}
.piiMark.masked{background:#e1eaf6;border-bottom:2px solid #829aba}
.piiMark.proposal{background:#ffe0a8;border-bottom:2px solid #c38b27}
.piiMark.issue{background:#ffd3d3;border-bottom:2px solid #c44747}
.diagnostics{border:1px solid #e0e5ed;padding:12px;border-radius:8px;background:#fcfdff;line-height:1.5}
.diagHelp{color:#5e6d82;font-size:12px;margin-top:6px}
.markLegend{display:flex;gap:12px;flex-wrap:wrap;margin-top:7px;font-size:12px}
.markLegend span{display:inline-flex;gap:5px;align-items:center}
.markLegend i{width:12px;height:12px;border-radius:3px;display:inline-block}
.maskActions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px}
#selectionInfo{color:#66788f;font-size:12px}
@media(max-width:660px){.sourcePreview{min-height:220px}}
</style></head><body>
<header>
 <div><h1>AegisRAG · 개인정보 검토</h1><p class="sub">보류 문서 검토 → 원문 수정 → Step1/Gate 재검증 → DB 반영</p></div>
 <div class="login"><input id="token" type="password" autocomplete="off" placeholder="터미널의 REVIEW_ACCESS_TOKEN"><button id="login" class="primary">검토 목록 열기</button></div>
</header>
<main class="shell">
 <div class="stats" id="summary">접근 토큰을 입력하면 검토 목록이 표시됩니다.</div>
 <div class="workspace">
  <aside class="panel side" aria-label="검토 대기 문서">
   <div class="sideHead"><h2>검토 대상 <span id="count" class="counter"></span></h2>
    <div class="counter">문서를 선택하면 오른쪽에 원문과 수정란이 나옵니다.</div>
    <input id="search" type="search" placeholder="문서 ID 또는 보류 사유 검색">
    <div class="filters"><button type="button" data-filter="ALL" class="active">전체</button><button type="button" data-filter="REVIEW_PENDING">검토 필요</button><button type="button" data-filter="BLOCKED">차단</button></div>
   </div>
   <ul id="queue"><li class="counter" style="padding:12px">로그인 후 표시</li></ul>
  </aside>
  <section class="panel detail" aria-label="문서 편집 및 DB 반영">
   <div class="detailHeader"><div><h2>문서 검토·수정</h2><div id="selected">왼쪽에서 문서를 선택해 주세요.</div></div><span id="statusBadge" class="badge" hidden></span></div>
   <div id="guidance" class="help"><strong>어떻게 처리하나요?</strong>보류 사유를 확인하고, 실제 개인정보 또는 잘못된 경계를 검토하세요. 필요한 부분만 수정한 다음 재검증하세요.<div class="minor">수정했다고 자동 승인되는 것은 아닙니다. 다시 Gate를 통과해야 READY가 됩니다.</div></div>
   <div class="diagnostics" aria-label="의심 구간 및 수동 검토">
    <strong>로컬 탐지 표시 (확정된 오류 위치가 아닙니다)</strong>
    <div id="diagSummary" class="diagHelp">문서를 선택하면 Step1과 Gate 신호를 다시 조회합니다.</div>
    <div class="markLegend"><span><i style="background:#cfe5ff"></i>Step1 탐지</span><span><i style="background:#e1eaf6"></i>기존 마스킹</span><span><i style="background:#ffe0a8"></i>Gate 보호 제안</span><span><i style="background:#ffd3d3"></i>Step1 보류 신호</span></div>
    <div id="diagNote" class="diagHelp">자동 표시 외에도 원문 전체를 살펴야 합니다.</div>
    <div class="maskActions"><button id="maskSelection" type="button">원문 드래그 구간을 [REDACTED]로 치환</button><span id="selectionInfo">원문에서 구간을 드래그할 수 있습니다. 선택만으로 승인되지 않습니다.</span></div>
   </div>

<div class="manualPanel" style="border:1px solid #d4dce6;border-radius:8px;margin:10px 0;padding:12px;background:#f9fbfe">
 <strong>수동 개인정보 구간 추가 · 실험 V1</strong>
 <p class="diagHelp">원문에서 개인정보를 드래그하여 유형을 선택하세요. 선택 구간을 유형별 HMAC으로 먼저 보호한 뒤 원래 Step1·Gate 전체를 다시 실행합니다. 탐지기와 충돌하면 승인하지 않습니다. 이 기능은 기존 탐지 결과의 강제 해제용이 아닙니다.</p>
 <div class="maskActions">
  <select id="manualLabel" aria-label="개인정보 유형">
   <option value="person">PERSON</option><option value="phone_number">PHONE_NUMBER</option>
   <option value="email">EMAIL</option><option value="address">ADDRESS</option>
   <option value="resident_registration_number">RESIDENT_REGISTRATION_NUMBER</option>
   <option value="account_number">ACCOUNT_NUMBER</option><option value="card_number">CARD_NUMBER</option>
  </select>
  <button id="manualAdd" type="button">선택 구간을 PII로 등록</button>
  <button id="manualClear" type="button">선택 목록 초기화</button>
 </div>
 <ul id="manualList" class="diagHelp" style="margin:6px 0;padding-left:19px"></ul>
 <div class="maskActions"><button id="manualApprove" type="button" class="primary">수동 구간 보호·재검증·승인</button>
 <span class="diagHelp">오탐 해제와 라벨 충돌은 별도 검토가 필요합니다. 현재 자동 승인되지 않습니다.</span></div>
</div>
   <div class="editors">
    <div class="editor"><h3>원본 문서 · 읽기 전용</h3><small>보류된 원문입니다. 이 내용을 외부 서비스에 붙여넣지 마세요.</small><div id="original" class="sourcePreview" tabindex="0" aria-label="탐지 구간 표시된 원본 문서"></div></div>
    <div class="editor"><h3>수정할 원문</h3><small>우측에서 필요한 부분만 수정하세요. 문서의 사실관계와 검색 근거는 최대한 유지하세요.</small><textarea id="revision" spellcheck="false" placeholder="문서를 선택하면 원문 사본이 입력됩니다."></textarea></div>
   </div>
   <div class="reviewActions"><input id="reviewer" placeholder="검토자 ID (영문/숫자)"><button id="approve" type="button" class="primary" disabled>수정 원문 재검증 및 승인</button><span class="hint">미수정 원문은 승인되지 않습니다. 필요한 개인정보 보호 수정만 진행하세요.</span></div>
   <div id="notice" class="notice" role="status" aria-live="polite"></div>
   <div class="commit"><h2 style="margin:0;font-size:15px">승인 문서 DB 반영</h2><span class="counter">대상: 격리 테스트 DB 127.0.0.1:5544 / aegis_blind_test · rag.aegis_live_docs</span><button id="commit" type="button" class="primary">승인 문서 DB 반영</button><div class="foot">검토가 끝난 READY 문서만 격리 테스트 DB(5544)로 UPSERT합니다. 기존 변경 없는 문서는 재임베딩하지 않습니다.</div></div>
  </section>
 </div>
</main>
<script>
"use strict";
let key="",current="",original="",items=[],filter="ALL";
const el=id=>document.getElementById(id);
const report=s=>{el("notice").textContent=s};
const reasons={
 "UNLINKED_MENTIONS_REVIEW":"인물 언급을 안전한 보호 토큰과 연결하지 못했습니다. 이름이 필수 근거인지 먼저 확인하세요. 불필요한 식별 표현만 제거·일반화하고, 필요한 이름이라면 임의로 바꾸지 말고 계속 보류하세요.",
 "GATE_NOT_READY":"Privacy Gate가 보호 범위의 안전성을 확인하지 못했습니다. 주소·이름·번호 및 미보호 표현을 확인하세요. 실제 문제 구간을 찾지 못했다면 임의로 수정하지 말고 보류하세요.",
 "STEP1_NOT_READY":"Step1 탐지 단계에서 자동 처리를 허용하지 않았습니다. 원문의 민감 정보를 점검하세요. 탐지 실패라면 원문을 억지로 수정하기보다 별도로 원인 분석해야 합니다.",
 "V5_ABSTAIN":"이름·개체 경계 판단이 불확실하여 자동 승인이 중단됐습니다. 실제 개인정보와 검색 근거가 무엇인지 검토하세요.",
 "RESIDUAL_PII_REQUIRES_REVIEW":"자동 가명처리 후에도 전화번호·이메일·식별번호 형식이 남았습니다. 해당 표현을 안전하게 수정하고 재검증하세요.",
 "SAFE_PIPELINE_FAILURE":"보호 파이프라인에서 안전하게 처리할 수 없는 오류가 발생했습니다. 문서 수정으로 회피하기보다 원인 진단이 먼저입니다.",
 "GATE_VALIDATION_FAILED":"Privacy Gate 검증에 실패했습니다. 누락된 개인정보가 있는지 점검하고 원인 불명이면 보류하세요."
};
async function call(path,method="GET",body=null){
 const headers={"X-Aegis-Review-Token":key};
 if(body!==null)headers["Content-Type"]="application/json";
 const resp=await fetch(path,{method,headers,cache:"no-store",body:body===null?null:JSON.stringify(body),credentials:"omit",redirect:"error"});
 const data=await resp.json();
 if(!resp.ok)throw Error(data.error||"REQUEST_FAILED");
 return data;
}
function dirty(){return !!current&&el("revision").value!==original}
function badge(state){const b=el("statusBadge");b.hidden=!state;b.textContent=state||"";b.className="badge"+(state==="BLOCKED"?" blocked":"")}
function guide(d){
 const node=el("guidance");node.replaceChildren();
 const title=document.createElement("strong");title.textContent="보류 사유: "+d.reason;
 const desc=document.createElement("div");desc.textContent=reasons[d.reason]||"자동 승인 불가 사유입니다. 개인정보 보호와 원문 의미 보존을 검토한 뒤 필요한 수정만 진행하세요.";
 const note=document.createElement("div");note.className="minor";note.textContent="주의: 현재 화면은 문제 구간 자동 강조를 지원하지 않습니다. 수정 후 Step1·Gate를 다시 통과하지 못하면 승인되지 않습니다.";
 node.append(title,desc,note);
}
function render(){
 const q=el("search").value.trim().toLowerCase(),ul=el("queue");ul.replaceChildren();let shown=0;
 for(const d of items){
  if(filter!=="ALL"&&d.state!==filter)continue;
  if(q&&!(`${d.doc_id} ${d.state} ${d.reason}`.toLowerCase().includes(q)))continue;
  shown++;
  const li=document.createElement("li"),btn=document.createElement("button");btn.type="button";
  if(d.doc_id===current)btn.classList.add("selected");
  const top=document.createElement("div");top.className="itemTop";
  const id=document.createElement("span");id.className="itemId";id.textContent=d.doc_id;
  const st=document.createElement("span");st.className="badge"+(d.state==="BLOCKED"?" blocked":"");st.textContent=d.state;
  const reason=document.createElement("span");reason.className="itemReason";reason.textContent=d.reason;
  top.append(id,st);btn.append(top,reason);btn.onclick=()=>openDoc(d.doc_id);li.appendChild(btn);ul.appendChild(li);
 }
 el("count").textContent=shown+" / "+items.length;
 if(!shown){const li=document.createElement("li");li.textContent="해당 문서가 없습니다.";li.className="counter";li.style.padding="12px";ul.appendChild(li)}
}
async function refresh(){
 const data=await call("/api/queue");items=data.documents.filter(d=>d.state!=="READY");
 el("summary").textContent="데이터셋: "+data.dataset+" · 전체 "+data.total+" · READY "+data.ready+" · REVIEW "+data.review_pending+" · BLOCKED "+data.blocked;
 render();
}

// All spans are advisory and local-only. Use textContent, NEVER innerHTML with source text.
let marks=[], selectionRange=null, diagnoseSerial=0;
const markRank={issue:4,proposal:3,entity:2,masked:1};
const markInfo={
 issue:"Step1 보류 신호(해당 구간을 확인하세요)",
 proposal:"Gate 추가 보호 제안(검토 후보)",
 entity:"Step1 탐지된 개인정보(이 자체가 보류 원인은 아님)",
 masked:"Step1이 이미 보호할 계획인 구간"
};
function paintMarked(){
 const host=el("original");host.replaceChildren();
 const edges=new Set([0,original.length]);
 marks=marks.filter(x=>Number.isInteger(x.start)&&Number.isInteger(x.end)&&x.start>=0&&x.end<=original.length&&x.start<x.end&&Object.hasOwn(markRank,x.kind));
 for(const m of marks){edges.add(m.start);edges.add(m.end)}
 const cut=[...edges].sort((a,b)=>a-b);
 const fragment=document.createDocumentFragment();
 for(let i=0;i<cut.length-1;i++){
  const a=cut[i],b=cut[i+1]; if(b<=a)continue;
  const best=marks.filter(x=>x.start<=a&&x.end>=b).sort((x,y)=>markRank[y.kind]-markRank[x.kind])[0];
  const text=original.slice(a,b);
  if(!best)fragment.appendChild(document.createTextNode(text));
  else{const span=document.createElement("mark");span.className="piiMark "+best.kind;span.textContent=text;span.title=markInfo[best.kind]+" / "+best.label;fragment.appendChild(span)}
 }
 host.appendChild(fragment);
}
function captureSelection(){
 selectionRange=null;
 const s=window.getSelection(),host=el("original");
 if(!s||s.isCollapsed||!s.anchorNode||!s.focusNode||!host.contains(s.anchorNode)||!host.contains(s.focusNode)){
  el("selectionInfo").textContent="원문에서 구간을 드래그해 선택하세요.";return;
 }
 try{
  const a=document.createRange();a.selectNodeContents(host);a.setEnd(s.anchorNode,s.anchorOffset);
  const b=document.createRange();b.selectNodeContents(host);b.setEnd(s.focusNode,s.focusOffset);
  const x=a.toString().length,y=b.toString().length;
  if(x===y)return;
  selectionRange=[Math.min(x,y),Math.max(x,y)];
  el("selectionInfo").textContent="원문에서 "+(selectionRange[1]-selectionRange[0])+"자 선택됨";
 }catch(err){el("selectionInfo").textContent="구간 선택을 확인하지 못했습니다."}
}
el("original").addEventListener("mouseup",captureSelection);
el("original").addEventListener("keyup",captureSelection);
el("maskSelection").onclick=()=>{
 if(!current||!selectionRange)return report("원본 문서에서 비식별화할 구간을 먼저 드래그하세요.");
 if(el("revision").value!==original)return report("이미 수정한 문서에서는 원본과 문자 위치가 달라질 수 있습니다. 선택 기능은 수정 전 원문에만 적용할 수 있어요. 필요하면 수정란에서 직접 편집하세요.");
 const [a,b]=selectionRange;
 if(!window.confirm("선택한 구간을 [REDACTED]로 대체할까요? 이 수정은 승인되지 않으며 Step1/Gate 재검증이 필요합니다."))return;
 el("revision").value=original.slice(0,a)+"[REDACTED]"+original.slice(b);
 el("revision").focus();selectionRange=null;
 el("selectionInfo").textContent="선택 구간이 수정본에 치환됐습니다. 다른 필요한 보호 구간도 직접 검토한 뒤 재검증하세요.";
 report("이 단계는 단순 원문 수정입니다. 이름/식별정보를 무조건 지우면 문서 검색 연결성이 손상될 수 있습니다. 보호 범위와 근거 보존을 확인하세요.");
};
async function diagnoseSelected(id,serial){
 el("diagSummary").textContent="Step1·Gate 의심 구간을 로컬에서 확인하고 있습니다…";
 try{
  const info=await call("/api/diagnose?id="+encodeURIComponent(id));
  if(serial!==diagnoseSerial||current!==id)return;
  marks=Array.isArray(info.marks)?info.marks:[];
  paintMarked();
  const counts={issue:0,proposal:0,entity:0,masked:0};
  for(const m of marks)if(Object.hasOwn(counts,m.kind))counts[m.kind]++;
  el("diagSummary").textContent="Step1: "+info.step1_status+" · Gate: "+info.gate_decision+" ("+info.gate_reason+") · 탐지 "+counts.entity+" / 보류 신호 "+counts.issue+" / 추가 보호 제안 "+counts.proposal;
  if(info.gate_reason==="NOT_AVAILABLE"||info.gate_decision==="BLOCKED"){
   el("diagNote").textContent="정확한 실패 위치는 특정되지 않았습니다. 탐지 표시가 있어도 실패 원인이라고 단정하면 안 됩니다. SAFE_PIPELINE_FAILURE라면 기술 진단을 우선하세요.";
  }else{
   el("diagNote").textContent="파란색은 이미 탐지한 구간이며 오류 표시가 아닙니다. 주황/빨강도 후보일 뿐입니다. 자동 탐지가 놓친 개인정보는 표시되지 않을 수 있습니다.";
  }
 }catch(err){
  if(serial!==diagnoseSerial||current!==id)return;
  el("diagSummary").textContent="추가 의심 구간 진단 실패: "+err.message;
  el("diagNote").textContent="진단 실패는 문서 승인 근거가 되지 않습니다. 보류 상태를 유지하고 오류를 확인하세요.";
 }
}


// Human span inputs use Python Unicode code-point offsets; browser selections
// provide UTF-16 code units. Convert explicitly (Korean + emoji safe).
let manualEdits=[],manualSourceHash="";
function resetManual(){
 manualEdits=[];manualSourceHash="";
 renderManual();
}
function renderManual(){
 const list=el("manualList");list.replaceChildren();
 for(const e of manualEdits){
  const li=document.createElement("li");
  li.textContent=e.label+" · 문자 구간 ["+e.start+", "+e.end+") ";
  const btn=document.createElement("button");btn.type="button";btn.textContent="제외";
  btn.onclick=()=>{manualEdits=manualEdits.filter(x=>x!==e);renderManual()};
  li.appendChild(btn);list.appendChild(li);
 }
 if(!manualEdits.length)list.textContent="등록된 구간 없음";
}
function utf16ToCodepointOffset(text,offset){
 if(!Number.isInteger(offset)||offset<0||offset>text.length)throw Error("INVALID_SELECTION");
 const prefix=text.slice(0,offset);
 if(prefix.length && prefix.charCodeAt(prefix.length-1)>=0xD800 && prefix.charCodeAt(prefix.length-1)<=0xDBFF)throw Error("SELECTION_SPLITS_SURROGATE");
 return Array.from(prefix).length;
}
el("manualAdd").onclick=()=>{
 if(!current||!selectionRange)return report("왼쪽 원본 미리보기에서 보호할 구간을 드래그하세요.");
 if(el("revision").value!==original)return report("원문을 수정한 상태에서는 구간 선택을 할 수 없습니다. 수정 승인 또는 수동 구간 보호 중 하나를 선택하세요.");
 try{
  const [a,b]=selectionRange;
  const start=utf16ToCodepointOffset(original,a),end=utf16ToCodepointOffset(original,b);
  if(start>=end)throw Error("INVALID_SELECTION");
  const label=el("manualLabel").value;
  if(manualEdits.some(x=>start<x.end&&x.start<end))throw Error("OVERLAPPING_MANUAL_SELECTION");
  manualEdits.push({start,end,label});manualEdits.sort((x,y)=>x.start-y.start);
  selectionRange=null;renderManual();
  report("선택 구간 등록 완료. 실제 보호와 승인 여부는 Step1·Gate 재검증 후 결정됩니다.");
 }catch(err){report("수동 구간 등록 실패: "+err.message)}
};
el("manualClear").onclick=()=>{manualEdits=[];renderManual();report("선택 구간 목록이 초기화됐습니다.")};
el("manualApprove").onclick=async()=>{
 if(!current||!manualEdits.length)return report("검토할 문서와 개인정보 구간을 먼저 등록하세요.");
 if(el("revision").value!==original)return report("원문 수정과 수동 구간 추가는 동시에 수행할 수 없습니다.");
 const reviewer=el("reviewer").value.trim();
 if(!/^[A-Za-z0-9_-]{2,40}$/.test(reviewer))return report("검토자 ID를 먼저 입력하세요.");
 if(!/^[0-9a-f]{64}$/.test(manualSourceHash))return report("원문 버전을 확인하지 못했습니다. 문서를 다시 열어 주세요.");
 if(!window.confirm("선택한 개인정보 구간을 HMAC으로 보호하고 Step1/Gate 재검증 후 승인할까요?"))return;
 el("manualApprove").disabled=true;
 try{
  const ans=await call("/api/review-spans","POST",{doc_id:current,source_sha256:manualSourceHash,edits:manualEdits,reviewer});
  report("수동 PII 보호 및 Gate 재검증 승인 완료: "+ans.doc_id+". DB 반영은 별도 버튼을 눌러야 합니다.");
  current="";original="";resetManual();el("revision").value="";el("selected").textContent="왼쪽에서 문서를 선택해 주세요.";
  if(typeof marks!=="undefined"&&typeof paintMarked==="function"){marks=[];paintMarked()}
  badge("");await refresh();
 }catch(err){report("수동 보호 승인 보류: "+err.message)}
 finally{el("manualApprove").disabled=false}
};
renderManual();

async function openDoc(id){
 if(dirty()&&!window.confirm("수정 내용을 저장하지 않고 다른 문서로 이동할까요?"))return;
 try{
  const d=await call("/api/document?id="+encodeURIComponent(id));
  const meta=items.find(x=>x.doc_id===id);
  current=id;original=d.raw_for_local_review; resetManual();manualSourceHash=d.source_sha256;
  el("selected").textContent=id;
  marks=[];selectionRange=null;paintMarked();el("diagSummary").textContent="의심 구간 진단 준비 중";
  el("revision").value=original;
  el("approve").disabled=false;
  badge(meta?.state||"");
  if(meta)guide(meta);
  report("선택 완료. 보류 사유를 확인하고 수정이 필요한 부분만 고친 뒤 재검증하세요.");
  render();
  const serial=++diagnoseSerial;void diagnoseSelected(id,serial);
 }catch(err){report("문서 조회 오류: "+err.message)}
}
el("search").oninput=render;
for(const b of document.querySelectorAll("[data-filter]"))b.onclick=()=>{filter=b.dataset.filter;for(const x of document.querySelectorAll("[data-filter]"))x.classList.toggle("active",x===b);render()};
el("login").onclick=async()=>{
 key=el("token").value.trim();if(!key){report("토큰을 입력하세요.");return}
 try{await refresh();report("접근 확인 완료. 왼쪽에서 문서를 선택하세요.");el("token").value=""}
 catch(err){key="";report("인증/조회 실패: "+err.message)}
};
el("approve").onclick=async()=>{
 if(!current)return report("문서를 먼저 선택하세요.");
 if(!dirty())return report("수정 없는 원문은 승인할 수 없습니다. 실제 수정이 필요하지 않다면 보류하고 원인을 확인하세요.");
 const reviewer=el("reviewer").value.trim();if(!/^[A-Za-z0-9_-]{2,40}$/.test(reviewer))return report("검토자 ID는 영문·숫자·_·- 2~40자로 입력하세요.");
 el("approve").disabled=true;
 try{
  const ans=await call("/api/review","POST",{doc_id:current,revised_source:el("revision").value,reviewer});
  report("Step1/Gate 재검증 후 승인 완료: "+ans.doc_id+". 아래 DB 반영은 별도 단계입니다.");
  current="";original="";marks=[];selectionRange=null;paintMarked();el("revision").value="";el("selected").textContent="왼쪽에서 문서를 선택해 주세요.";badge("");
  await refresh();
 }catch(err){report("검토 보류: "+err.message);el("approve").disabled=false}
};
el("commit").onclick=async()=>{
 if(!window.confirm("검증 완료한 READY 문서를 격리 테스트 DB(5544 / aegis_blind_test / rag.aegis_live_docs)에 반영할까요?"))return;
 el("commit").disabled=true;
 try{const ans=await call("/api/commit","POST",{confirm:"rag.aegis_live_docs"});report("DB 반영: "+JSON.stringify(ans));await refresh()}
 catch(err){report("DB 반영 실패: "+err.message)}
 finally{el("commit").disabled=false}
};
</script></body></html>"""



def _local_review_signals(source: str, detector) -> dict:
    """Advisory LOCAL-only spans. Never authorizes review, masking, or indexing.

    Returns offsets and codes, never copies source text or secrets into logs.
    All output remains protected by the existing review UI token and localhost.
    """
    if not isinstance(source, str) or not source or len(source) > 100000:
        raise ValueError('INVALID_REVIEW_SOURCE')
    result = detector.detect(source)
    if not isinstance(result, dict):
        raise ValueError('INVALID_DETECTOR_RESULT')
    status = result.get('status')
    if status not in ('READY_FOR_PSEUDONYMIZATION', 'REVIEW_REQUIRED', 'BLOCKED'):
        status = 'UNKNOWN'
    n = len(source)
    marks = []

    def append(items, kind: str, label: str | None = None):
        if not isinstance(items, list):
            return
        for item in items[:150]:
            if not isinstance(item, dict):
                continue
            a, b = item.get('start'), item.get('end')
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= n:
                continue
            raw_label = label if label is not None else item.get('label', '')
            safe_label = raw_label if isinstance(raw_label, str) and __import__('re').fullmatch(r'[a-z0-9_]{1,48}', raw_label) else 'unspecified'
            marks.append({'start': a, 'end': b, 'kind': kind, 'label': safe_label})

    append(result.get('mask_intervals'), 'masked', 'step1_mask')
    append(result.get('entities'), 'entity')
    issues = result.get('issues') if isinstance(result.get('issues'), list) else []
    append(issues, 'issue', 'step1_review_signal')

    gate_decision, gate_reason = 'NOT_RUN', 'NOT_AVAILABLE'
    if status == 'READY_FOR_PSEUDONYMIZATION':
        from name_protection_handoff import _FrozenDetectorView
        from privacy_gate import check_privacy_gate
        gate = check_privacy_gate(_FrozenDetectorView(detector, result), source)
        if isinstance(gate, dict):
            gd = gate.get('decision')
            gate_decision = gd if gd in ('READY_FOR_LOCAL_PSEUDONYMIZATION', 'REVIEW_REQUIRED', 'BLOCKED') else 'UNKNOWN'
            gr = gate.get('reason')
            allowed = {'unprotected_ax_address', 'unresolved_veil_person',
                       'entity_not_covered', 'detector_not_ready',
                       'gate_validation_failed', 'invalid_input',
                       'candidates_covered', 'veil_candidates_triaged',
                       'person_gap_repaired'}
            gate_reason = gr if gr in allowed else 'OTHER'
            # Gate proposals can include ALREADY-MASKED spans. Only highlight
            # portions not covered by Step1's current mask intervals.
            base = [(x['start'], x['end']) for x in result.get('mask_intervals', [])
                    if isinstance(x, dict) and type(x.get('start')) is int
                    and type(x.get('end')) is int and 0 <= x['start'] < x['end'] <= n]
            proposals = gate.get('proposed_protection', [])
            if isinstance(proposals, list):
                for p in proposals[:150]:
                    if not isinstance(p, dict):
                        continue
                    a, b = p.get('start'), p.get('end')
                    if type(a) is not int or type(b) is not int or not 0 <= a < b <= n:
                        continue
                    # Subtract Step1-masked union from Gate proposed protection.
                    sections = [(a, b)]
                    for lo, hi in base:
                        nxt = []
                        for left, right in sections:
                            if hi <= left or right <= lo:
                                nxt.append((left, right))
                            else:
                                if left < lo:
                                    nxt.append((left, min(lo, right)))
                                if hi < right:
                                    nxt.append((max(hi, left), right))
                        sections = nxt
                    for left, right in sections:
                        if left < right:
                            marks.append({'start': left, 'end': right,
                                          'kind': 'proposal', 'label': 'gate_review_candidate'})
    return {'step1_status': status, 'gate_decision': gate_decision,
            'gate_reason': gate_reason, 'issues_count': len(issues),
            'marks': marks[:300],
            'note': 'ADVISORY_ONLY_NOT_A_FAILURE_LOCALIZATION_OR_APPROVAL'}


class Context:
    def __init__(self,stage: Path,env: Path,device: str,token: str,detector):
        self.stage=stage
        self.env=env
        self.device=device
        self.token=token
        self.detector=detector


class Handler(BaseHTTPRequestHandler):
    ctx: Context

    def log_message(self,*args):
        return  # Never write URL/query/raw source to HTTP logs.

    def _send(self,status:int,data:dict|str,kind='application/json'):
        raw=(json.dumps(data,ensure_ascii=False).encode('utf-8')
             if kind=='application/json' else data.encode('utf-8'))
        self.send_response(status)
        self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def _auth(self):
        port=self.server.server_port
        if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
            return False
        token=self.headers.get('X-Aegis-Review-Token','')
        return bool(token) and hmac.compare_digest(token,self.ctx.token)

    def _origin(self):
        origin=self.headers.get('Origin','')
        port=self.server.server_port
        return origin in (f'http://127.0.0.1:{port}',f'http://localhost:{port}')

    def _failure(self,exc):
        # No exception body as it can contain raw source text or credentials.
        safe=str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'[A-Z][A-Z0-9_]+',str(exc)) else type(exc).__name__
        self._send(400,{'error':safe})

    def do_GET(self):
        from dataset_workflow import read_signed,summary
        if urlsplit(self.path).path=='/':
            if not self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
                return self._send(403,{'error':'INVALID_HOST'})
            return self._send(200,PAGE,'text/html')
        if not self._auth():
            return self._send(403,{'error':'UNAUTHORIZED'})
        try:
            stage=read_signed(self.ctx.stage,self._key())
            route=urlsplit(self.path).path
            if route=='/api/queue':
                result=summary(stage)
                result['documents']=[{'doc_id':d['doc_id'],'state':d['state'],
                                       'reason':d['reason']} for d in stage['documents']]
                return self._send(200,result)
            if route=='/api/diagnose':
                did=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
                matches=[d for d in stage['documents'] if d['doc_id']==did
                         and d['state'] in ('REVIEW_PENDING','BLOCKED')]
                if len(matches)!=1:
                    return self._send(404,{'error':'DOC_NOT_PENDING'})
                return self._send(200,_local_review_signals(
                    matches[0]['raw_for_local_review'],self.ctx.detector))
            if route=='/api/document':
                did=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
                matches=[d for d in stage['documents'] if d['doc_id']==did and d['state'] in ('REVIEW_PENDING','BLOCKED')]
                if len(matches)!=1:
                    return self._send(404,{'error':'DOC_NOT_PENDING'})
                d=matches[0]
                return self._send(200,{'doc_id':did,'raw_for_local_review':d['raw_for_local_review'],
                                         'source_sha256':d['source_sha256']})
            return self._send(404,{'error':'UNKNOWN_ROUTE'})
        except Exception as exc:
            self._failure(exc)

    def _key(self):
        from dataset_preparation import fixed_key
        return fixed_key(self.ctx.env)

    def do_POST(self):
        if not self._auth() or not self._origin():
            return self._send(403,{'error':'UNAUTHORIZED_ORIGIN'})
        length=self.headers.get('Content-Length','')
        if not length.isdigit() or not 1<=int(length)<=200000:
            return self._send(413,{'error':'INVALID_REQUEST_SIZE'})
        try:
            request=json.loads(self.rfile.read(int(length)))
            if not isinstance(request,dict):
                raise ValueError('INVALID_REQUEST_BODY')
            from dataset_workflow import read_signed,signed,write_private,review_corrected,export_approved
            key=self._key()
            stage=read_signed(self.ctx.stage,key)
            route=urlsplit(self.path).path
            if route=='/api/review-spans':
                if set(request)!={'doc_id','source_sha256','edits','reviewer'}:
                    raise ValueError('INVALID_MANUAL_REVIEW_REQUEST')
                from manual_review_spans import review_manual_spans
                review_manual_spans(stage,request['doc_id'],request['source_sha256'],
                                    request['edits'],request['reviewer'],
                                    self.ctx.detector,key)
                write_private(self.ctx.stage,signed(stage,key),replace=True)
                return self._send(200,{'doc_id':request['doc_id'],
                                       'state':'HUMAN_APPROVED_AFTER_FULL_RECHECK'})
            if route=='/api/review':
                did=request.get('doc_id')
                revised=request.get('revised_source')
                reviewer=request.get('reviewer')
                if not isinstance(did,str) or not isinstance(revised,str) or len(revised)>100000:
                    raise ValueError('INVALID_REVIEW_REQUEST')
                review_corrected(stage,did,revised,reviewer,self.ctx.detector,key)
                write_private(self.ctx.stage,signed(stage,key),replace=True)
                return self._send(200,{'doc_id':did,'state':'HUMAN_APPROVED_AFTER_GATE_RECHECK'})
            if route=='/api/commit':
                if request.get('confirm')!='rag.aegis_live_docs' or set(request)!={'confirm'}:
                    raise ValueError('CONFIRM_LIVE_UPSERT')
                import psycopg
                from dataset_workflow import BASE
                from live_index import validate_approved_batch,incremental_upsert
                from dataset_preparation import key_tag
                from settings import db_config
                # This experimental UI is intentionally TEST-DB ONLY.
                # An unrelated process environment variable must never
                # silently redirect its confirmed UPSERT into the 187-doc DB.
                target_db=db_config(self.ctx.env)
                if (target_db.get('dbname')!='aegis_blind_test'
                    or target_db.get('host') not in ('127.0.0.1','localhost')
                    or int(target_db.get('port',0)) != 5544):
                    raise ValueError('REVIEW_UI_REQUIRES_ISOLATED_5544_DB')
                batch=export_approved(stage)
                with tempfile.TemporaryDirectory(dir=BASE) as td:
                    temp=Path(td)/'approved.json'
                    write_private(temp,batch)
                    rows=validate_approved_batch(temp,self.ctx.env)
                with psycopg.connect(**target_db) as conn:
                    report=incremental_upsert(conn,rows,key_tag(key),self.ctx.device,confirmed=True)
                return self._send(200,report)
            return self._send(404,{'error':'UNKNOWN_ROUTE'})
        except Exception as exc:
            self._failure(exc)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',required=True,type=Path)
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    p.add_argument('--device',choices=['cpu','mps'],default='cpu')
    p.add_argument('--port',type=int,default=8781)
    args=p.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        p.error('port must be 1024..65535')
    from dotenv import load_dotenv
    load_dotenv(args.env)
    from dataset_preparation import fixed_key
    from dataset_workflow import read_signed
    read_signed(args.stage,fixed_key(args.env))
    from pii_detector import create_detector
    token=secrets.token_urlsafe(32)
    with create_detector() as detector:
        context=Context(args.stage,args.env,args.device,token,detector)
        class LocalHandler(Handler):
            ctx=context
        server=HTTPServer(('127.0.0.1',args.port),LocalHandler)
        print('REVIEW_UI',f'http://127.0.0.1:{args.port}/',flush=True)
        print('REVIEW_ACCESS_TOKEN',token,flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())
