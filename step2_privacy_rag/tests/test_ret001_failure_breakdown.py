"""Synthetic test that labels the three different reasons for missing Hit@5."""
import sys
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
from ret001_failure_breakdown import breakdown


class FailureDiagnoser(unittest.TestCase):
    def test_three_failure_reasons_separated_and_hits_ignored(self):
        docs=[{"doc_id":"D001"}]
        qs=[{"qid":f"Q{i:03d}","text":"synthetic"} for i in range(1,89)]
        gold=[{"qid":q["qid"],"relevant_doc_ids":["D001"],"type":"synthetic",
               "neutral_doc_ids":[],"confusable_doc_ids":[]} for q in qs]
        runs={q["qid"]:["D001"] for q in qs}
        qs[0]["text"]=None
        runs["Q001"]=[]
        gold[1]["relevant_doc_ids"]=["D999"]
        runs["Q002"]=["D001"]
        runs["Q003"]=["D001"]  # control query stays a hit
        # An indexable Gold document is in the corpus but not returned.
        runs["Q004"]=[]
        report=breakdown({"documents":docs,"queries":qs,
                          "gold_for_evaluation_only":gold},runs)
        self.assertEqual(report["reason_counts"],{
            "DEFERRED_QUERY":1,"READY_NO_INDEXABLE_GOLD":1,
            "RANKING_MISS":1})
        self.assertEqual([c["qid"] for c in report["cases"]],["Q001","Q002","Q004"])
        self.assertEqual(report["failed_hit5"],3)


if __name__=="__main__":
    unittest.main()
