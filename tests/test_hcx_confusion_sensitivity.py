"""Offline, synthetic unit tests for Gold-only/Confusable/Top-5 planning."""
import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from eval_hcx_confusion_sensitivity import build_conditions,choose_cases,safe_row
sys.path.insert(0,str(ROOT/"step2_privacy_rag/app"))
from hcx import BASE, OutboundError, _safe_429_diagnostics, call_hcx


def doc(name):
    return {"doc_id":name,"text":"Synthetic evidence text"}


class ConfusionPlan(unittest.TestCase):
    def test_q075_gold_confusable_top5(self):
        top=[doc(x) for x in ["H0147","H0102","H0161","H0059","H0092"]]
        conditions=build_conditions(top,{"H0147"},{"H0102"})
        self.assertEqual([[x["doc_id"] for x in c["docs"]] for c in conditions],
                         [["H0147"],["H0147","H0102"],
                          ["H0147","H0102","H0161","H0059","H0092"]])
        self.assertTrue(all(c["duplicate_of"] is None for c in conditions))
        self.assertEqual([x["doc_id"] for x in top],
                         ["H0147","H0102","H0161","H0059","H0092"])

    def test_q052_confusable_equals_actual_top5_no_extra_call(self):
        top=[doc(x) for x in ["H0017","H0023","H0062","H0024","H0159"]]
        c=build_conditions(top,{"H0017","H0023","H0062","H0159"},{"H0024"})
        self.assertEqual([len(x["docs"]) for x in c],[4,5,5])
        self.assertEqual(c[2]["duplicate_of"],"gold_confusable")

    def test_q048_all_gold_no_confusable(self):
        top=[doc(x) for x in ["H0048","H0131","H0132","H0047","H0135"]]
        c=build_conditions(top,set(x["doc_id"] for x in top),set())
        self.assertEqual(c[1]["skip_reason"],"NO_CONFUSABLE_IN_ACTUAL_TOP5")
        self.assertEqual(c[2]["duplicate_of"],"gold_only")

    def test_no_gold_in_top5_can_still_test_actual_top5(self):
        c=build_conditions([doc("H0101"),doc("H0102")],{"H0200"},{"H0102"})
        self.assertEqual(c[0]["skip_reason"],"NO_GOLD_IN_ACTUAL_TOP5")
        self.assertEqual(c[1]["skip_reason"],"NO_GOLD_IN_ACTUAL_TOP5")
        self.assertIsNone(c[2]["skip_reason"])

    def test_duplicate_doc_ids_or_gold_confusable_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError,"CONF_INVALID_TOP5"):
            build_conditions([doc("H0001"),doc("H0001")],set(),set())
        with self.assertRaisesRegex(ValueError,"CONF_OVERLAPPING_GOLD"):
            build_conditions([doc("H0001")],{"H0001"},{"H0001"})

    def test_gold_question_must_match_holdout_case(self):
        cases=[{"case_id":"Q001","question":"Synthetic case",
                "relevant_doc_ids":["H0001"]}]
        labels={"Q001":{"query":"Other synthetic case","relevant_doc_ids":["H0001"]}}
        with self.assertRaisesRegex(ValueError,"CONF_QUESTION_GOLD_MISMATCH"):
            choose_cases(cases,labels,["Q001"])

    def test_safe_report_contains_ids_not_document_text(self):
        top=[doc("H0147"),doc("H0102")]
        c=build_conditions(top,{"H0147"},{"H0102"})
        row=safe_row({"case_id":"Q075","category":"synthetic"},
                     c[1],{"H0147"},{"H0102"},1)
        self.assertEqual(row["document_ids"],["H0147","H0102"])
        self.assertNotIn("Synthetic evidence",str(row))
        self.assertEqual(row["confusable_included"],1)



class RateLimitDiagnosis(unittest.TestCase):
    def test_safe_provider_code_and_allowlisted_headers_only(self):
        response = json.dumps({"error":{"code":"42901",
                                        "message":"never echo PRIVATE_DOCUMENT or API_KEY"}})
        error = urllib.error.HTTPError(
            BASE,429,"rate limited",
            {"x-ratelimit-remaining-requests":"0",
             "x-ratelimit-remaining-tokens":"123",
             "x-ratelimit-reset-requests":"23s",
             "x-ratelimit-reset-tokens":"33s",
             "retry-after":"2",
             "Authorization":"Bearer API_KEY"},
            io.BytesIO(response.encode("utf-8")))
        diagnostics = _safe_429_diagnostics(
            error,request_bytes=400,question_chars=11,document_chars=25,
            document_count=1,max_completion_tokens=1024)
        self.assertEqual(diagnostics["provider_code"],"42901")
        self.assertEqual(diagnostics["remaining_requests"],"0")
        self.assertEqual(diagnostics["reset_requests"],"23s")
        self.assertEqual(diagnostics["max_completion_tokens"],1024)
        self.assertNotIn("PRIVATE_DOCUMENT",str(diagnostics))
        self.assertNotIn("API_KEY",str(diagnostics))
        self.assertNotIn("Authorization",str(diagnostics))

    def test_malformed_body_or_header_never_leaks(self):
        error = urllib.error.HTTPError(
            BASE,429,"test",
            {"x-ratelimit-reset-requests":"PRIVATE_DOCUMENT",
             "retry-after":"PRIVATE_DOCUMENT",
             "x-ratelimit-remaining-tokens":"0 more PRIVATE_DOCUMENT"},
            io.BytesIO(b'{"message":"PRIVATE_DOCUMENT","code":"NOT_A_NUMBER"}'))
        diagnostics = _safe_429_diagnostics(
            error,request_bytes=100,question_chars=3,document_chars=10,
            document_count=1,max_completion_tokens=1024)
        self.assertNotIn("provider_code",diagnostics)
        self.assertNotIn("reset_requests",diagnostics)
        self.assertNotIn("retry_after",diagnostics)
        self.assertNotIn("remaining_tokens",diagnostics)
        self.assertNotIn("PRIVATE_DOCUMENT",str(diagnostics))

    def test_call_hcx_429_keeps_safe_code_and_payload_size(self):
        response = json.dumps({"status":{"code":"42902","message":"PRIVATE_DOCUMENT"}})
        error = urllib.error.HTTPError(
            BASE,429,"rate limited",
            {"x-ratelimit-remaining-requests":"0"},
            io.BytesIO(response.encode("utf-8")))
        with patch.dict(os.environ,{
            "AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED":"YES",
            "LLM_API_KEY":"synthetic-not-real",
            "LLM_API_BASE":BASE,
            "AEGISRAG_HCX_EVIDENCE_MODE":"baseline",
        }):
            with patch("hcx.urllib.request.urlopen",side_effect=error):
                with self.assertRaises(OutboundError) as caught:
                    call_hcx(ROOT,"합성 테스트 질문",[{
                        "doc_id":"TEST001","text":"공개 합성 문서의 처리 상태: 완료"
                    }],False)
        self.assertEqual(str(caught.exception),"HCX_HTTP_429")
        self.assertEqual(caught.exception.diagnostics["provider_code"],"42902")
        self.assertGreater(caught.exception.diagnostics["request_bytes"],100)
        self.assertEqual(caught.exception.diagnostics["document_count"],1)
        self.assertNotIn("PRIVATE_DOCUMENT",str(caught.exception.diagnostics))

if __name__=="__main__":
    unittest.main()
