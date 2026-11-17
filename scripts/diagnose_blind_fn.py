#!/usr/bin/env python3
"""Triages strict-FN PII spans against Step1 predictions/masks without printing text.

Use only with the frozen Blind JSONL and private baseline per-doc report.
Evaluation Gold is NEVER passed to detectors, Gate, or a live index.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'step2_privacy_rag/app'))
sys.path.insert(0,str(ROOT/'step1_pii_detector/src'))

def overlap(a:tuple,b:tuple)->bool:
    return a[0]<b[1] and b[0]<a[1]

def classify_fn(gold:tuple[str,int,int],pred:set[tuple[str,int,int]],
                masks:list[dict])->dict:
    label,a,b=gold
    assert b>a
    if any(lab==label and overlap((a,b),(s,t)) for lab,s,t in pred):
        kind='same_label_boundary_mismatch'
    elif any(overlap((a,b),(s,t)) for lab,s,t in pred):
        kind='other_label_overlap'
    else:
        kind='no_entity_overlap'
    positions=set()
    for m in masks:
        left,right=m.get('start'),m.get('end')
        if type(left) is int and type(right) is int:
            positions.update(range(max(a,left),min(b,right)))
    covered=len(positions)
    return {'kind':kind,'mask_state':('fully_masked' if covered==b-a else
            'unmasked' if covered==0 else 'partially_masked'),
            'uncovered_chars':b-a-covered}

def select_fn_cases(rows:list[dict],report:dict)->list[tuple[int,dict,dict]]:
    per=report.get('per_doc')
    if (report.get('dataset_documents')!=len(rows)
        or not isinstance(per,list) or len(per)!=len(rows)):
        raise ValueError('BLIND_FN_REPORT_MISMATCH')
    out=[]
    seen=set()
    for record in per:
        i=record.get('case')
        if type(i) is not int or not (1<=i<=len(rows)) or i in seen:
            raise ValueError('BLIND_FN_REPORT_MISMATCH')
        seen.add(i)
        src=rows[i-1]
        hashed=hashlib.sha256(str(src['id']).encode('utf-8')).hexdigest()[:8]
        if (record.get('doc_id_hash8')!=hashed
            or record.get('gold_entities')!=len(src['entities'])):
            raise ValueError('BLIND_FN_INPUT_CHANGED')
        if record.get('strict_fn',0)>0:
            out.append((i,src,record))
    if not out:
        raise ValueError('BLIND_FN_NOT_FOUND')
    return out

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        from eval_blind_pii import read_rows,set_spans
        from pii_detector import create_detector
        rows=read_rows(args.input)
        baseline=json.loads(args.report.read_text(encoding='utf-8'))
        selected=select_fn_cases(rows,baseline)
        counts=Counter()
        state_counts=Counter()
        total=0
        print('BLIND_FN_TRIAGE_START',json.dumps({'documents_with_fn':len(selected),
                    'expected_fn':sum(x['strict_fn'] for _,_,x in selected)}),flush=True)
        with create_detector() as detector:
            for i,row,previous in selected:
                result=detector.detect(row['text'])
                gold=set_spans(row['entities'])
                pred=set_spans(result.get('entities',[]))
                fn=gold-pred
                local=Counter()
                uncovered=0
                for ent in sorted(fn):
                    detail=classify_fn(ent,pred,result.get('mask_intervals',[]))
                    local[detail['kind']]+=1
                    local[detail['mask_state']]+=1
                    local['fn_'+ent[0]]+=1
                    counts[detail['kind']]+=1
                    counts[detail['mask_state']]+=1
                    uncovered+=detail['uncovered_chars']
                prior_state=previous['intake_state']
                state_counts[prior_state]+=len(fn)
                total+=len(fn)
                print('BLIND_FN_CASE',json.dumps({'case':i,'baseline_intake_state':prior_state,
                    'strict_fn':len(fn),'baseline_fn':previous['strict_fn'],
                    'uncovered_gold_chars':uncovered,
                    'classes':dict(local),
                    'baseline_step1_uncovered_chars':previous['step1_gold_chars_uncovered']},
                    sort_keys=True),flush=True)
                if len(fn)!=previous['strict_fn']:
                    print('BLIND_FN_MODEL_DIFFERENCE',json.dumps({'case':i,
                          'expected':previous['strict_fn'],'observed':len(fn)}),flush=True)
        print('BLIND_FN_TRIAGE_SUMMARY',json.dumps({
            'strict_fn_observed':total,'class_counts':dict(counts),
            'fn_by_baseline_intake_state':dict(state_counts),
            'note':'Gold is used only after frozen model prediction for local evaluation. Boundary/label mismatch is not automatically a privacy leak. Mask coverage is not proof of complete de-identification.'
        },sort_keys=True),flush=True)
        return 0
    except Exception as exc:
        safe=str(exc) if isinstance(exc,ValueError) and str(exc).startswith('BLIND_FN_') else type(exc).__name__
        print('BLIND_FN_TRIAGE_FAILED',safe,file=sys.stderr,flush=True)
        return 2
if __name__=='__main__':
    raise SystemExit(main())
