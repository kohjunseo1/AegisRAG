"""Contract tests for guarded Holdout finalizer (no model/DB/HCX)."""
import unittest

import veil_person_policy

from scripts.holdout_finalizer import (
    POLICY, corrected_policy_source, ROLE_ANCHOR, QUOTE_GUARD, audit_stage_summary,
)


class HoldoutFinalizerTests(unittest.TestCase):
    def test_policy_fix_is_idempotent_on_repository_policy(self):
        before = POLICY.read_text(encoding="utf-8")
        fixed = corrected_policy_source(before)
        self.assertEqual(fixed, corrected_policy_source(fixed))
        self.assertIn('quoted_role_reference', fixed)
        self.assertIn('(?:이라는|이란|라는|란)', fixed)

    def test_policy_classifies_attributive_title_quote_as_review(self):
        namespace = vars(veil_person_policy).copy()
        exec(compile(corrected_policy_source(POLICY.read_text(encoding="utf-8")),
                     str(POLICY), "exec"), namespace)
        classifier = namespace['classify_veil_only_person']
        self.assertEqual(classifier('김 부장님이라는 제품명', 0, 1).action,
                         namespace['REVIEW'])
        self.assertEqual(classifier('김 과장님이 검토', 0, 1).action,
                         namespace['REDACT'])
        self.assertEqual(classifier('고객명: 김 부장', 5, 6).action,
                         namespace['REVIEW'])

    def test_replaces_overbroad_local_quotation_rule(self):
        broad = QUOTE_GUARD.replace(
            '(?:이라는|이란|라는|란)',
            '(?:이라는|이란|이라고|라는|란|라고)'
        )
        baseline = 'def f():\n' + broad + ROLE_ANCHOR + '        pass\n'
        revised = corrected_policy_source(baseline)
        self.assertIn('(?:이라는|이란|라는|란)', revised)
        self.assertNotIn('(?:이라는|이란|이라고|라는|란|라고)', revised)

    def make_stage(self):
        rows = [
            {'doc_id': f'holdoute2e__{i:024x}', 'state': 'READY',
             'reason': 'AUTO_GATE_READY', 'approval_state': 'AUTO_READY',
             'text': '가상 교육안내'}
            for i in range(150)
        ]
        rows.extend([
            {'doc_id': f'holdoute2e__{i:024x}', 'state': 'REVIEW_PENDING',
             'reason': 'GATE_NOT_READY', 'approval_state': None, 'text': None}
            for i in range(150, 187)
        ])
        return {'dataset': 'holdoute2e', 'documents': rows}

    def test_stage_audit_counts_only_aggregate_codes(self):
        report = audit_stage_summary(self.make_stage())
        self.assertEqual(report['documents'], 187)
        self.assertEqual(report['states'], {'READY': 150, 'REVIEW_PENDING': 37})
        self.assertEqual(report['reasons'],
                         {'AUTO_GATE_READY': 150, 'GATE_NOT_READY': 37})
        self.assertEqual(report['pending'], 37)
        self.assertNotIn('doc_id', str(report))
        self.assertNotIn('가상 교육안내', str(report))

    def test_stage_audit_rejects_status_tampering(self):
        data = self.make_stage()
        data['documents'][0]['approval_state'] = 'HUMAN_PENDING'
        with self.assertRaisesRegex(ValueError, 'UNAPPROVED_READY_STAGE_ROW'):
            audit_stage_summary(data)
        data = self.make_stage()
        data['documents'][0]['state'] = 'REVIEW_PENDING'
        with self.assertRaisesRegex(ValueError, 'PENDING_STAGE_CONTAINS_APPROVAL'):
            audit_stage_summary(data)

    def test_stage_audit_rejects_raw_reason_and_duplicate_ids(self):
        data = self.make_stage()
        data['documents'][150]['reason'] = 'GATE_NOT_READY: 민감 원문'
        with self.assertRaisesRegex(ValueError, 'INVALID_STAGE_REASON'):
            audit_stage_summary(data)
        data = self.make_stage()
        data['documents'][150]['doc_id'] = data['documents'][0]['doc_id']
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_STAGE_DOCUMENT_ID'):
            audit_stage_summary(data)

    def test_rejects_unexpected_policy_structure(self):
        with self.assertRaisesRegex(ValueError, 'UNEXPECTED_POLICY_STRUCTURE'):
            corrected_policy_source('def unrelated():\n    pass\n')


if __name__ == '__main__':
    unittest.main()
