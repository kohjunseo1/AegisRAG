"""Private blind benchmark source/Gold IDs, synthetic only."""
import json,tempfile,unittest,zipfile
from pathlib import Path
from scripts.prepare_blind_100 import docs_from_zip,validate,prepare

class BlindQATests(unittest.TestCase):
    def test_gold_and_hmac_mapping(self):
        doc={'id':'B0001','text':'서류 제출일은 금요일입니다.'}
        q=[{'qid':'BF001','query':'제출일이 언제야?','split':'dev','type':'fact_single'}]
        g=[{'qid':'BF001','relevant_source_ids':['B0001'],'expected_decision':'ANSWER','support_phrases':['금요일'],'split':'dev','type':'fact_single'}]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'sample.zip'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('blind_eval.jsonl',json.dumps(doc,ensure_ascii=False)+'\n')
                z.writestr('__MACOSX/._blind_eval.jsonl','ignored')
            docs=docs_from_zip(p)
            by=validate(q,g,docs)
            rows=prepare(q,by,'blind_v1',b'R'*32,'dev')
            self.assertEqual(rows[0]['expected_decision'],'ANSWER')
            self.assertTrue(rows[0]['relevant_doc_ids'][0].startswith('blind_v1__'))
            self.assertNotIn('B0001',rows[0]['relevant_doc_ids'][0])
            g[0]['support_phrases']=['없음']
            with self.assertRaisesRegex(ValueError,'BLIND_QA_GOLD_NOT_GROUNDED'):
                validate(q,g,docs)
