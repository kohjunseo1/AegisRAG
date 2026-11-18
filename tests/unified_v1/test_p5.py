from dataclasses import replace
from unittest.mock import patch
import unittest
from unified_v1.adapters import DetectionEngine, AxAdapter, VeilAdapter
from unified_v1.contracts import ContractError
from unified_v1.protection import V6GateAdapter, prepare
from unified_v1.retrieval import IndexedDocument, QuerySpec, approve_experiment
from unified_v1.generation import (PrivacyRecheck, ContextBuilder, MockTransport, MockGenerator, NetworkBlockedTransport)
from test_p2 import KEY, TAG
from test_p4 import TOKEN1, TOKEN2, REGION, approved, index


def guard(predictor=lambda text:[]):
    return PrivacyRecheck(DetectionEngine('own2',{'ax':AxAdapter('mock-independent',predictor),'veil':VeilAdapter('mock-independent',lambda t:[])}))

def setup_context(response=None,text=None):
    ix=index();p,a=approved(text or TOKEN1+' 합성 정책은 승인 절차를 거칩니다.')
    ix.add(IndexedDocument('synthetic_doc',p,a))
    q,b=approved(TOKEN1+' 정책 안내','query');query=QuerySpec(q,b)
    evidence=ix.retrieve(query,dense_scorer=lambda q,d:{'synthetic_doc':1})
    builder=ContextBuilder(guard(),key_tag=ix.key_tag,policy_version=ix.policy_version,index_version=ix.index_version,corpus_id=ix.corpus_id)
    transport=MockTransport(response or dict(decision='ANSWER',answer='승인 절차를 거칩니다.',citations=['synthetic_doc']))
    return MockGenerator(builder,transport),query,evidence

class GenerationTests(unittest.TestCase):
    def test_network_blocked_mock_e2e_from_raw_to_response(self):
        class Baseline:
            def ax_predict(self,text):
                start=text.find('금재원')
                return [dict(label='person',start=start,end=start+3)]
            def veil_predict(self,text):return self.ax_predict(text)
            def detect(self,text):
                spans=self.ax_predict(text)
                return dict(status='READY_FOR_PSEUDONYMIZATION',entities=spans,
                            mask_intervals=[dict(start=s['start'],end=s['end']) for s in spans])
        base=Baseline();engine=DetectionEngine('own2',{'ax':AxAdapter('mock',base.ax_predict),'veil':VeilAdapter('mock',base.veil_predict)})
        independent=guard();ix=index(key_tag=TAG)
        with patch('socket.socket',side_effect=AssertionError('NETWORK_FORBIDDEN')), patch('urllib.request.urlopen',side_effect=AssertionError('NETWORK_FORBIDDEN')):
            prepared=[]
            for kind,text in [('document','금재원 합성 정책은 승인 절차를 거칩니다.'),('query','금재원 정책 안내')]:
                ev=engine.detect(text,input_kind=kind)
                plan=V6GateAdapter(base).plan(text,ev)
                p=prepare(text,plan,input_kind=kind,hmac_key=KEY,key_tag=TAG)
                self.assertNotIn('금재원',p.sanitized_text)
                prepared.append(approve_experiment(p,corpus_id='synthetic',checker=independent.check))
            p,a=prepared[0];ix.add(IndexedDocument('synthetic_doc',p,a))
            q,a=prepared[1];query=QuerySpec(q,a)
            rows=ix.retrieve(query,dense_scorer=lambda q,d:{'synthetic_doc':1})
            builder=ContextBuilder(independent,key_tag=TAG,policy_version='evidence_v1',index_version=ix.index_version,corpus_id='synthetic')
            transport=MockTransport(dict(decision='ANSWER',answer='승인 절차를 거칩니다.',citations=['synthetic_doc']))
            result=MockGenerator(builder,transport).generate(query,rows)
            self.assertEqual(result.mode,'MOCK_PASS');self.assertEqual(transport.calls,1)
            self.assertNotIn('금재원',result.answer)
            self.assertEqual(result.citations,('synthetic_doc',))
            self.assertEqual(result.claim_support_status,'CITATION_IDS_ONLY_NOT_FACTUAL_VERIFICATION')
            self.assertTrue(result.support_evidence)

    def test_24hex_preserved_unknown_short_tokens_rejected(self):
        g=guard();self.assertTrue(g.check(TOKEN1,allowed_tokens={TOKEN1}))
        with self.assertRaisesRegex(ContractError,'UNAUTHORIZED'):g.check(TOKEN2,allowed_tokens={TOKEN1})
        for text in ['<PERSON_12345678>','<UNKNOWN_'+'a'*24+'>']:
            with self.assertRaises(ContractError):g.check(text)

    def test_residual_patterns_names_and_independent_failure(self):
        for text in ['연락 010-0000-0000','synthetic@example.invalid','금 재원 님']:
            with self.assertRaisesRegex(ContractError,'RESIDUAL_PII'):guard().check(text)
        def detect_bare_name(text):
            return [dict(label='person',start=0,end=3)] if '금재원' in text else []
        with self.assertRaisesRegex(ContractError,'RESIDUAL_PII'):guard(detect_bare_name).check('금재원 안내')
        with self.assertRaises(ContractError):PrivacyRecheck(None).check('안내')
        with self.assertRaises(ContractError):PrivacyRecheck(DetectionEngine('quality4',{})).check('안내')

    def test_pretransport_rejections(self):
        for bad in ['010-0000-0000','synthetic@example.invalid']:
            gen,q,rows=setup_context(text=bad)
            with self.assertRaises(ContractError):gen.generate(q,rows)
            self.assertEqual(gen.transport.calls,0)
        gen,q,rows=setup_context()
        for r in [replace(rows[0],approval_state='NOT_APPROVED'),replace(rows[0],key_tag='wrong'),replace(rows[0],index_version='wrong')]:
            with self.assertRaises(ContractError):gen.generate(q,(r,))
        self.assertEqual(gen.transport.calls,0)
        with self.assertRaises(ContractError):gen.generate(replace(q,preparation=replace(q.preparation,state='REVIEW')),rows)

    def test_schema_citation_and_output_failure(self):
        bad_responses=[dict(decision='ANSWER',answer='안내',citations=['invented']),
                       dict(decision='ANSWER',answer='안내',citations=[]),
                       dict(decision='ANSWER',answer='010-0000-0000',citations=['synthetic_doc']),
                       dict(decision='ANSWER',answer=TOKEN2,citations=['synthetic_doc']),
                       dict(decision='ANSWER',answer='안내',citations=['synthetic_doc'],extra='bad')]
        for response in bad_responses:
            gen,q,rows=setup_context(response)
            with self.assertRaises(ContractError):gen.generate(q,rows)

    def test_live_transport_and_region_citation_rejected(self):
        gen,q,rows=setup_context()
        with self.assertRaisesRegex(ContractError,'FORBIDDEN'):MockGenerator(gen.builder,NetworkBlockedTransport())
        with self.assertRaises(ContractError):NetworkBlockedTransport().complete({})
        q=replace(q,regions=(REGION,))
        with self.assertRaises(ContractError):gen.generate(q,rows)

    def test_truncation_and_duplicate_evidence_fail_closed(self):
        gen,q,rows=setup_context(text='합성 '*498+TOKEN1)
        with self.assertRaises(ContractError):gen.generate(q,rows)
        gen,q,rows=setup_context()
        with self.assertRaises(ContractError):gen.generate(q,rows+rows)
