"""Research-only Pareto comparison for name-boundary PROTECTION, never a deployment policy.

V4 compares MASK decisions only. It never changes identity spans, detector status,
HMAC tokens, raw PII, Gate, DB, or external services. Gold is evaluator-only.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from typing import Any

from name_boundary_policy import baseline_masks, propose, merge_intervals

POLICIES = (
    'baseline',
    'v3_all',
    'v4_skip_short',
    'v4_evidence_only',
    'v4_evidence_abstain',
    'v4_review_ambiguous_abstain',
)
_STATUSES = ('READY_FOR_PSEUDONYMIZATION', 'REVIEW_REQUIRED')


def chars(intervals):
    return {j for a, b in intervals for j in range(a, b)}


def valid_result(text: str, r: Any) -> None:
    if not isinstance(text, str) or not isinstance(r, dict):
        raise ValueError('invalid_result')
    if r.get('status') not in _STATUSES:
        raise ValueError('detector_not_ready_for_research')
    if not isinstance(r.get('entities'), list) or not isinstance(r.get('issues'), list):
        raise ValueError('invalid_detector_contract')
    if any(not isinstance(e, dict) or e.get('label') not in {
            'person', 'phone_number', 'email', 'address',
            'resident_registration_number', 'account_number', 'card_number',
        } or type(e.get('start')) is not int or type(e.get('end')) is not int
        or not (0 <= e['start'] < e['end'] <= len(text)) for e in r['entities']):
        raise ValueError('invalid_entities')
    masks = baseline_masks(text, r)
    covered = chars(masks)
    if any(not set(range(e['start'], e['end'])).issubset(covered) for e in r['entities']):
        raise ValueError('unprotected_detector_entity')


def _selected(p, status: str, policy: str) -> bool:
    if policy == 'baseline':
        return False
    if policy == 'v3_all':
        return True
    if policy == 'v4_skip_short':
        return p.action != 'EVIDENCE_SHORT_PROPOSED'
    if policy in ('v4_evidence_only', 'v4_evidence_abstain'):
        return p.action == 'EVIDENCE_LONG_PROPOSED'
    if policy == 'v4_review_ambiguous_abstain':
        return p.action == 'EVIDENCE_LONG_PROPOSED' or (
            p.action == 'AMBIGUOUS_PROTECT_ONLY' and status == 'REVIEW_REQUIRED')
    raise ValueError('unknown_policy')


def run_policy(text: str, r: dict, policy: str) -> dict:
    """No identity/HMAC decisions; 'would_abstain' is virtual and never changes Step1."""
    if policy not in POLICIES:
        raise ValueError('unknown_policy')
    valid_result(text, r)
    current = baseline_masks(text, r)
    old = chars(current)
    additions = []
    potential_deferred = set()
    ps = propose(text, r)
    overlap_rejected = 0
    selected = Counter()
    eligible = Counter()
    for p in ps:
        if p.protect_span is None:
            continue
        interval = p.protect_span
        gap = set(range(*interval)) - old
        if not gap:
            continue
        eligible[p.action] += 1
        # Extra coverage must not swallow an unrelated detector entity or mutate its label.
        other_overlap = any(e['label'] != 'person' and
                            p.protect_span[0] < e['end'] and e['start'] < p.protect_span[1]
                            for e in r['entities'])
        if other_overlap:
            overlap_rejected += 1
            potential_deferred.update(gap)
            continue
        if _selected(p, r['status'], policy):
            selected[p.action] += 1
            additions.append(interval)
        else:
            # Every unmasked candidate might still be a longer genuine name.
            # Selective policy MUST abstain rather than assume it is a particle.
            potential_deferred.update(gap)
    result = merge_intervals(current + additions, len(text))
    new_chars = chars(result)
    if not old.issubset(new_chars):
        raise ValueError('baseline_protection_reduced')
    virtual_review = (policy in ('v4_evidence_abstain', 'v4_review_ambiguous_abstain')
                      and r['status'] == 'READY_FOR_PSEUDONYMIZATION'
                      and bool(potential_deferred - new_chars))
    return {'mask_intervals': result, 'added': new_chars-old,
            'proposals': ps, 'selected': selected, 'eligible': eligible,
            'overlap_rejected': overlap_rejected,
            'would_abstain': virtual_review,
            'auto_eligible': r['status'] == 'READY_FOR_PSEUDONYMIZATION' and not virtual_review}


def score_one(text: str, gold_spans: list[tuple[int, int]], result: dict, group: str) -> dict:
    g = chars(gold_spans)
    before = run_policy(text, result, 'baseline')
    ref_chars = chars(before['mask_intervals'])
    out = {'group': group, 'status': result['status'], 'gold_chars': len(g),
           'baseline_missing': len(g-ref_chars),
           'policies': {}}
    for policy in POLICIES:
        x = run_policy(text, result, policy)
        masked = chars(x['mask_intervals'])
        new = x['added']
        out['policies'][policy] = {
            'covered_gold': len(masked & g),
            'missing_gold': len(g-masked),
            'added_gold': len(new & g),
            'added_non_gold': len(new-g),
            'ready_uncovered': int(x['auto_eligible'] and bool(g-masked)),
            'virtual_abstention': int(x['would_abstain']),
            'auto_eligible': int(x['auto_eligible']),
            'overlap_rejected':x['overlap_rejected'],
            'new_masking_per_action': dict(x['selected']),
        }
    return out


def aggregate(scores: list[dict]) -> dict:
    total_gold = sum(x['gold_chars'] for x in scores)
    total = {'n':len(scores), 'gold_person_chars':total_gold,
             'status_counts':dict(sorted(Counter(x['status'] for x in scores).items())),
             'policies':{}}
    for policy in POLICIES:
        rows = [x['policies'][policy] for x in scores]
        sums = {k:sum(x[k] for x in rows) for k in (
            'covered_gold','missing_gold','added_gold','added_non_gold',
            'ready_uncovered','virtual_abstention','auto_eligible','overlap_rejected')}
        sums['person_char_coverage'] = (round(sums['covered_gold']/total_gold, 5)
                                        if total_gold else None)
        sums['added_person_char_precision'] = (round(sums['added_gold']/(sums['added_gold']+sums['added_non_gold']),5)
                                               if sums['added_gold']+sums['added_non_gold'] else None)
        sums['selected_actions'] = dict(sorted(sum((Counter(x['new_masking_per_action']) for x in rows), Counter()).items()))
        total['policies'][policy] = sums
    return total


def inspect_holdout(text: str, detector_result: dict) -> dict:
    """Known holdout: do NOT consume Gold, do NOT print text/tokens/offsets."""
    return {'status':detector_result.get('status'),
            'policies':{
                name:{'additional_protected_chars':len((x:=run_policy(text,detector_result,name))['added']),
                      'would_abstain':x['would_abstain'],
                      'overlap_rejected':x['overlap_rejected']}
                for name in POLICIES}}
