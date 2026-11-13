#!/usr/bin/env python3
"""Read-only private stage comparison and FN case 152 Gate overlap audit.

Never print document text, PII spans, tokens, original IDs, masked text or secrets.
No writes, DB access or external API calls. Golden data used ONLY for evaluation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'step2_privacy_rag/app'))
sys.path.insert(0,str(ROOT/'step1_pii_detector/src'))

def covers(a:int,b:int,spans:list[dict])->bool:
    points=set()
    for ent in spans:
        x,y=ent.get('start'),ent.get('end')
        if type(x) is int and type(y) is int:
            points.update(range(max(a,x),min(b,y)))
    return len(points)==b-a

def overlaps(a:int,b:int,spans:list[dict])->bool:
    return any(type(p.get('start')) is int and type(p.get('end')) is int
               and a<p['end'] and p['start']<b for p in spans)

def compare_from_index(rows:list[dict],baseline:dict,stage:dict,key:bytes):
    from dataset_workflow import doc_id
    if stage.get('dataset')!='blind_v2':
        raise ValueError('BLIND_STAGE_NAMESPACE_MISMATCH')
    expected=baseline.get('per_doc')
    if baseline.get('dataset_documents')!=len(rows) or not isinstance(expected,list) or len(expected)!=len(rows):
        raise ValueError('BLIND_STAGE_BASELINE_MISMATCH')
    byid={doc['doc_id']:doc for doc in stage.get('documents',[])}
    if len(byid)!=len(rows):
        raise ValueError('BLIND_STAGE_COUNT_MISMATCH')
    changes=[]
    for i,(src,previous) in enumerate(zip(rows,expected),1):
        if previous.get('case')!=i:
            raise ValueError('BLIND_STAGE_BASELINE_ORDER_MISMATCH')
        opaque=doc_id('blind_v2',src['id'],key)
        row=byid.get(opaque)
        if row is None or row.get('source_id')!=src['id']:
            raise ValueError('BLIND_STAGE_DOC_ID_MISMATCH')
        old,new=previous['intake_state'],row['state']
        if old!=new:
            changes.append({'case':i,'old':old,'new':new,
                            'new_reason':row.get('reason') if row.get('reason') in {
                                'GATE_NOT_READY','SAFE_PIPELINE_FAILURE',
                                'RESIDUAL_PII_REQUIRES_REVIEW','UNLINKED_MENTIONS_REVIEW',
                                'AUTO_GATE_READY','STEP1_NOT_READY'} else 'OTHER'})
    return changes

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    p.add_argument('--case',type=int,default=152)
    a=p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        from dataset_preparation import fixed_key
        from dataset_workflow import read_signed,doc_id
        from eval_blind_pii import read_rows,set_spans
        from pii_detector import create_detector
        from name_protection_handoff import _FrozenDetectorView
        from privacy_gate import check_privacy_gate
        load_dotenv(a.env)
        rows=read_rows(a.input)
        baseline=json.loads(a.baseline.read_text(encoding='utf-8'))
        key=fixed_key(a.env)
        stage=read_signed(a.stage,key)
        changes=compare_from_index(rows,baseline,stage,key)
        print('BLIND_STAGE_DIFF',json.dumps({'count':len(changes),'changes':changes},ensure_ascii=False),flush=True)
        if not 1<=a.case<=len(rows):
            raise ValueError('BLIND_CASE_OUT_OF_RANGE')
        src=rows[a.case-1]
        row=next(r for r in stage['documents'] if r['doc_id']==doc_id('blind_v2',src['id'],key))
        with create_detector() as detector:
            result=detector.detect(src['text'])
            gold=set_spans(src['entities'])
            fn=gold-set_spans(result.get('entities',[]))
            gate=check_privacy_gate(_FrozenDetectorView(detector,result),src['text'])
            veil=[x for x in detector.veil_predict(src['text']) if x.get('label')=='person']
            details=[]
            for label,start,end in sorted(fn):
                if label!='person':
                    continue
                protect=gate.get('proposed_protection',[]) or []
                details.append({'fn_label':'person',
                                'step1_fully_covered':covers(start,end,result.get('mask_intervals',[])),
                                'veil_person_overlap':overlaps(start,end,veil),
                                'veil_person_full_coverage':covers(start,end,veil),
                                'gate_proposed_mask_overlap':overlaps(start,end,protect),
                                'gate_proposed_mask_full_coverage':covers(start,end,protect)})
            print('BLIND_FN_GATE_CORRELATION',json.dumps({
                'case':a.case,'stage_state':row['state'],
                'stage_reason':row.get('reason') if row.get('reason') in {
                    'GATE_NOT_READY','SAFE_PIPELINE_FAILURE','UNLINKED_MENTIONS_REVIEW',
                    'RESIDUAL_PII_REQUIRES_REVIEW','AUTO_GATE_READY'} else 'OTHER',
                'step1_status':result.get('status'),'gate_reason':gate.get('reason'),
                'strict_fn_count':len(fn),'person_fn_checks':details,
                'interpretation':'Veil overlap with a missed Gold PERSON supports a Gate-detected concern; unrelated candidate overlap does not prove the particular missing PERSON was recognized.'
            },ensure_ascii=False,sort_keys=True),flush=True)
        return 0
    except Exception as exc:
        safe=str(exc) if isinstance(exc,ValueError) and str(exc).startswith('BLIND_') else type(exc).__name__
        print('BLIND_STAGE_AUDIT_FAILED',safe,file=sys.stderr,flush=True)
        return 2
if __name__=='__main__':
    raise SystemExit(main())
