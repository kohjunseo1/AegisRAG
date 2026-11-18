from dataclasses import replace
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from unified_v1.adapters import SubprocessPredictor, DetectionEngine, AxAdapter, VeilAdapter
from unified_v1.contracts import ContractError
from unified_v1.protection import fuse, prepare
from unified_v1.pipeline import query_from_mentions
from unified_v1.identity import resolve
from unified_v1.synthetic import run_synthetic
from test_p2 import KEY, TAG, evidence, baseline
import test_p3
from test_p4 import approved, index

class HardeningTests(unittest.TestCase):
    def test_ipc_request_correspondence_secret_scrubbing_and_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            script=Path(root)/'worker.py'
            script.write_text('import sys,json,os\nr=json.load(sys.stdin)\nassert "HF_TOKEN" not in os.environ\nprint(json.dumps(dict(request_id=r["request_id"],fingerprint=r["fingerprint"],config_hash=r["config_hash"],candidates=[])))\n')
            command=[str(Path(sys.executable).absolute()),str(script)]
            with patch.dict(os.environ,{'HF_TOKEN':'synthetic-not-a-real-token'}):
                self.assertEqual(SubprocessPredictor(command,'config')('합성'),[])
            script.write_text('import json\nprint(json.dumps(dict(request_id="wrong",fingerprint="wrong",config_hash="wrong",candidates=[])))')
            with self.assertRaisesRegex(ContractError,'IPC_RESPONSE_MISMATCH'):SubprocessPredictor(command,'config')('합성')
            script.write_text('import time\ntime.sleep(1)')
            with self.assertRaisesRegex(ContractError,'MODEL_TIMEOUT'):SubprocessPredictor(command,'config',timeout=.02)('합성')
            with self.assertRaisesRegex(ContractError,'IPC_REQUEST_TOO_LARGE'):SubprocessPredictor(command,'config',max_bytes=10)('합성')

    def test_worker_malformed_request_never_logs_text(self):
        import subprocess
        worker=Path(__file__).resolve().parents[2]/'step2_privacy_rag/app/unified_v1/worker.py'
        r=subprocess.run([sys.executable,str(worker)],input=b'private-placeholder',capture_output=True)
        self.assertEqual(r.returncode,2)
        self.assertEqual(r.stdout,b'')
        self.assertEqual(r.stderr,b'')

    def test_key_tag_input_kind_evidence_immutability(self):
        ev=evidence('안내');plan=fuse('안내',ev,baseline())
        with self.assertRaisesRegex(ContractError,'HMAC_KEY_TAG_MISMATCH'):prepare('안내',plan,input_kind='document',hmac_key=b'x'*32,key_tag=TAG)
        with self.assertRaisesRegex(ContractError,'PREPARATION_INPUT_MISMATCH'):prepare('안내',plan,input_kind='query',hmac_key=KEY,key_tag=TAG)
        with self.assertRaises(TypeError):ev.model_status_by_source['ax']='ERROR'
        with self.assertRaises(ContractError):DetectionEngine('own2',{'ax':VeilAdapter('mock',lambda t:[])})
        with self.assertRaises(ContractError):replace(ev,model_status_by_source={'ax':'OK'})

    def test_unresolved_query_and_same_document_mentions_do_not_merge(self):
        b1=test_p3.IdentityTests().bundle('a');b2=test_p3.IdentityTests().bundle('b')
        b2=replace(b2,mentions=(replace(b2.mentions[0],doc_id=b1.mentions[0].doc_id),))
        result=resolve([b1,b2],hmac_key=KEY,key_tag=TAG)
        self.assertTrue(all(m.link_status=='UNRESOLVED' for m in result))
        q,a=approved('합성','query')
        spec=query_from_mentions(q,a,result)
        with self.assertRaisesRegex(ContractError,'CLARIFY_UNRESOLVED_IDENTITY'):index().retrieve(spec)

    def test_full_synthetic_profiles_report_only_mock(self):
        for profile in ['own2','team2','quality4']:
            r=run_synthetic(profile)
            self.assertEqual(r['status'],'MOCK_PASS')
            self.assertEqual(r['network'],'BLOCKED')
            self.assertIsNone(r['manifest']['peak_rss_mb'])
            self.assertEqual(r['manifest']['hcx_live_status'],'NOT_RUN_BY_POLICY')
            self.assertEqual(r['manifest']['timing_mode'],'NOT_MEASURED')
            for metrics in r['metrics'].values():
                self.assertFalse(metrics['real_model_metrics'])
                self.assertEqual(metrics['dataset_role'],'synthetic')

    def test_real_loader_window_contract_without_model_dependencies(self):
        """Exercise LocalHFPredictor's actual overflow path using injected HF/Torch doubles."""
        import contextlib
        import hashlib
        from types import SimpleNamespace
        from unified_v1.adapters import LocalHFPredictor
        from unified_v1.contracts import LABELS
        labels={0:'O'}
        for label in sorted(LABELS):
            labels[len(labels)]='B-'+label
            labels[len(labels)]='I-'+label
        person_id=next(i for i,t in labels.items() if t=='B-person')
        class Tokenizer:
            is_fast=True
            def __len__(self):return 100
            def __call__(self,text,**kw):
                assert kw['return_overflowing_tokens'] is True
                assert kw['truncation'] is True
                assert kw['max_length']==512 and kw['stride']==64
                windows=[list(range(start,min(start+510,len(text)))) for start in range(0,len(text),446)]
                return {'offset_mapping':[[(i,i+1) for i in w] for w in windows],
                        'input_ids':[[person_id if i>=len(text)-3 else 0 for i in w] for w in windows],
                        'attention_mask':[[1]*len(w) for w in windows],
                        'token_type_ids':[[0]*len(w) for w in windows]}
        class Logit:
            def __init__(self,ids):self.ids=ids
            def argmax(self,*args,**kw):return self
            def tolist(self):return self.ids
        class Model:
            config=SimpleNamespace(id2label=labels,label2id={t:i for i,t in labels.items()},num_labels=len(labels),type_vocab_size=1)
            def eval(self):return self
            def get_input_embeddings(self):return SimpleNamespace(weight=SimpleNamespace(shape=(100,4)))
            def forward(self,input_ids,attention_mask):return SimpleNamespace(logits=[Logit(input_ids[0])])
            __call__=forward
        fake_transformers=SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a,**kw:Tokenizer()),
                                          AutoModelForTokenClassification=SimpleNamespace(from_pretrained=lambda *a,**kw:Model()))
        fake_torch=SimpleNamespace(inference_mode=contextlib.nullcontext,tensor=lambda x:x)
        with tempfile.TemporaryDirectory() as root,patch.dict(sys.modules,{'transformers':fake_transformers,'torch':fake_torch}):
            hashes={}
            for name in ['config.json','tokenizer.json','tokenizer_config.json','model.safetensors']:
                (Path(root)/name).write_bytes(b'mock');hashes[name]=hashlib.sha256(b'mock').hexdigest()
            predictor=LocalHFPredictor(root,root,hashes)
            text='😀'+'합'*1400+'금재원'
            rows=predictor(text)
            protected={i for r in rows for i in range(r['start'],r['end'])}
            self.assertTrue(set(range(len(text)-3,len(text)))<=protected)
            self.assertTrue(any(int(r['window_id'])>0 for r in rows))

    def test_candidate_union_recovers_outside_dense_pool_and_profile_difference(self):
        from unified_v1.retrieval import IndexedDocument, QuerySpec
        from test_p4 import TOKEN1
        ix=index()
        for i in range(55):
            p,a=approved('합성 안내');ix.add(IndexedDocument('dense_'+str(i),p,a))
        p,a=approved(TOKEN1);ix.add(IndexedDocument('token_only',p,a))
        q,a=approved(TOKEN1,'query');query=QuerySpec(q,a)
        scores={did:100-i for i,did in enumerate(sorted(ix.ids))}
        scores['token_only']=-1
        own=ix.retrieve(query,profile='own_v6',dense_scorer=lambda q,d:scores)
        hybrid=ix.retrieve(query,profile='hybrid_v1',dense_scorer=lambda q,d:scores)
        self.assertNotIn('token_only',[r.doc_id for r in own])
        self.assertEqual(hybrid[0].doc_id,'token_only')
