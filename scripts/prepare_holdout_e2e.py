#!/usr/bin/env python3
"""Read-only preflight and private case conversion for the 187-doc/88-query Holdout.

Never runs Step1, opens the database, contacts HCX or logs raw content. Gold
is used ONLY to prepare an evaluation fixture and never for retrieval/indexing.
All generated files stay in git-ignored local_eval/ with owner-only permissions.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / 'step2_privacy_rag' / 'local_eval'
IDENT = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


class PreflightError(ValueError):
    """Static, non-sensitive diagnostic code."""


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise PreflightError('HOLDOUT_INPUT_FILE_MISSING')
    if not 0 < path.stat().st_size <= 50_000_000:
        raise PreflightError('HOLDOUT_INPUT_SIZE_INVALID')
    try:
        with path.open('r', encoding='utf-8') as source:
            lines = [json.loads(line) for line in source if line.strip()]
    except (ValueError, UnicodeError, OSError):
        raise PreflightError('HOLDOUT_INPUT_JSONL_INVALID') from None
    if not lines or any(not isinstance(row, dict) for row in lines):
        raise PreflightError('HOLDOUT_INPUT_ROWS_INVALID')
    return lines


def _ids(rows: list[dict], field: str) -> list[str]:
    ids = [row.get(field) for row in rows]
    if any(not isinstance(value, str) or not IDENT.fullmatch(value) for value in ids):
        raise PreflightError('HOLDOUT_ID_INVALID')
    if len(ids) != len(set(ids)):
        raise PreflightError('HOLDOUT_DUPLICATE_IDS')
    return ids


def build_cases(documents: list[dict], queries: list[dict], gold: list[dict],
                *, expected_docs: int = 187, expected_queries: int = 88
                ) -> tuple[list[dict], dict]:
    """Validate only schema and cross-file references; do not inspect PII or leak it."""
    if len(documents) != expected_docs or len(queries) != expected_queries or len(gold) != expected_queries:
        raise PreflightError('HOLDOUT_EXPECTED_COUNTS_MISMATCH')
    doc_ids = set(_ids(documents, 'id'))
    qids = _ids(queries, 'qid')
    gids = _ids(gold, 'qid')
    if set(qids) != set(gids):
        raise PreflightError('HOLDOUT_QUERY_GOLD_MISMATCH')
    if any(not isinstance(row.get('text'), str) or not row['text'].strip()
           for row in documents):
        raise PreflightError('HOLDOUT_DOCUMENT_TEXT_INVALID')
    if any(not isinstance(row.get('query'), str) or not 0 < len(row['query'].strip()) <= 1000
           for row in queries):
        raise PreflightError('HOLDOUT_QUERY_TEXT_INVALID')

    gold_lookup = {}
    for record in gold:
        relevant = record.get('relevant_doc_ids')
        if not isinstance(relevant, list) or any(
            not isinstance(x, str) or not IDENT.fullmatch(x) for x in relevant
        ):
            raise PreflightError('HOLDOUT_GOLD_DOCUMENT_IDS_INVALID')
        if len(relevant) != len(set(relevant)):
            raise PreflightError('HOLDOUT_GOLD_DOCUMENT_IDS_DUPLICATED')
        if not set(relevant).issubset(doc_ids):
            raise PreflightError('HOLDOUT_GOLD_OUTSIDE_CORPUS')
        gold_lookup[record['qid']] = relevant

    cases = [
        {'case_id': q['qid'], 'question': q['query'],
         'relevant_doc_ids': gold_lookup[q['qid']],
         'category': 'holdout_raw', 'split': 'holdout'}
        for q in queries
    ]
    summary = {
        'documents': len(documents),
        'queries': len(queries),
        'gold_rows': len(gold),
        'questions_with_relevant_documents': sum(bool(c['relevant_doc_ids']) for c in cases),
        'questions_without_relevant_documents': sum(not c['relevant_doc_ids'] for c in cases),
        'query_gold_id_match': True,
        'note': 'Input integrity only; no model, DB, outbound transfer, or answer accuracy evaluated.'
    }
    return cases, summary


def write_private_cases(path: Path, cases: list[dict]) -> None:
    dest = path.expanduser().resolve()
    base = PRIVATE_ROOT.resolve()
    if not dest.is_relative_to(base) or dest.suffix != '.jsonl':
        raise PreflightError('HOLDOUT_OUTPUT_MUST_BE_PRIVATE_JSONL')
    if dest.exists():
        raise PreflightError('HOLDOUT_OUTPUT_EXISTS_NO_OVERWRITE')
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation avoids overwriting private evaluation artifacts.
    try:
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        raise PreflightError('HOLDOUT_OUTPUT_CREATE_FAILED') from None
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            for case in cases:
                out.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + '\n')
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        dest.unlink(missing_ok=True)
        raise PreflightError('HOLDOUT_OUTPUT_WRITE_FAILED') from None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--documents', type=Path, default=PRIVATE_ROOT/'holdout_labeled.jsonl')
    p.add_argument('--queries', type=Path, default=PRIVATE_ROOT/'holdout_retrieval_queries.jsonl')
    p.add_argument('--gold', type=Path, default=PRIVATE_ROOT/'holdout_retrieval_queries_gold.jsonl')
    p.add_argument('--expected-documents', type=int, default=187)
    p.add_argument('--expected-queries', type=int, default=88)
    p.add_argument('--check-only', action='store_true')
    p.add_argument('--out', type=Path, help='Private JSONL for eval_chat_mvp.py; never tracked by Git')
    args = p.parse_args(argv)
    if args.expected_documents < 1 or args.expected_queries < 1:
        p.error('expected counts must be positive')
    if args.check_only == bool(args.out):
        p.error('select exactly one: --check-only OR --out')
    try:
        cases, summary = build_cases(
            _load_jsonl(args.documents), _load_jsonl(args.queries), _load_jsonl(args.gold),
            expected_docs=args.expected_documents, expected_queries=args.expected_queries)
        if args.out:
            write_private_cases(args.out, cases)
            summary['private_cases_written'] = True
        else:
            summary['private_cases_written'] = False
        print('HOLDOUT_E2E_PREFLIGHT', json.dumps(summary, sort_keys=True), flush=True)
        return 0
    except PreflightError as exc:
        print('HOLDOUT_E2E_PREFLIGHT_FAILED', str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
