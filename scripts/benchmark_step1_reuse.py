#!/usr/bin/env python3
"""Read-only local Step1 reuse benchmark (no HCX, PII content or HMAC logging).

Compare baseline and reuse in SEPARATE Python processes. Use --runs >=20 to
report observed warm p95. The benchmark only queries an existing V6 table.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import resource
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'step2_privacy_rag' / 'app'))
sys.path.insert(0, str(ROOT / 'step1_pii_detector' / 'src'))
from core import LivePipeline

# Public/generic question, never output a user's raw question.
QUESTION = '출장 결과 보고의 목적을 알려줘'
FIELDS = ('step1_ms', 'step1_init_ms', 'step1_process_ms', 'step1_close_ms',
          'embedding_ms', 'db_ms', 'rerank_ms', 'total_local_ms')


def percentile(vals, fraction):
    s = sorted(vals)
    return round(s[max(0, min(len(s) - 1, int((len(s) - 1) * fraction + 0.999999)))], 1)


def peak_rss_mib():
    # macOS ru_maxrss is in bytes; Linux in KiB.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == 'darwin' else 1024), 1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('baseline', 'reuse'), required=True)
    p.add_argument('--runs', type=int, default=20)
    p.add_argument('--device', choices=('cpu', 'mps'), default='cpu')
    args = p.parse_args()
    if not 1 <= args.runs <= 100:
        p.error('runs must be between 1 and 100')
    pipeline = LivePipeline(ROOT, args.device, reuse_step1=(args.mode == 'reuse'))
    data = []
    try:
        for i in range(args.runs + 1):
            start = time.perf_counter()
            state, prepared, docs = pipeline.ask_local(QUESTION)
            timings = dict(pipeline.timings)
            timings['total_local_ms'] = (time.perf_counter() - start) * 1000
            if state != 'READY' or prepared is None or len(docs) != 5:
                raise RuntimeError('BENCHMARK_UNEXPECTED_PIPELINE_STATUS')
            data.append({k: float(timings.get(k, 0)) for k in FIELDS})
    finally:
        pipeline.close()
    cold, warm = data[0], data[1:]
    result = {
        'mode': args.mode, 'device': args.device, 'cold_ms': {k: round(v, 1) for k, v in cold.items()},
        'warm_runs': len(warm), 'warm_metrics': {
            k: {'mean_ms': round(statistics.mean(x[k] for x in warm), 1),
                'p50_ms': percentile([x[k] for x in warm], .5),
                'p95_ms': percentile([x[k] for x in warm], .95) if len(warm) >= 20 else None}
            for k in FIELDS
        },
        'peak_process_rss_mib': peak_rss_mib(),
        'hcx_called': False,
        'db_written': False
    }
    print('L001_BENCHMARK ' + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
