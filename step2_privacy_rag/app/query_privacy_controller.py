"""Local-only disposition of unresolved or evidence-free chat queries.

This policy does not change Step1, Privacy Gate, HMAC, document approvals, or
homonym resolution. Its responses contain no original question or document text.
"""
from __future__ import annotations


def unresolved_query_response(gate_state: str) -> dict[str, str]:
    """Map a failed local Gate decision to a safe user-facing decision."""
    if gate_state in {'REVIEW', 'REVIEW_REQUIRED'}:
        return {
            'decision': 'CLARIFY',
            'answer': (
                '질문의 개인정보나 대상을 안전하게 확인하지 못했습니다. '
                '찾으려는 문서 종류나 업무 주제를 더 구체적으로 알려주시겠어요?'
            ),
        }
    # BLOCKED or unknown states fail closed, never becoming CLARIFY/READY.
    return {
        'decision': 'BLOCKED_OR_DEFERRED',
        'answer': '현재 질문을 안전하게 처리할 수 없습니다. 개인정보를 제외하고 다시 질문해 주세요.',
    }


def insufficient_evidence_response() -> dict[str, str]:
    """Do not invoke an external model if no approved evidence is available."""
    return {
        'decision': 'INSUFFICIENT_EVIDENCE',
        'answer': '현재 검색된 문서만으로는 요청하신 내용을 확인하기 어렵습니다. 문서 종류나 업무 주제를 더 알려주세요.',
    }
