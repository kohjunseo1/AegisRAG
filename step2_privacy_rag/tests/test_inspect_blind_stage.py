"""No PII/model dependencies: Stage diff and mask coverage unit tests."""
import hashlib
import hmac
import unittest
from scripts.inspect_blind_stage import covers,overlaps,compare_from_index
from dataset_workflow import doc_id

class StageSafeAudit(unittest.TestCase):
    def test_masks(self):
        self.assertTrue(covers(0,3,[{'start':0,'end':2},{'start':2,'end':3}]))
        self.assertFalse(covers(0,3,[{'start':0,'end':2}]))
        self.assertTrue(overlaps(0,3,[{'start':2,'end':5}]))
    def test_changed_status_without_raw_ids(self):
        key=b'K'*32
        docs=[{'id':'SYN100'}]
        expected={'dataset_documents':1,'per_doc':[{'case':1,'intake_state':'BLOCKED'}]}
        stage={'dataset':'blind_v2','documents':[{'doc_id':doc_id('blind_v2','SYN100',key),
               'source_id':'SYN100','state':'REVIEW_PENDING','reason':'GATE_NOT_READY'}]}
        changes=compare_from_index(docs,expected,stage,key)
        self.assertEqual(changes[0]['case'],1)
        self.assertEqual(changes[0]['old'],'BLOCKED')
        self.assertEqual(changes[0]['new'],'REVIEW_PENDING')
