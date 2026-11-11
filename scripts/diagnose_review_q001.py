#!/usr/bin/env python3
"""Q-001 read-only REVIEW triage: counts and safe reason codes only.

Requires private source JSONL to remain local. No outputs are stored, no HMAC
tokens or source spans are printed, no DB or network LLM calls, no Gold labels.
Never promotes REVIEW to READY.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'step1_pii_detector' / 'src'))
sys.path.insert(0, str(ROOT / 'step2_privacy_rag' / 'app'))
DEFAULT_SOURCE = ROOT / 'step2_privacy_rag' / 'local_eval' / 'holdout_labeled.jsonl'
ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


def summarize(doc_id: str, source: str, detector) -> dict:
    from name_boundary_policy import propose
    from name_protection_policy import decide
    from name_protection_handoff import _FrozenDetectorView
    from privacy_gate import check_privacy_gate
    from gate_validation import check_spans, merge_masks, covered, find_safe_name_merges
    from veil_person_policy import classify_veil_only_person

    result = detector.detect(source)
    issues = Counter(
        x.get('code', 'UNKNOWN') for x in result.get('issues', [])
        if isinstance(x, dict) and isinstance(x.get('code'), str)
    )
    output = {'id': doc_id, 'step1_status': result.get('status'),
              'issue_codes': dict(sorted(issues.items()))}
    if result.get('status') not in ('REVIEW_REQUIRED', 'READY_FOR_PSEUDONYMIZATION'):
        output['stage'] = 'STEP1_BLOCKED'
        return output

    candidates = propose(source, result)
    mask_policy = decide(source, result, 'v5_privacy_first')
    output['name_proposals'] = dict(sorted(Counter(p.action for p in candidates).items()))
    output['proposal_evidence'] = dict(sorted(Counter(p.evidence for p in candidates).items()))
    output['v5_decision_counts'] = mask_policy.get('decision_counts', {})
    output['extra_proposed_mask_characters'] = len(mask_policy.get('added', ()))
    output['would_abstain'] = bool(mask_policy.get('would_abstain'))
    if result.get('status') == 'REVIEW_REQUIRED':
        output['stage'] = 'STEP1_REVIEW'
        return output

    gate = check_privacy_gate(_FrozenDetectorView(detector, result), source)
    output['gate_reason'] = gate.get('reason', 'UNKNOWN')
    output['stage'] = 'GATE_REVIEW' if gate.get('decision') == 'REVIEW_REQUIRED' else str(gate.get('decision'))
    output['veil_policy_counts'] = gate.get('veil_policy_counts', {})
    if gate.get('reason') == 'unresolved_veil_person':
        # Only for explaining Gate's additional unresolved PERSON candidates.
        strict = check_spans(result.get('entities'), len(source), True)
        masks = merge_masks(check_spans(result.get('mask_intervals'), len(source), False))
        veil = check_spans(detector.veil_predict(source), len(source), True)
        veil_person = [e for e in veil if e['label'] == 'person']
        merges = find_safe_name_merges(source, strict, veil_person, masks)
        expanded = merge_masks(masks + [x['merged'] for x in merges])
        missing = [e for e in veil_person if not covered(e, expanded)]
        decisions = [classify_veil_only_person(source, e['start'], e['end']) for e in missing]
        output['veil_candidate_reason_counts'] = dict(sorted(Counter(x.reason for x in decisions).items()))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--ids', nargs='+', required=True, metavar='DOC_ID')
    args = parser.parse_args()
    try:
        if len(args.ids) != len(set(args.ids)) or any(not ID.fullmatch(x) for x in args.ids):
            raise ValueError('INVALID_DOC_ID_LIST')
        if not args.input.resolve().is_relative_to((ROOT / 'step2_privacy_rag' / 'local_eval').resolve()):
            raise ValueError('SOURCE_MUST_REMAIN_PRIVATE')
        from pii_detector import create_detector
        ids = set(args.ids)
        found = {}
        for line in args.input.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('id') in ids:
                if row['id'] in found or not isinstance(row.get('text'), str):
                    raise ValueError('INVALID_SOURCE_RECORD')
                found[row['id']] = row['text']
        if set(found) != ids:
            raise ValueError('MISSING_DOCUMENT_IDS')
        with create_detector() as detector:
            for doc_id in sorted(ids):
                print('Q001_TRIAGE ' + json.dumps(summarize(doc_id, found[doc_id], detector),
                      ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        # Library exceptions may contain sensitive text: never print the message.
        print('Q001_TRIAGE_FAILED ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
