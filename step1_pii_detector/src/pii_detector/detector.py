"""STEP 06B: Korean financial PII detector, frozen two-encoder fusion and conservative boundary validation.

No external connection, dataset, or pretrained weights are bundled here.
Model backends are injected as callables returning [{'label', 'start', 'end'}] relative to the chunk.
External API transfer is outside this module: never send unreviewed or unmasked text externally.
"""
from __future__ import annotations

import re
import time
import threading
import logging
import traceback
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable

LABELS = (
    'person','phone_number','email','address','resident_registration_number',
    'account_number','card_number',
)
FROZEN_OWNER = {lab: ('veil' if lab == 'address' else 'ax') for lab in LABELS}

ADDRESS_HEAD = re.compile(
    r'^(?:서울(?:특별시)?|부산(?:광역시)?|대구(?:광역시)?|인천(?:광역시)?|'
    r'광주(?:광역시)?|대전(?:광역시)?|울산(?:광역시)?|세종(?:특별자치시)?|'
    r'경기(?:도)?|강원(?:특별자치도|도)?|충북|충남|전북|전남|경북|경남|'
    r'제주(?:특별자치도|도)?|충청북도|충청남도|전라북도|전라남도|'
    r'경상북도|경상남도)'
)
PAREN_PREFIX = re.compile(r'(?<![A-Za-z0-9])\([0-9]{5}\)[ \t]{0,3}$')
BARE_PREFIX = re.compile(r'(?<![A-Za-z0-9])(?P<code>[0-9]{5})[ \t]{1,3}$')
NON_POSTAL_FIELD = re.compile(r'(?:상품|고객|접수|거래|주문|계약|카드|송장|사업자)\s*번호\s*[:：]?\s*$')
POSTAL_FIELD = re.compile(r'(?:우편번호|우편\s*코드|postal\s*code)\s*[:：#-]?\s*$', re.I)
ADDRESS_FIELD = re.compile(r'(?:배송지|주소|소재지|거주지|수취인\s*주소)\s*[:：]\s*$')
TRAILING_POSTAL = re.compile(r'^[ \t]{1,3}(?:(?:우편번호|우편\s*코드)\s*[:：]?\s*)?(?:\([0-9]{5}\)|[0-9]{5})(?![0-9])')
ROLE_CUE = re.compile(r'(?:심사역|담당자|직원|상담사|담당|고객)\s*$')
NEXT_CONTEXT = re.compile(r'^\s+(?:조건|문의|안내|처리|확인|신청|완료|등록|동의|심사)')


def span_key(e):
    return (str(e['label']), int(e['start']), int(e['end']))


def overlaps(a, b):
    return int(a['start']) < int(b['end']) and int(b['start']) < int(a['end'])


def normalize_spans(spans, text):
    seen = set()
    out = []
    for e in spans:
        lab, start, end = span_key(e)
        if lab not in LABELS or not (0 <= start < end <= len(text)):
            raise ValueError('invalid_label_or_offset')
        k = (lab, start, end)
        if k not in seen:
            seen.add(k)
            out.append({'label': lab, 'start': start, 'end': end})
    return sorted(out, key=lambda e: (e['start'], e['end'], e['label']))


def fuse_labelwise(ax, veil):
    """Replicate the STEP 05 frozen routing/tie-break (6 A.X types, address Veil)."""
    rows = []
    for source, predictions in (('ax', ax), ('veil', veil)):
        rows.extend(dict(e, source=source) for e in predictions if FROZEN_OWNER[e['label']] == source)
    # Exact duplicates are already disambiguated by the selected source, but retain stable ordering.
    rows.sort(key=lambda e: (-int(e['source'] == FROZEN_OWNER[e['label']]),
                             -(e['end'] - e['start']), e['start'], e['end'],
                             e['source'], e['label']))
    kept = []
    collisions = []
    for e in rows:
        block = next((x for x in kept if overlaps(x, e)), None)
        if block is None:
            kept.append(e)
        else:
            collisions.append({'reason': 'overlap', 'dropped_label': e['label'],
                               'kept_label': block['label']})
    output = []
    seen = set()
    for e in kept:
        k = span_key(e)
        if k not in seen:
            seen.add(k)
            output.append({'label': e['label'], 'start': e['start'], 'end': e['end']})
    return sorted(output, key=lambda e: (e['start'], e['end'], e['label'])), collisions


