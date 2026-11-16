"""Local query safety response contracts, synthetic only; no real models or DB."""
import unittest
from unittest.mock import MagicMock, patch

from app import respond
from core import PreparedQuery
from query_privacy_controller import (
    unresolved_query_response, insufficient_evidence_response,
)


class QueryDispositionContracts(unittest.TestCase):
    def test_review_only_has_local_clarification(self):
        out = unresolved_query_response('REVIEW')
        self.assertEqual(out['decision'], 'CLARIFY')
        self.assertTrue(out['answer'].endswith('?'))
        self.assertNotIn('REVIEW_REQUIRED', out['answer'])

    def test_blocked_and_unknown_fail_closed(self):
        for state in ('BLOCKED', 'BLOCKED_OR_DEFERRED', 'UNKNOWN', '', 'REVIEWED'):
            with self.subTest(state=state):
                self.assertEqual(unresolved_query_response(state)['decision'],
                                 'BLOCKED_OR_DEFERRED')

    @patch('app.call_hcx')
    def test_review_never_uses_hcx_or_raw_question(self, remote):
        pipeline = MagicMock()
        pipeline.timings = {'step1_ms': 3.0}
        pipeline.ask_local.return_value = ('REVIEW', None, [])
        private_question = '가상인 010-1234-5678 개인정보 알려줘'
        out = respond(pipeline, {'question': private_question})
        remote.assert_not_called()
        self.assertEqual(out['decision'], 'CLARIFY')
        self.assertEqual(out['citations'], [])
        self.assertEqual(out['sources'], [])
        self.assertEqual(out['mode'], 'local')
        self.assertNotIn('010-1234-5678', str(out))
        self.assertNotIn('가상인', str(out))

    @patch('app.call_hcx')
    def test_blocked_never_uses_hcx(self, remote):
        pipeline = MagicMock()
        pipeline.timings = {}
        pipeline.ask_local.return_value = ('BLOCKED', None, [])
        out = respond(pipeline, {'question': '합성 테스트'})
        remote.assert_not_called()
        self.assertEqual(out['decision'], 'BLOCKED_OR_DEFERRED')
        self.assertEqual(out['citations'], [])

    @patch('app.call_hcx')
    def test_ready_with_no_evidence_is_local_insufficient(self, remote):
        pipeline = MagicMock()
        pipeline.timings = {'db_ms': 8.0}
        pipeline.ask_local.return_value = (
            'READY', PreparedQuery('가명처리된 일반 문의', set(), set()), []
        )
        out = respond(pipeline, {'question': '일반 문의'})
        remote.assert_not_called()
        self.assertEqual(out['decision'], 'INSUFFICIENT_EVIDENCE')
        self.assertEqual(out['diagnostics']['retrieved_count'], 0)
        self.assertEqual(out['sources'], [])
        self.assertEqual(out['mode'], 'local')

    def test_no_evidence_has_no_citations_or_question_echo(self):
        out = insufficient_evidence_response()
        self.assertEqual(out['decision'], 'INSUFFICIENT_EVIDENCE')
        self.assertNotIn('question', out)
        self.assertNotIn('citations', out)


if __name__ == '__main__':
    unittest.main()
