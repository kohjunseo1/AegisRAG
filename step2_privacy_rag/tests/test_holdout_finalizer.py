"""Contract tests for guarded Holdout finalizer (no model/DB/HCX)."""
import unittest

import veil_person_policy

from scripts.holdout_finalizer import (
    POLICY, corrected_policy_source, ROLE_ANCHOR, QUOTE_GUARD,
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

    def test_rejects_unexpected_policy_structure(self):
        with self.assertRaisesRegex(ValueError, 'UNEXPECTED_POLICY_STRUCTURE'):
            corrected_policy_source('def unrelated():\n    pass\n')


if __name__ == '__main__':
    unittest.main()
