"""EXP6-B: no-inference candidate-boundary comparison on saved Hard403 scores.
Shadow evaluation only: no typed entity edits, no automatic release, no Gold edits.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path

from tools.audit_v7_residuals import cover, key
from tools.benchmark_step1 import load_data, require

def expand(text,tokens,protected,allow_space=False):
    """25% seeds with adjacent 10% same-best-type support. No arbitrary word list."""
    groups=[]; current=[]
    def flush():
        if current and any(t["p_pii"]>=.25 for t in current):
            groups.extend(dict(label=t["best_type"],start=t["start"],end=t["end"])
                          for t in current)
        current.clear()
    for t in tokens:
        a,b=t["start"],t["end"]
        require(0<=a<=b<=len(text),"INVALID_TOKEN_OFFSET")
        if (a==b or not text[a:b].strip() or str(t["winner"]).upper()!="O"
                or t["p_pii"]<.10 or all(protected[a:b])):
            flush()
            continue
        if current:
            prev=current[-1]
            gap=text[prev["end"]:a] if a>=prev["end"] else ""
            connected=(a<=prev["end"] or (allow_space and gap in (" ","\t")))
            if not connected or prev["best_type"]!=t["best_type"]:
                flush()
        current.append(t)
    flush()
    return sorted({key(e):e for e in groups}.values(),key=key)

def self_test():
    text="가나다 라"
    tok=[dict(start=a,end=b,winner="O",p_pii=p,best_type=l)
         for a,b,p,l in [(0,1,.12,"person"),(1,2,.30,"person"),
                         (2,3,.12,"address"),(4,5,.12,"person")]]
    assert [(x["start"],x["end"]) for x in expand(text,tok,bytearray(len(text)))]==[(0,1),(1,2)]
    assert not expand(text,[tok[0]],bytearray(len(text)))
    assert not expand(text,[tok[1]],bytearray([0,1,0,0,0]))
    print("BOUNDARY_SELF_TEST_PASS",flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scores",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    self_test()
    out=a.out.expanduser().resolve()
    require(not out.exists(),"OUTPUT_EXISTS")
    require(not any((x/".git").exists() for x in [out,*out.parents]),"OUTSIDE_GIT_REQUIRED")
    docs=load_data("Hard403",Path.home()/"aegis_external_eval/team4/synth_hard_eval.jsonl")
    records=json.loads(a.scores.expanduser().read_text(encoding="utf-8"))
    require(len(docs)==len(records),"CACHE_COUNT_MISMATCH")
    modes=("o_pii_10","o_pii_25","seed25_adjacent10","seed25_space10")
    totals={m:Counter() for m in modes}
    cases=[]
    for idx,(doc,r) in enumerate(zip(docs,records),1):
        t=doc["text"];n=len(t);gold=doc["entities"]
        require(r["doc_index"]==idx and r["length"]==n and
                sorted(map(key,r["gold"]))==sorted(map(key,gold)),"CACHE_GOLD_PARITY_MISMATCH")
        bm=cover(r["typed"],n);pm=cover(r["mask_intervals"],n);gm=cover(gold,n)
        misses=[g for g in gold if not any(bm[g["start"]:g["end"]])]
        leaky=any(not all(bm[g["start"]:g["end"]]) for g in gold)
        proposals={m:r["mode_candidates"][m] for m in modes[:2]}
        proposals[modes[2]]=expand(t,r["tokens"],pm)
        proposals[modes[3]]=expand(t,r["tokens"],pm,True)
        masks={}
        for mode,ps in proposals.items():
            m=cover(ps,n);masks[mode]=m;c=totals[mode]
            review=bool(ps or r["baseline_review"])
            c["review_docs"]+=review
            c["leaky_docs_reviewed"]+=leaky and review
            c["leaky_docs_unreviewed"]+=leaky and not review
            c["miss_overlap"]+=sum(any(m[g["start"]:g["end"]]) for g in misses)
            c["miss_full"]+=sum(all(m[g["start"]:g["end"]]) for g in misses)
            c["candidate_chars_outside_gold"]+=sum(x and not y and not z for x,y,z in zip(m,gm,pm))
            c["new_gold_chars_candidate_covered"]+=sum(x and y and not z for x,y,z in zip(m,gm,pm))
            c["remaining_gold_chars_after_proposal_union"]+=sum(y and not x and not z for x,y,z in zip(m,gm,pm))
        for g in misses:
            label,s,e=key(g)
            cases.append(dict(doc_index=idx,label=label,start=s,end=e,chars=e-s,
                              coverage={m:sum(mm[s:e]) for m,mm in masks.items()}))
    require(len(cases)==33,"MISS_COUNT_MISMATCH")
    for mode,expected in (("o_pii_10",(130,28,24,20,214,86)),("o_pii_25",(89,22,16,13,89,66))):
        require(tuple(totals[mode][k] for k in ("review_docs","leaky_docs_reviewed","miss_overlap",
            "miss_full","candidate_chars_outside_gold","new_gold_chars_candidate_covered"))==expected,
            "CASE_AUDIT_PARITY_MISMATCH_"+mode)
    for mode in modes[2:]:
        require(totals[mode]["review_docs"]==totals["o_pii_25"]["review_docs"],
                "EXPANSION_CHANGED_REVIEW_DOCUMENTS")
    report=dict(note="Shadow offset coverage, not actual masking or release. No typed span changes.",
                scenarios={m:dict(c) for m,c in totals.items()},cases=cases)
    out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
    for mode,c in totals.items():
        print("RESULT",mode,json.dumps(c,sort_keys=True),flush=True)
    print("BOUNDARY_CASES",json.dumps(cases,ensure_ascii=False,separators=(",",":")),flush=True)
    print("BOUNDARY_ABLATION_COMPLETE",out,flush=True)

if __name__=="__main__":
    main()
