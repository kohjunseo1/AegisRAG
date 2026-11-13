"""Fail-closed tests for single-name online query recovery."""
import unittest
from unittest.mock import patch

from document_preparation import Outcome
from query_recovery import recover_simple_name_query

TOKEN = '<PERSON_' + 'a'*24 + '>'
KEY = b'Z'*32


class TestQueryRecovery(unittest.TestCase):
    def _fake_prepare(self, doc_id, text, detector, key):
        if text == '가상명':
            return Outcome(doc_id, 'LOCAL_PREPARED', 'LOCAL_ONLY_NOT_EXTERNAL_AUTHORIZED',
                           sanitized_text=TOKEN,tokens=(TOKEN,))
        return Outcome(doc_id, 'REVIEW', 'GATE_NOT_READY')

    @patch('document_preparation.prepare')
    def test_single_name_and_known_information_intent(self, mock):
        mock.side_effect = self._fake_prepare
        result = recover_simple_name_query('가상명 정보', object(), KEY)
        self.assertEqual(result, TOKEN+' 관련 정보 알려줘')
        self.assertEqual(mock.call_count,1)
        mock.assert_called_once_with('Q_NAME_PROBE','가상명',mock.call_args.args[2],KEY)

    @patch('document_preparation.prepare')
    def test_unrecognized_or_multi_entity_queries_are_never_recovered(self, mock):
        mock.side_effect = self._fake_prepare
        for question in ('가상명 전화번호', '가상명 및 가상인 정보',
                         '가상명 주소 찾아줘', '가상명 010-1234-5678 정보',
                         '부산시 정보', '가상명 관련 자료와 연락처'):
            self.assertIsNone(recover_simple_name_query(question,object(),KEY))
        # NOTE: 부산시 정보 matches the syntactic shape, but standalone
        # detector must reject it. The fake detector does reject it.

    @patch('document_preparation.prepare')
    def test_isolated_name_must_have_exact_one_person_hmac(self, mock):
        mock.return_value=Outcome('Q_NAME_PROBE','LOCAL_PREPARED','SAFE',
                                  sanitized_text='[REDACTED_UNCLASSIFIED]',tokens=())
        self.assertIsNone(recover_simple_name_query('가상명 정보',object(),KEY))


if __name__=='__main__': unittest.main()
