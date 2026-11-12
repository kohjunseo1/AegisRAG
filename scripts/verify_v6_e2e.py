#!/usr/bin/env python3
"""Local-only AegisRAG verification.

Default: DB metadata probe (no edits/no LLM).
--synthetic-review: forced REVIEW and approved artifact contract, no DB edits.
--eval-artifact: retrieval metrics on a locally held private evaluation file;
                 never print questions or source text; never send to HCX.
--interactive: enter private queries locally; retrieval-only unless transport
               hcx/http is explicitly requested AND externally approved.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'step2_privacy_rag' / 'app'))
sys.path.insert(0, str(ROOT / 'step1_pii_detector' / 'src'))

from core import LivePipeline, PreparedQuery, PII
from metrics_retrieval import summarize_score, summarize_latency
from reproduction import artifact
from settings import db_config


def info(name: str, value) -> None:
    print(name, json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


def synthetic_review() -> None:
    """Explicitly simulated REVIEW, not a claim that the admin web UI works."""
    from document_preparation import prepare
    from smoke import SyntheticDetector, FIXTURE_KEY
    from dataset_preparation import key_tag
    from live_index import validate_approved_batch
    from unittest.mock import patch
    from tempfile import TemporaryDirectory
    text = '합성 시범 교육 신청 안내입니다.'
    detector = SyntheticDetector()
    detector.detect = lambda _t: {'status': 'REVIEW_REQUIRED'}
    review = prepare('SYNTH_APPROVAL_TEST', text, detector, FIXTURE_KEY)
    assert review.state == 'REVIEW' and review.sanitized_text is None
    ready = prepare('SYNTH_APPROVAL_TEST', text, SyntheticDetector(), FIXTURE_KEY)
    assert ready.state == 'LOCAL_PREPARED'
    # A mocked reviewer emits a synthetic approved batch. No real local admin.
    from pathlib import Path
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'synthetic_approval.json'
        obj = {'format': 'aegisrag-live-approved-v1',
               'hmac_key_tag': key_tag(FIXTURE_KEY),
               'documents': [{'doc_id': 'SYNTH_APPROVAL_TEST',
                              'text': ready.sanitized_text,
                              'region_keys': list(ready.region_keys),
                              'approval_state': 'HUMAN_APPROVED'}]}
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        with patch('live_index.fixed_key', return_value=FIXTURE_KEY):
            approved = validate_approved_batch(path, Path(directory) / '.env')
        assert len(approved) == 1
    info('SYNTHETIC_REVIEW', {
        'review_gate': 'REVIEW_REQUIRED', 'review_pending_inserted': False,
        'mock_reviewer_approval_contract': 'PASS',
        'db_written': False, 'admin_8771_tested': False})


def real_review_probes(pipeline):
    """Probe real Step1+Gate decisions for fabricated inputs; no DB writes."""
    cases = (
        '합성 직원 이슬이 부서 업무 보고서입니다.',
        '가상 담당자 김 과장님에게 상담 서류를 전달합니다.',
        '주소 후보는 서울특별시 강남구 가상로 123, 101동 1203호입니다.',
        '일반 안전교육 일정 공지입니다.',
    )
    results = []
    for number, content in enumerate(cases, 1):
        try:
            state = pipeline.prepare(content)
            outcome = 'LOCAL_PREPARED' if isinstance(state, PreparedQuery) else state
        except Exception as exc:
            from core import PipelineError
            outcome = str(exc) if isinstance(exc, PipelineError) else 'ERROR'
        results.append({'case': number, 'state': outcome})
    info('REAL_MODEL_REVIEW_PROBES', {
        'cases': results,
        'review_observed': any('REVIEW' in r['state'].upper() for r in results),
        'admin_8771_tested': False, 'db_written': False})


def index_ids(pipeline):
    from psycopg import connect, sql
    with connect(**pipeline.db) as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute(sql.SQL('SELECT doc_id FROM {} WHERE key_tag = %s').format(pipeline.table),
                        (pipeline.tag,))
            return {row[0] for row in cur.fetchall()}


def eval_artifact(pipeline, path: Path, max_queries: int):
    data = artifact(path, ROOT / '.env')
    ids = index_ids(pipeline)
    runs = {}
    latency = []
    gold_by_id = {g['qid']: g for g in data['gold_for_evaluation_only']}
    eligible = [q for q in data['queries']
                if q['text'] is not None and
                set(gold_by_id[q['qid']]['relevant_doc_ids']) & ids]
    subset = eligible[:max_queries] if max_queries else eligible
    for i, q in enumerate(subset, 1):
        start = time.perf_counter()
        rows = pipeline.retrieve(PreparedQuery(
            q['text'], set(PII.findall(q['text'])), set(q['region_keys'])), top_k=None)
        runs[q['qid']] = [r['doc_id'] for r in rows]
        latency.append({
            'total_ms': (time.perf_counter() - start) * 1000,
            'embedding_ms': pipeline.timings['embedding_ms'],
            'db_ms': pipeline.timings['db_ms'],
            'rerank_ms': pipeline.timings['rerank_ms']})
        # QIDs are metadata, not raw questions/documents.
        if i == len(subset) or i % 20 == 0:
            info('EVAL_PROGRESS', {'completed': i, 'eligible_selected': len(subset)})
    subset_qids = {q['qid'] for q in subset}
    report = summarize_score(
        [q for q in data['queries'] if q['qid'] in subset_qids],
        [g for g in data['gold_for_evaluation_only'] if g['qid'] in subset_qids],
        ids, runs)
    report['observed_eligible_subset'] = len(subset)
    report['input_total_queries'] = len(data['queries'])
    report['input_deferred'] = sum(q['text'] is None for q in data['queries'])
    report['input_no_indexable_gold'] = sum(
        q['text'] is not None and
        not (set(gold_by_id[q['qid']]['relevant_doc_ids']) & ids)
        for q in data['queries'])
    info('RETRIEVAL_METRICS', report)
    info('CPU_RETRIEVAL_LATENCY_MS', summarize_latency(latency))
    print('EVAL_NOTE: preprocessed queries exclude Step1 and HCX latency; use interactive mode for full local query timings.', flush=True)


def check_http(base: str, table: str):
    base = base.rstrip('/')
    if base not in {'http://127.0.0.1:8768', 'http://localhost:8768'}:
        raise ValueError('UNSUPPORTED_NONLOCAL_HTTP_HOST')
    with request.urlopen(base + '/api/health', timeout=8) as resp:
        health = json.load(resp)
    with request.urlopen(base + '/api/index-status', timeout=8) as resp:
        status = json.load(resp)
    info('HTTP_HEALTH', health)
    info('HTTP_INDEX', status)
    if status.get('table') != table or status.get('status') != 'READY':
        raise ValueError('HTTP_BACKEND_INDEX_MISMATCH')
    return base


def interactive(pipeline, transport: str, base: str):
    if transport != 'local' and os.environ.get('AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED') != 'YES':
        raise ValueError('EXTERNAL_TRANSFER_NOT_APPROVED')
    if transport == 'http':
        base = check_http(base, pipeline.table_name)
    print('INTERACTIVE: 입력한 질문은 저장하지 않음. 종료: 빈 줄 또는 exit', flush=True)
    while True:
        try:
            question = input('질문> ').strip()
        except EOFError:
            break
        if not question or question.lower() in {'exit','quit'}:
            break
        try:
            if transport == 'http':
                payload = json.dumps({'question': question}, ensure_ascii=False).encode('utf-8')
                req = request.Request(base + '/api/chat', data=payload, method='POST',
                                      headers={'Content-Type': 'application/json'})
                with request.urlopen(req, timeout=180) as resp:
                    result = json.load(resp)
                info('CHAT', {k: result.get(k) for k in ('decision','answer','citations','mode')})
                info('CHAT_DIAGNOSTICS', result.get('diagnostics', {}))
                continue
            start = time.perf_counter()
            state, prepared, docs = pipeline.ask_local(question)
            if prepared is None:
                info('LOCAL_QUERY', {'state': state, 'top5_doc_ids': [], 'hcx_called': False})
                continue
            info('LOCAL_QUERY', {'state':state, 'top5_doc_ids':[d['doc_id'] for d in docs],
                                 'hmac_matches':[d['hmac_matches'] for d in docs],
                                 'region_matches':[d['region_matches'] for d in docs],
                                 'timings_ms':pipeline.timings})
            if transport == 'hcx':
                from hcx import call_hcx
                reply, usage, redactions = call_hcx(pipeline.root,prepared.text,docs,bool(prepared.regions))
                info('HCX_ANSWER',reply)
                info('HCX_USAGE', usage)
                info('HCX_EXTRA_MS',round((time.perf_counter()-start)*1000,2))
        except error.HTTPError as exc:
            info('LOCAL_TEST_ERROR', {'stage':'http_chat','status':exc.code})
        except Exception as exc:
            # Exception bodies may embed private content, so print safe code only.
            from core import PipelineError
            from hcx import OutboundError
            code = str(exc) if isinstance(exc, (PipelineError, OutboundError)) else type(exc).__name__
            info('LOCAL_TEST_ERROR', {'code': code})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', choices=('cpu','mps'), default='cpu')
    ap.add_argument('--eval-artifact', type=Path)
    ap.add_argument('--max-queries', type=int, default=0, help='0=all eligible questions')
    ap.add_argument('--interactive', action='store_true')
    ap.add_argument('--transport', choices=('local','hcx','http'), default='local')
    ap.add_argument('--http-url', default='http://127.0.0.1:8768')
    ap.add_argument('--synthetic-review', action='store_true')
    ap.add_argument('--real-review-probes', action='store_true', help='try fabricated REVIEW candidates with the actual Step1 model; no DB writes')
    ap.add_argument('--synthetic-only', action='store_true', help='simulate review with no DB access')
    args = ap.parse_args()
    if args.max_queries < 0:
        ap.error('--max-queries must be nonnegative')
    if args.transport != 'local' and not args.interactive:
        ap.error('--transport hcx/http requires --interactive')
    if args.synthetic_only and not args.synthetic_review:
        ap.error('--synthetic-only requires --synthetic-review')
    if args.synthetic_review:
        synthetic_review()
    if args.synthetic_only:
        print('SYNTHETIC_REVIEW_ONLY_OK (real local admin web UI not tested)', flush=True)
        return 0
    pipeline = None
    try:
        pipeline = LivePipeline(ROOT,args.device,reuse_step1=True)
        state = pipeline.index_status()
        info('DB_INDEX_STATUS',state)
        if state['status'] != 'READY':
            print('E2E_STOP: DB index must be READY before further tests',flush=True)
            return 2
        if args.real_review_probes:
            real_review_probes(pipeline)
        if args.eval_artifact:
            eval_artifact(pipeline,args.eval_artifact,args.max_queries)
        if args.interactive:
            interactive(pipeline,args.transport,args.http_url)
        if not args.eval_artifact and not args.interactive and not args.real_review_probes:
            print('E2E_PROBE_OK (metadata only; for search use --interactive or --eval-artifact)',flush=True)
        return 0
    except Exception as exc:
        from core import PipelineError
        safe = str(exc) if isinstance(exc, PipelineError) else type(exc).__name__
        print('E2E_PROBE_FAILED',safe,flush=True)
        return 2
    finally:
        if pipeline:
            pipeline.close()


if __name__ == '__main__':
    raise SystemExit(main())
