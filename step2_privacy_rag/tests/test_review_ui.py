"""Review UI local-only auth and private content tests, synthetic, no models/DB."""
import json
from http.server import HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from scripts.dataset_review_ui import Handler,Context
from dataset_workflow import write_private,signed
from dataset_preparation import key_tag

KEY=b'Q'*32


class ReviewUIContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.basepath=Path(self.tmp.name)/'local_eval'
        self.localpatch=patch('dataset_workflow.BASE',self.basepath)
        self.localpatch.start()
        self.stage=self.basepath/'review'/'stage.json'
        data={'format':'aegisrag-dataset-stage-v1','dataset':'synthetic',
              'hmac_key_tag':key_tag(KEY),'documents':[{
                  'doc_id':'synthetic__A','source_id':'A',
                  'source_sha256':'0'*64,'state':'REVIEW_PENDING',
                  'reason':'GATE_NOT_READY','approval_state':None,
                  'text':None,'region_keys':[],'raw_for_local_review':'비공개 합성 원문'
              }]}
        write_private(self.stage,signed(data,KEY))
        ctx=Context(self.stage,Path('/tmp/.env'),'cpu','very-long-test-auth-token',None)
        class H(Handler):pass
        H.ctx=ctx
        self.server=HTTPServer(('127.0.0.1',0),H)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.localpatch.stop()
        self.tmp.cleanup()

    def _request(self,path,headers=None,data=None):
        req=urllib.request.Request(self.url+path,headers=headers or {},
                                   data=data)
        with patch.object(Handler,'_key',return_value=KEY):
            with urllib.request.urlopen(req) as resp:
                return resp.read().decode()

    def test_root_available_without_secret_but_raw_requires_auth(self):
        self.assertIn('AegisRAG',self._request('/'))
        with self.assertRaises(urllib.error.HTTPError) as err:
            self._request('/api/queue')
        self.assertEqual(err.exception.code,403)

    def test_authenticated_review_queue_and_raw(self):
        headers={'X-Aegis-Review-Token':'very-long-test-auth-token'}
        d=json.loads(self._request('/api/queue',headers))
        self.assertEqual(d['review_pending'],1)
        one=json.loads(self._request('/api/document?id=synthetic__A',headers))
        self.assertIn('비공개 합성',one['raw_for_local_review'])

    def test_review_then_commit_contract_without_real_models_db(self):
        from document_preparation import Outcome
        from dataset_workflow import read_signed
        from unittest.mock import MagicMock
        headers={'X-Aegis-Review-Token':'very-long-test-auth-token',
                 'Content-Type':'application/json','Origin':self.url}
        # A model-approved manually revised source is the ONLY route to READY.
        valid=Outcome('synthetic__A','LOCAL_PREPARED','LOCAL_ONLY_NOT_EXTERNAL_AUTHORIZED',
                      sanitized_text='가명처리된 합성 자료',tokens=(),region_keys=())
        with patch('dataset_workflow.prepare',return_value=valid):
            reviewed=json.loads(self._request('/api/review',headers,
                data=json.dumps({'doc_id':'synthetic__A',
                                 'revised_source':'비식별화된 수정 자료',
                                 'reviewer':'reviewer1'}).encode('utf-8')))
        self.assertEqual(reviewed['state'],'HUMAN_APPROVED_AFTER_GATE_RECHECK')
        stage=read_signed(self.stage,KEY)
        self.assertEqual(stage['documents'][0]['approval_state'],'HUMAN_APPROVED')
        self.assertIsNone(stage['documents'][0]['raw_for_local_review'])
        with patch('live_index.fixed_key',return_value=KEY), \
             patch('settings.db_config',return_value={}), \
             patch('psycopg.connect') as conn, \
             patch('live_index.incremental_upsert',
                   return_value={'inserted':1,'updated':0,'unchanged':0,
                                 'previous_rows':187,'resulting_rows':188}) as upsert:
            committed=json.loads(self._request('/api/commit',headers,
                data=json.dumps({'confirm':'rag.aegis_live_docs'}).encode('utf-8')))
        self.assertEqual(committed['resulting_rows'],188)
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[1][0]['doc_id'],'synthetic__A')

    def test_cross_origin_post_fails(self):
        headers={'X-Aegis-Review-Token':'very-long-test-auth-token',
                 'Content-Type':'application/json','Origin':'https://attacker.example'}
        with self.assertRaises(urllib.error.HTTPError) as err:
            self._request('/api/review',headers,data=b'{}')
        self.assertEqual(err.exception.code,403)


if __name__=='__main__':
    unittest.main()
