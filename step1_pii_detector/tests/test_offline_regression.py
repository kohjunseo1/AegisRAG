import unittest

from evaluation.offline_regression import evaluate


class OfflineRegressionTests(unittest.TestCase):
    def test_saved_prediction_metrics_include_policy_and_mask_union(self):
        text = "김 과장님"
        gold = [{"id": "synthetic-1", "text": text, "entities": []}]
        predictions = {"synthetic-1": [{"label": "person", "start": 0, "end": 1}]}

        report = evaluate(gold, predictions, include_postal=True)

        self.assertEqual(report["frozen_predictions"]["fp"], 1)
        self.assertEqual(report["after_two_core_rules"]["fp"], 0)
        self.assertEqual(report["policy"]["removed_false_positive_spans"], 1)
        self.assertEqual(report["policy"]["removed_true_positive_spans_added_as_false_negatives"], 0)
        self.assertGreaterEqual(report["policy"]["postprocessing_latency_ms"]["p95"], 0)
        self.assertIsNone(report["frozen_predictions"]["review_rate"])


if __name__ == "__main__":
    unittest.main()
