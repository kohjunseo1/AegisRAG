"""AegisRAG V5 read-only, fail-closed Step1->Gate->non-linked masking preflight.

An unresolved Korean name boundary does not *automatically* block a Step1-READY
item when its entire plausible name candidate can be masked without creating an
identity/HMAC alias. This is NOT a release/index/export authorization.

No DB/network/file IO. Never returns original text, sanitized text, offsets,
HMAC tokens, canonicalized names, or exceptions containing source text.
"""
from __future__ import annotations

from collections import Counter
import os
import re
from typing import Any

from name_protection_policy import decide, _classify, _explicit_names, _FIELD_RE
from name_boundary_policy import propose, baseline_masks, merge_intervals

STEP1_READY = 'READY_FOR_PSEUDONYMIZATION'
GATE_READY = 'READY_FOR_LOCAL_PSEUDONYMIZATION'
SAFE_ISSUE_CODES = {
    'possible_uncovered_name_suffix', 'review_only',
    'review_unlabeled_prefix', 'review_trailing_postcode', 'review_overlap',
    'span_overlap_discarded', 'entity_touches_chunk_edge',
}
_ALLOWED_STATES = {'LOCAL_DRYRUN_OK', 'REVIEW_QUEUE', 'BLOCKED'}
_ALLOWED_REASONS = {
    'LOCAL_ONLY_NOT_INDEX_AUTHORIZED', 'STEP1_REVIEW', 'STEP1_BLOCKED',
    'INVALID_INPUT', 'INVALID_STEP1_RESPONSE', 'V5_POLICY_ABSTAIN',
    'GATE_REVIEW', 'GATE_BLOCKED', 'INVALID_GATE_CONTRACT',
    'NAME_PROTECTION_INCOMPLETE', 'NAME_SPAN_COLLISION',
    'PROTECTION_REGRESSION', 'PSEUDONYMIZATION_FAILED',
    'VALIDATION_OR_RUNTIME_FAILURE', 'INVALID_NAME_PROPOSAL',
}
_COUNTER_KEYS = {
    'name_candidate_count', 'ambiguous_name_candidate_count',
    'extra_masked_chars', 'nonlinked_name_mentions', 'suppressed_person_hmac_entities',
    'preserved_person_hmac_entities', 'gate_checked',
    'explicit_name_field_added_chars', 'unlinked_explicit_name_fields',
}


def _safe_doc_id(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value))


def _result(doc_id: str, state: str, reason: str, **kwargs) -> dict:
    # Whitelist output columns and reasons to eliminate accidental PII logging.
    if not _safe_doc_id(doc_id):
        raise ValueError('unsafe_doc_id')
    if state not in _ALLOWED_STATES or reason not in _ALLOWED_REASONS:
        raise ValueError('unsafe_result_code')
    out = {'doc_id': doc_id, 'state': state, 'reason': reason}
    for key, value in kwargs.items():
        if key not in _COUNTER_KEYS or type(value) is not int or value < 0:
            raise ValueError('unsafe_result_field')
        out[key] = value
    return out


class _FrozenDetectorView:
    """Freeze Step1 result for Gate; still allow original independent PII heads."""
    def __init__(self, detector: Any, result: dict):
        self._result = result
        self.ax_predict = detector.ax_predict
        self.veil_predict = detector.veil_predict

    def detect(self, _: str) -> dict:
        return self._result


def _overlap(a: dict, b: dict) -> bool:
    return a['start'] < b['end'] and b['start'] < a['end']


def _covers(masks: list[tuple[int,int]], a: int, b: int) -> bool:
    # Adjacent ranges have already been merged.
    return any(x <= a and b <= y for x, y in masks)


def _pair_list(items: Any, n: int) -> list[tuple[int,int]]:
    if not isinstance(items, list):
        raise ValueError('invalid_spans')
    out = []
    for x in items:
        if (not isinstance(x, dict) or type(x.get('start')) is not int
            or type(x.get('end')) is not int or not 0 <= x['start'] < x['end'] <= n):
            raise ValueError('invalid_spans')
        out.append((x['start'], x['end']))
    return out


def _labeled_list(items: Any, n: int) -> list[dict]:
    _pair_list(items, n)
    if any(not isinstance(x.get('label'), str) for x in items):
        raise ValueError('invalid_labels')
    return items


