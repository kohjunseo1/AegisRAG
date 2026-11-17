"""Generic query failure UX tests. No live model, DB, secrets or real PII."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"step2_privacy_rag/app"))
from app import respond
from query_privacy_controller import unresolved_query_response,temporary_generation_failure_response
from hcx import OutboundError
from core import PreparedQuery


class GenericDisposition(unittest.TestCase):
    def test_review_person_asks_safe_intent_not_manual_admin(self):
        result=unresolved_query_response("REVIEW","unresolved_veil_person")
        self.assertEqual(result["decision"],"CLARIFY")
        self.assertIn("문서 종류",result["answer"])
        self.assertNotIn("개발자",result["answer"])

    def test_numeric_failure_cannot_pass_gate(self):
        result=unresolved_query_response("BLOCKED","invalid_number_value")
        self.assertEqual(result["decision"],"BLOCKED_OR_DEFERRED")
        self.assertIn("가명처리",result["answer"])
        self.assertNotIn("입력값",result["answer"])

    def test_unknown_reason_fail_closed(self):
        self.assertEqual(unresolved_query_response("REVIEW","unexpected")["decision"],"CLARIFY")
        self.assertEqual(unresolved_query_response("BLOCKED","unexpected")["decision"],"BLOCKED_OR_DEFERRED")
        self.assertEqual(unresolved_query_response("UNKNOWN",None)["decision"],"BLOCKED_OR_DEFERRED")

    @patch("app.call_hcx")
    def test_local_review_does_not_call_hcx(self,remote):
        pipeline=MagicMock()
        pipeline.ask_local.return_value=("REVIEW",None,[])
        pipeline.last_query_issue_code="unresolved_veil_person"
        pipeline.timings={}
        r=respond(pipeline,{"question":"합성 대상 자료"})
        self.assertEqual(r["decision"],"CLARIFY")
        self.assertEqual(r["mode"],"local")
        remote.assert_not_called()

    @patch("app.call_hcx")
    def test_upstream_provider_timeout_is_not_evidence_missing(self,remote):
        pipeline=MagicMock()
        pipeline.timings={}
        pipeline.root=ROOT
        pipeline.ask_local.return_value=("READY",PreparedQuery("합성 근거 질문",set(),set()),[
            {"doc_id":"S001","text":"합성 교육 자료","dense_rank":1,
             "hmac_matches":0,"region_matches":0,"verified_region_match":None}])
        remote.side_effect=OutboundError("HCX_TIMEOUT")
        r=respond(pipeline,{"question":"합성 근거 질문"})
        self.assertEqual(r["decision"],"TEMPORARY_ERROR")
        self.assertEqual(r["citations"],[])
        self.assertEqual(r["diagnostics"]["error_class"],"UPSTREAM_TEMPORARY")
        remote.assert_called_once()

    @patch("app.call_hcx")
    def test_query_residual_remains_gate_review_not_error_fallback(self,remote):
        pipeline=MagicMock()
        pipeline.root=ROOT
        pipeline.ask_local.return_value=("READY",PreparedQuery("합성 질문",set(),set()),[
            {"doc_id":"S001","text":"합성 문서"}])
        remote.side_effect=OutboundError("QUERY_RESIDUAL_PII_REQUIRES_REVIEW")
        r=respond(pipeline,{"question":"합성 질문"})
        self.assertEqual(r["decision"],"REVIEW_REQUIRED")


if __name__=="__main__":
    unittest.main()
