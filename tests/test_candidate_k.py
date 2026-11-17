"""Top-K curve scorer regression; no DB, models, external API, or private content."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from eval_candidate_k import summarize


class KCurveScoring(unittest.TestCase):
    def test_retrieval_ceiling_and_deferred_are_distinct(self):
        ranked={"QA":["D3","D2","D1"],"QB":[]}
        gold={"QA":{"relevant_doc_ids":["D1","D2"],
                    "confusable_doc_ids":["D3"]},
              "QB":{"relevant_doc_ids":["D4"],
                    "confusable_doc_ids":[]}}
        # Scorer requires 88 aligned queries; mimic a fixed 88-case set.
        for n in range(86):
            qid=f"Q{n:03d}"
            ranked[qid]=[]
            gold[qid]={"relevant_doc_ids":["UNINDEXED"],"confusable_doc_ids":[]}
        result=summarize(ranked,gold,{"QA"})
        curve={row["k"]:row for row in result["curve"]}
        self.assertEqual(curve[1]["hit"],0)
        self.assertEqual(curve[3]["hit"],1)
        self.assertEqual(curve[3]["macro_recall"],round(1/88,4))
        self.assertEqual(curve[1]["queries_with_confusable"],1)
        self.assertEqual(result["first_gold"]["QA"],2)
        self.assertIsNone(result["first_gold"]["QB"])


if __name__=="__main__":
    unittest.main()
