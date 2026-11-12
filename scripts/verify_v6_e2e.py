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
from metrics_retrieval import summarize_score, summarize_latency, failure_cases
from reproduction import artifact
from settings import db_config
from dataset_preparation import load_jsonl


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
        if outcome in {'STEP1_GATE_FAILED','STEP1_DETECTOR_FAILED_RESTART_REQUIRED'}:
            # Fail closed: repeat requests cannot repair a faulted live detector.
            results.extend({'case': n, 'state': 'NOT_RUN_AFTER_STEP1_FAILURE'}
                           for n in range(number+1, len(cases)+1))
            print('STEP1_FAULT: run python scripts/diagnose_step1.py in the current environment', flush=True)
            break
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
    # Warm the query embedding backend once with a public synthetic string.
    # Cold model loading is a startup metric, not per-query warmed CPU latency.
    warm_started = time.perf_counter()
    pipeline._encode('합성 검색 성능 측정용 문장')
    info('EMBEDDING_COLD_START_MS', round((time.perf_counter()-warm_started)*1000,2))
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
    # An all-query run includes non-READY and non-indexable Gold questions in
    # the denominator. Limited runs are explicitly marked "eligible subset".
    scope_queries = (data['queries'] if not max_queries else
                     [q for q in data['queries'] if q['qid'] in subset_qids])
    scope_gold = (data['gold_for_evaluation_only'] if not max_queries else
                  [g for g in data['gold_for_evaluation_only'] if g['qid'] in subset_qids])
    report = summarize_score(scope_queries, scope_gold, ids, runs)
    report['evaluation_scope'] = 'ALL_88_WITH_DEFERRED' if not max_queries else 'ELIGIBLE_SUBSET_ONLY'
    report['artifact_document_count'] = len(data['documents'])
    report['db_document_count'] = len(ids)
    report['artifact_matches_live_index'] = (len(data['documents']) == len(ids) and
        {d['doc_id'] for d in data['documents']} == ids)
    report['observed_eligible_subset'] = len(subset)
    report['input_total_queries'] = len(data['queries'])
    report['input_deferred'] = sum(q['text'] is None for q in data['queries'])
    report['input_no_indexable_gold'] = sum(
        q['text'] is not None and
        not (set(gold_by_id[q['qid']]['relevant_doc_ids']) & ids)
        for q in data['queries'])
    info('RETRIEVAL_METRICS', report)
    info('RETRIEVAL_FAILURE_CASES', failure_cases(scope_queries, scope_gold, runs, ids))
    info('CPU_RETRIEVAL_LATENCY_MS', summarize_latency(latency))
    if not report['artifact_matches_live_index']:
        print('EVAL_WARNING: preprocessing artifact doc_ids differ from live 187-document index; scope/baseline may not be directly comparable.', flush=True)
    print('EVAL_NOTE: preprocessed queries exclude Step1 and HCX latency; use interactive mode for full local query timings.', flush=True)



