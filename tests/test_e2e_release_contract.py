"""Synthetic regressions for privacy-safe E2E diagnostics and response policy."""
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
sys.path.insert(0,str(ROOT/"step2_privacy_rag/app"))
from build_e2e_suite import choose
from diagnose_gate_queries import number_span_diagnostics
from hcx import SYSTEM


class E2EReleaseContracts(unittest.TestCase):
    def test_number_error_diagnostic_contains_no_raw_value(self):
        private="123456789012X"
        rows=number_span_diagnostics(
            private,[{"label":"resident_registration_number","start":0,"end":len(private)}])
        self.assertEqual(rows[0]["error"],"invalid_number_value")
        self.assertTrue(rows[0]["contains_letters"])
        self.assertTrue(rows[0]["covers_entire_input"])
        self.assertNotIn(private,str(rows))
        self.assertNotIn(private[:7],str(rows))

    def test_correct_number_normalization_has_no_issue(self):
        self.assertEqual(number_span_diagnostics("1234567890123",[
            {"label":"resident_registration_number","start":0,"end":13}]),[])

    def test_stratify_e2e_suites_without_using_rankings(self):
        queries=[{"qid":"A01","query":"질문 A"},
                 {"qid":"A02","query":"질문 B"},
                 {"qid":"A03","query":"질문 C"},
                 {"qid":"A04","query":"질문 D"}]
        gold=[{"qid":"A01","type":"category1","relevant_doc_ids":["D1"]},
              {"qid":"A02","type":"category1","relevant_doc_ids":["D2"]},
              {"qid":"A03","type":"category2","relevant_doc_ids":["D3"]},
              {"qid":"A04","type":"category2","relevant_doc_ids":["D4"]}]
        selected=choose(queries,gold,3)
        self.assertEqual([x["case_id"] for x in selected],["A01","A03","A02"])
        self.assertEqual(selected[0]["relevant_doc_ids"],["D1"])

    def test_generic_answer_policy_has_temporal_and_privacy_guidance(self):
        self.assertIn("기록 시점",SYSTEM)
        self.assertIn("문서 탐색",SYSTEM)
        self.assertIn("보호 토큰 문자열",SYSTEM)
        self.assertIn("검색 순위",SYSTEM)
        self.assertNotIn("경조사 공지 알려줘",SYSTEM)


if __name__=="__main__":
    unittest.main()
