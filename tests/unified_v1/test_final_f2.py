import copy
from dataclasses import replace
import unittest
from unified_v1.contracts import ContractError,PersonMention
from unified_v1.sidecar import create_sidecar,validate_sidecar,coverage,legacy_lookup_key
from unified_v1.retrieval import IndexedDocument,QuerySpec
from test_p2 import KEY,TAG
from test_p4 import approved,index,TOKEN1,ENTITY1

class SidecarTests(unittest.TestCase):
    def test_no_raw_ownership_no_identity_and_signature_binding(self):
        rows=[dict(doc_id='d1',masked_text=TOKEN1),dict(doc_id='d2',masked_text=TOKEN1)]
        side=create_sidecar(rows,index_fingerprint='snapshot',key=KEY,key_tag=TAG)
        self.assertEqual(coverage(side)['unresolved_rate'],1)
        self.assertEqual(coverage(side)['entity_documents'],0)
        validate_sidecar(side,index_fingerprint='snapshot',key=KEY,key_tag=TAG)
        bad=copy.deepcopy(side);bad['payload']['entries']['d1']['entity_ids']=[ENTITY1]
        with self.assertRaises(ContractError):validate_sidecar(bad,index_fingerprint='snapshot',key=KEY,key_tag=TAG)
        with self.assertRaises(ContractError):validate_sidecar(side,index_fingerprint='different',key=KEY,key_tag=TAG)
        linked=PersonMention('m','d1','name',(),(),ENTITY1,'LINKED')
        with self.assertRaisesRegex(ContractError,'PROVENANCE'):create_sidecar(rows,index_fingerprint='snapshot',key=KEY,key_tag=TAG,linked_mentions=[linked])

    def test_lookup_candidate_ranked_without_becoming_exact_identity(self):
        ix=index();lookup=legacy_lookup_key(KEY,TOKEN1)
        for did,text,keys in [('name_candidate','절차 안내',(lookup,)),('dense_first','일반 안내',())]:
            p,a=approved(text);ix.add(IndexedDocument(did,p,a,name_lookup_keys=keys))
        p,a=approved('이름 후보 조회','query');q=QuerySpec(p,a,lookup_only=True,name_lookup_keys=(lookup,))
        result=ix.retrieve(q,profile='hybrid_v1',dense_scorer=lambda q,d:{'name_candidate':0,'dense_first':1})
        self.assertEqual(result[0].doc_id,'name_candidate');self.assertEqual(result[0].identity_match,'weak')