def eval_raw_holdout(pipeline, queries_file: Path, gold_file: Path):
    """Evaluate all 88 queries *live* against the existing DB without re-ingesting.

    This is the online-gate baseline, not the previous manually revalidated
    Human-assisted evaluation. A review/deferred query receives zero full-set
    retrieval credit, and its raw text is never logged or sent to an LLM.
    """
    from collections import Counter
    if not queries_file.is_file() or not gold_file.is_file():
        info('EVAL_INPUT_MISSING', {
            'queries_jsonl_exists': queries_file.is_file(),
            'gold_jsonl_exists': gold_file.is_file(),
            'hint': 'Provide --queries-jsonl and --gold-jsonl, or locate the original files in local_eval'})
        return False
    queries = load_jsonl(queries_file)
    gold = load_jsonl(gold_file)
    if len(queries) != 88 or len(gold) != 88:
        raise ValueError('EXPECTED_88_QUERY_AND_GOLD_ROWS')
    qids = [q.get('qid') for q in queries]
    gids = [g.get('qid') for g in gold]
    if (len(set(qids)) != 88 or len(set(gids)) != 88
            or set(qids) != set(gids)
            or any(not isinstance(q.get('query'), str) for q in queries)):
        raise ValueError('INVALID_EVALUATION_QUERY_IDS')
    ids = index_ids(pipeline)
    # Warm both backends before timing per-query service processing.
    warm_started = time.perf_counter()
    pipeline.preload_models()
    info('COLD_START_AND_PRELOAD_MS', round((time.perf_counter()-warm_started)*1000,2))
    prepared_queries, ranked = [], {}
    per_query_times = []
    states = Counter()
    for i, q in enumerate(queries, 1):
        started = time.perf_counter()
        result = pipeline.prepare(q['query'])
        if isinstance(result, PreparedQuery):
            state = 'READY'
            prepared_queries.append({'qid': q['qid'], 'text': result.text})
            docs = pipeline.retrieve(result, top_k=None)
            ranked[q['qid']] = [d['doc_id'] for d in docs]
            elapsed_ms = (time.perf_counter()-started)*1000
            per_query_times.append({
                'total_ms': elapsed_ms,
                'step1_ms': pipeline.timings.get('step1_process_ms', 0.0),
                'embedding_ms': pipeline.timings.get('embedding_ms', 0.0),
                'db_ms': pipeline.timings.get('db_ms', 0.0),
                'rerank_ms': pipeline.timings.get('rerank_ms', 0.0),
            })
        else:
            state = str(result)
            prepared_queries.append({'qid': q['qid'], 'text': None})
        states[state] += 1
        if i == 88 or i % 20 == 0:
            info('RAW_EVAL_PROGRESS', {'completed': i, 'total':88})
    scores = summarize_score(prepared_queries, gold, ids, ranked)
    scores['evaluation_scope'] = 'RAW_QUERY_ONLINE_GATE_88'
    scores['db_document_count'] = len(ids)
    scores['query_states'] = dict(states)
    scores['manual_review_overrides_applied'] = False
    info('RAW_88_RETRIEVAL_METRICS', scores)
    info('RAW_88_RETRIEVAL_FAILURE_CASES', failure_cases(prepared_queries, gold, ranked, ids))
    info('RAW_88_CPU_WARM_LATENCY_MS', summarize_latency(per_query_times))
    print('RAW_EVAL_NOTE: live Gate behavior; differs from prior Human-assisted experiment if it used administrator query overrides.', flush=True)
    return True


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


