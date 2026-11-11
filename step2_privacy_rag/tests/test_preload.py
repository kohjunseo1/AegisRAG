"""Offline L-002 model preloading and server startup security contracts."""
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'step1_pii_detector' / 'src'))
from core import LivePipeline, PreparedQuery, PipelineError
import app


def stub_pipeline(reuse=True):
    p = object.__new__(LivePipeline)
    p.reuse_step1 = reuse
    p._detector_failed = False
    p._detector = None
    p._models_preloaded = False
    p._preload_metrics = {}
    p._model = None
    p.timings = {}
    return p


class PreloadingContract(unittest.TestCase):
    def test_preload_only_with_persistent_step1(self):
        p = stub_pipeline(False)
        with self.assertRaisesRegex(PipelineError, 'PRELOAD_REQUIRES_STEP1_REUSE'):
            p.preload_models()

    def test_preload_warms_models_once_and_without_search(self):
        p = stub_pipeline()
        pq = PreparedQuery('합성 교육', set(), set())
        with patch.object(p, 'prepare', return_value=pq) as prepare, \
             patch.object(p, '_encode', return_value='synthetic_vector') as encode, \
             patch.object(p, 'retrieve', side_effect=AssertionError('DB query forbidden')):
            m1 = p.preload_models()
            m2 = p.preload_models()
        self.assertTrue(p._models_preloaded)
        self.assertEqual(m1, m2)
        self.assertEqual(prepare.call_count, 1)
        encode.assert_called_once_with('합성 교육')
        self.assertGreaterEqual(m1['total_ms'], 0)

    def test_review_during_preload_blocks_startup(self):
        p = stub_pipeline()
        with patch.object(p, 'prepare', return_value='REVIEW'), \
             patch.object(p, '_encode', side_effect=AssertionError('unsafe warmup')):
            with self.assertRaisesRegex(PipelineError, 'MODEL_PRELOAD_FAILED'):
                p.preload_models()
        self.assertTrue(p._detector_failed)
        self.assertFalse(p._models_preloaded)
        self.assertIsNone(p._model)

    def test_encoding_failure_blocks_startup_without_details(self):
        p = stub_pipeline()
        pq = PreparedQuery('합성 교육', set(), set())
        with patch.object(p, 'prepare', return_value=pq), \
             patch.object(p, '_encode', side_effect=RuntimeError('private user text')):
            with self.assertRaises(PipelineError) as caught:
                p.preload_models()
        self.assertEqual(str(caught.exception), 'MODEL_PRELOAD_FAILED')
        self.assertTrue(p._detector_failed)
        self.assertIsNone(p._model)

    @patch('app.HTTPServer')
    @patch('app.LivePipeline')
    def test_http_listens_only_after_successful_preload(self, mock_pipeline, mock_http):
        events = []
        mock_pipeline.return_value.preload_models.side_effect = lambda: (
            events.append('preload') or {'step1_ms': 1.0, 'embedding_ms': 2.0, 'total_ms': 3.0}
        )
        mock_http.side_effect = lambda *_: events.append('bind') or Mock()
        with patch.dict(os.environ, {'AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED':'YES'}), \
             patch.object(sys, 'argv', ['app.py', '--port', '8768', '--reuse-step1', '--preload']):
            self.assertEqual(app.main(), 0)
        self.assertEqual(events, ['preload', 'bind'])
        mock_pipeline.return_value.close.assert_called_once()

    @patch('app.HTTPServer')
    @patch('app.LivePipeline')
    def test_preload_failure_never_binds_listener(self, mock_pipeline, mock_http):
        mock_pipeline.return_value.preload_models.side_effect = PipelineError('MODEL_PRELOAD_FAILED')
        with patch.dict(os.environ, {'AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED':'YES'}), \
             patch.object(sys, 'argv', ['app.py', '--port', '8768', '--reuse-step1', '--preload']):
            self.assertEqual(app.main(), 2)
        mock_http.assert_not_called()
        mock_pipeline.return_value.close.assert_called_once()

    def test_cli_rejects_preload_without_reuse(self):
        with patch.object(sys, 'argv', ['app.py', '--preload']):
            with self.assertRaises(SystemExit) as err:
                app.main()
        self.assertEqual(err.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
