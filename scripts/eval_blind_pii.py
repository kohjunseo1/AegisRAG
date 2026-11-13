#!/usr/bin/env python3
"""Blind PII evaluation on an independent labeled JSONL or one-member ZIP.

Read-only local benchmark. Neither original text, spans, HMAC tokens nor
credentials are printed or uploaded. Gold spans are used for evaluation only,
never as a detector/model/Privacy Gate input. This does NOT write to DB.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import statistics
import time
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'step2_privacy_rag/app'))
sys.path.insert(0,str(ROOT/'step1_pii_detector/src'))

LABELS=frozenset({
    'person','phone_number','email','address','resident_registration_number',
    'account_number','card_number'
})
MAX_BYTES=30_000_000


def read_rows(path:Path) -> list[dict]:
    if not path.is_file():
        raise ValueError('BLIND_INPUT_MISSING')
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as z:
            entries=[x for x in z.infolist()
                     if not x.is_dir() and not x.filename.startswith('__MACOSX/')
                     and x.filename.endswith('.jsonl')]
            if len(entries)!=1 or entries[0].file_size>MAX_BYTES:
                raise ValueError('BLIND_ZIP_INVALID')
            raw=z.read(entries[0],pwd=None)
    elif path.suffix.lower()=='.jsonl' and path.stat().st_size<=MAX_BYTES:
        raw=path.read_bytes()
    else:
        raise ValueError('BLIND_JSONL_OR_ZIP_REQUIRED')
    if len(raw)>MAX_BYTES:
        raise ValueError('BLIND_INPUT_TOO_LARGE')
    try:
        rows=[json.loads(line) for line in raw.decode('utf-8-sig').splitlines()
              if line.strip()]
    except (UnicodeError,json.JSONDecodeError):
        raise ValueError('BLIND_JSONL_INVALID') from None
    if not rows or len(rows)>5000:
        raise ValueError('BLIND_ROW_COUNT_INVALID')
    ids=set()
    for r in rows:
        if not isinstance(r,dict) or not isinstance(r.get('text'),str) or not isinstance(r.get('entities'),list):
            raise ValueError('BLIND_SCHEMA_INVALID')
        source_id=r.get('id')
        if not isinstance(source_id,(str,int)) or str(source_id) in ids:
            raise ValueError('BLIND_DOC_ID_INVALID')
        ids.add(str(source_id))
        n=len(r['text'])
        if n>20000:
            raise ValueError('BLIND_DOCUMENT_TOO_LARGE')
        spans=[]
        for ent in r['entities']:
            if not isinstance(ent,dict):
                raise ValueError('BLIND_GOLD_INVALID')
            a,b,label=ent.get('start'),ent.get('end'),ent.get('label')
            if (type(a) is not int or type(b) is not int or not 0<=a<b<=n
                or label not in LABELS or r['text'][a:b]!=ent.get('text')):
                raise ValueError('BLIND_GOLD_OFFSET_MISMATCH')
            spans.append((a,b,label))
        spans.sort()
        if any(a2<b1 for (_,b1,_),(a2,_,_) in zip(spans,spans[1:])):
            raise ValueError('BLIND_GOLD_OVERLAP')
    return rows


def set_spans(items:list[dict]) -> set[tuple[str,int,int]]:
    if not isinstance(items,list):
        return set()
    return {(e['label'],int(e['start']),int(e['end']))
            for e in items if isinstance(e,dict)
            and e.get('label') in LABELS and type(e.get('start')) is int
            and type(e.get('end')) is int}


def mask_coverage(gold:list[dict], masks:list[dict], length:int) -> tuple[int,int]:
    """Gold character recall of Step1 masks (not a final-output guarantee)."""
    covered=bytearray(length)
    for m in masks or []:
        a,b=m.get('start'),m.get('end')
        if type(a) is int and type(b) is int and 0<=a<b<=length:
            covered[a:b]=b'\x01'*(b-a)
    total=hit=0
    for ent in gold:
        a,b=ent['start'],ent['end']
        total+=b-a
        hit+=sum(covered[a:b])
    return hit,total


def latency_summary(samples:list[float])->dict:
    if not samples:
        return {'n':0,'mean_ms':None,'p50_ms':None,'p95_ms':None}
    vals=sorted(float(x) for x in samples)
    def pct(q):
        i=(len(vals)-1)*q
        lo=int(i)
        return vals[lo]+(vals[min(lo+1,len(vals)-1)]-vals[lo])*(i-lo)
    return {'n':len(vals),'mean_ms':round(statistics.mean(vals),2),
            'p50_ms':round(pct(.50),2),'p95_ms':round(pct(.95),2)}


def counts_to_metrics(c:Counter)->dict:
    tp,fp,fn=c['tp'],c['fp'],c['fn']
    p=tp/(tp+fp) if tp+fp else (1.0 if not fn else 0.0)
    r=tp/(tp+fn) if tp+fn else 1.0
    f=2*p*r/(p+r) if p+r else 0.0
    return {'tp':tp,'fp':fp,'fn':fn,
            'precision':round(p,6),'recall':round(r,6),'strict_f1':round(f,6)}


def evaluate(rows:list[dict], env_file:Path)->dict:
    from dotenv import load_dotenv
    load_dotenv(env_file)
    from dataset_preparation import fixed_key
    from pii_detector import create_detector
    from document_preparation import prepare
    from name_protection_handoff import _FrozenDetectorView
    from hcx import redact_residual_pii
    key=fixed_key(env_file)
    strict=Counter()
    by_label={label:Counter() for label in sorted(LABELS)}
    doc_status=Counter()
    prepare_states=Counter()
    prepare_reasons=Counter()
    intake_states=Counter()
    projected_review_reasons=Counter()
    gold_chars=masked_gold_chars=0
    possible_literal_residuals=0
    ready_docs_with_suspected_residual=0
    ready_docs_with_step1_gap=0
    per_doc=[]
    stage1_ms=[]
    downstream_ms=[]
    privacy_total_ms=[]
    cold_started=time.perf_counter()
    detector=create_detector()
    init_ms=round((time.perf_counter()-cold_started)*1000,2)
    with detector:
        for i,row in enumerate(rows,1):
            doc_started=time.perf_counter()
            gold=set_spans(row['entities'])
            step1_started=time.perf_counter()
            result=detector.detect(row['text'])
            step1_elapsed=(time.perf_counter()-step1_started)*1000
            pred=set_spans(result.get('entities',[]))
            tp=pred&gold
            fp=pred-gold
            fn=gold-pred
            for kind,spans in [('tp',tp),('fp',fp),('fn',fn)]:
                strict[kind]+=len(spans)
                for label,_,_ in spans:
                    by_label[label][kind]+=1
            doc_status[str(result.get('status','UNKNOWN'))]+=1
            hit,total=mask_coverage(row['entities'],result.get('mask_intervals',[]),len(row['text']))
            masked_gold_chars+=hit
            gold_chars+=total
            # Freeze original Step1 output; Gate can call independent A.X/Veil
            # but must not re-infer another Step1 result.
            next_started=time.perf_counter()
            outcome=prepare('EVAL_%05d'%i,row['text'],_FrozenDetectorView(detector,result),key)
            downstream_elapsed=(time.perf_counter()-next_started)*1000
            stage1_ms.append(step1_elapsed)
            downstream_ms.append(downstream_elapsed)
            privacy_total_ms.append((time.perf_counter()-doc_started)*1000)
            prepare_states[outcome.state]+=1
            prepare_reasons[str(outcome.reason)]+=1
            # Mirror the existing dataset_workflow._process intake gate.
            auto_ready=(outcome.state=='LOCAL_PREPARED'
                        and outcome.unlinked_mentions==0
                        and isinstance(outcome.sanitized_text,str)
                        and bool(outcome.sanitized_text.strip()))
            if auto_ready:
                _,regex_residual=redact_residual_pii(outcome.sanitized_text)
                auto_ready=not any(regex_residual.values())
            if auto_ready:
                intake_state='READY'
            elif outcome.state=='BLOCKED':
                intake_state='BLOCKED'
            else:
                intake_state='REVIEW_PENDING'
            intake_states[intake_state]+=1
            if intake_state!='READY':
                if outcome.state=='BLOCKED':
                    projected_review_reasons['BLOCKED_'+outcome.reason]+=1
                elif outcome.state=='REVIEW':
                    projected_review_reasons['REVIEW_'+outcome.reason]+=1
                elif outcome.unlinked_mentions:
                    projected_review_reasons['UNLINKED_MENTIONS_REVIEW']+=1
                elif outcome.state=='LOCAL_PREPARED':
                    projected_review_reasons['RESIDUAL_PII_OR_EMPTY_REVIEW']+=1
                else:
                    projected_review_reasons['OTHER']+=1
            residual=0
            if auto_ready:
                # Conservative lexical signal only: a short given name can
                # coincidentally occur elsewhere. Not proof of PII leakage.
                masked=outcome.sanitized_text or ''
                residual=sum(1 for e in row['entities'] if e['text'] in masked)
                possible_literal_residuals+=residual
                ready_docs_with_suspected_residual+=int(residual>0)
                ready_docs_with_step1_gap+=int(hit<total)
            per_doc.append({'case':i,'doc_id_hash8':__import__('hashlib').sha256(
                             str(row['id']).encode()).hexdigest()[:8],
                            'gold_entities':len(gold),'strict_fn':len(fn),
                            'step1_gold_chars_uncovered':total-hit,
                            'prepare_state':outcome.state,
                            'prepare_reason':str(outcome.reason),
                            'intake_state':intake_state,
                            'strict_fn_labels':dict(Counter(e[0] for e in fn)),
                            'strict_fp_labels':dict(Counter(e[0] for e in fp)),
                            'suspected_unmasked_gold_literals':residual,
                            'timings_ms':{'step1_detector':round(step1_elapsed,2),
                                          'gate_and_local_pseudonymization':round(downstream_elapsed,2),
                                          'privacy_total':round(privacy_total_ms[-1],2)}})
            if i==len(rows) or i%25==0:
                print('BLIND_PROGRESS',json.dumps({'completed':i,'total':len(rows)}),flush=True)
    return {
        'dataset_documents':len(rows),
        'gold_entities':sum(len(r['entities']) for r in rows),
        'zero_gold_docs':sum(not r['entities'] for r in rows),
        'step1_statuses':dict(doc_status),
        'prepare_states':dict(prepare_states),
        'prepare_reasons':dict(prepare_reasons),
        'projected_intake_states':dict(intake_states),
        'projected_nonready_reasons':dict(projected_review_reasons),
        'timing_ms':{
            'cold_detector_model_load':init_ms,
            'first_document_step1':round(stage1_ms[0],2),
            'first_document_privacy_total':round(privacy_total_ms[0],2),
            'warm_documents_excluding_first':len(rows)-1,
            'warm_step1_detector':latency_summary(stage1_ms[1:]),
            'warm_gate_and_pseudonymization':latency_summary(downstream_ms[1:]),
            'warm_privacy_total':latency_summary(privacy_total_ms[1:]),
            'all_documents_privacy_processing_total_ms':round(sum(privacy_total_ms),2),
            'scope':'Raw document privacy pass only; excludes doc_type prefix, staging file IO, document embeddings and DB writes.'
        },
        'strict':counts_to_metrics(strict),
        'strict_by_label':{k:counts_to_metrics(c) for k,c in by_label.items()},
        'step1_mask_gold_character_recall':round(masked_gold_chars/gold_chars,6) if gold_chars else None,
        'step1_gold_characters_uncovered':gold_chars-masked_gold_chars,
        'ready_docs_with_step1_mask_gap':ready_docs_with_step1_gap,
        'ready_docs_with_suspected_literal_residual':ready_docs_with_suspected_residual,
        'suspected_literal_residual_count':possible_literal_residuals,
        'warning':'READY is projected using the same local intake conditions. Gold raw-literal residuals are conservative text matches and can yield false positives, especially short names. A Step1 mask gap may be repaired by downstream Gate/V5; review final sanitized evidence locally. Do not ingest any suspect READY document without review. This report is NOT zero-PII-leak certification.',
        'per_doc':per_doc,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path)
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    p.add_argument('--report',type=Path,help='optional private JSON output under step2_privacy_rag/local_eval')
    args=p.parse_args(argv)
    try:
        rows=read_rows(args.input)
        print('BLIND_SCHEMA_OK',json.dumps(
            {'documents':len(rows),'gold_entities':sum(len(r['entities']) for r in rows),
             'zero_gold_docs':sum(not r['entities'] for r in rows)},sort_keys=True),flush=True)
        report=evaluate(rows,args.env)
        summary={k:v for k,v in report.items() if k!='per_doc'}
        print('BLIND_PII_METRICS',json.dumps(summary,sort_keys=True,ensure_ascii=False),flush=True)
        if args.report:
            from dataset_preparation import private_write
            base=(ROOT/'step2_privacy_rag/local_eval').resolve()
            target=args.report.expanduser().resolve()
            if not target.is_relative_to(base) or target.suffix!='.json' or target.exists():
                raise ValueError('BLIND_REPORT_PATH_MUST_BE_NEW_LOCAL_JSON')
            private_write(target,report)
            print('BLIND_PRIVATE_REPORT_WRITTEN',str(target),flush=True)
        return 0
    except Exception as exc:
        safe=str(exc) if isinstance(exc,ValueError) and str(exc).startswith('BLIND_') else type(exc).__name__
        print('BLIND_PII_EVAL_FAILED',safe,file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
