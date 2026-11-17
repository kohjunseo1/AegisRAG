"""End-to-end HTTP boundary tests in local-only mode; synthetic mock data only."""
import json
import threading
import unittest
from http.server import HTTPServer
import urllib.request
from unittest.mock import MagicMock, patch

from app import Handler, respond
from core import PreparedQuery


class LocalOnlyHTTPContracts(unittest.TestCase):
    def setUp(self):
        self.pipeline = MagicMock()
        self.pipeline.root = "."
        self.pipeline.expected_rows = 187
        self.pipeline.timings = {"step1_ms": 12.0, "embedding_ms": 30.0}
        self.pipeline.index_status.return_value = {
            "status": "READY", "table": "rag.aegis_live_docs",
            "rows": 187, "expected_rows": 187, "key_matches": True}
        self.pipeline.ask_local.return_value = (
            "READY", PreparedQuery("가명처리된 합성질문", set(), set()), [
                dict(doc_id="S001", text="민감한_가상_원문을_반환해서는_안됨",
                     dense_rank=1, hmac_matches=0, region_matches=0,
                     verified_region_match=None)
            ],
        )
        class LocalHandler(Handler):
            pass
        LocalHandler.pipeline = self.pipeline
        LocalHandler.allow_hcx = False
        self.server = HTTPServer(("127.0.0.1", 0), LocalHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def send(self, question="합성 질문"):
        request = urllib.request.Request(
            self.base + "/api/chat",
            data=json.dumps({"question": question}, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    @patch("app.call_hcx", side_effect=AssertionError("HCX MUST NOT BE CALLED"))
    def test_ready_query_real_http_returns_only_metadata_not_answer(self, hcx):
        reply = self.send()
        self.assertEqual(reply["decision"], "LOCAL_SEARCH_ONLY")
        self.assertEqual(reply["mode"], "local")
        self.assertEqual(reply["citations"], [])
        self.assertEqual(reply["sources"], [])
        self.assertEqual(reply["diagnostics"]["retrieval_candidates"][0]["doc_id"], "S001")
        self.assertNotIn("text", reply["diagnostics"]["retrieval_candidates"][0])
        self.assertNotIn("민감한_가상_원문", str(reply))
        self.assertNotIn("가명처리된 합성질문", str(reply))
        hcx.assert_not_called()

    @patch("app.call_hcx", side_effect=AssertionError("HCX MUST NOT BE CALLED"))
    def test_review_returns_clarification_without_retrieval_or_hcx(self, hcx):
        self.pipeline.ask_local.return_value = ("REVIEW", None, [])
        reply = self.send()
        self.assertEqual(reply["decision"], "CLARIFY")
        self.assertEqual(reply["citations"], [])
        hcx.assert_not_called()

    @patch("app.call_hcx", side_effect=AssertionError("HCX MUST NOT BE CALLED"))
    def test_blocked_returns_safe_failure(self, hcx):
        self.pipeline.ask_local.return_value = ("BLOCKED", None, [])
        reply = self.send()
        self.assertEqual(reply["decision"], "BLOCKED_OR_DEFERRED")
        self.assertEqual(reply["citations"], [])
        hcx.assert_not_called()

    @patch("app.call_hcx", side_effect=AssertionError("HCX MUST NOT BE CALLED"))
    def test_missing_evidence_not_misreported_as_found(self, hcx):
        self.pipeline.ask_local.return_value = (
            "READY", PreparedQuery("합성 질의", set(), set()), [])
        reply = self.send()
        self.assertEqual(reply["decision"], "INSUFFICIENT_EVIDENCE")
        hcx.assert_not_called()

    def test_health_advertises_no_external_transfer(self):
        with urllib.request.urlopen(self.base + "/api/health") as response:
            reply = json.load(response)
        self.assertIs(reply["external_hcx_enabled"], False)
        self.assertIs(reply["db_write"], False)

    @patch("app.call_hcx", side_effect=AssertionError("HCX MUST NOT BE CALLED"))
    def test_env_approval_cannot_force_hcx_in_local_only_mode(self, hcx):
        with patch.dict("os.environ", {"AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED": "YES"}):
            reply = self.send()
        self.assertEqual(reply["decision"], "LOCAL_SEARCH_ONLY")
        hcx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
