"""Regression: citation diagnostic must not depend on decision annotations."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eval_chat_mvp import metric_report


class CitationMetricRegression(unittest.TestCase):
    def test_non_gold_citations_counted_without_expected_decision(self):
        def case(non_gold):
            return dict(
                labeled=True, gate_state="READY", decision="ANSWER",
                expected_decision=None, hit1=0, hit5=1,
                indexable_gold_count=2, rr=0.5, recall5_indexable=1.0,
                cited_expected=True, non_gold_citations=non_gold,
                total_ms=3000.0, phase_timings_ms={"hcx_ms": 2000.0}
            )
        result = metric_report([case(0), case(2)])
        self.assertEqual(result["decision_scored_cases"], 0)
        self.assertEqual(result["non_gold_citation_cases"], 1)
        self.assertEqual(result["answer_with_expected_citation"], 2)


if __name__ == "__main__":
    unittest.main()
