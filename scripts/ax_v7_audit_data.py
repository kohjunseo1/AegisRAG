"""Read-only V7 curated-v3 training and dev dataset audit (no model / HF).

Usage:
 python scripts/ax_v7_audit_data.py --train .../train.jsonl --dev .../dev.jsonl

Checks all rows, span offsets, verified negatives, missing annotations, repeated
names, accidental ordinary-noun-like surnames, punctuation and split leaks.
The result is a *data-contract* pass only: not actual A.X token alignment or
independent real-world generalization. It never outputs raw names/addresses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter
from pathlib import Path

from ax_v7_make_data import SHORT_FULL_NAMES, REGION_DISTRICTS, ROADS, VILLAGES

EXPECTED_FAMILIES = {
    "full_name", "short_full_name", "given_only_explicit",
    "single_name_explicit", "repeated_name_particle",
    "address_road", "address_lot", "address_detail",
    "negative_common_noun", "negative_field_header",
    "negative_organization", "negative_product",
}
POLICY = "v7_explicit_person_or_full_address_v3"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path, role):
    p = Path(path).expanduser().resolve()
    if p.suffix != ".jsonl" or not p.is_file():
        raise ValueError(role + ": FILE_MISSING_OR_NOT_JSONL")
    rows, ids, texts = [], set(), set()
    for lineno, line in enumerate(p.open(encoding="utf-8-sig"), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        doc_id, txt = row.get("id"), row.get("text")
        if not isinstance(doc_id,str) or not isinstance(txt,str) or len(txt)<2:
            raise ValueError(f"{role}:{lineno}:BAD_DOCUMENT_SCHEMA")
        if not doc_id.startswith(role+"_"):
            raise ValueError(f"{role}:{lineno}:SPLIT_ID_MISMATCH")
        if doc_id in ids or txt in texts:
            raise ValueError(f"{role}:{lineno}:DUPLICATE_ID_OR_TEXT")
        ids.add(doc_id)
        texts.add(txt)
        if row.get("annotation_scope")!="seven_only" or row.get("quality_tier")!="high_confidence" or row.get("label_policy")!=POLICY:
            raise ValueError(f"{role}:{lineno}:OLD_OR_UNREVIEWED_ANNOTATION_POLICY")
        family = row.get("case_family")
        if family not in EXPECTED_FAMILIES:
            raise ValueError(f"{role}:{lineno}:UNKNOWN_FAMILY")
        ents = row.get("entities")
        o_spans = row.get("verified_o_spans", [])
        if not isinstance(ents,list) or not isinstance(o_spans,list):
            raise ValueError(f"{role}:{lineno}:BAD_SPAN_ARRAY")
        intervals=[]
        for e in ents:
            label, a, b = e.get("label"),e.get("start"),e.get("end")
            if label not in ("person","address") or type(a)!=int or type(b)!=int or not 0<=a<b<=len(txt):
                raise ValueError(f"{role}:{lineno}:BAD_POSITIVE_SPAN")
            value=txt[a:b]
            if "text" in e and e["text"]!=value:
                raise ValueError(f"{role}:{lineno}:GOLD_TEXT_MISMATCH")
            intervals.append((a,b))
            if label=="person":
                if not re.fullmatch(r"[가-힣]+(?: [가-힣]+)?",value):
                    raise ValueError(f"{role}:{lineno}:SUSPICIOUS_PERSON_CHARS")
                compact=value.replace(" ","")
                if family=="single_name_explicit" and len(compact)!=1:
                    raise ValueError(f"{role}:{lineno}:SINGLE_PERSON_LENGTH")
                if family in ("short_full_name","given_only_explicit") and len(compact)!=2:
                    raise ValueError(f"{role}:{lineno}:SHORT_PERSON_LENGTH")
                if family=="short_full_name" and compact not in SHORT_FULL_NAMES:
                    raise ValueError(f"{role}:{lineno}:UNVETTED_SHORT_SURNAME_COMBINATION")
                if len(compact)>4:
                    raise ValueError(f"{role}:{lineno}:PERSON_LENGTH_TOO_LARGE")
                if not any(x in txt for x in ("성명","이름","신청인","수령인","계약자")):
                    raise ValueError(f"{role}:{lineno}:UNANCHORED_PERSON_POSITIVE")
                if "남 궁" in value or "황 보" in value:
                    raise ValueError(f"{role}:{lineno}:SPLIT_COMPOUND_SURNAME")
            elif label=="address":
                if not any(region in value and district in value for region,district in REGION_DISTRICTS):
                    raise ValueError(f"{role}:{lineno}:ADDRESS_MISSING_REGION_DISTRICT")
                if not re.search(r"\d+",value):
                    raise ValueError(f"{role}:{lineno}:ADDRESS_MISSING_BUILDING_NUMBER")
                if re.search(r"\b\d{5}\b",value):
                    raise ValueError(f"{role}:{lineno}:UNDECIDED_ZIP_POLICY")
                if family=="address_road" and not any(road+" " in value for road in ROADS):
                    raise ValueError(f"{role}:{lineno}:NOT_ROAD_ADDRESS")
                if family=="address_lot" and not any(village+" " in value for village in VILLAGES):
                    raise ValueError(f"{role}:{lineno}:NOT_LOT_ADDRESS")
                if family=="address_detail" and not re.search(r"\d+동\s+\d+호$",value):
                    raise ValueError(f"{role}:{lineno}:NOT_DETAILED_ADDRESS")
        intervals.sort()
        if any(a<end for (a,_),(start,end) in zip(intervals[1:],intervals[:-1])):
            raise ValueError(f"{role}:{lineno}:POSITIVE_OVERLAP")
        neg=[]
        for n in o_spans:
            a,b=n.get("start"),n.get("end")
            if type(a)!=int or type(b)!=int or not 0<=a<b<=len(txt) or txt[a:b]!=n.get("text"):
                raise ValueError(f"{role}:{lineno}:BAD_VERIFIED_NEGATIVE")
            if any(a<end and start<b for start,end in intervals):
                raise ValueError(f"{role}:{lineno}:NEGATIVE_POSITIVE_OVERLAP")
            neg.append((a,b))
        if family.startswith("negative_"):
            if ents or len(neg)!=1:
                raise ValueError(f"{role}:{lineno}:UNVERIFIED_NEGATIVE")
        else:
            if o_spans or not ents:
                raise ValueError(f"{role}:{lineno}:MISSING_POSITIVE")
            if family.startswith("address_") and any(x["label"]!="address" for x in ents):
                raise ValueError(f"{role}:{lineno}:ADDRESS_LABEL_MISMATCH")
            if not family.startswith("address_") and any(x["label"]!="person" for x in ents):
                raise ValueError(f"{role}:{lineno}:PERSON_LABEL_MISMATCH")
        if family=="repeated_name_particle":
            if len(ents)!=2 or txt[ents[0]["start"]:ents[0]["end"]]!=txt[ents[1]["start"]:ents[1]["end"]]:
                raise ValueError(f"{role}:{lineno}:REPEATED_NAME_SPAN_MISMATCH")
        if "]로 기재되어 있습니다" in txt:
            raise ValueError(f"{role}:{lineno}:SUSPECT_KOREAN_PARTICLE")
        rows.append(row)
    if not rows:
        raise ValueError(role+":EMPTY_DATASET")
    return p,rows


def positives(rows,label):
    return {r["text"][e["start"]:e["end"]].replace(" ","")
            for r in rows for e in r["entities"] if e["label"]==label}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train",required=True,type=Path)
    parser.add_argument("--dev",required=True,type=Path)
    args=parser.parse_args()
    splits={key:load(getattr(args,key),key) for key in ("train","dev")}
    t=splits["train"][1];d=splits["dev"][1]
    if {sha(re.sub(r"\s+","",x["text"])) for x in t} & {sha(re.sub(r"\s+","",x["text"])) for x in d}:
        raise ValueError("LEAK_TRAIN_DEV_TEXT")
    for label in ("person","address"):
        if positives(t,label)&positives(d,label):
            raise ValueError("LEAK_TRAIN_DEV_"+label.upper()+"_VALUES")
    result={}
    for name,(p,rows) in splits.items():
        counts=Counter(r["case_family"] for r in rows)
        missing=EXPECTED_FAMILIES-set(counts)
        if missing:
            raise ValueError(name+":MISSING_CASE_FAMILIES "+str(sorted(missing)))
        entity_counts=Counter(e["label"] for r in rows for e in r["entities"])
        lengths=[len(r["text"]) for r in rows]
        result[name]={
            "sha256":hashlib.sha256(p.read_bytes()).hexdigest(),
            "documents":len(rows), "families":dict(sorted(counts.items())),
            "Gold_spans":dict(sorted(entity_counts.items())),
            "hard_negative_docs":sum(bool(r.get("verified_o_spans")) for r in rows),
            "text_length_char_min_median_max":[min(lengths),statistics.median(lengths),max(lengths)],
        }
    print("V7_CURATED_DATA_AUDIT_PASS",json.dumps(result,ensure_ascii=False,sort_keys=True))
    print("AUDIT_SCOPE=data_only,no_real_AX_tokenizer,no_37_head,no_replay,no_independent_holdout,no_training")


if __name__=="__main__":
    main()
