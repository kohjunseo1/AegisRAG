from dataclasses import replace
import unittest
from unified_v1.contracts import ContractError, PreparationResult
from unified_v1.retrieval import (InMemoryIndex, IndexedDocument, QuerySpec, approve_experiment, evaluate_rankings)
from unified_v1.database import IsolatedMigration

TOKEN1='<PERSON_'+'a'*24+'>'
TOKEN2='<PHONE_NUMBER_'+'b'*24+'>'
REGION='region_v1_'+'c'*24
ENTITY1='e_'+'d'*24
ENTITY2='e_'+'e'*24

def approved(text,kind='document',**kw):
    p=PreparationResult('LOCAL_PREPARED','LINKED','NOT_APPROVED',text,'synthetic','evidence_v1',kind,coverage_verified=True)
    return approve_experiment(p,corpus_id='synthetic',checker=lambda t:True,**kw)

def index(key_tag='synthetic'):
    return InMemoryIndex(key_tag=key_tag,policy_version='evidence_v1',corpus_id='synthetic')

class RetrievalTests(unittest.TestCase):
    def test_union_profiles_and_provenance(self):
        ix=index()
        for did,text,region in [('both',TOKEN1+TOKEN2,(REGION,)),('all',TOKEN1+TOKEN2,()),
                                ('any',TOKEN1,()),('region','합성 지역',(REGION,)),('dense','합성 안내',())]:
            p,a=approved(text);ix.add(IndexedDocument(did,p,a,region))
        q,a=approved(TOKEN1+TOKEN2,'query');spec=QuerySpec(q,a,(REGION,))
        scorer=lambda q,d:{'dense':1.0,'region':.8,'any':.6,'all':.4,'both':.2}
        for profile in ['own_v6','team_v3','hybrid_v1']:
            rows=ix.retrieve(spec,profile=profile,dense_scorer=scorer)
            self.assertEqual([r.doc_id for r in rows],['both','all','any','region','dense'])
            self.assertEqual(len({r.doc_id for r in rows}),len(rows))
            self.assertIn('TOKEN_ALL',rows[0].evidence_reason_codes)
            self.assertIn('DENSE',rows[-1].evidence_reason_codes)
        self.assertEqual(len(ix.retrieve(spec)),4) # union token/region without dense provider

    def test_exact_entity_conflict_cannot_be_overturned_by_dense(self):
        ix=index()
        for did,entity in [('correct',ENTITY1),('conflict',ENTITY2)]:
            p,a=approved(TOKEN1);ix.add(IndexedDocument(did,p,a,entity_ids=(entity,)))
        q,a=approved(TOKEN1,'query')
        result=ix.retrieve(QuerySpec(q,a,entity_ids=(ENTITY1,)),dense_scorer=lambda q,d:{'correct':0,'conflict':1})
        self.assertEqual([r.doc_id for r in result],['correct'])
        with self.assertRaisesRegex(ContractError,'CLARIFY'):
            ix.retrieve(QuerySpec(q,a,identity_unresolved=True))

    def test_unapproved_raw_mask_only_key_and_modified_receipt_rejected(self):
        ix=index();p,a=approved('합성 안내')
        for bad in [replace(p,approval_state='NOT_APPROVED'),replace(p,state='REVIEW'),replace(p,key_tag='wrong'),
                    replace(p,sanitized_text='changed'),replace(p,protection_kind='MASKED_ONLY')]:
            with self.assertRaises(ContractError):ix.add(IndexedDocument('d',bad,a))
        with self.assertRaises(ContractError):approve_experiment(replace(p,protection_kind='MASKED_ONLY'),corpus_id='synthetic',checker=lambda t:True)
        with self.assertRaises(ContractError):approve_experiment(p,corpus_id='synthetic',checker=lambda t:False)
        ix.add(IndexedDocument('d',p,a));ix.add(IndexedDocument('d',p,a))
        p2,a2=approved('다른 합성 안내')
        with self.assertRaises(ContractError):ix.add(IndexedDocument('d',p2,a2))

    def test_same_gold_evaluator_full_denominator_and_confusable(self):
        ix=index()
        for did in ['gold','neutral','confusable']:
            p,a=approved('합성');ix.add(IndexedDocument(did,p,a))
        queries=[dict(qid='q1'),dict(qid='q2'),dict(qid='q3')]
        gold=[dict(qid=q['qid'],relevant_doc_ids=['gold'],neutral_doc_ids=['neutral'],confusable_doc_ids=['confusable']) for q in queries]
        result=evaluate_rankings(ix,queries,gold,{'q1':['neutral','gold','confusable']},{'q1':'READY','q2':'REVIEW','q3':'BLOCKED'})
        self.assertEqual((result['eligible'],result['total']),(1,3))
        self.assertEqual(result['hit1_rate'],1)
        self.assertEqual(result['online_hit1_rate'],0)
        self.assertEqual(result['online_full_set_hit5_rate'],.3333)
        self.assertEqual(result['confusable_at5_rate'],1)
        self.assertEqual(result['actual_top5']['q1'],['neutral','gold','confusable'])

    def test_db_boundaries_and_no_execution(self):
        for name in ['rag','rag.aegis_live_docs','aegis_unified_docs','aegis_unified_test_x; DROP TABLE documents']:
            with self.assertRaises(ContractError):IsolatedMigration(name)
        plan=IsolatedMigration('aegis_unified_test_'+'a'*32)
        self.assertTrue(all('rag.' not in s for s in plan.statements()))
        with self.assertRaisesRegex(ContractError,'BLOCKED'):plan.execute()

    def test_dense_failure_and_query_gate_before_embedding(self):
        ix=index();p,a=approved('합성');ix.add(IndexedDocument('d',p,a))
        q,b=approved('합성','query');calls=[]
        def scorer(q,d):calls.append(1);return {'d':float('nan')}
        with self.assertRaises(ContractError):ix.retrieve(QuerySpec(replace(q,state='REVIEW'),b),dense_scorer=scorer)
        self.assertFalse(calls)
        with self.assertRaisesRegex(ContractError,'DENSE_SCORING_FAILED'):ix.retrieve(QuerySpec(q,b),dense_scorer=scorer)