def _protection_plan(text: str, r: dict, g: dict, decision: dict, proposals: list) -> tuple[dict, dict]:
    """Derive an *in-memory* Gate-compatible mask, with no new identity link.

    Returns (new_gate_result, safe integer counters). If an unresolved candidate
    cannot be protected whole, or overlaps unrelated PII/role, fail closed.
    """
    n = len(text)
    gate_and_v5 = merge_intervals(_pair_list(g.get('mask_intervals'), n) +
                                  list(decision['mask_intervals']), n)
    # Additional independent, field-anchored name protection: V5 used these
    # fields as *evidence*, but a field's actual value may have been missed by
    # both NER heads. Protect the explicit value without creating a new HMAC
    # identity. This is a generic syntax cue, never a hardcoded individual.
    explicit_fields = [(m.start(1), m.end(1)) for m in _FIELD_RE.finditer(text)]
    masks = merge_intervals(gate_and_v5 + explicit_fields, n)
    explicit_add = sum(1 for a,b in explicit_fields for i in range(a,b)
                       if not any(s<=i<t for s,t in gate_and_v5))
    original = baseline_masks(text, r)
    if any(not _covers(masks, a, b) for a, b in original):
        raise ValueError('protection_regression')
    strict = _labeled_list(g.get('entities'), n)
    hmac_entities = _labeled_list(g.get('entities_for_pseudonymization'), n)
    roles = _pair_list(g.get('redaction_intervals'), n)

    # Never let a name repair erase/merge an independently detected non-PERSON
    # entity or a role redaction. Collision => manual review, not auto-redact.
    for a,b in explicit_fields:
        region={'start':a,'end':b}
        if any(_overlap(region,e) for e in strict + hmac_entities if e['label']!='person'):
            raise RuntimeError('name_span_collision')
        if any(a < t and s < b for s,t in roles):
            raise RuntimeError('name_span_collision')
    for p in proposals:
        a, b = p.candidate_full_span
        oa, ob = p.original_span
        if not (type(a) is int and type(b) is int and
                0 <= a < b <= n and a <= oa < ob <= b and b-a >= 3):
            raise ValueError('invalid_proposal')
        region = {'start': a, 'end': b}
        if any(_overlap(region, e) for e in strict + hmac_entities if e['label'] != 'person'):
            raise RuntimeError('name_span_collision')
        if any(a < t and s < b for s,t in roles):
            raise RuntimeError('name_span_collision')

    explicit = _explicit_names(text)
    classifications = {}
    ambiguous = 0
    for p in proposals:
        cls, _ = _classify(text, p, explicit)
        if cls not in {'LONG','SHORT','CONFLICT','AMBIGUOUS'}:
            raise ValueError('invalid_classification')
        a,b = p.candidate_full_span
        if cls in ('CONFLICT','AMBIGUOUS'):
            ambiguous += 1
            if not _covers(masks, a, b):
                raise RuntimeError('name_protection_incomplete')
        elif cls == 'LONG':
            if not _covers(masks, a, b):
                raise RuntimeError('name_protection_incomplete')
        elif cls == 'SHORT':
            # If independently supported as SHORT, suffix may be the particle.
            if not _covers(masks, a, b - 1):
                raise RuntimeError('name_protection_incomplete')
        classifications[(a,b)] = cls

    # Candidate-identities that cannot be matched to an original exact name
    # span must never be passed to deterministic HMAC. For an evidenced SHORT
    # or LONG, retaining an *unchanged* exact Gate name entity is allowed; this
    # does not introduce a new cross-name equality beyond pre-existing Gate.
    safe_entities = []
    suppressed = 0
    preserved_name = 0
    for entity in hmac_entities:
        if entity['label'] != 'person':
            safe_entities.append(entity)
            continue
        overlaps = [p for p in proposals if _overlap(
            entity, {'start':p.candidate_full_span[0], 'end':p.candidate_full_span[1]})]
        if not overlaps:
            # If a name field is detected only partially, HMAC for the partial
            # text could encode an incorrect identity. Redact the full field.
            fields_intersected=[f for f in explicit_fields if _overlap(
                entity,{'start':f[0],'end':f[1]})]
            if fields_intersected and any(
                (entity['start'],entity['end']) != field
                for field in fields_intersected):
                suppressed += 1
            else:
                safe_entities.append(entity)
                preserved_name += 1
            continue
        # Only preserve a previously selected exact HMAC entity, when all
        # overlapping proposals independently resolve to exactly that span.
        safe = all(
            classifications[p.candidate_full_span] in ('LONG','SHORT') and
            (entity['start'], entity['end']) == (
                p.candidate_full_span if classifications[p.candidate_full_span]=='LONG'
                else (p.candidate_full_span[0],p.candidate_full_span[1]-1))
            for p in overlaps
        )
        if safe:
            safe_entities.append(entity)
            preserved_name += 1
        else:
            suppressed += 1

    revised = dict(g)
    revised['mask_intervals'] = [{'start':a,'end':b} for a,b in masks]
    revised['entities_for_pseudonymization'] = safe_entities
    # No new HMAC records; suppressed PERSON becomes mask-only residual redaction.
    # Existing pseudonymizer requires source coverage of all strict entities.
    return revised, {
        'name_candidate_count': len(proposals),
        'ambiguous_name_candidate_count': ambiguous,
        'extra_masked_chars': len(decision['added']),
        'nonlinked_name_mentions': sum(1 for p in proposals if
                classifications[p.candidate_full_span] in ('AMBIGUOUS','CONFLICT')),
        'suppressed_person_hmac_entities': suppressed,
        'preserved_person_hmac_entities': preserved_name,
        'gate_checked': 1,
        'explicit_name_field_added_chars': explicit_add,
        'unlinked_explicit_name_fields': sum(1 for field in explicit_fields if not any(
            e['label']=='person' and (e['start'],e['end'])==field
            for e in hmac_entities)),
    }


