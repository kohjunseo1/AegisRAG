"""Safe Blind held-case selection tests, no model or private corpus."""
import hashlib
import unittest
from scripts.diagnose_blind_holds import select_nonready

class TestSelection(unittest.TestCase):
    def test_only_held_cases(self):
        docs=[{'id':'B0001'},{'id':'B0002'}]
        report={'dataset_documents':2,'per_doc':[
            {'case':1,'doc_id_hash8':hashlib.sha256(b'B0001').hexdigest()[:8],
             'intake_state':'READY'},
            {'case':2,'doc_id_hash8':hashlib.sha256(b'B0002').hexdigest()[:8],
             'intake_state':'BLOCKED'}]}
        self.assertEqual([i for i,*_ in select_nonready(docs,report)],[2])
        docs[1]['id']='B9999'
        with self.assertRaisesRegex(ValueError,'BLIND_DIAG_INPUT_CHANGED'):
            select_nonready(docs,report)
