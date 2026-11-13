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
<title>AegisRAG Local Review</title>
<style>
body{font-family:system-ui,'Apple SD Gothic Neo',sans-serif;background:#f6f8fb;color:#182338;margin:0}
main{max-width:1050px;margin:42px auto;padding:0 25px}
h1{font-size:24px}p{line-height:1.6}
.panel{background:#fff;border:1px solid #dbe2eb;padding:22px;margin:17px 0;border-radius:12px}
input,textarea,button{font:inherit;border:1px solid #c9d4e1;border-radius:7px;padding:10px}
input{max-width:100%;min-width:230px}textarea{display:block;width:100%;min-height:250px;box-sizing:border-box;line-height:1.6}
button{cursor:pointer;background:#194e83;color:#fff;border-color:#194e83;margin:7px 5px 7px 0}
button:disabled{opacity:.45;cursor:not-allowed}
.row{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
small,.note{color:#66788f}li{padding:8px;border-bottom:1px solid #eef0f3}
li button{margin:0 10px 0 0}.notice{white-space:pre-wrap;color:#23496f;margin:14px 0}
.warning{border-left:4px solid #c47a20;background:#fff7e8;padding:12px}
code{background:#eef3f8;padding:2px 4px;border-radius:4px}
</style></head><body>
<main><h1>AegisRAG · 로컬 개인정보 검토</h1>
<p class="warning">원문은 이 컴퓨터에서만 표시됩니다. 수정된 문서도 동일 Step1 + Privacy Gate를 통과해야 승인됩니다. 승인된 문서만 수동으로 DB 반영할 수 있습니다.</p>
<section class="panel"><h2>1. 접근 확인</h2>
<div class="row"><input id="token" type="password" autocomplete="off" placeholder="터미널의 리뷰 접근 토큰">
<button id="login">검토 목록 열기</button></div><div id="summary" class="notice"></div></section>
<section class="panel"><h2>2. 검토 대상</h2><ul id="queue"></ul></section>
<section class="panel"><h2>3. 문서 수정 및 재검증</h2>
<p class="note">대상 원문을 읽고 미보호 개인정보를 삭제하거나 비식별 처리하세요. Gate가 통과하지 않으면 승인되지 않습니다.</p>
<p id="selected">검토 대상을 선택해 주세요.</p>
<textarea id="revision" spellcheck="false" placeholder="로컬 원문을 불러와 수정합니다."></textarea>
<div class="row"><input id="reviewer" placeholder="검토자 ID (영문/숫자)"><button id="approve">수정 원문 재검증 및 승인</button></div>
</section>
<section class="panel"><h2>4. 승인된 문서 증분 적재</h2>
<p class="note">현재 READY 문서만 대상으로 합니다. 기존 문서는 재임베딩하지 않고, 새/변경 문서만 반영합니다.</p>
<input id="confirm" placeholder="rag.aegis_live_docs" autocomplete="off">
<button id="commit">승인 문서 DB 반영</button>
</section><div id="notice" class="notice"></div>
</main>
<script>
"use strict";
let key="",current="";
const el=id=>document.getElementById(id);
const report=s=>{el("notice").textContent=s};
async function call(path,method="GET",body=null){
 const headers={"X-Aegis-Review-Token":key};
 if(body!==null)headers["Content-Type"]="application/json";
 const resp=await fetch(path,{method,headers,cache:"no-store",
   body:body===null?null:JSON.stringify(body),credentials:"omit",redirect:"error"});
 const json=await resp.json();
 if(!resp.ok)throw Error(json.error||"REQUEST_FAILED");
 return json;
}
async function refresh(){
 const data=await call("/api/queue");
 el("summary").textContent="데이터셋: "+data.dataset+" · 전체 "+data.total+
   " · READY "+data.ready+" · REVIEW "+data.review_pending+" · BLOCKED "+data.blocked;
 const list=el("queue");list.replaceChildren();
 for(const d of data.documents){
  if(d.state==="READY")continue;
  const li=document.createElement("li"),btn=document.createElement("button");
  btn.textContent="검토";btn.onclick=()=>openDoc(d.doc_id);
  li.appendChild(btn);li.appendChild(document.createTextNode(d.doc_id+" · "+d.state+" · "+d.reason));
  list.appendChild(li);
 }
 if(!list.children.length)list.textContent="검토 대상 없음";
}
async function openDoc(id){
 try{
  const d=await call("/api/document?id="+encodeURIComponent(id));
  current=id;el("selected").textContent="현재 문서: "+id;
  el("revision").value=d.raw_for_local_review;
  report("원문을 확인한 뒤 필요한 부분을 수정하고 재검증하세요.");
 }catch(err){report("문서 조회 오류: "+err.message)}
}
el("login").onclick=async()=>{
 key=el("token").value.trim();
 if(!key){report("토큰을 입력하세요.");return}
 try{await refresh();report("접근 확인 완료.");el("token").value=""}
 catch(err){key="";report("인증/조회 실패: "+err.message)}
};
el("approve").onclick=async()=>{
 if(!current){report("검토할 문서를 선택하세요.");return}
 try{
  const ans=await call("/api/review","POST",{
   doc_id:current,revised_source:el("revision").value,reviewer:el("reviewer").value
  });
  report("재검증 승인 완료: "+ans.doc_id);
  current="";el("revision").value="";el("selected").textContent="검토 대상 선택";
  await refresh();
 }catch(err){report("검토 보류: "+err.message)}
};
el("commit").onclick=async()=>{
 if(el("confirm").value.trim()!=="rag.aegis_live_docs"){
  report("정확한 테이블명 rag.aegis_live_docs를 입력해 주세요.");return;
 }
 if(!window.confirm("READY/승인 문서만 현재 Live DB에 반영할까요?"))return;
 try{
  const ans=await call("/api/commit","POST",{confirm:"rag.aegis_live_docs"});
  report("DB 반영: "+JSON.stringify(ans));
  await refresh();
 }catch(err){report("DB 반영 실패: "+err.message)}
};
</script></body></html>"""


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
            if route=='/api/document':
                did=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
                matches=[d for d in stage['documents'] if d['doc_id']==did and d['state'] in ('REVIEW_PENDING','BLOCKED')]
                if len(matches)!=1:
                    return self._send(404,{'error':'DOC_NOT_PENDING'})
                d=matches[0]
                return self._send(200,{'doc_id':did,'raw_for_local_review':d['raw_for_local_review']})
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
