"""Pure synthetic tests for strict-FN/mask classification."""
import hashlib
import unittest
from scripts.diagnose_blind_fn import classify_fn,select_fn_cases

class TestFNTriage(unittest.TestCase):
    def test_overlap_classes(self):
        full=[{'start':0,'end':3}]
        self.assertEqual(classify_fn(('person',0,3),{('person',0,2)},full)['kind'],'same_label_boundary_mismatch')
        self.assertEqual(classify_fn(('person',0,3),{('address',0,3)},full)['kind'],'other_label_overlap')
        self.assertEqual(classify_fn(('person',0,3),set(),[])['kind'],'no_entity_overlap')
    def test_partial_mask(self):
        x=classify_fn(('person',0,3),set(),[{'start':0,'end':2}])
        self.assertEqual(x['mask_state'],'partially_masked')
        self.assertEqual(x['uncovered_chars'],1)
    def test_selection(self):
        docs=[{'id':'B0001','entities':[{}]},{'id':'B0002','entities':[]}]
        rows=[{'case':1,'doc_id_hash8':hashlib.sha256(b'B0001').hexdigest()[:8],
               'gold_entities':1,'strict_fn':1},
              {'case':2,'doc_id_hash8':hashlib.sha256(b'B0002').hexdigest()[:8],
               'gold_entities':0,'strict_fn':0}]
        self.assertEqual([i for i,*_ in select_fn_cases(docs,{'dataset_documents':2,'per_doc':rows})],[1])
