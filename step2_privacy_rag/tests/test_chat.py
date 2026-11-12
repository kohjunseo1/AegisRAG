"""Offline regression tests for the V6 live HCX E2E bridge."""
import unittest
from unittest.mock import MagicMock, patch
from core import order_results, PreparedQuery
from hcx import (validate_response, validate_outbound, redact_residual_pii,
                 OutboundError)
from app import respond

TK='<PERSON_' + 'a'*24 + '>'

class TestE2EContract(unittest.TestCase):
    def setUp(self):
        self.docs=[dict(doc_id='H%04d'%i, text='안전교육은 매주 수요일입니다.',
                        tokens=[], region_keys=[], dense_rank=i,
                        hmac_matches=0, region_matches=0,
                        verified_region_match=None) for i in range(1,6)]

    def test_ranking_preserved(self):
        rows=[dict(doc_id='A',tokens=[],region_keys=[],dense_rank=1),
              dict(doc_id='B',tokens=[TK],region_keys=[],dense_rank=2)]
        self.assertEqual(order_results(rows,{TK},set())[0]['doc_id'],'B')

    def test_topic_boost_finds_condolence_notice_over_unrelated_dense(self):
        rows=[
            dict(doc_id='S001',text='[공지] 시스템 점검 안내\\n로그인 화면 업데이트입니다.',
                 tokens=[],region_keys=[],dense_rank=1),
            dict(doc_id='S002',text='[부고] 동료 가족상\\n빈소와 발인 일정이 공지되었습니다.',
                 tokens=[],region_keys=[],dense_rank=35),
        ]
        ranked=order_results(rows,set(),set(),query_text='경조사 관련 공지 알려줘')
        self.assertEqual(ranked[0]['doc_id'],'S002')
        self.assertGreater(ranked[0]['topic_score'],0)
        frozen=order_results(rows,set(),set(),query_text='경조사 관련 공지 알려줘',
                             use_topic_boost=False)
        self.assertEqual(frozen[0]['doc_id'],'S001')

    def test_topic_boost_never_overrides_exact_person_token(self):
        rows=[
            dict(doc_id='S001',text='[부고] 가족상 공지',tokens=[],region_keys=[],dense_rank=1),
            dict(doc_id='S002',text='[업무] 대상 직원 기록',tokens=[TK],region_keys=[],dense_rank=40),
        ]
        self.assertEqual(order_results(rows,{TK},set(),query_text='경조사 관련 공지')[0]['doc_id'],'S002')

    def test_no_category_query_preserves_dense(self):
        rows=[
            dict(doc_id='S001',text='[업무] 상담 결과',tokens=[],region_keys=[],dense_rank=1),
            dict(doc_id='S002',text='[부고] 공지',tokens=[],region_keys=[],dense_rank=40),
        ]
        self.assertEqual(order_results(rows,set(),set(),query_text='처리 상태 알려줘')[0]['doc_id'],'S001')

    def test_redact_doc_preserve_hmac(self):
        text='고객 '+TK+' 연락처: 010-1234-5678; mail person@example.com'
        red,counts=redact_residual_pii(text)
        self.assertIn(TK,red)
        self.assertNotIn('010-1234-5678',red)
        self.assertNotIn('person@example.com',red)
        self.assertEqual(counts['phone'],1)
        self.assertEqual(counts['email'],1)

    def test_document_redaction_not_total_block(self):
        self.docs[1]['text']='문의 010-1234-5678 / '+TK
        q,docs,counts=validate_outbound('경조사 공지 알려줘',self.docs)
        self.assertEqual(len(docs),5)
        self.assertIn(TK,docs[1]['text'])
        self.assertIn('[REDACTED_PHONE]',docs[1]['text'])
        self.assertEqual(counts['phone'],1)

    def test_unmasked_query_not_sent(self):
        with self.assertRaisesRegex(OutboundError,'QUERY_RESIDUAL_PII_REQUIRES_REVIEW'):
            validate_outbound('010-1234-5678 누구?',self.docs)

    def test_long_doc_is_bounded_excerpt(self):
        self.docs[0]['text']='교육 안내 '*1000
        _,docs,counts=validate_outbound('교육 안내',self.docs)
        self.assertLessEqual(len(docs[0]['text']),1500)
        self.assertEqual(counts['clipped_documents'],1)

    def test_hcx_answer_valid(self):
        result=validate_response({'decision':'ANSWER','answer':'수요일','citations':['H0001']},self.docs,False)
        self.assertEqual(result['decision'],'ANSWER')

    def test_unsupported_citation_blocked(self):
        with self.assertRaisesRegex(OutboundError,'UNSUPPORTED_CITATION_ID'):
            validate_response({'decision':'ANSWER','answer':'답','citations':['H9999']},self.docs,False)

    def test_clarify_fallback(self):
        result=validate_response({'decision':'CLARIFY','answer':'','citations':[]},self.docs,False)
        self.assertTrue(result['answer'].endswith('?'))

    def test_insufficient(self):
        result=validate_response({'decision':'INSUFFICIENT_EVIDENCE','answer':'','citations':[]},self.docs,False)
        self.assertFalse(result['citations'])

    def test_region_mismatch_fails(self):
        with self.assertRaisesRegex(OutboundError,'REGION_MISMATCH_CITATION'):
            validate_response({'decision':'ANSWER','answer':'답','citations':['H0001']},self.docs,True)

    @patch('app.call_hcx')
    def test_live_chat_auto_hcx(self, mock_call):
        pl=MagicMock()
        pl.root='.'
        pl.ask_local.return_value=('READY',PreparedQuery('안전교육 알려줘',set(),set()),self.docs)
        mock_call.return_value=({'decision':'ANSWER','answer':'수요일입니다.','citations':['H0001']},
                                {'prompt_tokens':100,'completion_tokens':20},
                                {'email':0,'phone':0,'id_number':0,'clipped_documents':0})
        result=respond(pl,{'question':'안전교육 알려줘'})
        self.assertEqual(result['decision'],'ANSWER')
        self.assertEqual(result['mode'],'hcx')
        mock_call.assert_called_once()

    @patch('app.call_hcx')
    def test_nonanswer_exposes_candidate_ids_not_document_text(self, mock_call):
        pl = MagicMock()
        pl.root = '.'
        pl.ask_local.return_value = ('READY', PreparedQuery('안전교육', set(), set()), self.docs)
        mock_call.return_value = (
            {'decision': 'INSUFFICIENT_EVIDENCE',
             'answer': '확인 가능한 문서에서 질문에 답할 충분한 근거를 찾지 못했습니다.',
             'citations': []},
            {'prompt_tokens': 100, 'completion_tokens': 20},
            {'email': 0, 'phone': 0, 'id_number': 0, 'clipped_documents': 0},
        )
        out = respond(pl, {'question': '안전교육'})
        self.assertEqual(out['decision'], 'INSUFFICIENT_EVIDENCE')
        self.assertEqual(out['sources'], [])
        diag = out['diagnostics']
        self.assertEqual(diag['retrieved_count'], 5)
        self.assertEqual(diag['retrieval_candidates'][0]['doc_id'], 'H0001')
        self.assertNotIn('text', diag['retrieval_candidates'][0])
        self.assertEqual(diag['retrieval_candidates'][0]['topic_score'], 0)

    @patch('app.call_hcx')
    def test_review_never_calls_hcx(self,mock_call):
        pl=MagicMock()
        pl.ask_local.return_value=('REVIEW_REQUIRED',None,[])
        result=respond(pl,{'question':'검토 질의'})
        self.assertEqual(result['decision'],'REVIEW_REQUIRED')
        mock_call.assert_not_called()

if __name__=='__main__':unittest.main()
