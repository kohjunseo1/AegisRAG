"""EXP 1-3: offline independent-validator ablation, no model inference or Gold-driven rules.
Run from the integration checkout with existing V7 raw cache. Aggregate output only.
All scenarios are shadow evaluations, not release decisions. No production code changed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from starter import v7_frozen as F
from starter import rules_detector as R
from starter.v7_onepass import apply_policy
from tools.audit_v7_residuals import EXPECTED, cover, key, review_scenarios
from tools.benchmark_step1 import DATA, ONEPASS_SHA, load_data, metrics, require, score

# Explicit semantic fields, not arbitrary colons. No data-specific names or blacklist.
PERSON_FIELDS = r"(?:성명|이름|고객명|담당자명|신청인|수취인|수령인|예금주|명의자|송금인|작성자|대표자)"
NAME = r"(?:[가-힣]{1,4}(?:[ \t][가-힣]{1,3})?|[A-Za-z][A-Za-z'-]{1,29}(?:[ \t][A-Za-z][A-Za-z'-]{1,29}){0,2})"
FIELD_NAME = re.compile(rf"(?m)(?:^|[|;])[ \t]*{PERSON_FIELDS}[ \t]*[:：][ \t]*(?P<name>{NAME})[ \t]*(?=$|[\r\n|;])")
QUOTED_NAME = re.compile(rf"(?<![가-힣]){PERSON_FIELDS}(?:은|는|이|가)?[ \t]*[:：]?[ \t]*(?:'(?P<single>{NAME})'|\"(?P<double>{NAME})\"|“(?P<curly>{NAME})”)")
# Honorific/recipient constructions: candidate only, not proof that it is a name.
HONOR_NAME = re.compile(r"(?<![가-힣A-Za-z0-9])(?P<name>[가-힣]{1,4})[ \t]*(?:님|씨)(?:께서|에게|한테|께|의|은|는|이|가|도|와|과)?(?![가-힣])")
# Public-field context does not prove a number is public; do not automatically
# add it as a typed entity or declare it safe. Leave it review-only.
PUBLIC = re.compile(r"(?:대표\s*번호|대표\s*전화|공용\s*전화|고객\s*센터|콜센터|내선(?:번호)?)\s*[:：]?\s*$")
FIELD_PREFIX = {
    "phone_number": re.compile(r"(?:휴대\s*전화|휴대폰|핸드폰|연락처|전화번호|직통\s*번호)\s*[:：]\s*$"),
    "email": re.compile(r"(?:이메일|전자우편|메일|e-?mail)\s*[:：]\s*$",re.I),
    "resident_registration_number": re.compile(r"(?:주민등록번호|주민번호)\s*[:：]\s*$"),
    "account_number": re.compile(r"(?:계좌번호|입금계좌|출금계좌)\s*[:：]\s*$"),
    "card_number": re.compile(r"(?:카드번호|신용카드번호|체크카드번호)\s*[:：]\s*$"),
    "address": re.compile(r"(?:자택\s*주소|거주지|거주\s*주소|수취인\s*주소|배송지)\s*[:：]\s*$"),
}
PATTERNS = (("email",R.EMAIL),("resident_registration_number",R.RRN),
            ("card_number",R.CARD),("phone_number",R.PHONE),
            ("account_number",R.ACCOUNT),("address",R.ADDRESS))
# Reuse the team format patterns, not their dev-derived STOPWORDS or name booster.
GROUPS = ("format","field","honorific","repeat")
COMBINATIONS = [(g,(g,)) for g in GROUPS] + [
    ("format_field",("format","field")),("all",GROUPS)]

def candidate(label,a,b,group,reason,strong=False):
    return {"label":label,"start":a,"end":b,"group":group,
            "reason":reason,"strong":bool(strong)}

def overlap(a,b):
    return a["start"]<b["end"] and b["start"]<a["end"]

def field_before(text,a):
    # Do not cross lines or fields when attaching context.
    return re.split(r"[\r\n|;]",text[max(0,a-80):a])[-1]

def generate(text,typed):
    """Gold-independent proposals. Do not mutate input spans or text."""
    out=[]
    for label,pat in PATTERNS:
        for m in pat.finditer(text):
            a,b=m.span()
            if label=="account_number" and R.DATE_LIKE.fullmatch(m.group()):
                continue
            before=field_before(text,a)
            # Shape alone is not sufficient for an automatic typed addition.
            explicit=bool(FIELD_PREFIX[label].search(before))
            public=bool(PUBLIC.search(before)) if label=="phone_number" else False
            strong=explicit and not public
            # If an ADDRESS pattern crosses a line, do not auto-merge across it.
            if label=="address" and re.search(r"[\r\n]",m.group()):
                strong=False
            out.append(candidate(label,a,b,"format",
                        "explicit_field_and_format" if strong else
                        "public_phone_uncertain" if public else "format_only",strong))
    for m in FIELD_NAME.finditer(text):
        a,b=m.span("name")
        out.append(candidate("person",a,b,"field","bounded_person_field"))
    for m in QUOTED_NAME.finditer(text):
        g=next(g for g in ("single","double","curly") if m.group(g) is not None)
        a,b=m.span(g)
        out.append(candidate("person",a,b,"field","quoted_person_field"))
    for m in HONOR_NAME.finditer(text):
        a,b=m.span("name")
        # Honorific alone is always review-only, including single-character names.
        out.append(candidate("person",a,b,"honorific","name_or_role_honorific"))
    field_keys={key(e) for e in out if e["group"]=="field"}
    honor_keys={key(e) for e in out if e["group"]=="honorific"}
    anchors=set()
    for e in typed:
        if e["label"]!="person" or key(e) not in field_keys|honor_keys:
            continue
        n=re.sub(r"[ \t]","",text[e["start"]:e["end"]])
        if re.fullmatch(r"[가-힣]{2,4}",n) and n[0] in F.SURNAMES:
            anchors.add(n)
    for n in sorted(anchors):
        cut=2 if n[:2] in R.COMPOUND and len(n)>2 else 1
        form=re.escape(n[:cut])+r"[ \t]?"+re.escape(n[cut:])
        for m in re.finditer(r"(?<![가-힣A-Za-z0-9])"+form,text):
            a,b=m.span()
            after=re.match(r"[가-힣]*",text[b:]).group()
            if after and not (R.SUFFIX.fullmatch(after) or R.is_title_word(after)):
                continue
            k=("person",a,b)
            # Only a previously typed, independently anchored full name is a
            # seed. Do not propagate from validator-only candidates or given names.
            out.append(candidate("person",a,b,"repeat","anchored_name_repeat",
                                 k in field_keys or k in honor_keys))
    # Stable deduplication per group, retain strongest evidence for identical span.
    unique={}
    for e in out:
        k=(e["group"],*key(e))
        if k not in unique or e["strong"]:
            unique[k]=e
    return sorted(unique.values(),key=lambda e:(e["start"],e["end"],e["label"],e["group"]))

def choose(text,typed,info,proposals,groups,auto):
    """Hypothetical add-only policy: never remove or shrink baseline typed spans."""
    base_mask=cover(typed,len(text))
    dedup={}
    for e in proposals:
        if e["group"] not in groups:
            continue
        a,b=e["start"],e["end"]
        if all(base_mask[a:b]):
            continue # no new protection opportunity; label-only changes out of scope
        k=key(e)
        if k not in dedup:
            dedup[k]=dict(e)
        else:
            dedup[k]["strong"] |= e["strong"]
    candidates=list(dedup.values())
    additions=[]
    for e in candidates:
        if not auto or not e["strong"]:
            continue
        if any(overlap(e,p) for p in typed):
            continue
        if any(key(e)!=key(p) and overlap(e,p) for p in candidates):
            continue
        additions.append({k:e[k] for k in ("label","start","end")})
    added_keys={key(e) for e in additions}
    result=F.validate_spans(text,[*typed,*additions])
    result_mask=cover(result,len(text))
    pending=[e for e in candidates if key(e) not in added_keys
             and not all(result_mask[e["start"]:e["end"]])]
    return result,additions,pending

def measure(c,text,gold,base,typed,info,additions,pending,review):
    n=len(text)
    score(c,typed,gold,n)
    base_m=cover(base,n); typed_m=cover(typed,n)
    proposal_m=cover([*pending,*additions],n)
    protect_m=cover([*info["mask_intervals"],*typed,*pending],n)
    review_m=cover([e for e in pending],n)
    gk={key(e) for e in gold}
    c["docs"]+=1
    c["auto_added"]+=len(additions)
    c["auto_added_exact"]+=sum(key(e) in gk for e in additions)
    c["auto_new_fp"]+=sum(key(e) not in gk for e in additions)
    c["new_review_candidates"]+=len(pending)
    c["new_review_candidate_docs"]+=bool(pending)
    c["review_docs"]+=bool(review)
    old_miss=remaining=False
    for g in gold:
        a,b=g["start"],g["end"]
        size=b-a
        before=sum(base_m[a:b]);after=sum(typed_m[a:b])
        old_miss |= before<size
        remaining |= after<size
        c["newly_uncovered_chars"]+=sum(x and not y for x,y in zip(base_m[a:b],typed_m[a:b]))
        c["proposal_protected_chars"]+=sum(protect_m[a:b])
        if before==0:
            c["baseline_complete_miss"]+=1
            c["complete_miss_auto_fully_recovered"]+=after==size
            c["complete_miss_auto_any_overlap"]+=after>0
            c["complete_miss_candidate_overlap"]+=any(proposal_m[a:b])
            c["complete_miss_pending_overlap"]+=any(review_m[a:b])
            c["complete_miss_pending_full"]+=all(review_m[a:b])
            c["complete_miss_protected_proxy"]+=all(protect_m[a:b])
            c["complete_miss_remaining_unreviewed"]+=(after<size and not review)
        if before<size:
            c["baseline_uncovered_entities"]+=1
            c["baseline_uncovered_entities_now_full"]+=after==size
    c["baseline_leaky_docs"]+=old_miss
    c["baseline_leaky_docs_reviewed"]+=old_miss and bool(review)
    c["residual_leaky_docs"]+=remaining
    c["residual_leaky_docs_reviewed"]+=remaining and bool(review)
    c["residual_leaky_docs_unreviewed"]+=remaining and not review
    c["covered_docs_reviewed"]+=not remaining and bool(review)

def self_test():
    def proposals(t):
        return generate(t,[])
    assert not any(e["group"]=="field" for e in proposals("처리 결과: 정상\n시간: 12:30\n주소 변경 안내: 확인 바랍니다"))
    assert not any(e["group"]=="field" for e in proposals("설명 중 고객명: 김민준이라고 언급"))
    p=proposals("고객명: 김민준\n")
    assert any(e["group"]=="field" and e["start"]==5 and not e["strong"] for e in p)
    p=proposals("담당자명은 '가람'입니다.")
    assert any(e["group"]=="field" and not e["strong"] for e in p)
    p=proposals("연락처: 010-0000-1234")
    assert any(e["label"]=="phone_number" and e["strong"] for e in p)
    p=proposals("대표번호: 02-0000-1234")
    assert not any(e["strong"] for e in p)
    t="고객명: 김민준\n김 민준님"
    a=t.index("김민준")
    b=[{"label":"person","start":a,"end":a+3}]
    p=generate(t,b)
    assert any(e["group"]=="repeat" and e["start"]==t.index("김 민준") for e in p)
    final,added,pending=choose(t,b,{},p,("repeat",),True)
    assert all(key(e) in {key(x) for x in final} for e in b)
    print("VALIDATOR_SELF_TEST_PASS",flush=True)

def run(name,docs,logs):
    counters=defaultdict(Counter)
    raw_count=Counter()
    proposal_counts=Counter()
    per_group=defaultdict(Counter)
    current_review=0
    timings=[]
    for doc,entry in zip(docs,logs):
        text=doc["text"]; gold=doc["entities"]
        base,info=apply_policy(text,entry["spans"])
        score(raw_count,info["raw_spans"],gold,len(text))
        current_review+=bool(info["review"])
        scenarios=review_scenarios(text,base,info)
        audit_review=scenarios["audit_and_resolved_postal"]
        measure(counters["baseline_current"],text,gold,base,base,info,[],[],info["review"])
        measure(counters["baseline_audit"],text,gold,base,base,info,[],[],audit_review)
        start=time.perf_counter()
        proposals=generate(text,base)
        timings.append((time.perf_counter()-start)*1000)
        bm=cover(base,len(text))
        gk={key(e) for e in gold}
        for e in proposals:
            a,b=e["start"],e["end"]
            if all(bm[a:b]):
                continue
            group=e["group"]
            proposal_counts[group]+=1
            per_group[group]["exact_gold"]+=key(e) in gk
            per_group[group]["overlaps_uncovered_gold"]+=any(
                g["start"]<b and a<g["end"] and
                not all(bm[max(a,g["start"]):min(b,g["end"])]) for g in gold)
        for tag,groups in COMBINATIONS:
            for auto in (False,True):
                mode=tag+("_auto" if auto else "_review")
                typed,adds,pending=choose(text,base,info,proposals,groups,auto)
                measure(counters[mode],text,gold,base,typed,info,adds,pending,
                        [*audit_review,*pending])
    def sig(m):
        return tuple(m[k] for k in ("tp","fp","fn","fully_uncovered","leak_chars"))
    er,et,ev=EXPECTED[name]
    require(sig(metrics(raw_count))==er and sig(metrics(counters["baseline_current"]))==et
            and current_review==ev,"CACHE_REPRODUCTION_MISMATCH_"+name)
    result={}
    for mode,c in counters.items():
        m=metrics(c)
        m["proposal_protection_recall_proxy"]=c["proposal_protected_chars"]/c["gold_chars"] if c["gold_chars"] else 1.0
        result[mode]=m
    return {"scenarios":result,"uncovered_proposals_by_group":dict(proposal_counts),
            "candidate_quality":dict(per_group),
            "mean_candidate_generation_ms":sum(timings)/len(timings)}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-root",type=Path,default=Path.home()/"AegisRAG-unified-v1")
    p.add_argument("--out",type=Path)
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    self_test()
    if a.self_test:
        return
    require(a.out is not None,"OUTPUT_REQUIRED")
    out=a.out.expanduser().resolve()
    require(not out.exists(),"OUTPUT_EXISTS")
    require(not any((d/".git").exists() for d in [out,*out.parents]),"OUTPUT_MUST_BE_OUTSIDE_GIT")
    cache=a.old_root.expanduser()/"step2_privacy_rag/local_eval/v7_final_candidate_eval"
    manifest=json.loads((cache/"manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("model_sha256")==ONEPASS_SHA and manifest.get("pilot")==0
            and manifest.get("mode")=="token510_only_no_post","CACHE_MANIFEST_MISMATCH")
    hashes={x["role"]:x["sha256"] for x in manifest["data"]}
    paths={"A187":Path.home()/"AegisRAG/step2_privacy_rag/local_eval/holdout_labeled.jsonl",
           "B171":Path.home()/"Downloads/blind_eval.jsonl",
           "Hard403":Path.home()/"aegis_external_eval/team4/synth_hard_eval.jsonl"}
    reports={}
    for name,path in paths.items():
        require(hashes.get(name)==DATA[name][2],"CACHE_DATA_HASH_MISMATCH_"+name)
        docs=load_data(name,path)
        logs=json.loads((cache/f"PRIVATE_{name}_token510_candidate_offset_predictions.json").read_text(encoding="utf-8"))
        require(isinstance(logs,list) and len(logs)==len(docs),"CACHE_COUNT_MISMATCH_"+name)
        reports[name]=run(name,docs,logs)
        print("PARITY_PASS",name,flush=True)
        for mode,m in reports[name]["scenarios"].items():
            fields=("strict_f1","mask_recall","fully_uncovered","leak_chars","auto_added",
                    "auto_new_fp","complete_miss_auto_fully_recovered","complete_miss_pending_overlap",
                    "review_docs","residual_leaky_docs_unreviewed","covered_docs_reviewed",
                    "proposal_protection_recall_proxy","newly_uncovered_chars")
            print("RESULT",name,mode,json.dumps({k:m.get(k,0) for k in fields},sort_keys=True),flush=True)
    report={"note":"Original Gold unchanged; shadow review/protection proxies, not actual transfer. No training or inference.",
            "datasets":reports}
    out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write("\n")
    print("VALIDATOR_ABLATION_COMPLETE",out,flush=True)

if __name__=="__main__":
    main()
