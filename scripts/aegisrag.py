#!/usr/bin/env python3
"""Single AegisRAG entry point. Errors contain identifiers only."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'step2_privacy_rag/app'))
sys.path.insert(0, str(ROOT/'step1_pii_detector/src'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['models', 'init-db', 'prepare-v6', 'ingest-v6', 'evaluate-v6', 'run-chat', 'smoke', 'manifest'])
    p.add_argument('--env', type=Path, default=ROOT/'.env')
    p.add_argument('--input', type=Path)
    p.add_argument('--device', choices=['cpu','mps'], default='cpu')
    p.add_argument('--table')
    p.add_argument('--confirm')
    p.add_argument('--baseline', action='store_true')
    p.add_argument('--write', action='store_true')
    p.add_argument('--hcx-live', action='store_true', help='synthetic fixtures only; requires external transfer opt-in')
    p.add_argument('--files', nargs='+', type=Path)
    args, rest = p.parse_known_args()
    if rest and args.command not in {'prepare-v6','run-chat'}:
        p.error('unrecognized arguments')
    try:
        if args.command == 'models':
            from dotenv import load_dotenv
            load_dotenv(args.env)
            from pii_detector import create_detector
            with create_detector() as detector:
                if detector.detect('사내 안전교육 안내')['status'] == 'BLOCKED':
                    raise ValueError('STEP1_MODEL_SMOKE_BLOCKED')
            from settings import load_embedding_model
            load_embedding_model(args.device)
            print('PINNED_MODELS_READY')
        elif args.command == 'prepare-v6':
            import dataset_preparation
            return dataset_preparation.main(['--env', str(args.env)] + rest)
        elif args.command == 'run-chat':
            if args.env.resolve() != (ROOT/'.env').resolve():
                p.error('run-chat uses team root .env; select table with process environment')
            import app
            sys.argv = [sys.argv[0], '--device', args.device] + rest
            return app.main()
        elif args.command == 'smoke':
            from smoke import main as smoke
            return smoke(args.env, args.device, args.write, args.hcx_live)
        elif args.command == 'manifest':
            if not args.files or len({f.name for f in args.files}) != len(args.files):
                p.error('--files must have unique basenames')
            print(json.dumps({f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in args.files}, indent=2))
        else:
            import psycopg
            from settings import db_config
            from reproduction import artifact, target, encode_docs, ingest, evaluate
            if args.command == 'init-db':
                # Starter init is automatic ONLY on a new volume. Never replay role/schema creation.
                with psycopg.connect(**db_config(args.env)) as conn:
                    conn.execute('SET TRANSACTION READ ONLY')
                    vector, schema = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector'), to_regnamespace('rag') IS NOT NULL").fetchone()
                    if not (vector and schema):
                        raise ValueError('STARTER_SCHEMA_MISSING_RUN_COMPOSE_ON_NEW_VOLUME')
                print('STARTER_SCHEMA_READY_READ_ONLY')
            else:
                if not args.input:
                    p.error('--input required')
                data = artifact(args.input, args.env)
                if args.command == 'ingest-v6':
                    name = target(data)
                    if args.confirm != name:
                        print(json.dumps({'planned_table':name,'rows':len(data['documents']), 'action':'rerun with --confirm '+name}))
                        return 0 if args.confirm is None else 2
                    vectors = encode_docs(data, args.device)
                    with psycopg.connect(**db_config(args.env)) as conn:
                        outcome = ingest(conn, data, vectors, name, args.confirm)
                    print(outcome, name, len(data['documents']))
                else:
                    report, _ = evaluate(data, args.table or target(data), args.env, args.device, args.baseline)
                    print(json.dumps(report, sort_keys=True))
    except Exception as exc:
        # Libraries can put source text/DSNs/API bodies in exception messages.
        safe = type(exc).__name__
        if isinstance(exc, (ValueError, RuntimeError)) and (re.fullmatch(r'[A-Z][A-Z0-9_]+', str(exc)) or str(exc).startswith('BASELINE_METRICS_MISMATCH:')):
            safe = str(exc)
        print('REPRODUCTION_FAILED', args.command, safe, file=sys.stderr)
        print('Check: input file existence/schema, private HF read access + HF_TOKEN, fixed model cache, HMAC configuration, PostgreSQL availability/permissions. No stage was silently skipped.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
