"""Korean surname + title copula safety regression; synthetic only."""
import unittest
from veil_person_policy import classify_veil_only_person, REDACT, REVIEW
from privacy_gate import check_privacy_gate, READY
from entity_pseudonymizer import pseudonymize_local

KEY = b'Z'*32


def classify(text, name='박'):
    pos = text.index(name)
    return classify_veil_only_person(text, pos, pos + len(name))


class Fake:
    def __init__(self, text, strict=False):
        pos = text.index('박')
        self.span = {'start': pos, 'end': pos + 1, 'label': 'person'}
        self.strict = strict
    def detect(self, _):
        return {'status': 'READY_FOR_PSEUDONYMIZATION',
                'entities': [dict(self.span)] if self.strict else [],
                'mask_intervals': [{'start': self.span['start'], 'end': self.span['end']}] if self.strict else []}
    def ax_predict(self, _):
        return []
    def veil_predict(self, _):
        return [dict(self.span)]


class SurnameRoleCopulaTests(unittest.TestCase):
    def test_copula_examples_are_role_redactions(self):
        for text in (
            '안녕하세요, 신한은행 상담팀 박 대리입니다.',
            '담당 박 대리였습니다.',
            '박 대리이에요.',
            '박 대리예요.',
            '박 대리님입니다.',
            '박 대리라고 합니다.',
            '박 대리이었습니다.',
        ):
            with self.subTest(text=text):
                role = classify(text)
                self.assertEqual(role.action, REDACT)
                self.assertEqual(text[role.protection_start:role.protection_end].split()[0], '박')

    def test_no_unbounded_suffix_or_explicit_person_field(self):
        for text in ('성명: 박 대리입니다.', '박 대리입니다만',
                     '박 대리이신지', '박 대리점입니다.'):
            with self.subTest(text=text):
                self.assertEqual(classify(text).action, REVIEW)

    def test_veil_only_gate_and_pseudonymizer_keep_role_not_hmac(self):
        text = '안녕하세요, 신한은행 상담팀 박 대리입니다.'
        gate = check_privacy_gate(Fake(text), text)
        self.assertEqual(gate['decision'], READY)
        self.assertEqual(gate['entities_for_pseudonymization'], [])
        self.assertEqual(len(gate['redaction_intervals']), 1)
        redaction = gate['redaction_intervals'][0]
        self.assertEqual(text[redaction['start']:redaction['end']], '박 대리')
        output = pseudonymize_local(text,gate,hmac_key=KEY)
        self.assertIn('[ROLE_REFERENCE]입니다.', output.sanitized_text)
        self.assertEqual(output.hmac_entity_count, 0)
        self.assertNotIn('박 대리', output.sanitized_text)

    def test_strict_person_is_never_demoted_by_veil_only_rule(self):
        text = '안녕하세요, 신한은행 상담팀 박 대리입니다.'
        gate = check_privacy_gate(Fake(text, strict=True), text)
        self.assertEqual(gate['decision'], READY)
        self.assertEqual(len(gate['entities_for_pseudonymization']), 1)
        self.assertEqual(gate['redaction_intervals'], [])
        output = pseudonymize_local(text,gate,hmac_key=KEY)
        self.assertEqual(output.hmac_entity_count, 1)
        self.assertIn('<PERSON_', output.sanitized_text)

    def test_existing_particle_path_unchanged(self):
        self.assertEqual(classify('박 대리가 처리했습니다.').action, REDACT)
        self.assertEqual(classify('박 대리님은 담당자입니다.').action, REDACT)

if __name__ == '__main__':
    unittest.main()