def validate_boundaries(text, predictions):
    """Conservative postal span expansion. Uncertain strings generate review events, not auto-masking.

    Prefixed parenthesized 5 digits: apply with negative-field / overlap guards.
    Bare 5 digits before address: apply only with an explicit postal or address field.
    Trailing postcodes: review only, because appending them to an ADDRESS span can mask field labels.
    Name ending '이': review only. Never automatically remove a character from a PII span.
    """
    out = normalize_spans(predictions, text)
    events = []
    for i, e in enumerate(out):
        if e['label'] != 'address':
            continue
        a, b = e['start'], e['end']
        if not ADDRESS_HEAD.match(text[a:b]):
            continue
        before = text[max(0, a - 60):a]
        m = PAREN_PREFIX.search(before)
        mode = 'parenthesized' if m else None
        if m is None:
            m = BARE_PREFIX.search(before)
            if m is not None:
                mode = 'bare'
        if m is not None:
            new_a = max(0, a - 60) + m.start()
            earlier = text[max(0, new_a - 35):new_a]
            if NON_POSTAL_FIELD.search(earlier):
                events.append({'rule': 'postal', 'status': 'rejected_nonpostal_field',
                               'start': new_a, 'end': a})
            else:
                field_ok = bool(POSTAL_FIELD.search(earlier) or ADDRESS_FIELD.search(earlier))
                if mode == 'bare' and not field_ok:
                    events.append({'rule': 'postal', 'status': 'review_unlabeled_prefix',
                                   'start': new_a, 'end': a})
                else:
                    candidate = dict(e, start=new_a)
                    if any(overlaps(candidate, other) for j, other in enumerate(out) if j != i):
                        events.append({'rule': 'postal', 'status': 'review_overlap',
                                       'start': new_a, 'end': a})
                    else:
                        out[i] = candidate
                        events.append({'rule': 'postal', 'status': 'applied_' + mode,
                                       'old_start': a, 'start': new_a, 'end': b})
        # At end of a model-recognized address, a five-digit candidate is not blindly attached.
        after = text[b:min(len(text), b + 40)]
        suffix = TRAILING_POSTAL.match(after)
        if suffix is not None:
            events.append({'rule': 'postal', 'status': 'review_trailing_postcode',
                           'start': b + suffix.start(), 'end': b + suffix.end()})
    for e in out:
        if e['label'] != 'person':
            continue
        a, b = e['start'], e['end']
        name = text[a:b]
        if not (3 <= len(name) <= 5 and name.endswith('이')
                and len(name) >= 2 and '가' <= name[-2] <= '힣'
                and (ord(name[-2]) - 0xAC00) % 28 != 0):
            continue
        events.append({'rule': 'name_suffix', 'status': 'review_only',
                       'start': a, 'end': b,
                       'role_cue': bool(ROLE_CUE.search(text[max(0, a-16):a])),
                       'next_cue': bool(NEXT_CONTEXT.match(text[b:b+25]))})
    return normalize_spans(out, text), events


def chunk_windows(text, core_chars=140, context_chars=90):
    """Each window includes 90 chars of side context around a disjoint 140-char core."""
    n = len(text)
    if n == 0:
        return []
    if n <= core_chars + 2 * context_chars:
        return [{'window_start': 0, 'window_end': n, 'core_start': 0, 'core_end': n,
                 'text': text}]
    out = []
    for core_start in range(0, n, core_chars):
        core_end = min(n, core_start + core_chars)
        window_start = max(0, core_start - context_chars)
        window_end = min(n, core_end + context_chars)
        out.append({'window_start': window_start, 'window_end': window_end,
                    'core_start': core_start, 'core_end': core_end,
                    'text': text[window_start:window_end]})
    return out


