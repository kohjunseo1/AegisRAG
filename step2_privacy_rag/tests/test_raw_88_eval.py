"""Synthetic-only 88-query integration contract; never loads live models or private data."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from core import PreparedQuery
from scripts.verify_v6_e2e import eval_raw_holdout


class DummyPipeline:
    def __init__(self):
        self.timings = {}
        self.preloaded = 0
        self.prepared = 0
        self.retrieved = 0

    def preload_models(self):
        self.preloaded += 1
        return dict(step1_ms=1,embedding_ms=1,total_ms=2)

    def prepare(self, text):
        self.prepared += 1
        return 'REVIEW' if text == 'synthetic-review' else PreparedQuery('safe-query',set(),set())

    def retrieve(self, prepared, top_k=None):
        self.retrieved += 1
        self.timings = dict(step1_process_ms=1,embedding_ms=2,db_ms=1,rerank_ms=.1)
        return [dict(doc_id='H001')]


class RawOnlineEvalContracts(unittest.TestCase):
    def test_all_88_include_review_in_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qp, gp = root/'queries.jsonl', root/'gold.jsonl'
            with qp.open('w',encoding='utf-8') as f:
                for i in range(1,89):
                    f.write(json.dumps(dict(qid=f'Q{i:03}',query='synthetic-review' if i == 1 else 'synthetic-ready'))+'\n')
            with gp.open('w',encoding='utf-8') as f:
                for i in range(1,89):
                    f.write(json.dumps(dict(qid=f'Q{i:03}',relevant_doc_ids=['H001'],type='synthetic'))+'\n')
            p=DummyPipeline()
            results={}
            with patch('scripts.verify_v6_e2e.index_ids',return_value={'H001'}), \
                 patch('scripts.verify_v6_e2e.info',side_effect=lambda key,val: results.__setitem__(key,val)):
                self.assertTrue(eval_raw_holdout(p,qp,gp))
            metrics=results['RAW_88_RETRIEVAL_METRICS']
            self.assertEqual(metrics['total'],88)
            self.assertEqual(metrics['eligible'],87)
            self.assertEqual(metrics['deferred'],1)
            self.assertEqual(metrics['full_set_hit5_rate'],round(87/88,4))
            self.assertEqual(metrics['query_states'],{'READY':87,'REVIEW':1})
            self.assertEqual(p.retrieved,87)
            self.assertEqual(p.preloaded,1)

    def test_missing_inputs_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs={}
            with patch('scripts.verify_v6_e2e.info',side_effect=lambda k,v: outputs.__setitem__(k,v)):
                self.assertFalse(eval_raw_holdout(DummyPipeline(),root/'missingq',root/'missingg'))
            self.assertIn('EVAL_INPUT_MISSING',outputs)


if __name__=='__main__':
    unittest.main()
