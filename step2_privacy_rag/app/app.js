"use strict";
const $ = (id) => document.getElementById(id);
const thread = $("thread"), chat = $("chat"), question = $("question");
const send = $("send");
const modePill = $("mode-pill"), status = $("status");
let busy = false;
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
        if (d.review_scope === "QUERY_PREPARATION") items.push("질문 단계 REVIEW · 문서 검토와 별개");
        if (typeof d.hmac_token_count === "number") items.push("HMAC tokens: " + d.hmac_token_count);
        if (typeof d.region_key_count === "number") items.push("Region keys: " + d.region_key_count);
        if (typeof d.db_index_rows === "number") items.push("현재 인덱스: " + d.db_index_rows + "문서");
        if (typeof d.retrieved_count === "number") items.push("검색 Top-5 문서: " + d.retrieved_count + "건");
        if (typeof d.retrieved_with_hmac_match === "number") items.push("Top-5 중 HMAC 일치: " + d.retrieved_with_hmac_match + "건");
        if (Array.isArray(d.retrieved_doc_ids)) items.push("검색 문서 ID: " + d.retrieved_doc_ids.join(", "));
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
    addMessage("assistant", "요청을 처리하지 못했습니다. (" + String(err.message) + ")");
    status.textContent = "요청 실패";
  } finally {
    busy = false; send.disabled = false; $("reset").disabled = false; question.focus(); scrollBottom();
  }
}
send.addEventListener("click", ask);
question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(); }
});
$("reset").addEventListener("click", () => { thread.replaceChildren(); $("welcome").style.display = "block"; question.value = ""; status.textContent = "화면을 비웠습니다 · 각 질문은 독립 처리"; });
document.querySelectorAll(".sample").forEach(button => button.addEventListener("click", () => { question.value = button.textContent; question.focus(); }));
fetch("/api/health").then(r => r.json()).then(d => {
  const index = typeof d.expected_index_rows === "number" ? " · " + d.expected_index_rows + "문서 인덱스" : "";
  modePill.textContent = d.external_hcx_enabled ? "문서 상담" + index : "서버 설정 확인";
  status.textContent = d.external_hcx_enabled ? "질문을 입력해 주세요" + index : "HCX 서비스 비활성";
}).catch(() => { status.textContent = "서버 연결 확인 실패"; });
