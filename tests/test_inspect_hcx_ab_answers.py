"""Synthetic-only, no network, private HCX paired answer review tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from inspect_hcx_ab_answers import compare_case,redact_answer


class InspectHCXAB(unittest.TestCase):
    def test_masks_residual_phone_and_email(self):
        ans="전화 010-1234-5678, 이메일 user@example.com"
        safe=redact_answer(ans)
        self.assertNotIn("010-1234-5678",safe)
        self.assertNotIn("user@example.com",safe)
        self.assertIn("[REDACTED_PHONE]",safe)
        self.assertIn("[REDACTED_EMAIL]",safe)

    def test_compares_responses_without_exposing_original_question(self):
        original="SYNTHETIC_PRIVATE_INPUT"
        a=dict(case_id="Q027",category="synthetic",question=original,
               hcx_answer="근거 없는 예시.",
               observed=dict(decision="ANSWER",citations=["S002"],
                             top5_doc_ids=["S001","S002"]))
        b=dict(case_id="Q027",category="synthetic",question=original,
               hcx_answer="근거 있는 예시.",
               observed=dict(decision="ANSWER",citations=["S001"],
                             top5_doc_ids=["S001","S002"]))
        output=compare_case(a,b)
        self.assertEqual(output["case_id"],"Q027")
        self.assertEqual(output["baseline"]["citations"],["S002"])
        self.assertEqual(output["variant"]["citations"],["S001"])
        self.assertTrue(output["same_top5"])
        self.assertNotIn(original,str(output))


if __name__=="__main__":
    unittest.main()
