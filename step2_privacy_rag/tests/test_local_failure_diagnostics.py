"""Privacy Gate exceptions stay BLOCKED and local diagnostic never leaks exception content."""
import contextlib
import io
import os
import unittest
from unittest.mock import Mock,patch
from document_preparation import prepare

class TestLocalFailureDiagnostics(unittest.TestCase):
    def test_opt_in_safe_diagnostic_redacts_exception_string(self):
        detector=Mock()
        detector.detect.return_value={'status':'READY_FOR_PSEUDONYMIZATION',
                                      'entities':[],'mask_intervals':[]}
        sensitive='비밀 주소 010-9876-5432'
        with patch('document_preparation.decide',side_effect=RuntimeError(sensitive)), \
             patch.dict(os.environ,{'AEGISRAG_LOCAL_FAILURE_DIAG':'1'}):
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):
                result=prepare('EVAL_00001',sensitive,detector,b'K'*32)
        self.assertEqual(result.state,'BLOCKED')
        self.assertEqual(result.reason,'SAFE_PIPELINE_FAILURE')
        self.assertIn('BLIND_SAFE_FAILURE',stream.getvalue())
        self.assertIn('UNCLASSIFIED',stream.getvalue())
        self.assertNotIn(sensitive,stream.getvalue())
        self.assertNotIn('9876',stream.getvalue())

    def test_safe_pseudonymization_code_diagnostic(self):
        from local_pseudonymizer import PseudonymizationError
        detector=Mock()
        detector.detect.return_value={'status':'READY_FOR_PSEUDONYMIZATION',
                                      'entities':[],'mask_intervals':[]}
        with patch('document_preparation.decide',side_effect=PseudonymizationError('invalid_phone_value')), \
             patch.dict(os.environ,{'AEGISRAG_LOCAL_FAILURE_DIAG':'1'}):
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):
                result=prepare('EVAL_00007','비밀 예시',detector,b'K'*32)
        self.assertEqual(result.state,'BLOCKED')
        self.assertIn('"code": "invalid_phone_value"',stream.getvalue())
        self.assertNotIn('비밀 예시',stream.getvalue())

    def test_no_diagnostic_without_opt_in(self):
        detector=Mock()
        detector.detect.side_effect=RuntimeError('비밀정보 010-0000-0000')
        with patch.dict(os.environ,{'AEGISRAG_LOCAL_FAILURE_DIAG':'0'}):
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):
                result=prepare('EVAL_00002','가상 자료',detector,b'K'*32)
        self.assertEqual(result.state,'BLOCKED')
        self.assertEqual(stream.getvalue(),'')
