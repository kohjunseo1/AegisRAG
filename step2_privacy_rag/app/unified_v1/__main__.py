"""PII-free diagnostic CLI; defaults to missing adapters, never downloads/calls."""
import argparse
import json
from .contracts import Config, PROFILES
from .adapters import DetectionEngine, AxAdapter, VeilAdapter, KlueAdapter, KoElectraAdapter

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=tuple(PROFILES), default='own2')
    parser.add_argument('--synthetic', action='store_true', help='Use empty mock raw predictors; not real inference')
    parser.add_argument('--synthetic-e2e', action='store_true', help='Network-blocked full synthetic pipeline and manifest')
    parser.add_argument('--local-smoke',action='store_true',help='Explicit offline real-model synthetic smoke; no API')
    parser.add_argument('--runtime-python',help='Absolute isolated model Python executable')
    parser.add_argument('--db-readonly-smoke',action='store_true',help='Existing index/KoE5 synthetic read-only smoke')
    parser.add_argument('--env-file',help='Read-only existing .env path; never copied or printed')
    args = parser.parse_args()
    if args.local_smoke:
        if not args.runtime_python:parser.error('--runtime-python required')
        from .local_validation import run_local_smoke
        result=run_local_smoke(args.runtime_python)
        print(json.dumps(result,indent=2))
        return 0 if result['profiles'].get('quality3')=='REAL_READY' else 2
    if args.db_readonly_smoke:
        if not args.env_file:parser.error('--env-file required')
        from .local_validation import run_db_smoke
        print(json.dumps(run_db_smoke(args.env_file),indent=2))
        return 0
    if args.synthetic_e2e:
        from .synthetic import run_synthetic
        print(json.dumps(run_synthetic(args.profile),ensure_ascii=False,sort_keys=True,indent=2))
        return 0
    Config(detector_profile=args.profile)
    adapters = {}
    if args.synthetic:
        for cls in (AxAdapter, VeilAdapter, KlueAdapter, KoElectraAdapter):
            adapter = cls('mock-v1', lambda text: [])
            adapters[adapter.source_id] = adapter
    evidence = DetectionEngine(args.profile, adapters).detect('합성 정책 안내', input_kind='document')
    print(json.dumps({'mode':'MOCK' if args.synthetic else 'DRY_RUN', 'profile': args.profile,
                      'model_status':dict(evidence.model_status_by_source),
                      'decision':'LOCAL_ONLY' if evidence.ready else 'BLOCKED', 'api_live':False}))
    return 0 if evidence.ready else 2

if __name__ == '__main__':
    try:
        code=main()
    except Exception as error:
        print(json.dumps({'status':'BLOCKED','error_type':type(error).__name__}))
        code=2
    raise SystemExit(code)
