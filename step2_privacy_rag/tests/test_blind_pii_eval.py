"""Pure schema+scoring tests with synthetic data only, no HF model access."""
import json
import tempfile
from pathlib import Path
import unittest
import zipfile
from scripts.eval_blind_pii import read_rows,set_spans,mask_coverage,counts_to_metrics,latency_summary
from collections import Counter


class BlindPIIEvalContract(unittest.TestCase):
    def test_zip_jsonl_schema_and_gold_offsets(self):
        doc={'id':'SYN_1','text':'익명객',
             'entities':[{'start':0,'end':3,'text':'익명객','label':'person'}],
             'doc_type':'synthetic'}
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'sample.zip'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('blind.jsonl',json.dumps(doc,ensure_ascii=False)+'\n')
                archive.writestr('__MACOSX/._blind.jsonl','ignored')
            self.assertEqual(len(read_rows(path)),1)
            self.assertEqual(set_spans(doc['entities']),{('person',0,3)})
            h,total=mask_coverage(doc['entities'],[{'start':0,'end':2}],3)
            self.assertEqual((h,total),(2,3))

    def test_invalid_offsets_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'bad.jsonl'
            item={'id':'SYN_1','text':'익명객',
                  'entities':[{'start':1,'end':3,'text':'익명객','label':'person'}]}
            path.write_text(json.dumps(item,ensure_ascii=False),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'BLIND_GOLD_OFFSET_MISMATCH'):
                read_rows(path)

    def test_timing_percentiles(self):
        vals=latency_summary([10,20,30,40,50])
        self.assertEqual(vals['p50_ms'],30)
        self.assertEqual(vals['p95_ms'],48)
        self.assertEqual(latency_summary([])['n'],0)

    def test_strict_metric(self):
        result=counts_to_metrics(Counter(tp=2,fp=1,fn=2))
        self.assertEqual(result['precision'],0.666667)
        self.assertEqual(result['recall'],0.5)
        self.assertEqual(result['strict_f1'],0.571429)


if __name__=='__main__':
    unittest.main()
