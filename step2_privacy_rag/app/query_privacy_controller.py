"""Zero-cost local-only chat disposition for unresolved privacy and service cases.

Every state maps to a deterministic user message without calling HCX.
The reason is an allowlisted code produced locally; no raw input, spans, HMACs
or source document data are used to formulate these messages.
This never changes Gate approval or authorizes an unsafe retry.
"""
from __future__ import annotations

# Broad, reusable categories. Not Holdout IDs, input names or dataset strings.
_PERSON_REVIEW = (
    '인물 이름의 범위를 안전하게 확인하지 못했습니다. '
    '이름이나 식별번호 대신 문서 종류와 업무 주제를 중심으로 '
    '다시 질문해 주시겠어요?'
)
_ADDRESS_REVIEW = (
    '주소의 일부를 안전하게 보호할 수 없어 검색을 진행하지 않았습니다. '
    '상세주소는 제외하고 지역이나 관련 업무를 중심으로 다시 질문해 주시겠어요?'
)
_GENERIC_REVIEW = (
    '질문의 개인정보를 안전하게 보호할 수 없어 검색을 진행하지 않았습니다. '
    '개인정보 대신 문서 종류나 업무 내용을 알려주시겠어요?'
)
_NUMBER_FAILURE = (
    '입력된 숫자 식별자를 안전하게 가명처리하지 못했습니다. '
    '번호를 반복해서 입력하기보다는 문서 종류나 관련 업무로 검색해 주세요.'
)
_GENERIC_BLOCK = (
    '현재 입력을 안전하게 처리할 수 없습니다. '
    '개인정보나 식별번호를 제외하고 문서 종류 또는 업무 주제로 질문해 주세요.'
)
_TEMPORARY_SERVICE = (
    '검색은 완료했지만 답변 생성 서비스에 일시적인 문제가 발생했습니다. '
    '잠시 후 같은 질문으로 다시 시도해 주세요.'
)


def unresolved_query_response(gate_state: str, reason_code: str | None = None) -> dict[str, str]:
    """Fail closed, giving a useful hint without any manual per-query queue."""
    if gate_state in {'REVIEW', 'REVIEW_REQUIRED'}:
        message = {
            'unresolved_veil_person': _PERSON_REVIEW,
            'unprotected_ax_address': _ADDRESS_REVIEW,
        }.get(reason_code if isinstance(reason_code, str) else None, _GENERIC_REVIEW)
        return {'decision':'CLARIFY', 'answer':message}
    if reason_code in {'invalid_number_value','invalid_phone_value',
                       'invalid_account_value'}:
        return {'decision':'BLOCKED_OR_DEFERRED','answer':_NUMBER_FAILURE}
    return {'decision':'BLOCKED_OR_DEFERRED','answer':_GENERIC_BLOCK}


def insufficient_evidence_response() -> dict[str,str]:
    return {
        'decision':'INSUFFICIENT_EVIDENCE',
        'answer':(
            '현재 검색된 문서만으로는 요청하신 내용을 확인하기 어렵습니다. '
            '문서 종류나 업무 주제를 더 알려주세요.'
        ),
    }


def temporary_generation_failure_response() -> dict[str,str]:
    """A provider failure is not a privacy block and not missing evidence."""
    return {'decision':'TEMPORARY_ERROR','answer':_TEMPORARY_SERVICE}
