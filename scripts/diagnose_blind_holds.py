#!/usr/bin/env python3
"""Rerun only held Blind corpus cases for local PII-safe Gate/exception diagnostics.

No raw PII, detected spans, original IDs, tokens or exception messages are printed.
Never writes to PostgreSQL, calls an LLM, or changes approval outcomes.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'step2_privacy_rag/app'))
sys.path.insert(0,str(ROOT/'step1_pii_detector/src'))

def select_nonready(rows:list[dict], report:dict)->list[tuple[int,dict,dict]]:
    items=report.get('per_doc')
    if (report.get('dataset_documents')!=len(rows)
        or not isinstance(items,list) or len(items)!=len(rows)):
        raise ValueError('BLIND_DIAG_REPORT_MISMATCH')
    seen=set()
    selected=[]
    for entry in items:
        i=entry.get('case')
        if type(i) is not int or not 1<=i<=len(rows) or i in seen:
            raise ValueError('BLIND_DIAG_REPORT_MISMATCH')
        seen.add(i)
        source_hash=hashlib.sha256(str(rows[i-1]['id']).encode('utf-8')).hexdigest()[:8]
        if entry.get('doc_id_hash8')!=source_hash:
            raise ValueError('BLIND_DIAG_INPUT_CHANGED')
        if entry.get('intake_state')!='READY':
            selected.append((i,rows[i-1],entry))
    if not selected:
        raise ValueError('BLIND_DIAG_NO_HELD_DOCS')
    return selected


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path,help='171 clean corpus ZIP')
    p.add_argument('--report',required=True,type=Path,help='private per-doc baseline JSON')
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    p.add_argument('--limit',type=int,default=0,help='optional smoke test of first N held cases')
    p.add_argument('--only-blocked',action='store_true',
                   help='replay only prior BLOCKED docs, not the REVIEW queue')
    a=p.parse_args(argv)
    try:
        from eval_blind_pii import read_rows
        from dataset_preparation import fixed_key
        from document_preparation import prepare
        from name_protection_handoff import _FrozenDetectorView
        from hcx import redact_residual_pii
        from dotenv import load_dotenv
        from pii_detector import create_detector
        load_dotenv(a.env)
        records=read_rows(a.input)
        summary=json.loads(a.report.read_text(encoding='utf-8'))
        selected=select_nonready(records,summary)
        if a.only_blocked:
            selected=[x for x in selected if x[2].get('intake_state')=='BLOCKED']
            if not selected:
                raise ValueError('BLIND_DIAG_NO_BLOCKED_DOCS')
        if a.limit<0:raise ValueError('BLIND_DIAG_LIMIT_INVALID')
        if a.limit:selected=selected[:a.limit]
        key=fixed_key(a.env)
        # Activate exactly bounded, opt-in stage/error-type logs in prepare().
        os.environ['AEGISRAG_LOCAL_FAILURE_DIAG']='1'
        counts=Counter()
        changed=0
        print('BLIND_HELD_DIAG_START',json.dumps({'cases':len(selected),'baseline_nonready':sum(x.get('intake_state')!='READY' for x in summary['per_doc'])}),flush=True)
        with create_detector() as detector:
            for i,row,previous in selected:
                step1=detector.detect(row['text'])
                result=prepare('EVAL_%05d'%i,row['text'],_FrozenDetectorView(detector,step1),key)
                reason=result.reason
                if result.state=='LOCAL_PREPARED':
                    if result.unlinked_mentions:
                        reason='UNLINKED_NAME_MENTIONS'
                    elif not result.sanitized_text or not result.sanitized_text.strip():
                        reason='EMPTY_MASKED_DOCUMENT'
                    else:
                        _,residues=redact_residual_pii(result.sanitized_text)
                        reason='RESIDUAL_REGEX_PATTERN' if any(residues.values()) else 'READY'
                counts[reason]+=1
                changed+=int(previous.get('prepare_state')!=result.state)
                print('BLIND_HELD_CASE',json.dumps({
                    'case':i,'step1_status':step1.get('status'),
                    'previous_prepare':previous.get('prepare_state'),
                    'current_prepare':result.state,
                    'current_reason':reason,'unlinked_name_mentions':result.unlinked_mentions
                },sort_keys=True),flush=True)
        print('BLIND_HELD_DIAG_SUMMARY',json.dumps({
            'replayed':len(selected),'prepare_state_changes':changed,
            'safe_reasons':dict(counts),
            'note':'Raw-corpus replay only; actual intake additionally prefixes doc_type, so stage states must be measured separately. Never override BLOCKED to READY by state toggle.'
        },ensure_ascii=False,sort_keys=True),flush=True)
        return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and str(exc).startswith('BLIND_DIAG_') else type(exc).__name__
        print('BLIND_HELD_DIAG_FAILED',code,file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
