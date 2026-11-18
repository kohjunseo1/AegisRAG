"""Experiment-only, one-call evidence qualification + answer; never used in production.

Keeps HCX model, temperature, max output budget, original evidence and transport
safeguards. Adds a structured document assessment to test evidence selection.
No Gold labels or QID-specific examples enter the prompt. Rationale is NOT
claimed to reveal model internals. All quotes stay local, never in stdout.
"""
from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values
import hcx

VERDICTS = ("SUPPORTS", "WRONG_TASK", "WRONG_ENTITY", "WRONG_TIME", "INSUFFICIENT")
SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_review": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "supporting_quote": {"type": "string"},
                },
                "required": ["doc_id", "verdict", "supporting_quote"],
                "additionalProperties": False,
            },
        },
        "decision": {"type": "string",
                     "enum": ["ANSWER", "CLARIFY", "INSUFFICIENT_EVIDENCE"]},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["evidence_review", "decision", "answer", "citations"],
    "additionalProperties": False,
}
AUDIT_GUIDANCE = """
[실험 전용 문서별 근거 평가 — 내부 사고과정 설명은 요구하지 않습니다]
먼저 documents의 모든 doc_id에 대해 evidence_review 항목을 정확히 1개씩 작성하세요.
문서마다 질문의 대상·업무/사건 유형·시점/상태 조건을 각각 확인하세요.
SUPPORTS는 해당 질문의 핵심 사실을 직접 뒷받침하는 경우만 선택합니다.
문서에 동일한 고객명·HMAC·일반 키워드가 등장하더라도, 질문에서 요구한
대상 및 사건과 실제 문서 내용이 다르면 SUPPORTS로 표시하지 마세요.
WRONG_TASK는 업무·사건이 다를 때, WRONG_ENTITY는 대상이 다를 때,
WRONG_TIME은 필요한 시점·상태가 다를 때, INSUFFICIENT는 문서 안에
질문에 필요한 사실이 없거나 판단할 수 없을 때 사용하세요.
SUPPORTS인 경우 supporting_quote에 근거 문장 중 120자 이하의 정확한 원문
일부분을 그대로 복사하세요(새 내용을 만들어내면 안 됩니다).
다른 판정에는 supporting_quote를 빈 문자열로 반환하세요.
답변은 SUPPORTS로 표시한 문서만 사실 근거로 삼고, citations에도 그 문서
ID만 담으세요. 어떤 문서도 직접 근거를 제공하지 않을 때만 근거 부족을
선택하세요. evidence_review는 진단용이며 사용자 답변에 노출하지 마세요.
"""

def _review_checks(review: object, documents: list[dict]) -> list[dict]:
    """Validate IDs/structure; return only non-sensitive checks, not quote text."""
    if not isinstance(review, list) or len(review) != len(documents):
        raise hcx.OutboundError("AUDIT_REVIEW_COUNT_INVALID")
    indexed = {d["doc_id"]: d["text"] for d in documents}
    seen = set()
    safe = []
    for item in review:
        if not isinstance(item, dict) or set(item) != {
            "doc_id", "verdict", "supporting_quote"
        }:
            raise hcx.OutboundError("AUDIT_REVIEW_FIELDS_INVALID")
        did, verdict, quote = item["doc_id"], item["verdict"], item["supporting_quote"]
        if (not isinstance(did, str) or did not in indexed or did in seen
                or verdict not in VERDICTS or not isinstance(quote, str)):
            raise hcx.OutboundError("AUDIT_REVIEW_INVALID")
        seen.add(did)
        correct_quote = bool(quote) and len(quote) <= 120 and quote in indexed[did]
        safe.append({
            "doc_id": did,
            "verdict": verdict,
            "quote_valid": correct_quote if verdict == "SUPPORTS" else None,
            "quote_present": bool(quote),
        })
    return safe

def _checked_response(raw: object, documents: list[dict], has_region: bool) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(SCHEMA["required"]):
        raise hcx.OutboundError("AUDIT_RESPONSE_FIELDS_INVALID")
    checks = _review_checks(raw["evidence_review"], documents)
    base = hcx.validate_response({
        "decision": raw["decision"],
        "answer": raw["answer"],
        "citations": raw["citations"],
    }, documents, has_region)
    supporting = {x["doc_id"] for x in checks if x["verdict"] == "SUPPORTS"}
    # Log mismatch for assessment; do not claim the model's own verdict is truth.
    base["evidence_audit"] = checks
    base["citation_verdict_consistent"] = set(base["citations"]).issubset(supporting)
    return base

def call_hcx_evidence_audit(root: Path, prepared_question: str,
                            evidence: list[dict], has_region: bool
                            ) -> tuple[dict, dict, dict]:
    """One experimental HCX API call. No changes to live hcx.call_hcx."""
    if os.environ.get("AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED") != "YES":
        raise hcx.OutboundError("EXTERNAL_TRANSFER_NOT_APPROVED")
    env = dotenv_values(root / ".env")
    key = os.environ.get("LLM_API_KEY") or env.get("LLM_API_KEY")
    base = (os.environ.get("LLM_API_BASE") or env.get("LLM_API_BASE") or "").rstrip("/")
    if not key or base != hcx.BASE:
        raise hcx.OutboundError("INVALID_LLM_SETTINGS")
    question, documents, redactions = hcx.validate_outbound(prepared_question, evidence)
    payload = {
        "model": "HCX-007",
        "messages": [
            {"role": "system", "content": hcx.SYSTEM.replace(
                "decision, answer, citations를 갖는 JSON 객체만 반환하세요.",
                "decision, answer, citations, evidence_review를 갖는 JSON 객체만 반환하세요."
            ) + AUDIT_GUIDANCE},
            {"role": "user", "content": json.dumps({
                "question": question, "documents": documents,
            }, ensure_ascii=False)},
        ],
        "temperature": 0,
        "reasoning_effort": "none",
        "stream": False,
        "max_completion_tokens": 1024,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "aegis_audit_v1", "schema": SCHEMA},
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=encoded,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        if status == 429:
            details = hcx._safe_429_diagnostics(
                exc, request_bytes=len(encoded), question_chars=len(question),
                document_chars=sum(len(d["text"]) for d in documents),
                document_count=len(documents),
                max_completion_tokens=payload["max_completion_tokens"],
            )
            raise hcx.OutboundError("HCX_HTTP_429", diagnostics=details) from None
        category = (str(status) if status in
                    {400, 401, 403, 404, 408, 413, 422} else
                    "5XX" if 500 <= status < 600 else "OTHER")
        raise hcx.OutboundError("HCX_HTTP_" + category) from None
    except (TimeoutError, socket.timeout):
        raise hcx.OutboundError("HCX_TIMEOUT") from None
    except urllib.error.URLError:
        raise hcx.OutboundError("HCX_CONNECTION_FAILED") from None
    except Exception:
        raise hcx.OutboundError("HCX_API_REQUEST_FAILED") from None

    choice = (reply.get("choices") or [None])[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise hcx.OutboundError("HCX_INCOMPLETE")
    content = (choice.get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise hcx.OutboundError("HCX_EMPTY_CONTENT")
    try:
        raw = json.loads(content)
    except (ValueError, TypeError):
        raise hcx.OutboundError("HCX_INVALID_JSON") from None
    return _checked_response(raw, documents, has_region), reply.get("usage") or {}, redactions