class PIIDetector:
    """Two-encoder inference with deterministic routing, review signals and fail-closed errors.

    detect(text) never releases raw text. All content still requires pseudonymization BEFORE external APIs.
    The detector does NOT guarantee there are no entirely undetected PII items.
    """
    def __init__(self, ax_predict: Callable, veil_predict: Callable,
                 token_guard: Callable | None = None, parallel=True,
                 max_chars=20000, max_windows=150, timeout_seconds=60):
        self.ax_predict = ax_predict
        self.veil_predict = veil_predict
        self.token_guard = token_guard
        self.parallel = parallel
        self.max_chars = max_chars
        self.max_windows = max_windows
        self.timeout_seconds = timeout_seconds
        self._pool = ThreadPoolExecutor(max_workers=2) if parallel else None
        # Thread-safe at the REQUEST level; per-request AX/Veil execution remains parallel.
        self._request_lock = threading.RLock()
        self._closed = False
        self._faulted = False
        self._logger = logging.getLogger('pii_detector')

    def close(self):
        with self._request_lock:
            self._closed = True
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _blocked(reason, elapsed_ms=0):
        return {'status': 'BLOCKED', 'entities': [], 'review_required': True,
                'issues': [{'code': reason}], 'elapsed_ms': round(elapsed_ms, 2),
                'windows': 0}

    def _predict_all_windows(self, fn, windows, doc):
        result = []
        border_warning = []
        for w in windows:
            rows = normalize_spans(fn(w['text']), w['text'])
            shift = w['window_start']
            for e in rows:
                s, en = shift+e['start'], shift+e['end']
                mid = s + (en-s-1)//2
                # each entity is assigned to a single nonoverlapping core
                if w['core_start'] <= mid < w['core_end']:
                    result.append({'label':e['label'],'start':s,'end':en})
                    if (e['start'] == 0 and w['window_start'] != 0) or (e['end'] == len(w['text']) and w['window_end'] != len(doc)):
                        border_warning.append({'code': 'entity_touches_chunk_edge',
                                               'start': s, 'end': en})
        return normalize_spans(result, doc), border_warning

    def _audit_error(self, request_id, stage, exc):
        # NEVER log str(exc), the original document, locals, traceback lines or secrets.
        # Some model errors embed the raw input in the exception message.
        tb = traceback.extract_tb(exc.__traceback__)
        source = (f'{Path(tb[-1].filename).name}:{tb[-1].lineno}' if tb else 'unknown')
        self._logger.error('pii_failure request_id=%s stage=%s error_type=%s source=%s',
                           request_id, stage, type(exc).__name__, source)

    def detect(self, text):
        # Serialize requests sharing the same underlying tokenizer/model objects.
        # AX and Veil still run concurrently WITHIN each request.
        request_id = uuid4().hex[:16]
        with self._request_lock:
            if self._closed or self._faulted:
                result = self._blocked('detector_unavailable')
            else:
                result = self._detect_unlocked(text, request_id)
        result['request_id'] = request_id  # Correlates API response with safe internal logs.
        return result

    def _detect_unlocked(self, text, request_id):
        t0 = time.perf_counter()
        if not isinstance(text, str):
            return self._blocked('invalid_input_type')
        if not text.strip():
            return {'status': 'READY_FOR_PSEUDONYMIZATION', 'entities': [],
                    'review_required': False, 'issues': [], 'elapsed_ms': 0.0, 'windows': 0}
        if len(text) > self.max_chars:
            return self._blocked('input_exceeds_max_chars')
        # Preserve single-pass behavior of the frozen benchmark for short documents.
        # For longer texts, use overlapping context windows; never silently truncate.
        try:
            if len(text) <= 600 and (self.token_guard is None or self.token_guard(text)):
                windows = [{'window_start': 0, 'window_end': len(text),
                            'core_start': 0, 'core_end': len(text), 'text': text}]
            else:
                windows = chunk_windows(text)
            if len(windows) > self.max_windows:
                return self._blocked('too_many_windows')
        except Exception as exc:
            self._audit_error(request_id, 'window_preparation', exc)
            return self._blocked('tokenizer_or_window_failure', (time.perf_counter()-t0)*1000)
        stage = 'token_guard'
        try:
            if self.token_guard is not None:
                for w in windows:
                    if not self.token_guard(w['text']):
                        return self._blocked('token_limit_exceeded', (time.perf_counter()-t0)*1000)
            stage = 'model_inference'
            if self.parallel:
                fa = self._pool.submit(self._predict_all_windows, self.ax_predict, windows, text)
                fv = self._pool.submit(self._predict_all_windows, self.veil_predict, windows, text)
                deadline = time.perf_counter() + self.timeout_seconds
                ax, wa = fa.result(timeout=max(0, deadline-time.perf_counter()))
                veil, wv = fv.result(timeout=max(0, deadline-time.perf_counter()))
            else:
                ax, wa = self._predict_all_windows(self.ax_predict, windows, text)
                veil, wv = self._predict_all_windows(self.veil_predict, windows, text)
            stage = 'fusion_and_boundary_validation'
            hybrid, collision = fuse_labelwise(ax, veil)
            entities, post_events = validate_boundaries(text, hybrid)
            issues = wa + wv
            # no original PII strings in logs/results, only type and character offsets
            issues.extend({'code': x['status'], 'rule': x['rule'],
                           'start': x.get('start'), 'end': x.get('end')}
                          for x in post_events if x['status'].startswith('review_'))
            issues.extend({'code':'span_overlap_discarded', **x} for x in collision)
            # Protect genuinely ambiguous full-name/subject-particle boundaries.
            # Review only; the PII span is NOT silently expanded or shortened.
            for e in entities:
                if e['label'] != 'person':
                    continue
                a, b = int(e['start']), int(e['end'])
                if b < len(text) and text[b] == '이':
                    left = text[max(0, a-20):a]
                    right = text[b+1:b+30]
                    if ROLE_CUE.search(left) and NEXT_CONTEXT.match(right):
                        issues.append({'code': 'possible_uncovered_name_suffix',
                                       'start': b, 'end': b+1})
            status = 'REVIEW_REQUIRED' if issues else 'READY_FOR_PSEUDONYMIZATION'
            return {'status': status, 'entities': entities, 'review_required': bool(issues),
                    'issues': issues, 'elapsed_ms': round((time.perf_counter()-t0)*1000,2),
                    'windows': len(windows),
                    'postal_expansions': sum(e['status'].startswith('applied_') for e in post_events)}
        except FutureTimeout as exc:
            # A running thread cannot be force-killed. Do not permit a later request
            # to reuse an underlying backend while a timed-out task may still run.
            self._faulted = True
            self._audit_error(request_id, stage, exc)
            return self._blocked('model_timeout', (time.perf_counter()-t0)*1000)
        except Exception as exc:
            # Avoid leaking raw input through an exception message.
            self._audit_error(request_id, stage, exc)
            return self._blocked('model_or_postprocess_failure', (time.perf_counter()-t0)*1000)
