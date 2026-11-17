"""Synthetic-only comparison tests; no model/DB/network."""
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from compare_hcx_variants import compare

def row(decision="ANSWER",citation="S001",gold=True,gate="READY"):
    return {
      "decision":decision,"citations":[citation] if citation else [],
      "cited_expected":gold,
      "gate_state":gate,"top5_doc_ids":["S001","S002"],
      "non_gold_citations":int(not gold),
      "phase_timings_ms":{"hcx_ms":1000}
    }


class CompareHCX(unittest.TestCase):
    def test_matching_candidates_with_changed_answer_are_reported(self):
        a={"Q101":row(),"Q102":row("INSUFFICIENT_EVIDENCE",None,False)}
        b={"Q101":row("ANSWER","S002",False),"Q102":row()}
        report=compare(a,b)
        self.assertEqual(report["cases"],2)
        self.assertEqual(report["changes"].get("retrieval_shift",0),0)
        self.assertEqual(report["changes"]["answer_gained"],1)
        self.assertEqual(report["changes"]["reference_citation_lost"],1)
        self.assertEqual(len(report["cases_for_manual_evidence_review"]),2)

    def test_fail_closed_on_mismatched_case_ids(self):
        with self.assertRaisesRegex(ValueError,"AB_CASES_DIFFER"):
            compare({"Q101":row()},{"Q102":row()})


if __name__=="__main__":
    unittest.main()
