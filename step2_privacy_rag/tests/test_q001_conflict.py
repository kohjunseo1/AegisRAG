"""Q-001 regression: a Veil lexical ignore must not override strict Step1 PII.

Synthetic only. No original document text, labels or private data.
"""
import unittest

from privacy_gate import check_privacy_gate, READY


class Detector:
    def __init__(self, status, strict, masks, veil):
        self.result = {
            "status": status,
            "entities": strict,
            "mask_intervals": masks,
        }
        self.veil = veil

    def detect(self, text):
        return self.result

    def ax_predict(self, text):
        return []

    def veil_predict(self, text):
        return self.veil


def span(text, part, label="person"):
    p = text.index(part)
    return {"start": p, "end": p + len(part), "label": label}


class KinshipCandidateConflict(unittest.TestCase):
    def setUp(self):
        # A known lexical false-positive class from the V4 policy.
        self.text = "[부고] 장인상 안내"
        self.veil = span(self.text, "장인상")

    def gate(self, strict):
        masks = [{"start": x["start"], "end": x["end"]} for x in strict]
        return check_privacy_gate(
            Detector("READY_FOR_PSEUDONYMIZATION", strict, masks, [self.veil]),
            self.text,
        )

    def test_no_strict_collision_can_ignore_lexical_candidate(self):
        r = self.gate([])
        self.assertEqual(r["decision"], READY)
        self.assertEqual(r["veil_policy_counts"].get("ignored"), 1)

    def test_partial_strict_person_collision_must_review(self):
        r = self.gate([span(self.text, "장인")])
        self.assertEqual(r["decision"], "REVIEW_REQUIRED")
        self.assertEqual(r["reason"], "unresolved_veil_person")
        self.assertEqual(r["veil_policy_counts"].get("ambiguous"), 1)

    def test_partial_strict_other_label_collision_must_review(self):
        r = self.gate([span(self.text, "장인", "account_number")])
        self.assertEqual(r["decision"], "REVIEW_REQUIRED")
        self.assertEqual(r["reason"], "unresolved_veil_person")

    def test_step1_review_never_promoted(self):
        r = check_privacy_gate(
            Detector("REVIEW_REQUIRED", [], [], [self.veil]), self.text)
        self.assertEqual(r["decision"], "REVIEW_REQUIRED")
        self.assertEqual(r["reason"], "detector_not_ready")


if __name__ == "__main__":
    unittest.main()
