"""Network-blocked synthetic P1-P5 demo. Never accepts or prints user source text."""
import re
from unittest.mock import patch
from dataset_preparation import key_tag
from .adapters import AxAdapter, VeilAdapter, KlueAdapter, KoElectraAdapter, DetectionEngine
from .contracts import config_hash, fingerprint
from .experiment import mock_manifest
from .generation import PrivacyRecheck, ContextBuilder, MockTransport, MockGenerator
from .identity import resolve
from .pipeline import LocalPipeline, query_from_mentions
from .retrieval import InMemoryIndex, IndexedDocument, approve_experiment, evaluate_rankings

class SyntheticBaseline:
    """Known fixture raw predictions; no statistical detection quality claim."""
    def raw(self,text):
        out=[]
        for label,value in [('person','금재원'),('phone_number','010-0000-0000'),('email','synthetic@example.invalid')]:
            for match in re.finditer(re.escape(value),text):
                out.append(dict(label=label,start=match.start(),end=match.end()))
        return out
    ax_predict=raw
    veil_predict=raw

    def detect(self,text):
        rows=self.raw(text)
        return {'status':'READY_FOR_PSEUDONYMIZATION','entities':rows,
                'mask_intervals':[dict(start=r['start'],end=r['end']) for r in rows]}

def run_synthetic(profile='own2'):
    baseline=SyntheticBaseline()
    adapters={a.source_id:a for a in [cls('mock-only-v1',baseline.raw)
                                    for cls in [AxAdapter,VeilAdapter,KlueAdapter,KoElectraAdapter]]}
    engine=DetectionEngine(profile,adapters)
    independent=PrivacyRecheck(DetectionEngine('own2',{'ax':AxAdapter('independent-mock-v1',baseline.raw),
                                                      'veil':VeilAdapter('independent-mock-v1',baseline.raw)}))
    key=b'unified-synthetic-only-key-00000000';tag=key_tag(key)
    pipeline=LocalPipeline(engine,baseline,hmac_key=key,key_tag=tag,reuse_raw=False)
    text='고객명: 금재원\n전화: 010-0000-0000\n이메일: synthetic@example.invalid\n합성 정책은 승인 절차를 거칩니다.'
    question='고객명: 금재원\n전화: 010-0000-0000\n이메일: synthetic@example.invalid\n정책 안내'
    def forbidden(*args,**kwargs):raise AssertionError('SYNTHETIC_NETWORK_FORBIDDEN')
    with patch('socket.socket.connect',forbidden),patch('socket.create_connection',forbidden),patch('urllib.request.urlopen',forbidden):
        doc=pipeline.prepare(text,input_kind='document',doc_id='synthetic_doc')
        query=pipeline.prepare(question,input_kind='query',doc_id='synthetic_query')
        mentions=resolve([doc.identity,query.identity],hmac_key=key,key_tag=tag)
        approved_doc,da=approve_experiment(doc.preparation,corpus_id='synthetic',checker=independent.check)
        approved_query,qa=approve_experiment(query.preparation,corpus_id='synthetic',checker=independent.check)
        entity_ids=tuple(m.entity_id for m in mentions if m.doc_id=='synthetic_doc' and m.entity_id)
        index=InMemoryIndex(key_tag=tag,policy_version='evidence_v1',corpus_id='synthetic')
        index.add(IndexedDocument('synthetic_doc',approved_doc,da,entity_ids=entity_ids))
        spec=query_from_mentions(approved_query,qa,[m for m in mentions if m.doc_id=='synthetic_query'])
        builder=ContextBuilder(independent,key_tag=tag,policy_version='evidence_v1',index_version=index.index_version,corpus_id='synthetic')
        metrics={}
        for retrieval_profile in ['own_v6','team_v3','hybrid_v1']:
            rows=index.retrieve(spec,profile=retrieval_profile,dense_scorer=lambda q,d:{'synthetic_doc':1.0})
            rank=[r.doc_id for r in rows]
            metrics[retrieval_profile]=evaluate_rankings(index,[{'qid':'synthetic_q1'}],
                        [{'qid':'synthetic_q1','relevant_doc_ids':['synthetic_doc']}],{'synthetic_q1':rank},{'synthetic_q1':'READY'})
        transport=MockTransport({'decision':'ANSWER','answer':'승인 절차를 거칩니다.','citations':['synthetic_doc']})
        answer=MockGenerator(builder,transport).generate(spec,rows)
        manifest=mock_manifest(run_id='synthetic-unified-v1',profile=profile,key_tag=tag,
                               index_fingerprint=index.index_fingerprint,
                               dataset_fingerprint=config_hash({'document':fingerprint(text),'query':fingerprint(question)}))
        return {'status':'MOCK_PASS','generation_decision':answer.decision,'citations':list(answer.citations),
                'claim_support_status':answer.claim_support_status,'transport_calls':transport.calls,
                'network':'BLOCKED','production_db':'NOT_ACCESSED','metrics':metrics,'manifest':manifest.to_dict()}
