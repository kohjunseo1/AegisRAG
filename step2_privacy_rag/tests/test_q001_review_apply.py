"""Synthetic end-to-end reviewed masking/HMAC (no real model/DB/HXC)."""
import hashlib
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
sys.path.insert(0,str(ROOT/"step2_privacy_rag/app"))
from review_apply_q001 import reviewed_prepare,validated_choices,ReviewValidationError
from review_dashboard_q001 import get_cases

KEY=b"review-e2e-synthetic-fixed-key-at-least-32-bytes"


def span(text,value,label="person",start=0):
    a=text.index(value,start)
    return {"label":label,"start":a,"end":a+len(value)}


class SyntheticDetector:
    def detect(self,text):
        if text.startswith("[부고]"):
            entity=span(text,"장인")
            return {"status":"READY_FOR_PSEUDONYMIZATION","issues":[],
                    "entities":[entity],"mask_intervals":[{"start":entity["start"],"end":entity["end"]}]}
        entity=span(text,"오가은")
        return {"status":"REVIEW_REQUIRED","issues":[{"code":"possible_uncovered_name_suffix"}],
                "entities":[entity],"mask_intervals":[{"start":entity["start"],"end":entity["end"]}]}
    def ax_predict(self,text):
        return []
    def veil_predict(self,text):
        return [span(text,"장인상")] if text.startswith("[부고]") else []


def review_record(case,kind,choice):
    target=case["candidates"][0]
    return {
        "format":"q001-review-draft-v1","document_id":case["id"],
        "candidate_index":0,"kind":kind,
        "source_span":[target["start"],target["end"]],
        "source_digest":hashlib.sha256(case["source"].encode()).hexdigest(),
        "human_classification":choice,"state":"NEEDS_INDEPENDENT_REVALIDATION",
    }


class ReviewApplyContract(unittest.TestCase):
    def setUp(self):
        self.det=SyntheticDetector()

    def run_name(self,decision):
        text="오가은이 상담했습니다."
        cases=get_cases({"H1000":text},self.det)
        choice=validated_choices(cases,[review_record(cases["H1000"],"name_boundary",decision)])
        return reviewed_prepare("H1000",text,self.det,KEY,cases["H1000"],choice["H1000"])

    def test_short_name_with_particle_keeps_suffix_masked_and_hmac_short(self):
        result=self.run_name("SHORT_NAME_PLUS_PARTICLE")
        self.assertEqual(result.review_type,"review_name")
        self.assertGreaterEqual(result.final_protected_characters,result.original_protected_characters)
        self.assertNotIn("오가은",result.sanitized_text)
        self.assertNotIn("오가은이",result.sanitized_text)
        self.assertIn("[REDACTED_UNCLASSIFIED]",result.sanitized_text)
        self.assertIn("<PERSON_",result.sanitized_text)

    def test_long_name_keeps_fourth_syllable_in_hmac(self):
        result=self.run_name("LONG_NAME")
        short=self.run_name("SHORT_NAME_PLUS_PARTICLE")
        self.assertNotEqual(result.sanitized_text,short.sanitized_text)
        self.assertNotIn("[REDACTED_UNCLASSIFIED]",result.sanitized_text)
        self.assertIn("<PERSON_",result.sanitized_text)

    def test_fake_obituary_person_is_nonlinked_but_keeps_original_mask(self):
        text="[부고] 장인상 안내"
        cases=get_cases({"H0091":text},self.det)
        choice=validated_choices(cases,[review_record(cases["H0091"],"veil_candidate",
            "NON_PERSON_EXPRESSION")])
        result=reviewed_prepare("H0091",text,self.det,KEY,cases["H0091"],choice["H0091"])
        self.assertIn("[REDACTED_UNCLASSIFIED]",result.sanitized_text)
        self.assertNotIn("<PERSON_",result.sanitized_text)
        self.assertNotIn("장인상",result.sanitized_text)
        self.assertEqual(result.review_type,"review_veil")

    def test_draft_not_approved_without_explicit_choice(self):
        text="오가은이 상담했습니다."
        case=get_cases({"H1000":text},self.det)
        with self.assertRaises(ReviewValidationError):
            validated_choices(case,[])
        with self.assertRaises(ReviewValidationError):
            validated_choices(case,[review_record(case["H1000"],"name_boundary","UNRESOLVED")])

    def test_source_hash_change_blocks_release(self):
        case=get_cases({"H1000":"오가은이 상담했습니다."},self.det)
        rec=review_record(case["H1000"],"name_boundary","LONG_NAME")
        rec["source_digest"]="0"*64
        with self.assertRaises(ReviewValidationError):
            validated_choices(case,[rec])

    def test_unapproved_new_model_issue_is_not_overridden(self):
        class BadDetector(SyntheticDetector):
            def detect(self,text):
                data=super().detect(text)
                data["issues"]=[{"code":"unprotected_address_candidate"}]
                return data
        text="오가은이 상담했습니다."
        detector=BadDetector()
        case=get_cases({"H1000":text},detector)["H1000"]
        with self.assertRaises(ReviewValidationError):
            reviewed_prepare("H1000",text,detector,KEY,case,["LONG_NAME"])

    def test_original_non_person_PII_masks_cannot_be_removed(self):
        class ExtraDetector(SyntheticDetector):
            def detect(self,text):
                data=super().detect(text)
                phone=span(text,"010-1234-5678","phone_number")
                data["entities"].append(phone)
                data["mask_intervals"].append({"start":phone["start"],"end":phone["end"]})
                return data
        text="오가은이 상담했습니다. 010-1234-5678"
        d=ExtraDetector()
        case=get_cases({"H1000":text},d)["H1000"]
        out=reviewed_prepare("H1000",text,d,KEY,case,["LONG_NAME"])
        self.assertNotIn("010-1234-5678",out.sanitized_text)
        self.assertIn("<PHONE_NUMBER_",out.sanitized_text)


if __name__=="__main__":
    unittest.main()
