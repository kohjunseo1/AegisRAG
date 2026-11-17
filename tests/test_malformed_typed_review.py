"""No-model unit tests for malformed typed span handling. No raw records."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'step2_privacy_rag' / 'app'))

from local_pseudonymizer import pseudonymize_local
from name_protection_handoff import _protection_plan


class MalformedTypedReviewTest(unittest.TestCase):
    def plan_and_mask(self, value, label):
        n = len(value)
        ent = {'start': 0, 'end': n, 'label': label}
        mask = {'start': 0, 'end': n}
        r = {'entities': [ent], 'mask_intervals': [mask]}
        g = {
            'decision': 'READY_FOR_LOCAL_PSEUDONYMIZATION',
            'entities': [ent],
            'entities_for_pseudonymization': [ent],
            'mask_intervals': [mask],
            'redaction_intervals': [],
        }
        d = {'mask_intervals': [], 'added': []}
        revised, counts = _protection_plan(value, r, g, d, [])
        masked = pseudonymize_local(value, revised, hmac_key=b't' * 32)
        return revised, counts, masked

    def test_invalid_email_is_mask_only(self):
        revised, counts, result = self.plan_and_mask('ab', 'email')
        self.assertEqual(counts['malformed_typed_count'], 1)
        self.assertEqual(revised['entities_for_pseudonymization'], [])
        self.assertEqual(result.sanitized_text, '[REDACTED_UNCLASSIFIED]')
        self.assertEqual(result.hmac_entity_count, 0)

    def test_invalid_phone_is_mask_only(self):
        revised, counts, result = self.plan_and_mask('123', 'phone_number')
        self.assertEqual(counts['malformed_typed_count'], 1)
        self.assertEqual(result.sanitized_text, '[REDACTED_UNCLASSIFIED]')
        self.assertEqual(result.hmac_entity_count, 0)

    def test_valid_email_keeps_hmac(self):
        revised, counts, result = self.plan_and_mask('ab@example.com', 'email')
        self.assertEqual(counts['malformed_typed_count'], 0)
        self.assertEqual(len(revised['entities_for_pseudonymization']), 1)
        self.assertTrue(result.sanitized_text.startswith('<EMAIL_'))
        self.assertEqual(result.hmac_entity_count, 1)

    def test_valid_phone_keeps_hmac(self):
        _, counts, result = self.plan_and_mask('010-1234-5678', 'phone_number')
        self.assertEqual(counts['malformed_typed_count'], 0)
        self.assertTrue(result.sanitized_text.startswith('<PHONE_NUMBER_'))
        self.assertEqual(result.hmac_entity_count, 1)


if __name__ == '__main__':
    unittest.main()
