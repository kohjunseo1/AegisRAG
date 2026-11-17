#!/usr/bin/env python3
"""Compare paired private HCX E2E evaluations without logging question/answer/PII.

This is a diagnostic report, not factual accuracy or an independent blind score.
The source reports remain in private local_eval; only case IDs/summary are printed.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=(ROOT/"step2_privacy_rag/local_eval").resolve()


def read_report(path: Path) -> dict[str,dict]:
    resolved=path.expanduser().resolve()
    if not resolved.is_relative_to(PRIVATE) or resolved.suffix!=".json":
        raise ValueError("AB_REPORT_NOT_IN_PRIVATE_LOCAL_EVAL")
    raw=json.loads(resolved.read_text(encoding="utf-8"))
    cases=raw.get("cases")
    if not isinstance(cases,list) or not cases:
        raise ValueError("AB_REPORT_EMPTY")
    m={}
    for c in cases:
        if not isinstance(c,dict) or not isinstance(c.get("case_id"),str):
            raise ValueError("AB_INVALID_CASE")
        cid=c["case_id"]
        if cid in m:
            raise ValueError("AB_DUPLICATE_ID")
        obs=c.get("observed")
        if not isinstance(obs,dict) or "gate_state" not in obs:
            raise ValueError("AB_MISSING_OBSERVED")
        m[cid]=obs
    return m


def compare(reference:dict[str,dict],experiment:dict[str,dict])->dict:
    if set(reference)!=set(experiment):
        raise ValueError("AB_CASES_DIFFER")
    ids=sorted(reference)
    shifts=[]
    counts=Counter()
    for cid in ids:
        a,b=reference[cid],experiment[cid]
        if a.get("gate_state")!=b.get("gate_state"):
            counts["gate_shift"]+=1
        if a.get("top5_doc_ids")!=b.get("top5_doc_ids"):
            counts["retrieval_shift"]+=1
        x,y=a.get("decision"),b.get("decision")
        if x!=y:
            counts["decision_shift"]+=1
        if x=="ANSWER" and y!="ANSWER":
            counts["answer_lost"]+=1
        if x!="ANSWER" and y=="ANSWER":
            counts["answer_gained"]+=1
        if a.get("citations")!=b.get("citations"):
            counts["citations_changed"]+=1
        # A Gold document citation does not imply factual correctness.
        if a.get("cited_expected") is True and b.get("cited_expected") is False:
            counts["reference_citation_lost"]+=1
        if a.get("cited_expected") is False and b.get("cited_expected") is True:
            counts["reference_citation_gained"]+=1
        if b.get("non_gold_citations",0)>a.get("non_gold_citations",0):
            counts["extra_non_gold_citations"]+=1
        if (x!=y or a.get("citations")!=b.get("citations")
                or a.get("gate_state")!=b.get("gate_state")
                or a.get("top5_doc_ids")!=b.get("top5_doc_ids")):
            shifts.append({
                "case_id":cid,"gate":[a.get("gate_state"),b.get("gate_state")],
                "decision":[x,y],"citations":[a.get("citations"),b.get("citations")],
                "ref_cited":[a.get("cited_expected"),b.get("cited_expected")],
                "top5_changed":a.get("top5_doc_ids")!=b.get("top5_doc_ids"),
            })
    def avg(xs,key):
        z=[float(x[key]) for x in xs if isinstance(x.get(key),(int,float))]
        return round(statistics.mean(z),2) if z else None
    ref_hcx=[v.get("phase_timings_ms",{}).get("hcx_ms") for v in reference.values()]
    var_hcx=[v.get("phase_timings_ms",{}).get("hcx_ms") for v in experiment.values()]
    ref_avg=avg([{"value":n} for n in ref_hcx if n is not None],"value")
    var_avg=avg([{"value":n} for n in var_hcx if n is not None],"value")
    return {
        "cases":len(ids),"changes":dict(counts),
        "reference_mean_hcx_ms":ref_avg,"experiment_mean_hcx_ms":var_avg,
        "hcx_delta_ms":round(var_avg-ref_avg,2) if ref_avg is not None and var_avg is not None else None,
        "cases_for_manual_evidence_review":shifts,
        "notes":[
            "The same retrieval candidates are expected: retrieval_shift must remain 0.",
            "Gold may omit genuinely relevant documents. Cite IDs are diagnostics, not factual validity.",
            "Even unchanged citations may contain different or unsupported claims; compare answers manually."
        ]
    }


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",type=Path,required=True)
    p.add_argument("--variant",type=Path,required=True)
    args=p.parse_args()
    try:
        print("HCX_AB_COMPARE",json.dumps(
            compare(read_report(args.baseline),read_report(args.variant)),
            ensure_ascii=False,sort_keys=True),flush=True)
    except Exception as exc:
        safe=str(exc) if isinstance(exc,ValueError) and str(exc).startswith("AB_") else type(exc).__name__
        print("HCX_AB_COMPARE_FAILED",safe,file=sys.stderr,flush=True)
        raise SystemExit(2)


if __name__=="__main__":
    main()
