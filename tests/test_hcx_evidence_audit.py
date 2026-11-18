"""Synthetic, zero-network tests of the isolated HCX structured evidence A/B."""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "step2_privacy_rag" / "app"))

from eval_hcx_confusion_sensitivity import build_audit_ab_conditions
from hcx import BASE, OutboundError
from hcx_evidence_audit import (_review_checks, _checked_response,
                                call_hcx_evidence_audit, SCHEMA, AUDIT_GUIDANCE)


def docs(*ids):
    return [{"doc_id": did, "text": "가상 면접 일정은 2월 17일입니다."}
            for did in ids]


class AuditPlan(unittest.TestCase):
    def test_pair_and_top5_ab_modes_preserve_order(self):
        top = docs("D11", "D22", "D33", "D44", "D55")
        result = build_audit_ab_conditions(top, {"D11"}, set(), "D22")
        self.assertEqual([r["condition"] for r in result], [
            "pair_baseline", "pair_audit", "top5_baseline", "top5_audit"])
        self.assertEqual([r["answer_mode"] for r in result],
                         ["baseline", "audit", "baseline", "audit"])
        self.assertEqual([[d["doc_id"] for d in r["docs"]] for r in result],
                         [["D11", "D22"], ["D11", "D22"],
                          ["D11","D22","D33","D44","D55"],
                          ["D11","D22","D33","D44","D55"]])
        self.assertTrue(all(r["duplicate_of"] is None for r in result))

    def test_order_when_non_gold_ranked_first(self):
        top = docs("D22", "D11", "D33")
        result = build_audit_ab_conditions(top, {"D11"}, set(), "D22")
        self.assertEqual([d["doc_id"] for d in result[0]["docs"],["D22","D11"])

    def test_reused_only_inside_same_mode(self):
        result = build_audit_ab_conditions(
            docs("D11","D22"), {"D11"}, set(), "D22")
        self.assertEqual(result[2]["duplicate_of"],"pair_baseline")
        self.assertEqual(result[3]["duplicate_of"],"pair_audit")
        self.assertIsNone(result[1]["duplicate_of"])

    def test_reject_unretrieved_or_gold_focus(self):
        with self.assertRaisesRegex(ValueError,"CONF_FOCUS_NOT_NON_GOLD_IN_TOP5"):
            build_audit_ab_conditions(docs("D11","D22"),{"D11"},set(),"D99")
        with self.assertRaisesRegex(ValueError,"CONF_FOCUS_NOT_NON_GOLD_IN_TOP5"):
            build_audit_ab_conditions(docs("D11","D22"),{"D11"},set(),"D11")


class AuditResponse(unittest.TestCase):
    def test_valid_quote_and_no_text_exposed(self):
        documents = docs("D11","D22")
        reviews = [
            {"doc_id":"D11","verdict":"SUPPORTS","supporting_quote":"2월 17일"},
            {"doc_id":"D22","verdict":"WRONG_TASK","supporting_quote":""},
        ]
        checks = _review_checks(reviews,documents)
        self.assertEqual(checks[0]["quote_valid"],True)
        self.assertIsNone(checks[1]["quote_valid"])
        self.assertNotIn("2월 17일",str(checks))
        self.assertNotIn("가상 면접",str(checks))

    def test_unsupported_quote_is_detected_not_accepted_as_grounding(self):
        documents = docs("D11","D22")
        raw = {
            "evidence_review": [
                {"doc_id":"D11","verdict":"SUPPORTS",
                 "supporting_quote":"문서에 없는 날짜: 3월 1일"},
                {"doc_id":"D22","verdict":"WRONG_TASK","supporting_quote":""},
            ],
            "decision":"ANSWER","answer":"가상 답변","citations":["D11"],
        }
        result = _checked_response(raw,documents,False)
        self.assertFalse(result["evidence_audit"][0]["quote_valid"])
        self.assertEqual(result["citations"],["D11"])

    def test_inconsistent_citation_is_flagged(self):
        documents = docs("D11","D22")
        raw = {
            "evidence_review": [
                {"doc_id":"D11","verdict":"SUPPORTS","supporting_quote":"2월 17일"},
                {"doc_id":"D22","verdict":"WRONG_TASK","supporting_quote":""},
            ],
            "decision":"ANSWER","answer":"가상 답변","citations":["D22"],
        }
        result = _checked_response(raw,documents,False)
        self.assertFalse(result["citation_verdict_consistent"])

    def test_review_must_cover_exact_ids(self):
        with self.assertRaises(OutboundError):
            _review_checks([
                {"doc_id":"D11","verdict":"SUPPORTS","supporting_quote":"2월 17일"},
                {"doc_id":"D11","verdict":"WRONG_TASK","supporting_quote":""},
            ], docs("D11","D22"))

    def test_prompt_is_generic_no_specific_case(self):
        self.assertNotIn("Q027",AUDIT_GUIDANCE)
        self.assertNotIn("H0059",AUDIT_GUIDANCE)
        self.assertEqual(SCHEMA["required"],[
            "evidence_review","decision","answer","citations"])

    def test_mocked_api_call_preserves_outbound_safeguards(self):
        observed = {}
        class Response(io.BytesIO):
            pass
        def fake_urlopen(request,timeout):
            payload = json.loads(request.data.decode("utf-8"))
            observed.update(payload)
            answer = {
                "evidence_review": [
                    {"doc_id":"D11","verdict":"SUPPORTS",
                     "supporting_quote":"2월 17일"},
                    {"doc_id":"D22","verdict":"WRONG_TASK",
                     "supporting_quote":""},
                ],
                "decision":"ANSWER","answer":"가상 면접 일정은 2월 17일입니다.",
                "citations":["D11"],
            }
            response = {"choices":[{"finish_reason":"stop",
                "message":{"content":json.dumps(answer,ensure_ascii=False)}}],
                "usage":{"prompt_tokens":150,"completion_tokens":80}}
            return Response(json.dumps(response,ensure_ascii=False).encode("utf-8"))
        with patch.dict(os.environ,{
            "AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED":"YES",
            "LLM_API_KEY":"only-synthetic-test-key",
            "LLM_API_BASE":BASE,
        }):
            with patch("hcx_evidence_audit.urllib.request.urlopen",
                       side_effect=fake_urlopen):
                result,usage,_ = call_hcx_evidence_audit(
                    ROOT,"가상 면접 일정은 무엇인가요?",docs("D11","D22"),False)
        self.assertEqual(result["citations"],["D11"])
        self.assertEqual(result["evidence_audit"][0]["quote_valid"],True)
        self.assertEqual(usage["prompt_tokens"],150)
        self.assertEqual(observed["model"],"HCX-007")
        self.assertEqual(observed["temperature"],0)
        self.assertEqual(observed["max_completion_tokens"],1024)
        self.assertEqual(observed["messages"][1]["role"],"user")
        self.assertNotIn("gold",observed["messages"][1]["content"].lower())
        self.assertNotIn("H0059",str(observed))


if __name__ == "__main__":
    unittest.main()
