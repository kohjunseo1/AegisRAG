#!/usr/bin/env python3
"""Review-assisted local HMAC preparation. Never auto-approves Step1 REVIEW.

Only validated, human-classified name-boundary and Veil-PERSON candidates may
be corrected. Preserves every original protected source position, re-runs Gate
using the original A.X and Veil heads, and pseudonymizes locally. No DB or HCX.
Review UI journal entries authorize correction only when every candidate has a
current, explicit non-UNRESOLVED choice. Generic PII failures remain BLOCKED.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"scripts"),str(ROOT/"step2_privacy_rag/app"),
              str(ROOT/"step1_pii_detector/src")]

from gate_validation import check_spans, merge_masks, covered, overlaps
from name_boundary_policy import propose
from name_protection_policy import decide, _FIELD_RE
from name_protection_handoff import _FrozenDetectorView
from privacy_gate import check_privacy_gate
from local_pseudonymizer import pseudonymize_local
from region_keys import keys_from_gate
import review_dashboard_q001 as ui

NAME_ISSUES={"possible_uncovered_name_suffix","review_only"}
STEP1_READY="READY_FOR_PSEUDONYMIZATION"
GATE_READY="READY_FOR_LOCAL_PSEUDONYMIZATION"


class ReviewValidationError(ValueError):
    """Safe, fixed code: no input data in exceptions."""


def _reject(code:str):
    raise ReviewValidationError(code)


@dataclass(frozen=True)
class ReviewedOutcome:
    doc_id:str
    sanitized_text:str
    region_keys:tuple[str,...]
    token_count:int
    review_type:str
    original_protected_characters:int
    final_protected_characters:int


def _mask_positions(spans:list[dict])->set[int]:
    return {i for x in spans for i in range(x["start"],x["end"])}


def validated_choices(cases:dict, journal_entries:list[dict]) -> dict[str,list[str]]:
    """Verify last recorded decision for EACH candidate, original doc and offsets."""
    latest={}
    for record in journal_entries:
        if not isinstance(record,dict) or record.get("format")!="q001-review-draft-v1":
            _reject("INVALID_JOURNAL_RECORD")
        did=record.get("document_id")
        ix=record.get("candidate_index")
        if did not in cases or type(ix) is not int or not 0<=ix<len(cases[did]["candidates"]):
            _reject("UNKNOWN_REVIEW_CANDIDATE")
        target=cases[did]["candidates"][ix]
        if (record.get("source_span")!=[target["start"],target["end"]]
            or record.get("source_digest")!=hashlib.sha256(
                cases[did]["source"].encode("utf-8")).hexdigest()
            or record.get("kind")!=target["kind"]
            or record.get("state")!="NEEDS_INDEPENDENT_REVALIDATION"
            or record.get("human_classification") not in ui.CHOICES[target["kind"]]):
            _reject("INVALID_OR_STALE_REVIEW_DECISION")
        latest[(did,ix)]=record["human_classification"]
    out={}
    for did,c in cases.items():
        choices=[latest.get((did,i),"UNRESOLVED") for i in range(len(c["candidates"]))]
        if "UNRESOLVED" in choices:
            _reject("REVIEW_STILL_UNRESOLVED")
        out[did]=choices
    return out


def reviewed_prepare(doc_id:str, source:str, detector:Any, key:bytes,
                     case:dict, choices:list[str]) -> ReviewedOutcome:
    if not isinstance(source,str) or type(key) is not bytes or len(key)<32:
        _reject("INVALID_INPUT")
    original=detector.detect(source)
    if not isinstance(original,dict) or original.get("status") not in (
        STEP1_READY,"REVIEW_REQUIRED"):
        _reject("STEP1_UNSUPPORTED_STATUS")
    old_strict=check_spans(original.get("entities"),len(source),True)
    old_masks=merge_masks(check_spans(original.get("mask_intervals"),len(source),False))
    if any(not covered(e,old_masks) for e in old_strict):
        _reject("ORIGINAL_STRICT_UNCOVERED")
    if (case.get("source")!=source or len(case.get("candidates",[]))!=len(choices)
        or not case["candidates"]):
        _reject("STALE_REVIEW_CASE")
    edits=[]
    proposed={p.candidate_full_span:p for p in propose(source,original)}
    for item,choice in zip(case["candidates"],choices):
        a,b=item["start"],item["end"]
        if not (type(a) is int and type(b) is int and 0<=a<b<=len(source)):
            _reject("INVALID_REVIEW_SPAN")
        if item["kind"]=="name_boundary":
            p=proposed.get((a,b))
            if p is None:
                _reject("NAME_CANDIDATE_CHANGED")
            target=(a,b) if choice=="LONG_NAME" else (a,b-1)
            if choice not in ("LONG_NAME","SHORT_NAME_PLUS_PARTICLE"):
                _reject("INVALID_NAME_REVIEW")
            orig=p.original_span
            matches=[s for s in old_strict if s["label"]=="person"
                     and (s["start"],s["end"])==orig]
            if len(matches)!=1:
                _reject("NAME_ORIGINAL_ENTITY_MISMATCH")
            edits.append({"kind":"name_boundary","a":a,"b":b,
                          "old":matches[0],"new":{"label":"person",
                              "start":target[0],"end":target[1]}})
        elif item["kind"]=="veil_candidate":
            if choice not in ("PERSON_PII","NON_PERSON_EXPRESSION"):
                _reject("INVALID_VEIL_REVIEW")
            from veil_person_policy import classify_veil_only_person, IGNORE
            independent_veil=check_spans(detector.veil_predict(source),len(source),True)
            if not any(x["label"]=="person" and (x["start"],x["end"])==(a,b)
                       for x in independent_veil):
                _reject("VEIL_CANDIDATE_CHANGED")
            matches=[x for x in old_strict if x["label"]=="person"
                     and a<=x["start"]<x["end"]<=b]
            interfering=[x for x in old_strict if overlaps(x,{"start":a,"end":b})
                         and x not in matches]
            if len(matches)!=1 or interfering:
                _reject("VEIL_STRICT_ENTITY_UNSAFE")
            if choice=="NON_PERSON_EXPRESSION":
                # Manual dismissal limited to a syntactically documented
                # lexical false-positive family; still retain old masks.
                policy=classify_veil_only_person(source,a,b)
                if policy.action!=IGNORE:
                    _reject("NO_INDEPENDENT_LEXICAL_EVIDENCE")
                replacement=None
            else:
                replacement={"label":"person","start":a,"end":b}
            edits.append({"kind":"veil_candidate","a":a,"b":b,
                          "old":matches[0],"new":replacement})
        else:
            _reject("UNKNOWN_REVIEW_KIND")

    # Never accept multiple edits to the same strict person or intersecting
    # review candidates: an ambiguous compound correction needs a new review.
    keys=[(x["old"]["start"],x["old"]["end"]) for x in edits]
    if len(keys)!=len(set(keys)):
        _reject("MULTIPLE_EDITS_SAME_ENTITY")
    for i,x in enumerate(edits):
        if any(x["a"]<y["b"] and y["a"]<x["b"] for y in edits[i+1:]):
            _reject("OVERLAPPING_REVIEW_EDITS")

    if original["status"]=="REVIEW_REQUIRED":
        issues=original.get("issues")
        if (not isinstance(issues,list) or not issues
            or any(not isinstance(i,dict) or i.get("code") not in NAME_ISSUES
                   for i in issues)
            or any(e["kind"]!="name_boundary" for e in edits)):
            _reject("OTHER_STEP1_REVIEW_REQUIRES_MANUAL_POLICY")
    else:
        if any(e["kind"]=="name_boundary" for e in edits):
            _reject("NAME_REVIEW_STATUS_CHANGED")

    revised=deepcopy(original)
    revised["status"]=STEP1_READY
    revised["issues"]=[]
    old_entity_tuples={(s["label"],s["start"],s["end"]) for s in old_strict}
    replaced={(e["old"]["label"],e["old"]["start"],e["old"]["end"]) for e in edits}
    new_entities=[deepcopy(s) for s in old_strict
                  if (s["label"],s["start"],s["end"]) not in replaced]
    new_entities.extend(deepcopy(e["new"]) for e in edits if e["new"] is not None)
    new_entities.sort(key=lambda x:(x["start"],x["end"],x["label"]))
    if any(overlaps(x,y) for x,y in zip(new_entities,new_entities[1:])):
        _reject("CORRECTED_ENTITY_OVERLAP")
    masks=merge_masks(old_masks+[{"start":e["a"],"end":e["b"]} for e in edits])
    if not _mask_positions(old_masks).issubset(_mask_positions(masks)):
        _reject("ORIGINAL_MASK_REGRESSION")
    revised["entities"]=new_entities
    revised["mask_intervals"]=masks

    # Original independent model heads remain in use. A corrected strict
    # result does not suppress Veil and A.X proposals.
    gate=check_privacy_gate(_FrozenDetectorView(detector,revised),source)
    if gate.get("decision")!=GATE_READY:
        _reject("CORRECTED_GATE_REVIEW_OR_BLOCK")
    decision=decide(source,revised,"v5_privacy_first")
    if decision["would_abstain"]:
        _reject("V5_EXTRA_ABSTENTION")
    for p in propose(source,revised):
        if (p.candidate_full_span not in
            {(e["a"],e["b"]) for e in edits if e["kind"]=="name_boundary"}):
            _reject("NEW_UNRESOLVED_NAME_CANDIDATE")
    # Preserve V5 explicit name fields and all masks for every original PII
    # object, including the dismissed false-positive H0091 PERSON interval.
    additional=[{"start":a,"end":b} for a,b in decision["mask_intervals"]]
    additional.extend({"start":m.start(1),"end":m.end(1)}
                      for m in _FIELD_RE.finditer(source))
    final_masks=merge_masks(gate["mask_intervals"]+additional)
    if not _mask_positions(old_masks).issubset(_mask_positions(final_masks)):
        _reject("FINAL_PROTECTION_REGRESSION")
    if any(not covered(e,final_masks) for e in new_entities):
        _reject("CORRECTED_ENTITY_NOT_PROTECTED")
    gate=dict(gate,mask_intervals=final_masks)
    transformed=pseudonymize_local(
        source,gate,hmac_key=key,include_vault_records=False)
    selected={(x["start"],x["end"],x["label"])
              for x in gate["entities_for_pseudonymization"]}
    for e in edits:
        if e["new"] is None:
            if any(a>=e["a"] and b<=e["b"] and label=="person" for a,b,label in selected):
                _reject("FALSE_PERSON_STILL_HAS_HMAC")
        elif (e["new"]["start"],e["new"]["end"],"person") not in selected:
            _reject("APPROVED_NAME_NOT_HMAC_LINKED")
    if transformed.vault_records is not None:
        _reject("UNEXPECTED_VAULT_CAPTURE")
    keys=tuple(sorted(set(keys_from_gate(source,gate,key))))
    return ReviewedOutcome(
        doc_id,transformed.sanitized_text,keys,
        transformed.hmac_entity_count,
        "review_name" if all(e["kind"]=="name_boundary" for e in edits) else "review_veil",
        len(_mask_positions(old_masks)),len(_mask_positions(final_masks)))


def prepare_approved_batch(sources:dict[str,str], detector:Any,key:bytes,
                           journal_entries:list[dict]) -> list[ReviewedOutcome]:
    cases=ui.get_cases(sources,detector)
    decisions=validated_choices(cases,journal_entries)
    return [reviewed_prepare(did,sources[did],detector,key,cases[did],decisions[did])
            for did in sorted(cases)]


def load_journal(path:Path) -> list[dict]:
    return [json.loads(line) for line in ui.private_path(path).read_text(
        encoding="utf-8").splitlines() if line.strip()]
