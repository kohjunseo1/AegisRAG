"""Synthetic read-only retrieval ablation contracts (no models/DB/HCX)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"step2_privacy_rag"/"app"))
sys.path.insert(0, str(ROOT/"scripts"))

from retrieval_ablation import grams, lexical_rerank, coverage_select, make_lexical_index
from eval_retrieval_ablations import report


def doc(did, rank, text, hmac=0, region=0):
    return dict(doc_id=did, text=text, dense_rank=rank, hmac_matches=hmac,
                region_matches=region)


class AblationContract(unittest.TestCase):
    def test_hmac_and_redaction_markers_excluded_from_lexical_grams(self):
        token = "<PERSON_" + "a"*24 + ">"
        self.assertEqual(grams(token + " [REDACTED_EMAIL]"), frozenset())
        self.assertIn("출장", grams("출장비 신청"))

    def test_lexical_recovers_specific_topic_within_same_privacy_tier(self):
        rows = [doc("A", 1, "휴가 일정"), doc("B", 2, "출장비 정산 절차")]
        ranked = lexical_rerank(rows, "출장비 정산", .3)
        self.assertEqual(ranked[0]["doc_id"], "B")

    def test_lexical_never_overrides_verified_identity_or_region(self):
        rows = [doc("A", 2, "휴가 일정", hmac=1, region=1),
                doc("B", 1, "출장비 정산 절차", hmac=0, region=1),
                doc("C", 3, "출장비 정산 절차", hmac=1, region=0)]
        ranked = lexical_rerank(rows, "출장비 정산", 1)
        self.assertEqual([r["doc_id"] for r in ranked], ["A", "C", "B"])

    def test_coverage_keeps_top1_and_highest_priority_tier(self):
        rows = [doc("A", 1, "동일한 공지 내용", hmac=1),
                doc("B", 2, "동일한 공지 내용", hmac=1),
                doc("C", 3, "서로 다른 일정 공지", hmac=1),
                doc("D", 4, "아주 다른 내용", hmac=0)]
        ranked = coverage_select(rows, k=3, pool_size=4, penalty=.75)
        self.assertEqual(ranked[0]["doc_id"], "A")
        self.assertEqual(set(d["doc_id"] for d in ranked[:3]), {"A", "B", "C"})
        self.assertEqual(ranked[3]["doc_id"], "D")

    def test_coverage_may_promote_different_evidence(self):
        rows = [doc("A", 1, "동일한 공지 내용"),
                doc("B", 2, "동일한 공지 내용"),
                doc("C", 3, "완전히 다른 보험 교육 안내")]
        ranked = coverage_select(rows, k=2, pool_size=3, penalty=1.0)
        self.assertEqual([r["doc_id"] for r in ranked[:2]], ["A", "C"])

    def test_report_has_zero_for_deferred_and_multiple_gold_recall(self):
        gold = {"Q1": {"relevant_doc_ids": ["A", "B"],
                       "confusable_doc_ids": ["C"]},
                "Q2": {"relevant_doc_ids": ["B"], "confusable_doc_ids": []}}
        s = report({"Q1": ["A", "C"], "Q2": []}, gold, {"Q1"})
        self.assertEqual((s["hit1"], s["hit5"]), (1, 1))
        self.assertEqual(s["recall5_total"], .25)
        self.assertEqual(s["confusable_top5"], 1)


if __name__ == "__main__":
    unittest.main()
