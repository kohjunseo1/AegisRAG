"""Synthetic human E2E evaluation scoring contract; never private contents."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from score_e2e_human_review import grade


def row(qid,decision, **extra):
    return dict(case_id=qid,observed=dict(decision=decision),**extra)


class ScoreE2EHuman(unittest.TestCase):
    def test_answer_requires_grounding_correctness_completeness_temporal_privacy(self):
        valid=dict(human_answer_correct=True,human_citation_supported=True,
                   human_answer_complete=True,human_temporal_correct=True,
                   human_privacy_safe=True,human_decision_appropriate=True)
        rows=[row("Q001","ANSWER",**valid),
              row("Q002","ANSWER",**{**valid,"human_temporal_correct":False}),
              row("Q003","ANSWER",**{**valid,"human_citation_supported":None})]
        stats=grade(rows)
        self.assertEqual((stats["cases"],stats["scored"],stats["passes"],stats["failures"],stats["pending"]),(3,2,1,1,1))
        self.assertEqual(stats["success_rate_scored"],.5)
        self.assertEqual(stats["success_rate_all"],round(1/3,4))
        self.assertEqual(stats["pending_qids"],["Q003"])

    def test_clarification_can_be_success_without_hcx_answer(self):
        rows=[row("Q001","CLARIFY",
                  human_privacy_safe=True,human_decision_appropriate=True),
              row("Q002","BLOCKED_OR_DEFERRED",
                  human_privacy_safe=True,human_decision_appropriate=True),
              row("Q003","INSUFFICIENT_EVIDENCE",
                  human_privacy_safe=True,human_decision_appropriate=False)]
        stats=grade(rows)
        self.assertEqual(stats["passes"],2)
        self.assertEqual(stats["failures"],1)

    def test_unknown_decision_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"E2E_SCORE_UNKNOWN_DECISION"):
            grade([row("Q004","LOCAL_SEARCH_ONLY")])


if __name__=="__main__":
    unittest.main()
