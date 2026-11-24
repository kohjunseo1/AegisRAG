"""Run after vendoring; uses synthetic examples only, no weights."""
import unittest
from starter.v7_onepass import apply_policy
from tools.vendor_v7 import self_test
from starter import v7_frozen as F

class V7PolicyTest(unittest.TestCase):
    def test_frozen_functions(self):
        self_test(F)

    def test_review_and_protection(self):
        text = "결제 완료"
        raw = [{"label":"person","start":3,"end":5}]
        typed, info = apply_policy(text,raw)
        self.assertEqual(typed,[])
        self.assertEqual(info["mask_intervals"],[{"start":3,"end":5}])
        self.assertEqual(info["review"][0]["source"],"event_status_nominal")
        self.assertEqual(info["status"],"REVIEW_REQUIRED")
        self.assertFalse(info["deployment_approved"])

    def test_no_combined_surname_filter(self):
        raw = [{"label":"person","start":0,"end":1}]
        typed, _ = apply_policy("김님",raw)
        self.assertEqual(typed,raw)

    def test_safe_empty(self):
        typed, info = apply_policy("",[])
        self.assertEqual(typed,[])
        self.assertEqual(info["mask_intervals"],[])

if __name__ == "__main__":
    unittest.main()
