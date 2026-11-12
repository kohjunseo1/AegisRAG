"use strict";
const $ = (id) => document.getElementById(id);
const thread = $("thread"), chat = $("chat"), question = $("question");
const send = $("send");
const modePill = $("mode-pill"), status = $("status");
let busy = false;
let composing = false;
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = String(text);
  return node;
};
const scrollBottom = () => { chat.scrollTop = chat.scrollHeight; };
function addMessage(role, text, result = null) {
  const outer = el("div", "message " + role);
  if (role === "user") {
    outer.appendChild(el("div", "bubble", text));
  } else {
    outer.appendChild(el("div", "avatar", "A"));
    const box = el("div", "content");
    box.appendChild(el("div", "answer", text));
    if (result) {
      const badges = el("div", "badges");
      badges.appendChild(el("span", "status-badge", result.decision));
      badges.appendChild(el("span", "status-badge", result.mode === "hcx" ? "HCX-007" : "로컬 검색"));
      box.appendChild(badges);
      const sources = (result.sources || []).filter(d => (result.citations || []).includes(d.doc_id));
      if (sources.length) {
        const details = el("details", "sources");
        details.appendChild(el("summary", "", "인용한 문서 " + sources.length + "건 보기"));
        for (const d of sources) {
          const item = el("div", "source");
          item.appendChild(el("strong", "", d.doc_id + (result.citations.includes(d.doc_id) ? " · 인용됨" : "")));
          item.appendChild(el("p", "", d.masked_text));
          details.appendChild(item);
        }
        box.appendChild(details);
      }
      if (result.diagnostics) {
        const d = result.diagnostics;
        const items = ["Gate: " + d.gate_state];
        if (typeof d.hmac_token_count === "number") items.push("HMAC tokens: " + d.hmac_token_count);
        if (typeof d.region_key_count === "number") items.push("Region keys: " + d.region_key_count);
        if (typeof d.hcx_input_tokens === "number") items.push("입력 토큰: " + d.hcx_input_tokens);
        if (d.timings_ms) items.push("지연(ms): " + JSON.stringify(d.timings_ms));
        const debug = el("details", "usage");
        debug.appendChild(el("summary", "", "개발자 상세"));
        debug.appendChild(el("p", "", items.join(" · ")));
        box.appendChild(debug);
      }
    }
    outer.appendChild(box);
  }
  thread.appendChild(outer);
  scrollBottom();
}
function explainError(code) {
  if (code === "HCX_HTTP_401" || code === "HCX_HTTP_403") return "답변 생성 API 인증 또는 접근 권한을 확인해 주세요.";
  if (code === "HCX_HTTP_400" || code === "HCX_HTTP_422") return "답변 생성 API가 요청 형식을 거부했습니다. 서버 설정을 확인해 주세요.";
  if (code === "HCX_HTTP_429") return "답변 생성 API의 호출 한도에 도달했습니다.";
  if (code === "HCX_HTTP_5XX" || code === "HCX_TIMEOUT" || code === "HCX_CONNECTION_FAILED") return "답변 생성 API 연결이 불안정합니다.";
  if (code.startsWith("HCX_")) return "문서 검색 이후 답변 생성 단계에서 실패했습니다.";
  if (code === "V6_DB_TABLE_NOT_FOUND") return "지정한 검색 테이블이 없습니다. AEGISRAG_TABLE 값을 확인해 주세요.";
  if (code === "V6_DB_ACCESS_DENIED") return "검색 테이블에 대한 DB 사용자 권한이 없습니다.";
  if (code === "V6_DB_AUTH_FAILED") return "DB 사용자명 또는 비밀번호를 확인해 주세요.";
  if (code === "V6_DB_CONNECTION_FAILED") return "PostgreSQL 서버 또는 접속 포트를 확인해 주세요.";
  if (code === "V6_DB_DATABASE_NOT_FOUND") return "설정한 데이터베이스가 없습니다.";
  if (code === "V6_DB_SCHEMA_MISMATCH") return "검색 테이블 구조 또는 pgvector 확장을 확인해 주세요.";
  if (code.startsWith("V6_DB_")) return "문서 검색 인덱스의 연결 또는 쿼리 설정을 확인해 주세요.";
  return "요청을 처리하지 못했습니다.";
}
async function ask() {
  const value = question.value.trim();
  if (!value || busy) return;
  busy = true; send.disabled = true;
  $("reset").disabled = true;
  $("welcome").style.display = "none";
  question.value = "";
  addMessage("user", value);
  status.textContent = "문서를 확인하고 답변을 준비하고 있습니다…";
  try {
    const response = await fetch("/api/chat", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: value}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "REQUEST_FAILED");
    addMessage("assistant", result.answer, result);
    status.textContent = "완료";
  } catch (err) {
    const code = String(err.message || "REQUEST_FAILED");
    addMessage("assistant", explainError(code) + " (" + code + ")");
    status.textContent = "요청 실패";
  } finally {
    busy = false; send.disabled = false; $("reset").disabled = false; question.focus(); scrollBottom();
  }
}
question.addEventListener("compositionstart", () => { composing = true; });
question.addEventListener("compositionend", () => { composing = false; });
// Blurring before a click-submit commits an active Korean IME composition.
send.addEventListener("click", () => {
  if (composing) {
    question.blur();
    Promise.resolve().then(ask);
  } else {
    ask();
  }
});
question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    // The first Enter completes Hangul composition; the next Enter sends.
    if (event.isComposing || composing || event.keyCode === 229) return;
    event.preventDefault();
    ask();
  }
});
$("reset").addEventListener("click", () => { thread.replaceChildren(); $("welcome").style.display = "block"; question.value = ""; status.textContent = "화면을 비웠습니다 · 각 질문은 독립 처리"; });
document.querySelectorAll(".sample").forEach(button => button.addEventListener("click", () => { question.value = button.textContent; question.focus(); }));
fetch("/api/health").then(r => r.json()).then(d => {
  modePill.textContent = d.external_hcx_enabled ? "문서 상담" : "서버 설정 확인";
  status.textContent = d.external_hcx_enabled ? "질문을 입력해 주세요" : "HCX 서비스 비활성";
  return fetch("/api/index-status").then(async response => {
    const index = await response.json();
    if (!response.ok) throw new Error(index.error || "INDEX_STATUS_FAILED");
    if (index.status === "READY") {
      modePill.textContent = "문서 상담 · " + index.rows + "건";
      status.textContent = "검색 인덱스: " + index.table + " (" + index.rows + "/" + index.expected_rows + "건)";
    } else {
      modePill.textContent = "DB 설정 확인";
      status.textContent = "인덱스 불일치: " + index.table + " (" + index.rows + "/" + index.expected_rows + "건, 키 일치=" + index.key_matches + ")";
    }
  });
}).catch(() => { status.textContent = "검색 인덱스 연결 또는 상태 확인 실패"; });