def preview_one(doc_id: str, text: str, detector: Any, *, key: bytes | None = None) -> dict:
    """Return only safe count/state codes. Never authorize indexing/remote use."""
    if not _safe_doc_id(doc_id):
        raise ValueError('unsafe_doc_id')
    if not isinstance(text, str):
        return _result(doc_id, 'BLOCKED', 'INVALID_INPUT')
    counters: dict = {}
    try:
        result = detector.detect(text)
        if not isinstance(result, dict):
            return _result(doc_id,'BLOCKED','INVALID_STEP1_RESPONSE')
        status = result.get('status')
        if status not in (STEP1_READY, 'REVIEW_REQUIRED'):
            return _result(doc_id,'BLOCKED','STEP1_BLOCKED')
        decision = decide(text, result, 'v5_privacy_first')
        proposals = propose(text, result)
        counters = {
            'name_candidate_count':len(proposals),
            'extra_masked_chars':len(decision['added']),
        }
        if status == 'REVIEW_REQUIRED':
            return _result(doc_id,'REVIEW_QUEUE','STEP1_REVIEW',**counters)
        if decision.get('would_abstain'):
            return _result(doc_id,'REVIEW_QUEUE','V5_POLICY_ABSTAIN',**counters)

        from privacy_gate import check_privacy_gate
        gate = check_privacy_gate(_FrozenDetectorView(detector, result), text)
        if gate.get('decision') != GATE_READY:
            code = 'GATE_REVIEW' if gate.get('decision') == 'REVIEW_REQUIRED' else 'GATE_BLOCKED'
            return _result(doc_id, 'REVIEW_QUEUE' if code == 'GATE_REVIEW' else 'BLOCKED',code,**counters)

        try:
            g, info = _protection_plan(text, result, gate, decision, proposals)
        except RuntimeError as exc:
            # RuntimeError here is *only* used for our own literal safe codes.
            reason = ('NAME_PROTECTION_INCOMPLETE' if str(exc)=='name_protection_incomplete'
                      else 'NAME_SPAN_COLLISION')
            return _result(doc_id,'REVIEW_QUEUE',reason,**counters)
        except ValueError:
            return _result(doc_id,'BLOCKED','INVALID_GATE_CONTRACT',**counters)

        from entity_pseudonymizer import pseudonymize_local, PseudonymizationError
        if key is None:
            key = os.urandom(32)  # throwaway; never persist or log key
        try:
            transformed = pseudonymize_local(text, g, hmac_key=key, include_vault_records=False)
        except PseudonymizationError:
            return _result(doc_id,'REVIEW_QUEUE','PSEUDONYMIZATION_FAILED',**counters)
        # Both the original source and all protected candidate positions are
        # reconstructed as token/redaction by pseudonymize_local(); do not print
        # or persist resulting text or HMAC tokens.
        del transformed
        return _result(doc_id,'LOCAL_DRYRUN_OK','LOCAL_ONLY_NOT_INDEX_AUTHORIZED',**info)
    except Exception:
        # Do not serialize exception details (they can contain original PII).
        return _result(doc_id,'BLOCKED','VALIDATION_OR_RUNTIME_FAILURE',**counters)


def summarize(records: list[dict]) -> dict:
    s=Counter(x['state'] for x in records)
    r=Counter(x['reason'] for x in records)
    return {
        'n':len(records),
        'state_counts':dict(sorted(s.items())),
        'reason_counts':dict(sorted(r.items())),
        'step1_review_count':r['STEP1_REVIEW'],
        'gate_review_count':r['GATE_REVIEW'],
        'unlinked_name_mentions':sum(x.get('nonlinked_name_mentions',0) for x in records),
        'newly_added_mask_chars':sum(x.get('extra_masked_chars',0) for x in records),
        'explicit_name_field_added_chars':sum(x.get('explicit_name_field_added_chars',0) for x in records),
        'actual_index_writes':0,
        'automatic_review_to_ready_promotions':0,
    }
