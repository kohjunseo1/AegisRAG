"""Pure tests for the natural language E2E evaluation runner."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.eval_chat_mvp import load_cases, metric_report


class TestMVPEval(unittest.TestCase):
    def test_labeled_and_unlabeled_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'cases.jsonl'
            p.write_text(
                json.dumps({'case_id':'A','question':'경조사 공지','relevant_doc_ids':['H0025']})+'\n'+
                json.dumps({'case_id':'B','question':'복리후생 공지'})+'\n',
                encoding='utf-8')
            cs=load_cases(p)
            self.assertEqual(len(cs),2)
            rows=[
                {'labeled':True,'hit1':1,'hit5':1,'gate_state':'READY',
                 'decision':'ANSWER','cited_expected':True,'total_ms':20},
                {'labeled':False,'hit1':0,'hit5':0,'gate_state':'REVIEW',
                 'decision':None,'cited_expected':None,'total_ms':40},
            ]
            s=metric_report(rows)
            self.assertEqual(s['total_cases'],2)
            self.assertEqual(s['labeled_cases'],1)
            self.assertEqual(s['hit5_rate_labeled'],1.0)
            self.assertEqual(s['hcx_decisions']['ANSWER'],1)

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.jsonl'
            obj={'case_id':'A','question':'합성 질문'}
            p.write_text(json.dumps(obj)+'\n'+json.dumps(obj)+'\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'MVP_CASE_SCHEMA_INVALID'):
                load_cases(p)


if __name__=='__main__':
    unittest.main()
