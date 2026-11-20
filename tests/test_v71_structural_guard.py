"""Deterministic contract tests for non-deployed V7.1 structural proposals."""
from __future__ import annotations
import importlib.util
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "step2_privacy_rag" / "app" / "unified_v1" / "structural_guard_v71.py"
spec = importlib.util.spec_from_file_location("_v71_structural_guard_test", MODULE)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
candidates, mask_supplements = module.candidates, module.mask_supplements


class TestV71StructuralGuard(unittest.TestCase):
    def test_phone(self):
        source="고객 연락처: 010.1234.5678 확인"
        found=candidates(source)
        self.assertEqual([(p["label"],source[p["start"]:p["end"]]) for p in found],
                         [("phone_number","010.1234.5678")])

    def test_account(self):
        self.assertEqual(candidates("입금계좌: 123-4567-891234")[0]["label"],"account_number")

    def test_card(self):
        self.assertEqual(candidates("결제 카드번호: 4129-6222-3344-5566")[0]["label"],"card_number")

    def test_email(self):
        self.assertEqual(candidates("메일: user42@example.test")[0]["label"],"email")

    def test_rrn(self):
        self.assertEqual(candidates("주민등록번호: 990101-1234567")[0]["label"],
                         "resident_registration_number")

    def test_inquiry_id_does_not_become_phone(self):
        self.assertEqual(candidates("문의번호: 010-1234-5678"),[])

    def test_unanchored_card_not_approved(self):
        self.assertEqual(candidates("참조번호: 4129-6222-3344-5566"),[])

    def test_partial_existing_mask(self):
        s="연락처: 010-1234-5678"; a=s.index("010")
        self.assertEqual(len(mask_supplements(s,[{"start":a,"end":a+3}])),1)

    def test_fully_existing_mask(self):
        s="연락처: 010-1234-5678"; a=s.index("010")
        self.assertEqual(mask_supplements(s,[{"start":a,"end":a+13}]),[])

    def test_adjacent_card_and_account_cues(self):
        s="카드번호: 4129-6222-3344-5566, 계좌번호: 123-4567-891234"
        self.assertEqual([x["label"] for x in candidates(s)],
                         ["card_number","account_number"])


if __name__=="__main__":
    unittest.main()
