"""Synthetic-only loader and local HTTP contracts; never load the private corpus."""
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from server import SOURCES, Handler, load_data


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.text = '🙂 가상 고객 안내 <script>alert(1)</script>'
        start = self.text.index('가상')
        for split, filename in SOURCES.items():
            row = dict(id=split+'001', split=split, doc_type='합성', text=self.text,
                       entities=[dict(label='person', text='가상', start=start, end=start+2)],
                       hard_cases=['synthetic'])
            if split != 'holdout':
                row.update(template='fixture', persons=['SYNTHETIC'], roles={})
            (self.directory/filename).write_text(json.dumps(row, ensure_ascii=False)+'\n')

    def tearDown(self):
        self.temp.cleanup()

    def test_only_three_official_paths_are_loaded_without_modification(self):
        (self.directory/'extra_experiment.jsonl').write_text('invalid JSON must never be read')
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.directory.iterdir()}
        data = load_data(self.directory)
        self.assertEqual(data['summary']['documents'], 3)
        self.assertEqual(data['summary']['entities'], 3)
        self.assertEqual(len(data['summary']['cross_split_duplicates']), 1)
        self.assertNotIn('template', data['splits'][2]['fields'])
        self.assertEqual(data['documents'][0]['char_count'],len(self.text))
        self.assertEqual(sum(s['annotation_issues'] for s in data['splits']),0)
        self.assertEqual(before,{p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.directory.iterdir()})

    def test_annotation_errors_are_reported_not_silently_corrected(self):
        path=self.directory/SOURCES['dev'];row=json.loads(path.read_text())
        row['entities']=[dict(label='person',text='틀린 정답',start=2,end=4),
                         dict(label='email',text='합성',start=999,end=1000),
                         dict(label='address',text=self.text[2:5],start=2,end=5)]
        path.write_text(json.dumps(row,ensure_ascii=False))
        data=load_data(self.directory)
        self.assertEqual(data['splits'][0]['annotation_issues'],2)
        self.assertEqual(data['splits'][0]['overlap_documents'],1)
        self.assertEqual(data['documents'][0]['record']['entities'],row['entities'])

    def test_missing_source_fails_explicitly(self):
        (self.directory/SOURCES['test']).unlink()
        with self.assertRaisesRegex(ValueError,'MISSING_SOURCE'):
            load_data(self.directory)

    def test_duplicate_ids_are_rejected(self):
        path=self.directory/SOURCES['dev'];text=path.read_text();path.write_text(text+text)
        with self.assertRaisesRegex(ValueError,'DUPLICATE_DOCUMENT_ID'):
            load_data(self.directory)

    def test_http_host_origin_asset_allowlist_and_no_cache(self):
        class TestHandler(Handler):
            data=load_data(self.directory)
        server=ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            for route in ['/','/app.js','/style.css','/api/data']:
                with urllib.request.urlopen(base+route) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.headers['Cache-Control'],'no-store')
                    self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
            for route,headers,status in [('/server.py',{},404),('/api/data',{'Host':'attacker.invalid'},403),
                                        ('/api/data',{'Origin':'https://example.invalid'},403),
                                        ('/api/data',{'Sec-Fetch-Site':'cross-site'},403)]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(base+route,headers=headers))
                self.assertEqual(error.exception.code,status)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
