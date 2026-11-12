"""Pure tests for the natural language E2E evaluation runner."""
import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch
from scripts.eval_chat_mvp import load_cases, metric_report, write_example_cases


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

    def test_example_generator_writes_valid_git_ignored_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root/'step2_privacy_rag/local_eval/mvp_natural_questions.jsonl'
            with patch('scripts.eval_chat_mvp.ROOT', root):
                write_example_cases(path)
                examples = load_cases(path)
                self.assertEqual(len(examples), 15)
                self.assertIn('경조사', examples[0]['question'])
                with self.assertRaisesRegex(ValueError, 'MVP_EXAMPLES_ALREADY_EXIST_NO_OVERWRITE'):
                    write_example_cases(path)

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.jsonl'
            obj={'case_id':'A','question':'합성 질문'}
            p.write_text(json.dumps(obj)+'\n'+json.dumps(obj)+'\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'MVP_CASE_SCHEMA_INVALID'):
                load_cases(p)


if __name__=='__main__':
    unittest.main()
