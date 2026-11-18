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
    args = parser.parse_args()
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
    raise SystemExit(main())
