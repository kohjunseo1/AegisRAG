"""Synthetic-only tests for Q-001 review audit and fair 88-query reporting."""
import hashlib
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
from compare_88_retrieval import aggregate,score_one,load_runs
from q001_review_audit import audit

class EvaluationContract(unittest.TestCase):
    def test_one_relevant_document_is_hit_even_when_many_gold_docs(self):
        g={"qid":"Q001","relevant_doc_ids":["A","B","C","D"],
           "neutral_doc_ids":[],"confusable_doc_ids":[]}
        result=score_one(["C","X","Y","Z","W"],g)["official"]
        self.assertEqual(result["hit5"],1)
        self.assertEqual(result["recall5"],.25)

    def test_neutral_filtering_inflates_official_without_changing_raw(self):
        g={"qid":"Q001","relevant_doc_ids":["A"],
           "neutral_doc_ids":["N1","N2","N3","N4","N5"],"confusable_doc_ids":[]}
        result=score_one(["N1","N2","N3","N4","N5","A"],g)
        self.assertEqual(result["raw"]["hit5"],0)
        self.assertEqual(result["official"]["hit5"],1)

    def test_deferred_query_counts_as_failure_in_all_query_evaluation(self):
        g=[{"qid":"Q001","relevant_doc_ids":["A"],"neutral_doc_ids":[]},
           {"qid":"Q002","relevant_doc_ids":["B"],"neutral_doc_ids":[]}]
        totals, misses=aggregate(g,{"Q001":["A"],"Q002":[]},{"Q001","Q002"})
        self.assertEqual(totals["official"]["n"],2)
        self.assertEqual(totals["official"]["hit5"],1)
        self.assertEqual(misses,["Q002"])

    def test_review_audit_never_approves(self):
        source="최다현이 상담"
        cases={"H0002":{"id":"H0002","source":source,
                          "candidates":[{"kind":"name_boundary","start":0,"end":4}]}}
        event={"format":"q001-review-draft-v1","document_id":"H0002",
               "candidate_index":0,"kind":"name_boundary","source_span":[0,4],
               "source_digest":hashlib.sha256(source.encode()).hexdigest(),
               "human_classification":"LONG_NAME",
               "state":"NEEDS_INDEPENDENT_REVALIDATION"}
        report=audit(cases,[event])
        self.assertEqual(report["review_complete"],1)
        self.assertTrue(report["cases"][0]["still_blocked_from_index"])
        self.assertFalse(report["automatic_promotion"])

    def test_changed_source_invalidates_review(self):
        cases={"H0002":{"id":"H0002","source":"변경된 문서",
                        "candidates":[{"kind":"name_boundary","start":0,"end":3}]}}
        event={"format":"q001-review-draft-v1","document_id":"H0002",
               "candidate_index":0,"kind":"name_boundary","source_span":[0,3],
               "source_digest":"f"*64,"human_classification":"LONG_NAME",
               "state":"NEEDS_INDEPENDENT_REVALIDATION"}
        report=audit(cases,[event])
        self.assertEqual(report["review_complete"],0)
        self.assertEqual(report["invalid_event_counts"]["stale_or_invalid_source"],1)

if __name__=="__main__":
    unittest.main()
