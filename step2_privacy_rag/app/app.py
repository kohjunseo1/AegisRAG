#!/usr/bin/env python3
"""Local AegisRAG V6 free-form retrieval chat.

Run from repository root:
    python scripts/aegisrag.py run-chat --port 8768

Read-only PGVector + automatic HCX for READY requests.
Service-level external transfer authorization is required once at startup.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from core import LivePipeline, PipelineError
from hcx import call_hcx, OutboundError, redact_residual_pii

HERE = Path(__file__).resolve().parent
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


def respond(pipeline: LivePipeline, message: dict, allow_hcx: bool = True) -> dict:
    if not isinstance(message, dict) or set(message) != {'question'}:
        raise PipelineError('INVALID_REQUEST_BODY')
    question = message['question']
    if not isinstance(question, str) or not question.strip() or len(question) > 1000:
        raise PipelineError('INVALID_QUESTION')
    if not allow_hcx:
        raise OutboundError('HCX_SERVICE_NOT_AUTHORIZED')

    # No raw user question is passed to the LLM or written to logs.
    start = time.perf_counter()
    state, prepared, evidence = pipeline.ask_local(question)
    hcx_start = time.perf_counter()
    if prepared is None:
        is_review = 'REVIEW' in state.upper()
        return {'decision': 'REVIEW_REQUIRED' if is_review else 'BLOCKED_OR_DEFERRED',
                'answer': ('질문의 인물·개체 표현을 안전하게 연결할 수 없어 검색을 보류했습니다. 인물 이름만 입력하거나, 찾으려는 문서 종류를 구체적으로 다시 입력해 주세요.' if is_review
                           else '현재 질문을 안전하게 처리할 수 없습니다. 표현을 바꿔 다시 질문해 주세요.'),
                'citations': [], 'sources': [],
                'diagnostics': {'gate_state': state,
                    'timings_ms': {**getattr(pipeline, 'timings', {}), 'total_ms': round((time.perf_counter()-start)*1000, 2)}
                    if isinstance(getattr(pipeline, 'timings', None), dict) else {}}, 'mode': 'hcx'}

    # All downstream calls use only masked query and DB V6 already-prepared docs.
    try:
        result, usage, redactions = call_hcx(pipeline.root, prepared.text, evidence,
                                            bool(prepared.regions))
    except OutboundError as exc:
        if str(exc) in {'QUERY_RESIDUAL_PII_REQUIRES_REVIEW', 'OUTBOUND_RESIDUAL_PII_REMAINS'}:
            return {'decision': 'REVIEW_REQUIRED',
                    'answer': '추가 개인정보 보호 검토가 필요하여 답변을 보류했습니다.',
                    'citations': [], 'sources': [],
                    'diagnostics': {'gate_state': 'OUTBOUND_REVIEW_REQUIRED'}, 'mode': 'hcx'}
        raise
    if isinstance(getattr(pipeline, 'timings', None), dict):
        pipeline.timings['hcx_ms'] = (time.perf_counter() - hcx_start) * 1000
        pipeline.timings['total_ms'] = (time.perf_counter() - start) * 1000
    sources = []
    for d in evidence:
        display_text, _ = redact_residual_pii(d['text'])
        sources.append({'doc_id': d['doc_id'], 'masked_text': display_text[:2000],
                        'dense_rank': d['dense_rank'], 'hmac_matches': d['hmac_matches'],
                        'region_matches': d['region_matches']})
    # Keep only non-sensitive retrieval metadata for troubleshooting. These
    # candidates are NOT citations or proof that the answer was supported.
    retrieval_candidates = [
        {'doc_id': d['doc_id'], 'dense_rank': d['dense_rank'],
         'hmac_matches': d['hmac_matches'], 'region_matches': d['region_matches'],
         'topic_score': d.get('topic_score', 0)}
        for d in evidence
    ]
    diagnostics = {
        'gate_state': state,
        'query_recovery': getattr(pipeline, 'last_query_recovery', None),
        'retrieval_candidates': retrieval_candidates,
        'retrieved_count': len(retrieval_candidates),
        'hmac_token_count': len(prepared.tokens),
        'region_key_count': len(prepared.regions),
        'db_index_rows': getattr(pipeline, 'expected_rows', 177),
        'hcx_input_tokens': usage.get('prompt_tokens'),
        'hcx_output_tokens': usage.get('completion_tokens'),
        'outbound_redaction_counts': redactions,
    }
    answer, output_pii_counts = redact_residual_pii(result['answer'])
    diagnostics['response_redaction_counts'] = output_pii_counts
    if isinstance(getattr(pipeline, 'timings', None), dict):
        diagnostics['timings_ms'] = {k: round(v, 2) for k, v in pipeline.timings.items()}
    sources = [d for d in sources if d['doc_id'] in result['citations']]
    return {'decision': result['decision'], 'answer': answer,
            'citations': result['citations'], 'sources': sources,
            'diagnostics': diagnostics, 'mode': 'hcx'}


class Handler(BaseHTTPRequestHandler):
    pipeline: LivePipeline
    allow_hcx: bool = True

    def log_message(self, *args):
        # Do not record raw user prompts or URLs.
        return

    def _send(self, payload: bytes, status: int, mimetype: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', mimetype)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, data: dict, code: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self._send(payload, code, 'application/json; charset=utf-8')

    def _valid_host(self) -> bool:
        return self.headers.get('Host', '') in {f'127.0.0.1:{self.server.server_port}',
                                                f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self._valid_host():
            return self._json({'error': 'INVALID_HOST'}, 403)
        path = urlparse(self.path).path
        if path == '/api/health':
            return self._json({'status': 'OK', 'data_scope': 'V6_PRIVATE_LOCAL',
                               'external_hcx_enabled': self.allow_hcx,
                               'db_write': False})
        if path == '/api/index-status':
            try:
                return self._json(self.pipeline.index_status())
            except PipelineError as exc:
                return self._json({'error': str(exc)}, 503)
        if path not in ASSETS:
            return self._json({'error': 'NOT_FOUND'}, 404)
        name, mimetype = ASSETS[path]
        self._send((HERE / name).read_bytes(), 200, mimetype)

    def do_POST(self):
        if not self._valid_host():
            return self._json({'error': 'INVALID_HOST'}, 403)
        if urlparse(self.path).path != '/api/chat':
            return self._json({'error': 'NOT_FOUND'}, 404)
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://localhost:{self.server.server_port}',
                                     f'http://127.0.0.1:{self.server.server_port}'}:
            return self._json({'error': 'INVALID_ORIGIN'}, 403)
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self._json({'error': 'INVALID_CONTENT_TYPE'}, 415)
        try:
            n = int(self.headers.get('Content-Length', '-1'))
            if not 0 < n <= 6144:
                return self._json({'error': 'INVALID_BODY_SIZE'}, 400)
            message = json.loads(self.rfile.read(n))
            result = respond(self.pipeline, message, self.allow_hcx)
            return self._json(result)
        except (PipelineError, OutboundError) as e:
            print('LIVE_CHAT_BLOCKED', type(e).__name__, str(e), flush=True)
            return self._json({'error': str(e)}, 422)
        except Exception as e:
            # No exception details / request contents / DB DSNs / API responses.
            print('LIVE_CHAT_FAILED', type(e).__name__, flush=True)
            return self._json({'error': 'INTERNAL_ERROR', 'error_type': type(e).__name__}, 500)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', choices=['cpu', 'mps', 'auto'], default='cpu')
    ap.add_argument('--port', type=int, default=8767)
    ap.add_argument('--reuse-step1', action=argparse.BooleanOptionalAction, default=True,
                    help='reuse A.X/Veil across requests (default: enabled)')
    ap.add_argument('--preload', action=argparse.BooleanOptionalAction, default=True,
                    help='warm Step1 and KoE5 before accepting HTTP requests (default: enabled)')
    args = ap.parse_args()
    if not 1024 <= args.port <= 65535:
        ap.error('port must be between 1024 and 65535')
    if args.preload and not args.reuse_step1:
        ap.error('--preload requires --reuse-step1')
    if os.environ.get('AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED') != 'YES':
        ap.error('service requires one-time external transfer authorization: AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES')
    pipeline = None
    try:
        pipeline = LivePipeline(Path(__file__).resolve().parents[2], args.device, reuse_step1=args.reuse_step1)
        if args.preload:
            metrics = pipeline.preload_models()
            # Safe aggregate timings only: never print prompts, HMAC or model internals.
            print('L002_MODELS_PRELOADED step1_ms=' + str(metrics['step1_ms']) +
                  ' embedding_ms=' + str(metrics['embedding_ms']) +
                  ' total_ms=' + str(metrics['total_ms']), flush=True)
        # Do not listen for incoming connections until preload has succeeded.
        Handler.pipeline = pipeline
        Handler.allow_hcx = True
        server = HTTPServer(('127.0.0.1', args.port), Handler)
    except Exception as e:
        if pipeline is not None:
            pipeline.close()
        print('LIVE_CHAT_STARTUP_FAILED', type(e).__name__, flush=True)
        return 2
    print('AEGISRAG_V6_LIVE_CHAT_READY http://127.0.0.1:' + str(args.port), flush=True)
    print('MODE=V6_LIVE_AUTOMATIC_HCX_APPROVED', flush=True)
    print('DB_READ_ONLY=TRUE GOLD_USED=FALSE SERVER_LOCALHOST_ONLY=TRUE', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        pipeline.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
