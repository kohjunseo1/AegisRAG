"""Manual PII protection overlay, local only. No raw PII or tokens in logs.

The overlay is additive: source positions explicitly confirmed by a reviewer
are protected with label-scoped HMAC *before* a new Step1/Privacy Gate run.
It never overrides a detector's refusal, does not unmask model findings, and
is not authorization for external data transfer.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re

from dataset_workflow import _hash, _process
from entity_pseudonymizer import LABELS, make_hmac_token, PseudonymizationError

TOKEN_RE = re.compile(r'<[A-Z_]+_[0-9a-f]{24}>')
SHA = re.compile(r'[0-9a-f]{64}\Z')


def _valid_spans(text: str, edits: list) -> list[dict]:
    if not isinstance(edits, list) or not 1 <= len(edits) <= 60:
        raise ValueError('MANUAL_SPANS_REQUIRED')
    n = len(text)
    spans = []
    for item in edits:
        if not isinstance(item, dict) or set(item) != {'start', 'end', 'label'}:
            raise ValueError('MANUAL_SPAN_SHAPE_INVALID')
        a, b, label = item['start'], item['end'], item['label']
        if (type(a) is not int or type(b) is not int or not 0 <= a < b <= n
            or not isinstance(label, str) or label not in LABELS):
            raise ValueError('MANUAL_SPAN_INVALID')
        if not text[a:b].strip():
            raise ValueError('MANUAL_EMPTY_SPAN')
        spans.append({'start': a, 'end': b, 'label': label})
    spans.sort(key=lambda s: (s['start'], s['end']))
    if any(x['end'] > y['start'] for x, y in zip(spans, spans[1:])):
        raise ValueError('MANUAL_SPANS_OVERLAP')
    return spans


def _existing_candidates(detector, source: str):
    """Read original heads; block partial overlaps or conflicting typed claims.

    On unpredictable detector shapes, fail closed. No detector output values
    or original strings are ever serialized into errors or logs.
    """
    result = detector.detect(source)
    if not isinstance(result, dict):
        raise ValueError('MANUAL_DETECTOR_INVALID')
    status = result.get('status')
    if status not in ('READY_FOR_PSEUDONYMIZATION', 'REVIEW_REQUIRED'):
        raise ValueError('MANUAL_STEP1_NOT_REVIEWABLE')
    n = len(source)
    candidates = []
    for field, items in (
        ('entities', result.get('entities')),
        ('mask_intervals', result.get('mask_intervals')),
        ('ax', detector.ax_predict(source)),
        ('veil', detector.veil_predict(source)),
    ):
        if not isinstance(items, list):
            raise ValueError('MANUAL_CANDIDATES_INVALID')
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('MANUAL_CANDIDATES_INVALID')
            a, b = item.get('start'), item.get('end')
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= n:
                raise ValueError('MANUAL_CANDIDATES_INVALID')
            label = item.get('label')
            if field != 'mask_intervals' and label not in LABELS:
                raise ValueError('MANUAL_CANDIDATES_INVALID')
            candidates.append((a, b, label, field))
    return candidates


def review_manual_spans(stage: dict, did: str, source_sha256: str,
                        edits: list, reviewer: str, detector, key: bytes) -> dict:
    if not isinstance(reviewer, str) or not re.fullmatch(r'[A-Za-z0-9_-]{2,40}', reviewer):
        raise ValueError('INVALID_REVIEWER')
    if not isinstance(source_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        raise ValueError('MANUAL_SOURCE_HASH_INVALID')
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('MANUAL_KEY_INVALID')
    if not isinstance(stage, dict) or not isinstance(stage.get('documents'), list):
        raise ValueError('MANUAL_STAGE_INVALID')
    docs = [(i, d) for i, d in enumerate(stage['documents'])
            if isinstance(d, dict) and d.get('doc_id') == did]
    if len(docs) != 1:
        raise ValueError('UNKNOWN_REVIEW_DOC_ID')
    idx, old = docs[0]
    if old.get('state') not in ('REVIEW_PENDING', 'BLOCKED'):
        raise ValueError('DOC_NOT_PENDING_REVIEW')
    # Don't hide technical failure by preprocessing tokens until the cause is fixed.
    if old.get('reason') in {'SAFE_PIPELINE_FAILURE', 'INVALID_INPUT',
                              'INVALID_DETECTOR_RESULT'}:
        raise ValueError('MANUAL_TECHNICAL_FAILURE_REQUIRES_DIAGNOSIS')
    raw = old.get('raw_for_local_review')
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 100000:
        raise ValueError('MANUAL_SOURCE_INVALID')
    if _hash(raw) != source_sha256 or old.get('source_sha256') != source_sha256:
        raise ValueError('MANUAL_STALE_SOURCE')
    if TOKEN_RE.search(raw):
        raise ValueError('MANUAL_SOURCE_ALREADY_CONTAINS_TOKENS')
    spans = _valid_spans(raw, edits)
    candidates = _existing_candidates(detector, raw)
    # Cannot select part of an independently proposed PII entity. Require the
    # entire candidate to fit in one human span or exclude it completely.
    # Multiple heads disagreeing on types remain REVIEW, not silently overridden.
    for a, b, label, head in candidates:
        intersects = [e for e in spans if e['start'] < b and a < e['end']]
        for e in intersects:
            if not (e['start'] <= a and b <= e['end']):
                raise ValueError('MANUAL_PARTIAL_DETECTOR_OVERLAP')
            if label is not None and label != e['label']:
                raise ValueError('MANUAL_LABEL_CONFLICT_REQUIRES_REVIEW')
    tokens = []
    for e in spans:
        try:
            token, _ = make_hmac_token(key, e['label'], raw[e['start']:e['end']])
        except (PseudonymizationError, ValueError):
            raise ValueError('MANUAL_LABEL_VALUE_INVALID') from None
        tokens.append(token)
    parts, cursor = [], 0
    for e, token in zip(spans, tokens):
        parts.extend((raw[cursor:e['start']], token))
        cursor = e['end']
    parts.append(raw[cursor:])
    prepared_input = ''.join(parts)
    # Crucial: use the unchanged detector, Gate, name policy and pseudonymizer.
    # If they refuse, human approval cannot override them.
    candidate = _process(old['source_id'], did, prepared_input, detector, key)
    if candidate.get('state') != 'READY' or not isinstance(candidate.get('text'), str):
        raise ValueError('MANUAL_RECHECK_NOT_READY')
    final = candidate['text']
    # Every manually authorized token must survive the full preparation; if
    # Step1/Gate mangles/omits one, keep the original document in review.
    for token in set(tokens):
        if final.count(token) != tokens.count(token):
            raise ValueError('MANUAL_TOKEN_MISSING_AFTER_RECHECK')
    # Original detector spans outside the selected regions should not be
    # silently left verbatim after preprocessing has changed the context.
    for a, b, label, head in candidates:
        if any(e['start'] <= a and b <= e['end'] for e in spans):
            continue
        value = raw[a:b]
        if len(value.strip()) >= 3 and value in final:
            raise ValueError('MANUAL_ORIGINAL_CANDIDATE_UNPROTECTED')
    candidate.update(
        approval_state='HUMAN_APPROVED',
        reason='MANUAL_SPANS_RECHECKED',
        source_sha256=source_sha256,
        protected_input_sha256=_hash(prepared_input),
        reviewer=reviewer,
        approved_at_utc=datetime.now(timezone.utc).isoformat(),
        manual_span_overrides=[dict(e) for e in spans],
        manual_review_method='TYPED_HMAC_THEN_FULL_STEP1_GATE',
    )
    stage['documents'][idx] = candidate
    return stage
