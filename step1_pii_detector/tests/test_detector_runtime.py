"""Offline tests for detector lifecycle, concurrency, and safe failure behavior."""
import unittest
import concurrent.futures
import io
import logging
import threading
import time
from pii_detector.detector import PIIDetector

class TestDetectorRuntime(unittest.TestCase):
    def test_concurrency_serialized(self):
        busy = threading.Lock()
        def ax(t):
            if not busy.acquire(blocking=False):
                raise RuntimeError('backend cannot be reentered')
            try:
                time.sleep(0.008)
                return [{'label':'person','start':3,'end':6}]
            finally: busy.release()
        with PIIDetector(ax, lambda t: [], parallel=True) as d:
            t='고객 김민수의 정보'
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                results=list(pool.map(d.detect,[t]*20))
        self.assertEqual(len(results),20)
        self.assertTrue(all(r['status']=='READY_FOR_PSEUDONYMIZATION' for r in results))
        self.assertTrue(all(r['entities']==results[0]['entities'] for r in results))

    def test_name_review_not_auto_cut_or_expanded(self):
        t='직원 이슬이 조건 안내를 맡았다.'
        a=t.index('이슬')
        with PIIDetector(lambda s:[{'label':'person','start':a,'end':a+2}],lambda s:[]) as d:
            r=d.detect(t)
        self.assertEqual(r['status'],'REVIEW_REQUIRED')
        self.assertIn('possible_uncovered_name_suffix',[e['code'] for e in r['issues']])
        self.assertEqual(r['entities'],[{'label':'person','start':a,'end':a+2}])

    def test_safe_internal_log_without_exception_message(self):
        logger=logging.getLogger('pii_detector')
        stream=io.StringIO()
        handler=logging.StreamHandler(stream)
        previous=logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        try:
            def ax(s): raise RuntimeError('SECRET_PERSON 김민수 original text')
            with PIIDetector(ax,lambda s:[]) as d:
                r=d.detect('비밀 원문 김민수')
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        self.assertEqual(r['status'],'BLOCKED')
        self.assertEqual(len(r['request_id']),16)
        log=stream.getvalue()
        self.assertIn('RuntimeError',log)
        self.assertIn(r['request_id'],log)
        self.assertNotIn('SECRET_PERSON',log)
        self.assertNotIn('김민수',log)
        self.assertNotIn('비밀 원문',log)
        self.assertNotIn('SECRET_PERSON',str(r))

    def test_closed_detector_blocked(self):
        d=PIIDetector(lambda s:[],lambda s:[])
        d.close()
        self.assertEqual(d.detect('정보')['status'],'BLOCKED')

    def test_timeout_faults_detector_until_reinitialized(self):
        def slow(s): time.sleep(.06); return []
        d=PIIDetector(slow, lambda s:[],timeout_seconds=.002)
        try:
            r=d.detect('정보')
            self.assertEqual(r['status'],'BLOCKED')
            self.assertEqual(r['issues'][0]['code'],'model_timeout')
            later=d.detect('정보')
            self.assertEqual(later['issues'][0]['code'],'detector_unavailable')
        finally: d.close()

if __name__=='__main__':unittest.main(verbosity=2)
