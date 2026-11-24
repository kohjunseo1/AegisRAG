"""Local-only cache audit of V7 Step1. No model inference, Gold changes or network.
Only aggregate JSON on stdout. Detailed offsets/context stay outside Git.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from tools.benchmark_step1 import DATA, ONEPASS_SHA, load_data, score, metrics, require
from starter import v7_frozen as F
from starter.v7_onepass import apply_policy

EXPECTED = {
    "A187": ((904,37,8,0,20),(909,3,3,0,0),32),
    "B171": ((1435,64,4,0,0),(1435,56,4,0,0),8),
    "Hard403": ((1811,230,147,33,157),(1815,222,143,33,153),19),
}
SUPPRESSION = {"standalone_surname_title","explicit_internal_extension",
               "event_status_nominal","bereavement_kinship_role"}
HONOR = re.compile(r"^[ \t]*(?:님|씨)(?=$|[ \t\r\n,.;:!?)\]가-힣])")
FIELD = re.compile(r"(?:고객명|성명|이름|담당자명|신청인|수취인)\s*[:：]?\s*$")
PUBLIC_PHONE = re.compile(r"(?:대표\s*번호|고객\s*센터|콜센터|공용\s*전화|문의\s*전화|내선(?:번호)?)\s*[:：]?\s*$")
TITLE_AFTER = F.SURNAME_TITLE_AFTER

def key(e):
    return e["label"],e["start"],e["end"]

def cover(spans,n):
    m=bytearray(n)
    for e in spans:
        a,b=e["start"],e["end"]
        m[a:b]=b"\x01"*(b-a)
    return m

def gold_policy_flag(text,e):
    """Heuristic annotation-conflict candidates, never exclusions or adjudication."""
    label,a,b=key(e)
    val=text[a:b]
    before=text[max(0,a-50):a]
    after=text[b:b+28]
    standalone=a==0 or not ("가"<=text[a-1]<="힣")
    if label=="person":
        if len(val)==1 and val in F.SURNAMES and standalone:
            if TITLE_AFTER.match(after):
                return "possible_surname_title"
            if HONOR.match(after):
                return "ambiguous_surname_or_given_honorific"
        if len(val)==2 and val[0] in F.SURNAMES and val[1] in ("씨","님") and standalone:
            return "ambiguous_surname_or_given_honorific"
        if FIELD.search(before):
            return "explicit_person_field"
        if F.classify_nonperson(text,a,b):
            return "possible_nonperson_context"
    if label=="phone_number" and PUBLIC_PHONE.search(before):
        return "possible_public_or_extension_phone"
    return "none"

def ambiguous_honorific(text,e):
    return gold_policy_flag(text,e)=="ambiguous_surname_or_given_honorific"

def postal_resolved(event,typed,info):
    """Only clear previously reported overlap if final join actually covers it.
    Never use Gold to decide a review signal.
    """
    if event["source"]!="postal:review_overlap":
        return False
    a,b=event["start"],event["end"]
    if not isinstance(a,int) or not isinstance(b,int) or a>=b:
        return False
    joined=[j for j in info["join_events"] if j.get("status")=="merged"
            and j.get("start",a+1)<=a and j.get("end",b-1)>=b]
    if not joined:
        return False
    hits=[e for e in typed if e["start"]<b and a<e["end"]]
    return len(hits)==1 and hits[0]["label"]=="address" and hits[0]["start"]<=a and hits[0]["end"]>=b

def review_scenarios(text,typed,info):
    orig=info["review"]
    # What-if ONLY: audit suppression, if raw+typed protection kept.
    # No change to actual policy, never authorizes release.
    keep=[e for e in orig if e["source"] not in SUPPRESSION]
    resolved=[e for e in keep if not postal_resolved(e,typed,info)]
    honor=[{"source":"ambiguous_surname_or_given_honorific",**e}
           for e in typed if e["label"]=="person" and ambiguous_honorific(text,e)]
    return {"current":orig,"audit_suppressions":keep,
            "audit_and_resolved_postal":resolved,
            "plus_ambiguous_honorific":resolved+honor}

def csv_write(path,rows,fields):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

def audit(name,docs,logs,local_context):
    rc,tc=Counter(),Counter()
    labels=defaultdict(Counter)
    flags=defaultdict(Counter)
    review=defaultdict(Counter)
    event_counts=Counter()
    reasons=Counter()
    review_impact=defaultdict(Counter)
    errors=[]
    doc_reports=[]
    all_gold_flags=Counter()
    all_pred_flags=Counter()
    for idx,(doc,entry) in enumerate(zip(docs,logs),1):
        text=doc["text"]; gold=doc["entities"]
        typed,info=apply_policy(text,entry["spans"])
        raw=info["raw_spans"]
        score(rc,raw,gold,len(text));score(tc,typed,gold,len(text))
        rawm=cover(raw,len(text)); tm=cover(typed,len(text)); um=cover(info["mask_intervals"],len(text))
        pk={key(e) for e in typed};gk={key(e) for e in gold}
        scenarios=review_scenarios(text,typed,info)
        fully=partly=lost=0
        for g in gold:
            label,a,b=key(g)
            flag=gold_policy_flag(text,g)
            all_gold_flags[flag]+=1
            hit=sum(tm[a:b]);rawhit=sum(rawm[a:b]);unionhit=sum(um[a:b])
            labels[label]["gold"]+=1
            labels[label]["gold_chars"]+=b-a
            labels[label]["leak_chars"]+=b-a-hit
            labels[label]["raw_to_typed_newly_uncovered_chars"]+=sum(x and not y for x,y in zip(rawm[a:b],tm[a:b]))
            labels[label]["raw_to_typed_newly_covered_chars"]+=sum(y and not x for x,y in zip(rawm[a:b],tm[a:b]))
            if hit==0:
                fully+=1;labels[label]["fully_uncovered"]+=1
            elif hit<b-a:
                partly+=1;labels[label]["partly_uncovered_only"]+=1
            if key(g) in pk:
                labels[label]["tp"]+=1
                continue
            labels[label]["fn"]+=1
            lost+=1
            hits=[e for e in typed if e["start"]<b and a<e["end"]]
            kind=("fully_uncovered" if hit==0 else "partly_uncovered" if hit<b-a else
                  "same_label_boundary" if any(e["label"]==label for e in hits) else "wrong_label_full_coverage")
            flags[kind][flag]+=1
            if hit==0:
                review_impact[label]["complete_miss"]+=1
                for s,events in scenarios.items():
                    review_impact[label][s+"_caught_doc"]+=bool(events)
                    review_impact[label][s+"_overlap_signal"]+=any(
                        isinstance(e.get("start"),int) and isinstance(e.get("end"),int)
                        and e["start"]<b and a<e["end"] for e in events)
            row={"doc_index":idx,"kind":"FN","label":label,"start":a,"end":b,
                 "error_type":kind,"policy_flag":flag,"covered_chars":hit,
                 "raw_covered_chars":rawhit,"union_covered_chars":unionhit,
                 "review_current":bool(scenarios["current"]),
                 "review_proposed":bool(scenarios["plus_ambiguous_honorific"]),
                 "review_reasons":";".join(sorted({e["source"] for e in scenarios["current"]})),
                 "gold_value":text[a:b] if local_context else "",
                 "local_context":text[max(0,a-40):min(len(text),b+40)] if local_context else ""}
            errors.append(row)
        for e in typed:
            label,a,b=key(e)
            flag=gold_policy_flag(text,e);all_pred_flags[flag]+=1
            if key(e) in gk:
                continue
            labels[label]["fp"]+=1
            errors.append({"doc_index":idx,"kind":"FP","label":label,"start":a,"end":b,
                 "error_type":"overlap_gold" if any(g["start"]<b and a<g["end"] for g in gold) else "no_gold_overlap",
                 "policy_flag":flag,"covered_chars":"","raw_covered_chars":"","union_covered_chars":"",
                 "review_current":bool(scenarios["current"]),"review_proposed":bool(scenarios["plus_ambiguous_honorific"]),
                 "review_reasons":";".join(sorted({e["source"] for e in scenarios["current"]})),
                 "gold_value":"","local_context":text[max(0,a-40):min(len(text),b+40)] if local_context else ""})
        for s,events in scenarios.items():
            c=review[s];c["docs"]+=1;c["review_docs"]+=bool(events)
            c["leaky_docs"]+=bool(fully or partly)
            c["leaky_docs_reviewed"]+=bool(events) and bool(fully or partly)
            c["leaky_docs_not_reviewed"]+=not events and bool(fully or partly)
            c["complete_miss_docs_reviewed"]+=bool(events) and bool(fully)
            c["complete_miss_docs_not_reviewed"]+=not events and bool(fully)
            c["covered_docs_reviewed"]+=bool(events) and not (fully or partly)
            c["complete_miss_entities_reviewed"]+=fully if events else 0
        for event in info["review"]:
            reason=event["source"];event_counts[reason]+=1
            a,b=event.get("start"),event.get("end")
            if isinstance(a,int) and isinstance(b,int):
                reasons[reason+"__overlaps_gold"]+=any(g["start"]<b and a<g["end"] for g in gold)
                reasons[reason+"__resolved_postal"]+=postal_resolved(event,typed,info)
        if lost or scenarios["current"] or scenarios["plus_ambiguous_honorific"]:
            doc_reports.append({"doc_index":idx,"fully_uncovered":fully,"partly_uncovered":partly,
                 "strict_fn":lost,"current":bool(scenarios["current"]),
                 "audit_suppressions":bool(scenarios["audit_suppressions"]),
                 "audit_and_resolved_postal":bool(scenarios["audit_and_resolved_postal"]),
                 "plus_ambiguous_honorific":bool(scenarios["plus_ambiguous_honorific"]),
                 "reasons":";".join(sorted({e["source"] for e in scenarios["current"]})),
                 "proposed_reasons":";".join(sorted({e["source"] for e in scenarios["plus_ambiguous_honorific"]}))})
    raw_result,typed_result=metrics(rc),metrics(tc)
    def signature(m):
        return tuple(m[k] for k in ("tp","fp","fn","fully_uncovered","leak_chars"))
    expected_raw,expected_typed,expected_review=EXPECTED[name]
    require(signature(raw_result)==expected_raw and signature(typed_result)==expected_typed
            and review["current"]["review_docs"]==expected_review,"CACHE_REPRODUCTION_MISMATCH_"+name)
    return {"raw":raw_result,"typed":typed_result,"per_label":dict(labels),
            "policy_flags_by_error":dict(flags),"all_gold_policy_flags":all_gold_flags,
            "all_typed_policy_flags":all_pred_flags,"review_scenarios":dict(review),
            "review_events":event_counts,"review_event_gold_overlap":reasons,
            "complete_miss_review_by_label":dict(review_impact)},errors,doc_reports

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-root",type=Path,default=Path.home()/"AegisRAG-unified-v1")
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--local-context",action="store_true",
                   help="Write original values/context to PRIVATE local CSV only. Never upload it.")
    a=p.parse_args()
    root=a.old_root.expanduser().resolve()
    cache=root/"step2_privacy_rag/local_eval/v7_final_candidate_eval"
    manifest=json.loads((cache/"manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("model_sha256")==ONEPASS_SHA and manifest.get("pilot")==0
            and manifest.get("mode")=="token510_only_no_post","CACHE_MANIFEST_MISMATCH")
    hashes={x["role"]:x["sha256"] for x in manifest["data"]}
    paths={"A187":Path.home()/"AegisRAG/step2_privacy_rag/local_eval/holdout_labeled.jsonl",
           "B171":Path.home()/"Downloads/blind_eval.jsonl",
           "Hard403":Path.home()/"aegis_external_eval/team4/synth_hard_eval.jsonl"}
    out=a.out.expanduser().resolve()
    require(not out.exists(),"OUTPUT_EXISTS")
    require(not any((parent/".git").exists() for parent in [out,*out.parents]),"OUTPUT_MUST_BE_OUTSIDE_GIT")
    reports={}; details={}
    for name,path in paths.items():
        require(hashes.get(name)==DATA[name][2],"CACHE_DATA_HASH_MISMATCH_"+name)
        docs=load_data(name,path)
        logs=json.loads((cache/f"PRIVATE_{name}_token510_candidate_offset_predictions.json").read_text(encoding="utf-8"))
        require(isinstance(logs,list) and len(logs)==len(docs),"CACHE_COUNT_MISMATCH_"+name)
        report,errors,doc_reports=audit(name,docs,logs,a.local_context)
        reports[name]=report
        details[name]=(errors,doc_reports)
        print("AUDIT_PARITY_PASS",name,flush=True)
    out.mkdir(parents=True,mode=0o700)
    for name,(errors,docs) in details.items():
        if errors:csv_write(out/f"{name}_PRIVATE_errors.csv",errors,list(errors[0]))
        if docs:csv_write(out/f"{name}_PRIVATE_review_docs.csv",docs,list(docs[0]))
    report={"note":"Offline Gold-based evaluation only; no Gold modification, no release approval. Review scenarios are hypothetical and rely on retained protection, not actual transfer.",
            "datasets":reports}
    fd=os.open(out/"aggregate.json",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write("\n")
    print("AUDIT_AGGREGATE",json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)
    print("AUDIT_COMPLETE",out,flush=True)

if __name__=="__main__":
    main()
