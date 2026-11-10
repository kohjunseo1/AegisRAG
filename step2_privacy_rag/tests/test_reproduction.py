import copy
import unittest
from unittest.mock import MagicMock, patch

from reproduction import ingest, target, validate_target, provenance
from smoke import FIXTURE_KEY
from prepare_v6_retrieval_v1 import key_tag


class ReproductionSafety(unittest.TestCase):
    def setUp(self):
        self.data = dict(documents=[dict(doc_id='S001',text='합성 안내',region_keys=[])],hmac_key_tag=key_tag(FIXTURE_KEY))

    def test_existing_baseline_cannot_be_write_target(self):
        conn = MagicMock()
        for name in ['rag.aegis_koe5_v6_exp1','rag.chunks','public.x; DROP TABLE rag.chunks','rag.aegis_repro_invalid']:
            with self.assertRaises(ValueError):
                ingest(conn,self.data,[],name,name)
        conn.cursor.assert_not_called()

    def test_confirmation_required_before_any_sql(self):
        conn = MagicMock(); name = target(self.data)
        with self.assertRaises(ValueError):
            ingest(conn,self.data,[],name,None)
        conn.cursor.assert_not_called()

    def test_gold_never_changes_index(self):
        other = copy.deepcopy(self.data)
        other['gold_for_evaluation_only'] = [{'qid':'Q001','relevant_doc_ids':['wrong']}]
        other['queries'] = [{'query':'평가 전용'}]
        self.assertEqual(target(self.data),target(other))

    def test_document_or_key_changes_get_separate_table(self):
        for key,value in [('documents',[dict(doc_id='S001',text='다른 합성 안내',region_keys=[])]),('hmac_key_tag','other')]:
            other = copy.deepcopy(self.data); other[key] = value
            self.assertNotEqual(target(self.data),target(other))

    @patch('reproduction.fixed_key', return_value=FIXTURE_KEY)
    @patch('reproduction.LivePipeline')
    def test_indexable_and_full_gold_recall_are_distinct(self, pipeline, _):
        from reproduction import evaluate
        data = dict(documents=[dict(doc_id='A'),dict(doc_id='B')],
                    queries=[dict(qid='Q1',text='합성 질의',region_keys=[])],
                    gold_for_evaluation_only=[dict(qid='Q1',relevant_doc_ids=['A','B','MISSING'])])
        pipeline.return_value.retrieve.return_value = [dict(doc_id='A'),dict(doc_id='B')]
        report, _ = evaluate(data, 'rag.synthetic', None)
        self.assertEqual(report['recall5'], 1.0)
        self.assertEqual(report['recall5_full_gold'], .6667)


if __name__ == '__main__':
    unittest.main()
