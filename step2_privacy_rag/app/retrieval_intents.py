"""Safe, local, rule-based intent synonyms for Korean document-type retrieval.

This is a broad vocabulary for document categories, not a learned classifier,
Gold-label lookup or query-time external LLM call. Only redacted document text
is inspected. Never use this score to weaken HMAC or region precedence.
"""
from __future__ import annotations

# General Korean office terminology. Avoid identifiers or test doc IDs here.
_GROUPS = (
    (
        ("경조사", "경조 관련", "경조 공지", "부고", "조문", "장례", "혼사", "결혼식", "축의"),
        ("부고", "별세", "발인", "빈소", "조문", "장례", "모친상", "부친상",
         "장인상", "장모상", "결혼", "혼사", "축하", "경조사"),
    ),
    (
        ("복리후생", "복지제도", "사내복지", "직원 복지", "복지 공지"),
        ("복리후생", "복지", "복지포인트", "건강검진", "경조금", "복지제도"),
    ),
    (
        ("출장비", "출장 경비", "출장비 신청", "여비", "교통비 정산", "출장 정산"),
        ("출장비", "여비", "교통비", "숙박비", "출장 정산", "출장 경비", "출장 신청"),
    ),
    (
        ("휴가", "연차", "휴직", "근태"),
        ("휴가", "연차", "휴직", "근태", "반차", "휴가 신청"),
    ),
    (
        ("카드 발급", "발급 심사", "신규 카드"),
        ("카드 발급", "발급 심사", "신규 발급", "발급 카드"),
    ),
    (
        ("교육 안내", "사내 교육", "보안교육", "교육 일정", "연수 안내"),
        ("교육", "연수", "교육 일정", "보안 교육", "교육 안내"),
    ),
)


def topic_score(query: str, masked_document: str) -> int:
    """Compute a bounded relevance boost using sanitized textual evidence.

    Heading match gets priority over body match; an unrelated document gets 0.
    Tokens/region must still rank ahead of any semantic topic score.
    """
    if not query or not masked_document:
        return 0
    query = query.lower()
    title, _, body = masked_document.partition("\n")
    title = title[:200].lower()
    body = body.lower()
    best = 0
    for triggers, signals in _GROUPS:
        if not any(term in query for term in triggers):
            continue
        score = 0
        if any(term in title for term in signals):
            score += 3
        if any(term in body for term in signals):
            score += 1
        best = max(best, score)
    return best
