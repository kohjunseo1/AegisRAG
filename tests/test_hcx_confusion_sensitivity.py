"""Offline, synthetic unit tests for Gold-only/Confusable/Top-5 planning."""
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from eval_hcx_confusion_sensitivity import build_conditions,choose_cases,safe_row


def doc(name):
    return {"doc_id":name,"text":"Synthetic evidence text"}


class ConfusionPlan(unittest.TestCase):
    def test_q075_gold_confusable_top5(self):
        top=[doc(x) for x in ["H0147","H0102","H0161","H0059","H0092"]]
        conditions=build_conditions(top,{"H0147"},{"H0102"})
        self.assertEqual([[x["doc_id"] for x in c["docs"]] for c in conditions],
                         [["H0147"],["H0147","H0102"],
                          ["H0147","H0102","H0161","H0059","H0092"]])
        self.assertTrue(all(c["duplicate_of"] is None for c in conditions))
        self.assertEqual([x["doc_id"] for x in top],
                         ["H0147","H0102","H0161","H0059","H0092"])

    def test_q052_confusable_equals_actual_top5_no_extra_call(self):
        top=[doc(x) for x in ["H0017","H0023","H0062","H0024","H0159"]]
        c=build_conditions(top,{"H0017","H0023","H0062","H0159"},{"H0024"})
        self.assertEqual([len(x["docs"]) for x in c],[4,5,5])
        self.assertEqual(c[2]["duplicate_of"],"gold_confusable")

    def test_q048_all_gold_no_confusable(self):
        top=[doc(x) for x in ["H0048","H0131","H0132","H0047","H0135"]]
        c=build_conditions(top,set(x["doc_id"] for x in top),set())
        self.assertEqual(c[1]["skip_reason"],"NO_CONFUSABLE_IN_ACTUAL_TOP5")
        self.assertEqual(c[2]["duplicate_of"],"gold_only")

    def test_no_gold_in_top5_can_still_test_actual_top5(self):
        c=build_conditions([doc("H0101"),doc("H0102")],{"H0200"},{"H0102"})
        self.assertEqual(c[0]["skip_reason"],"NO_GOLD_IN_ACTUAL_TOP5")
        self.assertEqual(c[1]["skip_reason"],"NO_GOLD_IN_ACTUAL_TOP5")
        self.assertIsNone(c[2]["skip_reason"])

    def test_duplicate_doc_ids_or_gold_confusable_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError,"CONF_INVALID_TOP5"):
            build_conditions([doc("H0001"),doc("H0001")],set(),set())
        with self.assertRaisesRegex(ValueError,"CONF_OVERLAPPING_GOLD"):
            build_conditions([doc("H0001")],{"H0001"},{"H0001"})

    def test_gold_question_must_match_holdout_case(self):
        cases=[{"case_id":"Q001","question":"Synthetic case",
                "relevant_doc_ids":["H0001"]}]
        labels={"Q001":{"query":"Other synthetic case","relevant_doc_ids":["H0001"]}}
        with self.assertRaisesRegex(ValueError,"CONF_QUESTION_GOLD_MISMATCH"):
            choose_cases(cases,labels,["Q001"])

    def test_safe_report_contains_ids_not_document_text(self):
        top=[doc("H0147"),doc("H0102")]
        c=build_conditions(top,{"H0147"},{"H0102"})
        row=safe_row({"case_id":"Q075","category":"synthetic"},
                     c[1],{"H0147"},{"H0102"},1)
        self.assertEqual(row["document_ids"],["H0147","H0102"])
        self.assertNotIn("Synthetic evidence",str(row))
        self.assertEqual(row["confusable_included"],1)


if __name__=="__main__":
    unittest.main()
