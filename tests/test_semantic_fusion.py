"""Synthetic contracts for offline semantic-fusion evaluation; no DB or models."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from eval_semantic_fusion import clean_for_semantic, document_heading, dense_ranks, fuse, metrics


def row(did, rank, hmac=0, region=0):
    return {"doc_id":did,"dense_rank":rank,
            "hmac_matches":hmac,"region_matches":region}


class SemanticFusionContracts(unittest.TestCase):
    def test_semantic_view_strips_hmac_and_redacted_markers(self):
        self.assertEqual(clean_for_semantic(
            "<PERSON_"+"a"*24+"> 감사 편지 [REDACTED_UNCLASSIFIED]"),
            "감사 편지")
        self.assertEqual(document_heading("\n[부고] 사내 공지\n내용"),
                         "[부고] 사내 공지")

    def test_stable_rank_by_doc_id(self):
        self.assertEqual(dense_ranks([.5,.5,.1],["B","A","C"]),
                         {"A":1,"B":2,"C":3})

    def test_title_view_can_promote_relevant_doc(self):
        rows = [row("A",1),row("B",4)]
        ranked = fuse(rows,method="title50",
                      title_ranks={"A":100,"B":1},intent_ranks=None)
        self.assertEqual([r["doc_id"] for r in ranked],["B","A"])

    def test_hmac_and_region_tiers_never_relaxed(self):
        rows = [row("A",2,1,1),row("C",5,1,0),row("B",1,0,1)]
        title_ranks={"B":1,"A":187,"C":2}
        intent_ranks={"B":1,"A":187,"C":2}
        for name in ("title25","title50","intent50","title_intent"):
            ranked=fuse(rows,method=name,
                        title_ranks=title_ranks,intent_ranks=intent_ranks)
            self.assertEqual([r["doc_id"] for r in ranked],["A","C","B"])

    def test_eval_counts_deferred_as_miss(self):
        gold={"Q1":{"relevant_doc_ids":["X","Y"]},
              "Q2":{"relevant_doc_ids":["X"]}}
        r=metrics({"Q1":["X","Z"],"Q2":[]},gold)
        self.assertEqual((r["hit1"],r["hit5"]),(1,1))
        self.assertEqual(r["recall5"],.25)


if __name__=="__main__":
    unittest.main()
