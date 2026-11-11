#!/usr/bin/env python3
"""L-002 local latency benchmark: lazy model startup vs preloaded readiness.

Uses public-style synthetic query only, existing V6 DB READ ONLY, NO HCX calls.
Run each mode in a separate Python process; includes startup/cold/warm metrics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'step2_privacy_rag' / 'app'))
sys.path.insert(0, str(ROOT / 'step1_pii_detector' / 'src'))
from core import LivePipeline

QUERY = '사내 안전교육 일정 알려줘'


def rss_mib():
    amount = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(amount / (1024 * 1024 if sys.platform == 'darwin' else 1024), 1)


def percentile(values, q):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * q + 0.999999)))
    return round(ordered[index], 1)


def benchmark(mode, device, runs):
    start = perf_counter()
    pipeline = LivePipeline(ROOT, device, reuse_step1=True)
    rows = []
    signatures = set()
    ready_ms = 0.0
    preload_metrics = None
    try:
        if mode == 'preload':
            preload_metrics = pipeline.preload_models()
        ready_ms = (perf_counter() - start) * 1000

        for _ in range(runs + 1):
            t = perf_counter()
            state, prepared, docs = pipeline.ask_local(QUERY)
            elapsed = (perf_counter() - t) * 1000
            if state != 'READY' or prepared is None or len(docs) != 5:
                raise RuntimeError('L002_BENCHMARK_NOT_READY')
            signatures.add(hashlib.sha256(
                '|'.join(d['doc_id'] for d in docs).encode('utf-8')
            ).hexdigest()[:16])
            rows.append({
                'request_ms': elapsed,
                'step1_ms': float(pipeline.timings['step1_ms']),
                'embedding_ms': float(pipeline.timings['embedding_ms']),
                'db_ms': float(pipeline.timings['db_ms']),
            })
    finally:
        pipeline.close()
    if len(signatures) != 1:
        raise RuntimeError('L002_RANKING_NOT_STABLE')
    first, warm = rows[0], rows[1:]
    return {
        'mode': mode, 'device': device, 'warm_runs': runs,
        'ready_after_ms': round(ready_ms, 1),
        'preload_components_ms': preload_metrics,
        'first_request_ms': round(first['request_ms'], 1),
        'startup_through_first_ms': round(ready_ms + first['request_ms'], 1),
        'first_step1_ms': round(first['step1_ms'], 1),
        'first_embedding_ms': round(first['embedding_ms'], 1),
        'warm_request_mean_ms': round(statistics.mean(r['request_ms'] for r in warm), 1),
        'warm_request_p50_ms': percentile([r['request_ms'] for r in warm], 0.5),
        'warm_request_p95_ms': percentile([r['request_ms'] for r in warm], 0.95)
        if runs >= 20 else None,
        'top5_rank_signature': next(iter(signatures)),
        'peak_process_rss_mib': rss_mib(),
        'db_written': False, 'hcx_called': False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('lazy', 'preload'), required=True)
    p.add_argument('--device', choices=('cpu', 'mps'), default='cpu')
    p.add_argument('--runs', type=int, default=5)
    args = p.parse_args()
    if not 1 <= args.runs <= 100:
        p.error('runs must be 1..100')
    result = benchmark(args.mode, args.device, args.runs)
    print('L002_BENCHMARK ' + json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
