"""AegisRAG V6 local free-form retrieval. No evaluation labels, no database writes.

This module intentionally returns *locally displayable* masked document text. It
must not be interpreted as a certificate that content is safe for a remote API.
"""
from __future__ import annotations

import os
import re
import sys
import time
from settings import settings, db_config, table_identifier, load_embedding_model
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PII = re.compile(r"<[A-Z][A-Z0-9_]*_[0-9a-f]{24}>")
REGION = re.compile(r"region_v1_[0-9a-f]{24}\Z")
TABLE = 'rag.aegis_koe5_v6_exp1'
MODEL_NAME = 'nlpai-lab/KoE5'


class PipelineError(Exception):
    """Non-sensitive error identifier, suitable for UI rendering."""


def db_failure_code(exc: Exception) -> str:
    """Expose only a stable PostgreSQL failure category, never exception text."""
    import psycopg
    if not isinstance(exc, psycopg.Error):
        return 'V6_DB_READ_ERROR'
    state = getattr(exc, 'sqlstate', None) or ''
    if state == '42P01':
        return 'V6_DB_TABLE_NOT_FOUND'
    if state == '42501':
        return 'V6_DB_ACCESS_DENIED'
    if state.startswith('28'):
        return 'V6_DB_AUTH_FAILED'
    if state == '3D000':
        return 'V6_DB_DATABASE_NOT_FOUND'
    if state in {'42703', '42883', '42704'}:
        return 'V6_DB_SCHEMA_MISMATCH'
    if state.startswith('08') or (isinstance(exc, psycopg.OperationalError) and not state):
        return 'V6_DB_CONNECTION_FAILED'
    return 'V6_DB_QUERY_FAILED'


def order_results(rows: list[dict], query_tokens: set[str], region_keys: set[str]) -> list[dict]:
    """C ranking: HMAC count, region count, DB dense rank (original order)."""
    def counts(row):
        return (len(query_tokens & set(row['tokens'])),
                len(region_keys & set(row['region_keys'])))
    for row in rows:
        row['hmac_matches'], row['region_matches'] = counts(row)
        row['verified_region_match'] = (row['region_matches'] > 0) if region_keys else None
    return sorted(rows, key=lambda r: (-r['hmac_matches'], -r['region_matches'], r['dense_rank']))


@dataclass
class PreparedQuery:
    text: str
    tokens: set[str]
    regions: set[str]


