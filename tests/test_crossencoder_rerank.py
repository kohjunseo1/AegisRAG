"""Synthetic contracts for local CrossEncoder rerank; no model download."""
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from eval_crossencoder_rerank import semantic_surface,sort_reranked,score


def doc(did,rank,identity=0,region=0):
    return {"doc_id":did,"dense_rank":rank,
            "hmac_matches":identity,"region_matches":region}


class CrossEncoderContracts(unittest.TestCase):
    def test_hmac_removed_in_favor_of_coarse_local_role(self):
        a=semantic_surface("고객 <PERSON_"+"a"*24+">의 불만 [REDACTED_UNCLASSIFIED]")
        self.assertNotIn("a"*24,a)
        self.assertIn("인물",a)
        self.assertIn("불만",a)
        self.assertIn("비식별정보",a)

    def test_ce_promotes_same_tier_document(self):
        rows=[doc("A",1),doc("B",3)]
        out=sort_reranked(rows,[-2.0,4.0],1)
        self.assertEqual([r["doc_id"] for r in out],["B","A"])

    def test_hmac_region_never_bypassed_even_with_extreme_scores(self):
        rows=[doc("A",2,1,1),doc("B",3,1,0),doc("C",1,0,1)]
        for strength in (1.0,.25,.5):
            out=sort_reranked(rows,[-500,0,500],strength)
            self.assertEqual([r["doc_id"] for r in out],["A","B","C"])

    def test_zero_scored_deferred_and_non_gold(self):
        runs={"Q1":["A","B"],"Q2":[]}
        labels={"Q1":{"relevant_doc_ids":["A","C"],"confusable_doc_ids":["B"]},
                "Q2":{"relevant_doc_ids":["D"]}}
        out=score(runs,labels)
        self.assertEqual(out["hit1"],1)
        self.assertEqual(out["hit5"],1)
        self.assertEqual(out["recall5"],round(.5/2,4))

    def test_bad_parameters_and_nonfinite_scores_fail_closed(self):
        with self.assertRaises(ValueError):
            sort_reranked([doc("A",1)],[float("nan")],1)
        with self.assertRaises(ValueError):
            sort_reranked([doc("A",1)],[],1)


if __name__=="__main__":
    unittest.main()
