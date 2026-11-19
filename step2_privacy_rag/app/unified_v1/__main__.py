"""Safe-default diagnostics and explicit offline experiments; paid calls require opt-in."""
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
    final_modes=parser.add_mutually_exclusive_group()
    final_modes.add_argument('--check-models',action='store_true')
    final_modes.add_argument('--benchmark-profiles',action='store_true')
    final_modes.add_argument('--evaluate-step1',action='store_true')
    final_modes.add_argument('--evaluate-retrieval',action='store_true')
    final_modes.add_argument('--e2e-local',action='store_true')
    final_modes.add_argument('--e2e-isolated-synthetic',action='store_true')
    final_modes.add_argument('--synthetic-hcx-live',action='store_true')
    parser.add_argument('--documents',help='Local raw labeled seen_dev documents, never exported')
    parser.add_argument('--artifact',help='Local frozen legacy query/Gold artifact')
    parser.add_argument('--compare-e2e',action='store_true',help='Compare 3 detector x 3 retrieval combinations on same local raw queries')
    parser.add_argument('--queries',help='Local raw query JSONL for opt-in local E2E')
    parser.add_argument('--limit',type=int,default=None)
    parser.add_argument('--retriever',choices=('own_v6','team_v3','hybrid_v1'),default='hybrid_v1')
    parser.add_argument('--report-dir',help='PII-free summary JSON directory')
    parser.add_argument('--opt-in',action='store_true',help='Explicit future fixed-synthetic live permission; not used automatically')
    parser.add_argument('--max-calls',type=int,default=1)
    parser.add_argument('--budget',default='0')
    parser.add_argument('--reserve-per-call',default='0')
    args = parser.parse_args()
    if args.limit is not None and args.limit<1:parser.error('--limit must be positive')
    final_mode=next((name for name in ['check_models','benchmark_profiles','evaluate_step1','evaluate_retrieval','e2e_local','e2e_isolated_synthetic','synthetic_hcx_live'] if getattr(args,name)),None)
    if final_mode:
        from . import experiments as experiment
        from pathlib import Path
        if final_mode not in {'evaluate_retrieval','synthetic_hcx_live'} and not args.runtime_python:parser.error('--runtime-python required')
        if final_mode=='check_models':result=experiment.check_models(args.runtime_python)
        elif final_mode=='benchmark_profiles':result=experiment.benchmark_profiles(args.runtime_python)
        elif final_mode=='evaluate_step1':
            if not args.documents:parser.error('--documents required')
            result=experiment.evaluate_step1(args.runtime_python,args.documents,limit=args.limit)
        elif final_mode=='evaluate_retrieval':
            if not args.env_file or not args.artifact:parser.error('--env-file and --artifact required')
            result=experiment.evaluate_retrieval(args.env_file,args.artifact)
        elif final_mode=='e2e_local':
            if not args.env_file:parser.error('--env-file required')
            if args.compare_e2e:
                if not args.queries or not args.artifact:parser.error('--queries and --artifact required for E2E comparison')
                result=experiment.e2e_compare(args.runtime_python,args.env_file,args.queries,args.artifact,limit=args.limit)
            else:result=experiment.e2e_local(args.runtime_python,args.env_file,queries_path=args.queries,limit=args.limit or 10,profile=args.profile,retrieval_profile=args.retriever)
        elif final_mode=='e2e_isolated_synthetic':result=experiment.isolated_synthetic_e2e(args.runtime_python)
        else:
            if args.documents or args.queries or args.artifact:parser.error('private input paths are forbidden for live synthetic smoke')
            if args.opt_in and (not args.env_file or not args.runtime_python):parser.error('--runtime-python and --env-file required for explicit live opt-in')
            result=experiment.synthetic_hcx_live(args.runtime_python,args.env_file,opt_in=args.opt_in,max_calls=args.max_calls,budget=args.budget,reserve_per_call=args.reserve_per_call)
        if args.report_dir:
            directory=Path(args.report_dir);directory.mkdir(parents=True,exist_ok=True)
            target=directory/(final_mode+'.json')
            temporary=target.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');temporary.replace(target)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if result.get('status') in {'FAIL','BLOCKED','NOT_RUN_BY_POLICY'} or result.get('legacy_parity_status')=='FAIL' else 0

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