class LivePipeline:
    def __init__(self, root: Path, device: str = 'cpu', env_file: Path | None = None, *, reuse_step1: bool = False):
        self.root = root.resolve()
        env_file = env_file or self.root / '.env'
        self.device = device
        if device not in {'cpu', 'mps', 'auto'}:
            raise PipelineError('INVALID_DEVICE')
        self.step2 = self.root / 'step2_privacy_rag'
        step1 = self.root / 'step1_pii_detector' / 'src'
        if str(step1) not in sys.path:
            sys.path.insert(0, str(step1))
        from dataset_preparation import fixed_key, key_tag
        self.secret = fixed_key(env_file)
        if not isinstance(self.secret, bytes) or len(self.secret) < 32:
            raise PipelineError('INVALID_HMAC_SECRET')
        self.tag = key_tag(self.secret)
        setting = settings(env_file)
        self.table_name = setting('AEGISRAG_TABLE', TABLE)
        self.table = table_identifier(self.table_name)
        self.expected_rows = int(setting('AEGISRAG_EXPECTED_ROWS', '177'))
        if self.expected_rows < 1:
            raise PipelineError('INVALID_EXPECTED_ROWS')
        self.db = db_config(env_file)
        self.timings = {}
        self._model = None
        # Opt-in only until the real Mac memory/latency regression is verified.
        self.reuse_step1 = bool(reuse_step1)
        self._detector = None
        self._detector_failed = False
        self._models_preloaded = False
        self._preload_metrics = {}

    def preload_models(self) -> dict[str, float]:
        """Warm local Step1 and KoE5 before the HTTP server starts.

        No DB, network LLM, or private document access. A safe public-style
        synthetic sentence is passed through the identical query preparation
        path to warm both model inference backends, not just load their files.
        """
        if not self.reuse_step1:
            raise PipelineError('PRELOAD_REQUIRES_STEP1_REUSE')
        if self._detector_failed:
            raise PipelineError('STEP1_DETECTOR_FAILED_RESTART_REQUIRED')
        if self._models_preloaded:
            return dict(self._preload_metrics)

        start = time.perf_counter()
        try:
            ready = self.prepare('출장 결과 보고의 목적을 알려줘')
            if not isinstance(ready, PreparedQuery):
                raise PipelineError('PRELOAD_STEP1_PROBE_NOT_READY')
            step1_ms = (time.perf_counter() - start) * 1000
            embed_start = time.perf_counter()
            # The model remains cached on the same LivePipeline instance.
            self._encode(ready.text)
            embedding_ms = (time.perf_counter() - embed_start) * 1000
            metrics = dict(step1_ms=round(step1_ms, 2),
                           embedding_ms=round(embedding_ms, 2),
                           total_ms=round((time.perf_counter() - start) * 1000, 2))
            self._preload_metrics = metrics
            self._models_preloaded = True
            return dict(metrics)
        except Exception as exc:
            # Never start a half-warmed service, nor reuse an uncertain detector.
            self._detector_failed = True
            self._models_preloaded = False
            self._preload_metrics = {}
            self._model = None
            self.close()
            raise PipelineError('MODEL_PRELOAD_FAILED') from exc

    def close(self) -> None:
        """Release a long-lived detector when the chat/benchmark process exits."""
        detector = self._detector
        self._detector = None
        if detector is not None:
            detector.close()

    def prepare(self, raw_question: str) -> PreparedQuery | str:
        from pii_detector import create_detector
        from document_preparation import prepare
        if self._detector_failed:
            raise PipelineError('STEP1_DETECTOR_FAILED_RESTART_REQUIRED')
        # The default continues to release Step1 before loading KoE5.
        detector = None
        try:
            started = time.perf_counter()
            if self.reuse_step1:
                if self._detector is None:
                    self._detector = create_detector()
                detector = self._detector
            else:
                detector = create_detector()
            self.timings['step1_init_ms'] = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            try:
                result = prepare('Q_LIVE', raw_question, detector, self.secret)
            finally:
                self.timings['step1_process_ms'] = (time.perf_counter() - started) * 1000
                if not self.reuse_step1 and detector is not None:
                    started_close = time.perf_counter()
                    detector.close()
                    self.timings['step1_close_ms'] = (time.perf_counter() - started_close) * 1000
        except Exception as exc:
            if self.reuse_step1:
                # Do not silently recreate or retry a detector after a failed call.
                # A timed-out backend can still have an active worker thread.
                self._detector_failed = True
                self.close()
            raise PipelineError('STEP1_GATE_FAILED') from exc
        if getattr(result, 'state', None) != 'LOCAL_PREPARED' or getattr(result, 'unlinked_mentions', ()):
            state = str(getattr(result, 'state', 'REVIEW_REQUIRED'))
            return state if state not in {'LOCAL_PREPARED', 'READY'} else 'REVIEW_REQUIRED'
        masked = getattr(result, 'sanitized_text', None)
        regions = getattr(result, 'region_keys', ())
        if not isinstance(masked, str) or not masked.strip():
            raise PipelineError('EMPTY_PREPARED_QUESTION')
        if not isinstance(regions, (tuple, list)) or any(
            not isinstance(x, str) or not REGION.fullmatch(x) for x in regions
        ):
            raise PipelineError('INVALID_REGION_METADATA')
        return PreparedQuery(text=masked, tokens=set(PII.findall(masked)), regions=set(regions))

    def _encode(self, text: str) -> str:
        import numpy as np
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            import torch
            device = ('mps' if torch.backends.mps.is_available() else 'cpu') if self.device == 'auto' else self.device
            if device == 'mps' and not torch.backends.mps.is_available():
                raise PipelineError('MPS_UNAVAILABLE')
            model = load_embedding_model(device)
            model.max_seq_length = 512
            if model.get_sentence_embedding_dimension() != 1024:
                raise PipelineError('INVALID_MODEL_DIMENSION')
            self._model = model
            print('LIVE_KOE5_READY device=' + device, flush=True)
        arr = np.asarray(self._model.encode(['query: ' + text], normalize_embeddings=True,
                                            convert_to_numpy=True, show_progress_bar=False,
                                            batch_size=1), dtype=np.float32)
        if arr.shape != (1, 1024) or not np.isfinite(arr).all():
            raise PipelineError('INVALID_EMBEDDING')
        return '[' + ','.join(format(float(x), '.9g') for x in arr[0]) + ']'

    def index_status(self) -> dict[str, Any]:
        """Read-only index diagnostics; do not return any token or document text."""
        import psycopg
        from psycopg import sql
        try:
            with psycopg.connect(**self.db) as conn:
                with conn.cursor() as cur:
                    cur.execute('SET TRANSACTION READ ONLY')
                    cur.execute(sql.SQL(
                        'SELECT COUNT(*), COUNT(DISTINCT key_tag), MIN(key_tag) FROM {}'
                    ).format(self.table))
                    actual, distinct_tags, observed_tag = cur.fetchone()
        except Exception as exc:
            raise PipelineError(db_failure_code(exc)) from None
        matching = (actual == self.expected_rows and distinct_tags == 1 and observed_tag == self.tag)
        return {'status': 'READY' if matching else 'MISMATCH',
                'table': self.table_name, 'rows': actual,
                'expected_rows': self.expected_rows, 'key_matches': distinct_tags == 1 and observed_tag == self.tag}

    def retrieve(self, prepared: PreparedQuery, top_k: int | None = 5) -> list[dict[str, Any]]:
        import psycopg
        from psycopg import sql
        start = time.perf_counter()
        vec = self._encode(prepared.text)
        self.timings['embedding_ms'] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        try:
            with psycopg.connect(**self.db) as conn:
                with conn.cursor() as cur:
                    cur.execute('SET TRANSACTION READ ONLY')
                    cur.execute(sql.SQL('SELECT COUNT(*), COUNT(DISTINCT key_tag) FROM {}').format(self.table))
                    num, tag_count = cur.fetchone()
                    if num != self.expected_rows or tag_count != 1:
                        raise PipelineError('V6_DB_INDEX_INCOMPLETE')
                    cur.execute(sql.SQL('SELECT DISTINCT key_tag FROM {}').format(self.table))
                    if cur.fetchone()[0] != self.tag:
                        raise PipelineError('V6_DB_HMAC_KEY_MISMATCH')
                    cur.execute(sql.SQL('''SELECT doc_id, masked_text, tokens, region_keys
                        FROM {} WHERE key_tag=%s
                        ORDER BY embedding <=> %s::vector, doc_id''').format(self.table), (self.tag, vec))
                    raw_rows = cur.fetchall()
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(db_failure_code(exc)) from None
        self.timings['db_ms'] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        if len(raw_rows) != self.expected_rows or len({r[0] for r in raw_rows}) != self.expected_rows:
            raise PipelineError('V6_DB_RESULT_COUNT_ERROR')
        rows = [dict(doc_id=did, text=masked, tokens=tok or [], region_keys=reg or [],
                     dense_rank=i+1) for i, (did, masked, tok, reg) in enumerate(raw_rows)]
        ranked = order_results(rows, prepared.tokens, prepared.regions)
        self.timings['rerank_ms'] = (time.perf_counter() - start) * 1000
        return ranked[:top_k]

    def ask_local(self, raw_question: str) -> tuple[str, PreparedQuery | None, list[dict]]:
        if not isinstance(raw_question, str) or not raw_question.strip() or len(raw_question) > 1000:
            raise PipelineError('INVALID_QUESTION')
        self.timings = dict.fromkeys(('step1_ms', 'step1_init_ms', 'step1_process_ms', 'step1_close_ms', 'embedding_ms', 'db_ms', 'rerank_ms', 'hcx_ms', 'total_ms'), 0.0)
        start = time.perf_counter()
        prepared = self.prepare(raw_question)
        self.timings['step1_ms'] = (time.perf_counter() - start) * 1000
        if isinstance(prepared, str):
            return prepared, None, []
        rows = self.retrieve(prepared)
        return 'READY', prepared, rows
