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
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

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


HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>Q-001 Local Human Review</title><style>
body{font:16px system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}
.notice{background:#fff3d5;border:1px solid #c96;padding:1rem}
.row{display:flex;gap:.6rem;align-items:center;margin:1rem 0;flex-wrap:wrap}
input,select,button{font:inherit;padding:.4rem}
#source{white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid #999;
padding:1rem;max-height:320px;overflow:auto;line-height:1.8}
mark.suspect{background:#ffbd6d}mark.masked{background:#c0e4c7}
article{border:1px solid #ccc;padding:.8rem;margin:.7rem 0;border-radius:8px}
</style></head><body><h2>Q-001 · 로컬 검토 대시보드</h2>
<p class="notice">실험 전용. 원문을 외부로 보내지 않습니다. 검토 판단은 로컬 기록만
남기며 자동 가명처리·승인·DB 적재·외부 전송으로 연결되지 않습니다.</p>
<div class="row"><label>터미널 세션 토큰 <input type="password" id="secret" autocomplete="off"></label>
<button id="login">문서 목록 보기</button></div>
<div class="row"><label>대상 문서 <select id="ids"></select></label>
<button id="open">문서 원문 및 후보 확인</button></div>
<p id="status" role="status"></p><h3 id="heading"></h3>
<p>주황색: REVIEW 후보 · 연두색: 기존 보호 구간</p>
<div id="source"></div><div id="candidates"></div>
<script>
const elem=id=>document.getElementById(id);
const node=(tag,text)=>{let x=document.createElement(tag);x.textContent=text;return x};
async function api(url,payload){
const opts={headers:{"X-Local-Review-Token":elem("secret").value.trim()}};
if(payload){opts.method="POST";opts.headers["Content-Type"]="application/json";opts.body=JSON.stringify(payload)}
let r=await fetch(url,opts), data=await r.json();
if(!r.ok)throw Error(data.error||"request_failed");return data
}
function show(doc){
elem("heading").textContent=doc.id+" / Step1 "+doc.step1_status+" / Gate "+doc.gate_reason;
let all=[...doc.candidates,...doc.masks], positions=new Set([0,doc.source.length]);
for(const a of all){positions.add(a.start);positions.add(a.end)}
let cuts=[...positions].sort((a,b)=>a-b), area=elem("source");area.replaceChildren();
for(let i=0;i<cuts.length-1;i++){
let a=cuts[i],b=cuts[i+1];if(a===b)continue;
let hot=doc.candidates.some(x=>x.start<b&&x.end>a);
let masked=doc.masks.some(x=>x.start<b&&x.end>a);
let span=node(hot||masked?"mark":"span",doc.source.slice(a,b));
if(hot)span.className="suspect";else if(masked)span.className="masked";
area.appendChild(span)
}
let out=elem("candidates");out.replaceChildren();
let labels={LONG_NAME:"끝 '이'까지 이름",SHORT_NAME_PLUS_PARTICLE:"짧은 이름 + 조사 '이'",
PERSON_PII:"실제 개인정보(인명)",NON_PERSON_EXPRESSION:"인명이 아닌 표현",
UNRESOLVED:"불확실 — REVIEW 유지"};
doc.candidates.forEach((item,i)=>{
let card=node("article",""),title=node("h4",(i+1)+". "+item.kind+" / "+item.reason);
card.appendChild(title);card.appendChild(node("p","후보: "+doc.source.slice(item.start,item.end)));
if(item.overlapping_labels)card.appendChild(node("p","Step1 겹침: "+item.overlapping_labels.join(",")));
let select=document.createElement("select");
let choices=item.kind==="name_boundary"?["UNRESOLVED","LONG_NAME","SHORT_NAME_PLUS_PARTICLE"]:
["UNRESOLVED","PERSON_PII","NON_PERSON_EXPRESSION"];
for(const ch of choices){let option=document.createElement("option");option.value=ch;option.textContent=labels[ch];select.appendChild(option)}
let button=node("button","판단 기록 (승인 아님)");
button.onclick=async()=>{if(!confirm("검토 판단만 기록합니다. 자동 처리되지 않습니다. 계속할까요?"))return;
try{let v=await api("/api/review",{id:doc.id,candidate:i,choice:select.value});
elem("status").textContent="기록됨: "+v.state}catch(e){elem("status").textContent="오류: "+e.message}};
card.appendChild(select);card.appendChild(button);out.appendChild(card)
})
}
elem("login").onclick=async()=>{try{
let d=await api("/api/cases"),select=elem("ids");select.replaceChildren();
for(const c of d.cases){let o=document.createElement("option");o.value=c.id;o.textContent=c.id+" ("+c.step1_status+")";select.appendChild(o)}
elem("status").textContent="검토 목록 로드 완료"
}catch(e){elem("status").textContent="인증 오류: "+e.message}};
elem("open").onclick=async()=>{try{show(await api("/api/case/"+encodeURIComponent(elem("ids").value)))}
catch(e){elem("status").textContent="조회 오류: "+e.message}};
</script></body></html>"""


def create_handler(cases, token, port, journal):
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
                self.reply(200, {"cases": [{"id": x["id"], "step1_status": x["step1_status"]}
                                           for x in cases.values()]}); return
            if self.path.startswith("/api/case/"):
                did = self.path[len("/api/case/"):]
                if did in cases:
                    self.reply(200, cases[did]); return
            self.reply(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            if not self.valid_origin() or not self.authenticated():
                self.reply(403, {"error": "AUTH_REQUIRED"}); return
            if self.path != "/api/review":
                self.reply(404, {"error": "NOT_FOUND"}); return
            if self.headers.get("Content-Type") != "application/json":
                self.reply(415, {"error": "JSON_REQUIRED"}); return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 < size <= 1024:
                    raise ValueError("BAD_SIZE")
                record = review_record(json.loads(self.rfile.read(size)), cases)
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
    args = ap.parse_args()
    try:
        if not 1024 <= args.port <= 65535 or private_path(args.input) == private_path(args.journal):
            raise ValueError("BAD_CONFIG")
        sources = read_sources(args.input, args.ids)
        from pii_detector import create_detector
        with create_detector() as detector:
            cases = get_cases(sources, detector)
        secret = secrets.token_urlsafe(32)
        server = HTTPServer(("127.0.0.1", args.port),
                            create_handler(cases, secret, args.port, args.journal))
        print("Q001_REVIEW_LOCAL http://127.0.0.1:" + str(args.port), flush=True)
        print("Q001_REVIEW_SESSION_TOKEN " + secret, flush=True)
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
