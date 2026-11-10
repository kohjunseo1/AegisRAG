"""Conservative, auditable, rule-based triage for *Veil-only* PERSON candidates.

This module is a diagnostic/proposed V4 component, NOT a production security gate.
It must never override Step 1 strict entities/masks. It must not authorize external
transfers or create an HMAC identity for a surname-plus-job-title reference.

Inputs: unmodified text and a Veil candidate offset [start,end).
Outputs: IGNORE_EXTRA_CANDIDATE / REDACT_ROLE_REFERENCE / REVIEW_REQUIRED.

Policy is intentionally narrowly scoped: absence of a match means REVIEW.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

IGNORE = "IGNORE_EXTRA_CANDIDATE"
REDACT = "REDACT_ROLE_REFERENCE"
REVIEW = "REVIEW_REQUIRED"

# Lexicon at a *category* level. Extend from a versioned HR configuration if needed.
JOB_TITLES = {
    "사원", "주임", "대리", "과장", "차장", "부장", "팀장", "실장", "본부장",
    "센터장", "지점장", "이사", "상무", "전무", "대표", "선임", "책임",
    "수석", "매니저", "심사역", "조사역", "검사역", "감사역", "수석연구원",
}
TITLE_ALTS = "|".join(re.escape(t) for t in sorted(JOB_TITLES, key=len, reverse=True))
# A role title can be followed by a Korean grammatical particle, but not arbitrary
# Hangul, preventing partial matching of unknown compound job titles.
PARTICLE_ALTS = "|".join(sorted({
    "께서는", "께서", "에게서", "에게", "한테", "으로", "부터", "까지", "이랑",
    "은", "는", "이", "가", "을", "를", "도", "의", "과", "와", "로", "께",
}, key=len, reverse=True))
ROLE_AFTER = re.compile(
    rf"^[ \t]*(?P<title>{TITLE_ALTS})(?:님)?"
    rf"(?=$|[^가-힣]|(?:{PARTICLE_ALTS})(?=$|[^가-힣]))"
)

# Use a strong *field boundary*: a generic nearby keyword is not enough.
PRODUCT_FIELD = re.compile(r"^[ \t]*(?:카드[ \t]*)?상품(?:명)?[ \t]*[:：][ \t]*$")
PRODUCT_SUFFIX = re.compile(
    r"^[ \t]*(?:(?:포인트|마일리지|리워드)[ \t]*)?"
    r"(?:체크|신용|선불|법인|교통)?[ \t]*카드[ \t]*$"
)
PERSON_FIELD = re.compile(
    r"(?:고객[ \t]*명|성명|이름|성함|예금주|수취인|신청인|민원인|"
    r"보호자|명의자|계약자|환자명|수령인|연락담당자)\s*[:：]\s*$"
)
BANK_SUFFIX = re.compile(
    r"^은행(?=$|[^가-힣]|(?:에서|으로|에게|에|은|는|이|가|을|를|과|와|의|로)(?=$|[^가-힣]))"
)
KIN = {"장인", "장모", "부친", "모친", "조부", "조모", "빙부", "빙모", "시부", "시모", "외조부", "외조모"}
CONDOLENCE = re.compile(r"^(?:" + "|".join(sorted(map(re.escape,KIN), key=len, reverse=True)) + r")상$")


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    # For role references only: full surname + title span, in original offsets.
    protection_start: Optional[int] = None
    protection_end: Optional[int] = None


def classify_veil_only_person(text: str, start: int, end: int) -> Decision:
    """Classify only an *additional*, otherwise-uncovered Veil PERSON candidate.

    Caller MUST separately verify that this candidate is not an existing strict
    PERSON or protected candidate. This is NOT authorization to remove any masks.
    """
    if not isinstance(text, str) or type(start) is not int or type(end) is not int or not (0 <= start < end <= len(text)):
        return Decision(REVIEW, "invalid_candidate")
    val = text[start:end]
    if not re.fullmatch(r"[가-힣]{1,6}", val):
        return Decision(REVIEW, "unsupported_candidate_form")
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    if le < 0:
        le = len(text)
    before = text[ls:start]
    after = text[end:le]

    # Higher priority than any exclusion: if the candidate sits in a clearly
    # PERSON-valued field, do not auto-ignore or redact it as a role.
    if PERSON_FIELD.search(before):
        return Decision(REVIEW, "explicit_person_field")

    # Card PRODUCT = entire field value: product label + candidate + known card
    # designation. This does not fire merely because 'card' occurs nearby.
    if PRODUCT_FIELD.fullmatch(before) and PRODUCT_SUFFIX.fullmatch(after):
        return Decision(IGNORE, "structured_card_product")

    # Organization suffix must be contiguous, excluding values like '홍 은행'.
    # Does not expand scope to other organization kinds by proximity alone.
    if BANK_SUFFIX.match(after):
        return Decision(IGNORE, "joined_bank_name")

    # A kinship-event word in an explicit obituary heading is not a person name.
    if CONDOLENCE.fullmatch(val) and "[부고]" in text[max(0,ls-60):end]:
        return Decision(IGNORE, "kinship_bereavement_word")

    # A single Korean syllable followed by a category-defined job title is an
    # identity-ambiguous role reference, NOT a deterministic HMAC PERSON.
    if len(val) == 1:
        matched = ROLE_AFTER.match(after)
        if matched:
            return Decision(REDACT, "surname_job_title", start, end + matched.end())

    return Decision(REVIEW, "no_safe_rule")
