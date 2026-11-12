#!/usr/bin/env python3
"""Evaluate actual local retrieval and optionally live HCX for *new* Korean queries.

- Read-only against the configured live pgvector DB.
- No original question or document text is written to stdout/reports.
- Gold doc IDs are optional; unlabeled cases cannot be scored for accuracy.
- Live HCX costs money and requires explicit --hcx-live AND service opt-in.
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'step2_privacy_rag' / 'app'))


def load_cases(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError('MVP_CASES_FILE_NOT_FOUND')
    cases = []
    ids = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        case_id = obj.get('case_id')
        question = obj.get('question')
        refs = obj.get('relevant_doc_ids')
        if (not isinstance(case_id, str) or not case_id
                or not case_id.replace('_', '').replace('-', '').isalnum()
                or case_id in ids or len(case_id) > 60
                or not isinstance(question, str)
                or not 0 < len(question.strip()) <= 1000
                or (refs is not None and
                    (not isinstance(refs, list)
                     or not refs
                     or any(not isinstance(x, str) or not x.startswith('H') for x in refs)))):
            raise ValueError('MVP_CASE_SCHEMA_INVALID')
        ids.add(case_id)
        cases.append(obj)
    if not cases:
        raise ValueError('MVP_CASES_EMPTY')
    return cases


def metric_report(rows: list[dict]) -> dict:
    n = len(rows)
    labeled = [r for r in rows if r['labeled']]
    states = Counter(r['gate_state'] for r in rows)
    decisions = Counter(r['decision'] for r in rows if r['decision'])
    lat = sorted(r['total_ms'] for r in rows)
    def pct(p):
        index = (len(lat)-1) * p
        left = int(index)
        frac = index-left
        return round(lat[left] + (lat[min(left+1,len(lat)-1)]-lat[left]) * frac, 2)
    return {
        'total_cases': n, 'labeled_cases': len(labeled),
        'gate_states': dict(states),
        'hcx_decisions': dict(decisions),
        'hit1_labeled': sum(r['hit1'] for r in labeled),
        'hit5_labeled': sum(r['hit5'] for r in labeled),
        'hit1_rate_labeled': round(sum(r['hit1'] for r in labeled)/len(labeled),4) if labeled else None,
        'hit5_rate_labeled': round(sum(r['hit5'] for r in labeled)/len(labeled),4) if labeled else None,
        'answer_with_expected_citation': sum(r.get('cited_expected') is True for r in labeled),
        'latency_ms': {'mean': round(statistics.mean(lat),2), 'p50':pct(.5), 'p95':pct(.95)},
        'note': 'Citations matching a labeled document do not establish factual answer correctness. Unlabeled questions are not scored.'
    }


def evaluate(cases: list[dict], *, device='cpu', hcx_live=False) -> dict:
    from core import LivePipeline
    from app import respond
    pipeline = LivePipeline(ROOT, device=device, reuse_step1=True)
    try:
        status = pipeline.index_status()
        if status['status'] != 'READY':
            raise ValueError('MVP_INDEX_NOT_READY')
        started = time.perf_counter()
        pipeline.preload_models()
        print('MVP_MODEL_PRELOAD_MS', round((time.perf_counter()-started)*1000,2), flush=True)
        rows=[]
        for case in cases:
            start = time.perf_counter()
            if hcx_live:
                result = respond(pipeline, {'question':case['question']},allow_hcx=True)
                state = result.get('diagnostics', {}).get('gate_state', '')
                candidates = result.get('diagnostics', {}).get('retrieval_candidates', [])
                ranked = [x['doc_id'] for x in candidates]
                decision = result.get('decision')
                cited = result.get('citations', [])
            else:
                state, prepared, evidence = pipeline.ask_local(case['question'])
                ranked = [x['doc_id'] for x in evidence]
                decision = None
                cited = []
            refs = set(case.get('relevant_doc_ids') or [])
            scored = bool(refs)
            row = {
                'case_id':case['case_id'],
                'category':case.get('category','unspecified'),
                'gate_state':state,
                'decision':decision,
                'top5_doc_ids':ranked[:5],
                'citations':cited,
                'labeled':scored,
                'hit1':int(bool(refs and ranked[:1] and ranked[0] in refs)),
                'hit5':int(bool(refs.intersection(ranked[:5]))),
                'cited_expected':bool(refs.intersection(cited)) if (scored and hcx_live) else None,
                'total_ms':round((time.perf_counter()-start)*1000,2),
            }
            rows.append(row)
            print('MVP_CASE',json.dumps(row,ensure_ascii=False,sort_keys=True),flush=True)
        summary=metric_report(rows)
        print('MVP_EVALUATION_SUMMARY',json.dumps(summary,ensure_ascii=False,sort_keys=True),flush=True)
        return summary
    finally:
        pipeline.close()


# The demo includes only generic, publicly discussable synthetic queries.
# This generator writes to git-ignored local_eval; no question JSONL is committed.
_EXAMPLES = [
    ('FAMILY_01','document_topic','경조사 관련 공지 알려줘',['H0025']),
    ('FAMILY_02','document_topic','부고 관련 공지를 찾아줘',['H0025']),
    ('FAMILY_03','document_topic','장례식 안내 공지가 있나?',['H0025']),
    ('FAMILY_04','document_topic','직원 가족상 관련 문서를 알려줘',['H0025']),
    ('GENERAL_01','unlabeled','복리후생 관련 공지 요약해줘',None),
    ('GENERAL_02','unlabeled','출장비 신청 방법과 제출 서류 알려줘',None),
    ('GENERAL_03','unlabeled','고객 상담 처리 완료 사례 알려줘',None),
    ('GENERAL_04','unlabeled','고객 앱 로그인 오류 관련 기록 찾아줘',None),
    ('GENERAL_05','unlabeled','사내 교육 일정 알려줘',None),
    ('GENERAL_06','unlabeled','휴가 신청 절차가 적힌 문서 찾아줘',None),
    ('GENERAL_07','unlabeled','최근 안내한 주요 공지사항 알려줘',None),
    ('GENERAL_08','unlabeled','고객 정보 정정 관련 문서 찾아줘',None),
    ('GENERAL_09','unlabeled','주소 변경 요청이 처리된 사례를 알려줘',None),
    ('GENERAL_10','unlabeled','서류 반려 사유가 기록된 사례를 알려줘',None),
    ('GENERAL_11','unlabeled','인사팀에서 안내한 소식을 정리해줘',None),
]


def write_example_cases(path: Path) -> None:
    parent = (ROOT / 'step2_privacy_rag' / 'local_eval').resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(parent) or resolved.suffix != '.jsonl':
        raise ValueError('MVP_EXAMPLES_MUST_BE_LOCAL_EVAL_JSONL')
    if resolved.exists():
        raise ValueError('MVP_EXAMPLES_ALREADY_EXIST_NO_OVERWRITE')
    resolved.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    data = []
    for key,group,question,refs in _EXAMPLES:
        item = {'case_id':key,'category':group,'question':question}
        if refs:
            item['relevant_doc_ids'] = refs
        data.append(json.dumps(item,ensure_ascii=False))
    # Open with exclusive creation and owner-only permissions.
    fd = os.open(resolved,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as handle:
        handle.write('\\n'.join(data)+'\\n')
    print('MVP_EXAMPLES_WRITTEN',str(resolved),'cases',len(data),flush=True)



def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',type=Path,default=ROOT/'step2_privacy_rag/local_eval/mvp_natural_questions.jsonl')
    p.add_argument('--write-example', action='store_true',
                   help='create git-ignored synthetic question examples under local_eval; no model calls')
    p.add_argument('--device',choices=['cpu','mps'],default='cpu')
    p.add_argument('--hcx-live',action='store_true',help='Call paid HCX API after local masking and outbound checks')
    args=p.parse_args(argv)
    if args.write_example:
        try:
            write_example_cases(args.cases)
        except Exception as exc:
            safe = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('MVP_') else type(exc).__name__
            print('MVP_EXAMPLE_WRITE_FAILED',safe,file=sys.stderr)
            return 2
        return 0
    if args.hcx_live and os.environ.get('AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED') != 'YES':
        p.error('live HCX requires AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES')
    try:
        return 0 if evaluate(load_cases(args.cases),device=args.device,hcx_live=args.hcx_live) else 2
    except Exception as exc:
        # Exceptions might contain original query snippets; only expose type and safe code.
        safe = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('MVP_') else type(exc).__name__
        print('MVP_EVAL_FAILED',safe,file=sys.stderr,flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
