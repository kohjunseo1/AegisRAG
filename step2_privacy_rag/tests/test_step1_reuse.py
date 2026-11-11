"""Synthetic fail-closed regression for opt-in Step1 lifetime management."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'step1_pii_detector' / 'src'))
from core import LivePipeline, PipelineError


class FakeDetector:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def pipeline(reuse):
    # Test only lifetime state, no real credentials or database.
    p = object.__new__(LivePipeline)
    p.reuse_step1 = reuse
    p._detector = None
    p._detector_failed = False
    p.secret = b'K' * 32  # public synthetic test key, never used for live data
    p.timings = {}
    return p


OK = SimpleNamespace(state='LOCAL_PREPARED', sanitized_text='안전한 합성 질문',
                     region_keys=(), unlinked_mentions=0)
REVIEW = SimpleNamespace(state='REVIEW', sanitized_text=None,
                         region_keys=(), unlinked_mentions=0)


class Step1ReuseContract(unittest.TestCase):
    @patch('document_preparation.prepare', return_value=OK)
    @patch('pii_detector.create_detector')
    def test_reuse_initializes_once(self, create, prepare):
        detector = FakeDetector()
        create.return_value = detector
        p = pipeline(True)
        first = p.prepare('합성 질문')
        second = p.prepare('합성 질문')
        self.assertEqual(first, second)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(detector.close_calls, 0)
        self.assertIn('step1_init_ms', p.timings)
        p.close()
        self.assertEqual(detector.close_calls, 1)
        p.close()
        self.assertEqual(detector.close_calls, 1)

    @patch('document_preparation.prepare', return_value=OK)
    @patch('pii_detector.create_detector')
    def test_baseline_still_recreates_and_closes(self, create, prepare):
        detectors = [FakeDetector(), FakeDetector()]
        create.side_effect = detectors
        p = pipeline(False)
        p.prepare('합성 질문')
        p.prepare('합성 질문')
        self.assertEqual(create.call_count, 2)
        self.assertEqual([d.close_calls for d in detectors], [1, 1])
        self.assertIsNone(p._detector)

    @patch('document_preparation.prepare', return_value=REVIEW)
    @patch('pii_detector.create_detector')
    def test_review_is_never_automatically_upgraded(self, create, prepare):
        detector = FakeDetector()
        create.return_value = detector
        p = pipeline(True)
        self.assertEqual(p.prepare('검토 질의'), 'REVIEW')
        self.assertEqual(p.prepare('검토 질의'), 'REVIEW')
        self.assertEqual(create.call_count, 1)
        p.close()

    @patch('document_preparation.prepare')
    @patch('pii_detector.create_detector')
    def test_exception_requires_restart_no_unsafe_retry(self, create, prepare):
        detector = FakeDetector()
        create.return_value = detector
        prepare.side_effect = RuntimeError('secret from model exception')
        p = pipeline(True)
        with self.assertRaises(PipelineError) as caught:
            p.prepare('합성 질문')
        self.assertEqual(str(caught.exception), 'STEP1_GATE_FAILED')
        self.assertTrue(p._detector_failed)
        self.assertEqual(detector.close_calls, 1)
        with self.assertRaisesRegex(PipelineError, 'STEP1_DETECTOR_FAILED_RESTART_REQUIRED'):
            p.prepare('합성 질문')
        self.assertEqual(create.call_count, 1)


if __name__ == '__main__':
    unittest.main()
