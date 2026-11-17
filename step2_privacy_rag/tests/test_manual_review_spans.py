"""Conservative manual span approval, synthetic only, no network/model/DB."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from document_preparation import Outcome
from manual_review_spans import review_manual_spans
from dataset_workflow import _hash

KEY=b'M'*32

class Detector:
    def detect(self,text):
        return {'status':'REVIEW_REQUIRED', 'entities':[], 'mask_intervals':[]}
    def ax_predict(self,text):return []
    def veil_predict(self,text):return []

class ManualOverlayTest(unittest.TestCase):
    def setUp(self):
        self.raw='담당자 홍길동 연락 예정'
        self.hash=_hash(self.raw)
        self.stage={'documents':[{'doc_id':'synthetic__1','source_id':'1',
               'source_sha256':self.hash,'state':'REVIEW_PENDING',
               'reason':'STEP1_NOT_READY','raw_for_local_review':self.raw}]}
    @staticmethod
    def mock_prepare(did,text,detector,key):
        return Outcome(did,'LOCAL_PREPARED','OK',sanitized_text=text)
    @patch('dataset_workflow.prepare',side_effect=mock_prepare.__func__)
    def test_manual_person_really_pseudonymized(self,_):
        start=self.raw.index('홍길동')
        review_manual_spans(self.stage,'synthetic__1',self.hash,
                 [{'start':start,'end':start+3,'label':'person'}],
                 'reviewer1',Detector(),KEY)
        saved=self.stage['documents'][0]
        self.assertEqual(saved['state'],'READY')
        self.assertEqual(saved['approval_state'],'HUMAN_APPROVED')
        self.assertNotIn('홍길동',saved['text'])
        self.assertIn('<PERSON_',saved['text'])
        self.assertEqual(saved['manual_span_overrides'][0]['label'],'person')
    @patch('dataset_workflow.prepare',side_effect=mock_prepare.__func__)
    def test_stale_version_rejected(self,_):
        with self.assertRaisesRegex(ValueError,'MANUAL_STALE_SOURCE'):
            review_manual_spans(self.stage,'synthetic__1','0'*64,
                 [{'start':4,'end':7,'label':'person'}],
                 'reviewer1',Detector(),KEY)
        self.assertEqual(self.stage['documents'][0]['state'],'REVIEW_PENDING')
    @patch('dataset_workflow.prepare',side_effect=mock_prepare.__func__)
    def test_span_overlap_rejected(self,_):
        with self.assertRaisesRegex(ValueError,'MANUAL_SPANS_OVERLAP'):
            review_manual_spans(self.stage,'synthetic__1',self.hash,
              [{'start':4,'end':7,'label':'person'}, {'start':6,'end':8,'label':'person'}],
              'reviewer1',Detector(),KEY)
    @patch('dataset_workflow.prepare',side_effect=mock_prepare.__func__)
    def test_missing_token_not_approved(self,mock):
        mock.side_effect=lambda did,text,detector,key: Outcome(did,'LOCAL_PREPARED','OK',sanitized_text='원문 제거')
        pos=self.raw.index('홍길동')
        with self.assertRaisesRegex(ValueError,'MANUAL_TOKEN_MISSING_AFTER_RECHECK'):
            review_manual_spans(self.stage,'synthetic__1',self.hash,
               [{'start':pos,'end':pos+3,'label':'person'}],
               'reviewer1',Detector(),KEY)
        self.assertEqual(self.stage['documents'][0]['state'],'REVIEW_PENDING')
    @patch('dataset_workflow.prepare',side_effect=mock_prepare.__func__)
    def test_recheck_review_not_auto_ready(self,mock):
        mock.side_effect=lambda did,text,detector,key: Outcome(did,'REVIEW','GATE_NOT_READY')
        pos=self.raw.index('홍길동')
        with self.assertRaisesRegex(ValueError,'MANUAL_RECHECK_NOT_READY'):
            review_manual_spans(self.stage,'synthetic__1',self.hash,
               [{'start':pos,'end':pos+3,'label':'person'}],
               'reviewer1',Detector(),KEY)
        self.assertEqual(self.stage['documents'][0]['state'],'REVIEW_PENDING')

if __name__=='__main__':unittest.main()