def interactive(pipeline, transport: str, base: str, *, show_masked_evidence: bool = False, diagnose_gate: bool = False):
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
                info('LOCAL_QUERY', {'state': state, 'top5_doc_ids': [], 'hcx_called': False,
                                     'timings_ms':pipeline.timings})
                if diagnose_gate and state in {'REVIEW','REVIEW_REQUIRED','BLOCKED'}:
                    from document_preparation import prepare as prepare_doc
                    if pipeline._detector is not None:
                        outcome = prepare_doc('Q_LIVE', question, pipeline._detector, pipeline.secret)
                        step1 = pipeline._detector.detect(question)
                        codes = sorted(set(str(i.get('code','')) for i in step1.get('issues',[])
                                           if isinstance(i, dict) and
                                           str(i.get('code','')).replace('_','').isalnum()))
                        detail = {
                            'state': outcome.state, 'reason': outcome.reason,
                            'unlinked_mentions': outcome.unlinked_mentions,
                            'step1_status': step1.get('status'),
                            'step1_issue_codes': codes,
                            'step1_entity_labels': sorted(set(
                                e.get('label','') for e in step1.get('entities',[])
                                if isinstance(e, dict)))}
                        if outcome.reason == 'GATE_NOT_READY':
                            # Reuse frozen Step1 output; no private content or spans
                            # are logged. This diagnostic cannot authorize a retry.
                            from name_protection_handoff import _FrozenDetectorView
                            from privacy_gate import check_privacy_gate
                            gate = check_privacy_gate(
                                _FrozenDetectorView(pipeline._detector, step1), question)
                            detail.update({
                                'gate_decision': gate.get('decision'),
                                'gate_reason': gate.get('reason'),
                                'gate_uncovered_count': gate.get('uncovered_count', 0),
                                'veil_policy_counts': gate.get('veil_policy_counts', {}),
                            })
                        info('LOCAL_GATE_REASON', detail)
                continue
            info('LOCAL_QUERY', {'state':state, 'top5_doc_ids':[d['doc_id'] for d in docs],
                                 'query_hmac_token_count': len(prepared.tokens),
                                 'query_region_key_count': len(prepared.regions),
                                 'hmac_matches':[d['hmac_matches'] for d in docs],
                                 'region_matches':[d['region_matches'] for d in docs],
                                 'timings_ms':pipeline.timings})
            if show_masked_evidence:
                from hcx import redact_residual_pii
                for doc in docs:
                    sanitized, counts = redact_residual_pii(doc['text'])
                    info('LOCAL_EVIDENCE', {'doc_id':doc['doc_id'],
                                            'masked_excerpt':sanitized[:450],
                                            'residual_redaction_counts':counts})
                print('LOCAL_EVIDENCE_NOTE: excerpts are pseudonymized but not guaranteed PII-free. Do not publish them.',flush=True)
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
            if code in {'STEP1_GATE_FAILED','STEP1_DETECTOR_FAILED_RESTART_REQUIRED'}:
                print('STEP1_FAULT: stop this process and run python scripts/diagnose_step1.py', flush=True)
                break


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', choices=('cpu','mps'), default='cpu')
    ap.add_argument('--eval-artifact', type=Path)
    ap.add_argument('--eval-raw', action='store_true', help='evaluate actual 88 raw queries against current live DB, no re-ingest')
    ap.add_argument('--queries-jsonl', type=Path, default=ROOT/'step2_privacy_rag/local_eval/holdout_retrieval_queries.jsonl')
    ap.add_argument('--gold-jsonl', type=Path, default=ROOT/'step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl')
    ap.add_argument('--max-queries', type=int, default=0, help='0=all eligible questions')
    ap.add_argument('--interactive', action='store_true')
    ap.add_argument('--show-masked-evidence', action='store_true',
                    help='opt in to local-only pseudonymized evidence snippets (may contain residual PII)')
    ap.add_argument('--diagnose-gate', action='store_true',
                    help='show safe REVIEW reason codes for local interactive queries')
    ap.add_argument('--transport', choices=('local','hcx','http'), default='local')
    ap.add_argument('--http-url', default='http://127.0.0.1:8768')
    ap.add_argument('--synthetic-review', action='store_true')
    ap.add_argument('--real-review-probes', action='store_true', help='try fabricated REVIEW candidates with the actual Step1 model; no DB writes')
    ap.add_argument('--synthetic-only', action='store_true', help='simulate review with no DB access')
    args = ap.parse_args()
    if args.max_queries < 0:
        ap.error('--max-queries must be nonnegative')
    if args.eval_raw and args.eval_artifact:
        ap.error('--eval-raw and --eval-artifact cannot be combined')
    if args.eval_raw and args.max_queries:
        ap.error('--eval-raw always evaluates all 88 queries (do not use --max-queries)')
    if args.transport != 'local' and not args.interactive:
        ap.error('--transport hcx/http requires --interactive')
    if (args.show_masked_evidence or args.diagnose_gate) and (not args.interactive or args.transport != 'local'):
        ap.error('--show-masked-evidence/--diagnose-gate require --interactive local transport')
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
            if not args.eval_artifact.is_file():
                info('EVAL_ARTIFACT_MISSING', {
                    'exists': False, 'hint': 'prepared.json was an example path; find an existing artifact or use --eval-raw with original queries and Gold JSONL'})
                return 2
            eval_artifact(pipeline,args.eval_artifact,args.max_queries)
        if args.eval_raw:
            if not eval_raw_holdout(pipeline, args.queries_jsonl, args.gold_jsonl):
                return 2
        if args.interactive:
            interactive(pipeline,args.transport,args.http_url,show_masked_evidence=args.show_masked_evidence,
                        diagnose_gate=args.diagnose_gate)
        if not args.eval_artifact and not args.eval_raw and not args.interactive and not args.real_review_probes:
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
