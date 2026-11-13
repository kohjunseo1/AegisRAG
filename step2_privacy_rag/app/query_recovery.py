"""Fail-closed, lightweight recovery of one-name document-search questions.

Only invokes the SAME detector/Privacy Gate on a shorter independent name.
No LLM, no dictionary of real names, no Gold labels, no raw PII sent out.
This deliberately does not attempt to resolve homonyms or override other Gate
reasons. Any ambiguity means keep REVIEW.
"""
from __future__ import annotations

import re
from typing import Any

# Anchored Korean name + an allowlisted, non-PII search-intent phrase.
_PATTERNS = (
    (re.compile(r'^([가-힣]{2,4})\s+(?:정보|관련 정보|고객 정보)(?:\s+(?:알려줘|찾아줘|보여줘))?$'), '관련 정보 알려줘'),
    (re.compile(r'^([가-힣]{2,4})\s+(?:자료|관련 자료|관련 문서|문서)(?:\s+(?:알려줘|찾아줘|보여줘))?$'), '관련 자료 찾아줘'),
    (re.compile(r'^([가-힣]{2,4})\s+(?:이름 있는 자료|이름 있는 문서)(?:\s+(?:알려줘|찾아줘|보여줘))?$'), '이름이 포함된 자료 찾아줘'),
)
_PERSON_TOKEN = re.compile(r'<PERSON_[0-9a-f]{24}>\Z')


def recover_simple_name_query(question: str, detector: Any, key: bytes) -> str | None:
    if not isinstance(question,str) or len(question) > 90:
        return None
    normalized = question.strip()
    match = None
    safe_intent = None
    for pattern, intent in _PATTERNS:
        match = pattern.fullmatch(normalized)
        if match:
            safe_intent = intent
            break
    if not match:
        return None
    # Use the original, unmodified detector and full Gate on the isolated name.
    from document_preparation import prepare
    isolated = prepare('Q_NAME_PROBE', match.group(1), detector, key)
    if (isolated.state != 'LOCAL_PREPARED'
            or isolated.unlinked_mentions
            or isolated.region_keys):
        return None
    protected = isolated.sanitized_text
    if not isinstance(protected,str) or not _PERSON_TOKEN.fullmatch(protected.strip()):
        return None
    if len(isolated.tokens) != 1 or isolated.tokens[0] != protected.strip():
        return None
    # Only a validated HMAC token and a static benign intent enter retrieval.
    return protected.strip() + ' ' + safe_intent
