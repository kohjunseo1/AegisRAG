from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unified_v1.selection import raw_score, score_fusion, fusion_outcome, pack, unpack, write_json
from unified_v1.selection_retrieval import score_ranking,pure_reference,chunk_rank
from unified_v1.identity import extract_owned_fields,pseudonym_token
from unified_v1.sidecar import legacy_lookup_key,create_sidecar
from unified_v1.protection import fuse
from unified_v1.contracts import ContractError
from test_p2 import evidence,baseline,KEY,TAG
from test_p4 import approved,index

class SelectionTests(unittest.TestCase):
    def test_frozen_state_comes_from_final_prepare_not_intermediate_gate(self):
        from types import SimpleNamespace
        from unified_v1.selection import frozen_outcome
        text='합성 업무 안내';ev=evidence(text)
        with patch('unified_v1.selection.SnapshotGateAdapter.check',return_value=dict(decision='REVIEW_REQUIRED')):
            with patch('document_preparation.prepare',return_value=SimpleNamespace(state='BLOCKED')):
                out=frozen_outcome(text,ev)
        self.assertTrue(out['blocked']);self.assertFalse(out['ready'])

    def test_raw_union_has_no_gate_dependency_and_counts_unicode_boundaries(self):
        gold={('person',1,4)}
        c=raw_score({('address',1,4),('person',1,3)},gold)
        self.assertEqual((c['tp'],c['fp'],c['fn'],c['boundary_only'],c['fully_missed']),(0,2,1,1,0))
        self.assertEqual(raw_score(set(),gold)['fully_missed'],1)

    def test_partial_protection_is_not_whole_entity_and_pending_is_not_ready(self):
        out=dict(pred=set(),hmac=set(),masks=((1,3),),ready=False,blocked=False,pending=1)
        c=score_fusion(out,{('person',1,4)})
        self.assertEqual((c['whole_protected'],c['mask_completely_missed'],c['REVIEW']),(0,0,1))

    def test_hybrid_label_conflicts_preserve_boundary_and_do_not_change_hmac(self):
        text='합성 고객';ev=evidence(text)
        frozen=dict(pred={('person',0,2)},hmac={('person',0,2)},masks=((0,2),),ready=True,blocked=False,pending=0)
        with patch('unified_v1.selection.frozen_outcome',return_value=frozen):
            out=fusion_outcome(text,ev,'F3',dict(spans=[dict(label='address',start=0,end=2)],review=[]))
        self.assertEqual(out['hmac'],frozen['hmac']);self.assertEqual(out['masks'],frozen['masks']);self.assertFalse(out['ready'])

    def test_cascade_does_not_claim_to_find_marker_free_misses(self):
        text='합성 업무 안내';ev=evidence(text)
        frozen=dict(pred=set(),hmac=set(),masks=(),ready=True,blocked=False,pending=0)
        with patch('unified_v1.selection.frozen_outcome',return_value=frozen):out=fusion_outcome(text,ev,'F5')
        self.assertFalse(out['cascade_trigger']);self.assertFalse(out['masks'])

    def test_actual_cascade_does_not_call_auxiliary_models_without_trigger(self):
        from unittest.mock import Mock
        from unified_v1.selection import ConditionalCascade
        session=Mock();session.infer.return_value=evidence('합성 업무 안내')
        frozen=dict(pred=set(),hmac=set(),masks=(),ready=True,blocked=False,pending=0)
        with patch('unified_v1.selection.frozen_outcome',return_value=frozen):out=ConditionalCascade(session).run('합성 업무 안내')
        self.assertFalse(out['cascade_trigger']);session.infer.assert_called_once_with('합성 업무 안내',profile='own2')
        self.assertEqual(session.workers.mock_calls,[])

    def test_raw_snapshot_roundtrip_and_private_file_mode(self):
        ev=evidence('금재원',[dict(label='person',start=0,end=3)])
        restored=unpack(pack(ev));self.assertEqual(restored.original_input_fingerprint,ev.original_input_fingerprint)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'private/result.json';write_json(path,dict(count=1),True)
            self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_query_and_document_lookup_share_frozen_token_namespace(self):
        for name in ['금재원','금 재원','남궁 수']:
            text='성명: '+name;span=dict(label='person',start=4,end=len(text))
            ev=evidence(text,[span]);plan=fuse(text,ev,baseline([span]))
            bundle=extract_owned_fields(text,plan,doc_id='query',hmac_key=KEY,key_tag=TAG)
            token=pseudonym_token(KEY,'person',name)
            self.assertEqual(bundle.mentions[0].name_lookup_key,legacy_lookup_key(KEY,token))
            self.assertIsNone(bundle.mentions[0].entity_id)

    def test_top10_bounds_and_full_actual_cutoff_neutral_scoring(self):
        from unified_v1.retrieval import IndexedDocument,QuerySpec
        ix=index()
        for i in range(12):
            p,a=approved('합성 안내');ix.add(IndexedDocument('d'+str(i),p,a))
        q,a=approved('합성 안내','query');spec=QuerySpec(q,a)
        scorer=lambda q,d:{r.doc_id:0 for r in d}
        self.assertEqual(len(ix.retrieve(spec,top_k=10,dense_scorer=scorer)),10)
        for k in [0,11,True]:
            with self.assertRaises(ContractError):ix.retrieve(spec,top_k=k,dense_scorer=scorer)
        gold=dict(relevant_doc_ids=['r'],neutral_doc_ids=['n'],confusable_doc_ids=['c'])
        c=score_ranking(['n','r','c'],gold,True)
        self.assertEqual((c['hit1'],c['online_hit1'],c['hit5'],c['mrr10'],c['confusable5']),(1,0,1,1,1))

    def test_reference_chunk_merge_deduplicates_and_never_splits_token(self):
        fixture=Path(__file__).parent/'fixtures/reference'
        split=pure_reference('ingest.py','chunk',reference=fixture);merge=pure_reference('search.py','_merge',reference=fixture)
        token='<PERSON_'+'a'*24+'>'
        self.assertEqual(split(token+'\n'+token,40),[token,token])
        self.assertEqual([r['doc_id'] for r in merge([[dict(doc_id='a'),dict(doc_id='a')],[dict(doc_id='b')]],2)],['a','b'])

    def test_db_rejects_malformed_identity_before_embedding_or_sql(self):
        from unittest.mock import Mock
        from unified_v1.postgres import ExistingIndexReader
        from unified_v1.retrieval import QuerySpec
        reader=ExistingIndexReader.__new__(ExistingIndexReader)
        reader.key_tag='synthetic';reader.policy_version='evidence_v1';reader.corpus_id='synthetic'
        p,a=approved('합성 안내','query');encoder=Mock()
        with self.assertRaisesRegex(ContractError,'INVALID_IDENTITY'):
            reader.retrieve(QuerySpec(p,a,name_lookup_keys=('arbitrary',)),encoder=encoder)
        encoder.encode_query.assert_not_called()

    def test_guard_diagnostics_preserve_model_offsets_and_reset_between_inputs(self):
        from unified_v1.generation import PrivacyRecheck
        class Engine:
            def detect(self,text,**kw):
                return evidence(text,[dict(label='person',start=0,end=2)] if text=='업무 검토' else [])
        guard=PrivacyRecheck(Engine())
        with self.assertRaisesRegex(ContractError,'RESIDUAL_PII'):guard.check('업무 검토')
        self.assertEqual((guard.last_diagnostics[0]['source'],guard.last_diagnostics[0]['start'],guard.last_diagnostics[0]['end']),('ax',0,2))
        self.assertTrue(guard.check('절차 안내'))
        self.assertEqual(guard.last_diagnostics,())

    def test_multiple_legacy_name_matches_without_ownership_require_clarification(self):
        from unified_v1.contracts import PersonMention
        from unified_v1.pipeline import query_from_mentions
        from unified_v1.retrieval import IndexedDocument
        from unified_v1.generation import ContextBuilder
        from test_p5 import guard
        token='<PERSON_'+'a'*24+'>';name='n_'+'a'*24
        ix=index()
        for did in ['a','b']:
            p,a=approved(token);ix.add(IndexedDocument(did,p,a,name_lookup_keys=(name,)))
        p,a=approved(token,'query');q=query_from_mentions(p,a,[PersonMention('m','q',name,(),('NO_OWNERSHIP_PROOF',))])
        rows=ix.retrieve(q)
        builder=ContextBuilder(guard(),key_tag=ix.key_tag,policy_version=ix.policy_version,index_version=ix.index_version,corpus_id=ix.corpus_id)
        with self.assertRaisesRegex(ContractError,'CLARIFY_UNVERIFIED'):builder.build(q,rows)

if __name__=='__main__':unittest.main()
