"""Exercise the shipped HTTP handler and UI assets with public synthetic content."""
import json
from http.server import HTTPServer
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from app import Handler
from core import PreparedQuery


class HTTPContract(unittest.TestCase):
    def setUp(self):
        self.pipeline = MagicMock()
        self.pipeline.root = '.'
        self.pipeline.expected_rows = 1
        self.pipeline.timings = dict(step1_ms=1.0)
        self.pipeline.ask_local.return_value = ('READY', PreparedQuery('합성 교육 안내',set(),set()),
            [dict(doc_id='S001',text='합성 교육은 수요일입니다.',dense_rank=1,
                  hmac_matches=0,region_matches=0,verified_region_match=None)])
        class TestHandler(Handler):
            pipeline = self.pipeline
            allow_hcx = True
        self.server = HTTPServer(('127.0.0.1',0),TestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_health_and_static_assets(self):
        for path in ['/', '/app.js', '/style.css', '/api/health']:
            with urllib.request.urlopen(self.base+path) as response:
                self.assertEqual(response.status,200)
                self.assertEqual(response.headers['Cache-Control'],'no-store')
                self.assertTrue(response.read())

    @patch('app.call_hcx')
    def test_post_answer_and_review(self,hcx):
        hcx.return_value = (dict(decision='ANSWER',answer='수요일입니다.',citations=['S001']), {}, {})
        def ask():
            req = urllib.request.Request(self.base+'/api/chat', data=json.dumps(dict(question='합성 질문')).encode(),
                headers={'Content-Type':'application/json','Origin':self.base})
            with urllib.request.urlopen(req) as response:
                return json.load(response)
        result = ask()
        self.assertEqual(result['citations'],['S001'])
        self.assertIn('total_ms',result['diagnostics']['timings_ms'])
        self.pipeline.ask_local.return_value = ('REVIEW',None,[])
        self.assertEqual(ask()['decision'],'REVIEW_REQUIRED')
        self.assertEqual(hcx.call_count,1)

    def test_cross_origin_rejected(self):
        req = urllib.request.Request(self.base+'/api/chat', data=b'{}',headers={
            'Content-Type':'application/json','Origin':'https://example.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(req)
        self.assertEqual(context.exception.code,403)
