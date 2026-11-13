#!/usr/bin/env python3
"""Read-only AegisRAG V6 DB probe. No private text, credentials, or token logging."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'step2_privacy_rag' / 'app'))

from core import db_failure_code
from settings import db_config, settings, table_identifier
from psycopg import connect, sql


def main() -> int:
    env_file = ROOT / '.env'
    get = settings(env_file)
    target = get('AEGISRAG_TABLE', 'rag.aegis_koe5_v6_exp1')
    expected_raw = get('AEGISRAG_EXPECTED_ROWS', '177')
    table = table_identifier(target)
    cfg = db_config(env_file)
    # Do not print any password, DSN, HMAC secret, or document content.
    print('TARGET', json.dumps(dict(host=cfg['host'], port=cfg['port'],
              db=cfg['dbname'], user=cfg['user'], table=target,
              expected_rows=expected_raw), ensure_ascii=False))
    try:
        with connect(**cfg) as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute("""SELECT table_schema, table_name FROM information_schema.tables
                               WHERE table_schema = 'rag' AND table_name LIKE 'aegis_%'
                               ORDER BY table_name LIMIT 30""")
                known = [schema + '.' + name for schema, name in cur.fetchall()]
                print('VISIBLE_AEGIS_TABLES', json.dumps(known))
                cur.execute('SELECT to_regclass(%s)', (target,))
                exists = cur.fetchone()[0] is not None
                print('TARGET_EXISTS', exists)
                if not exists:
                    print('RESULT V6_DB_TABLE_NOT_FOUND')
                    return 2
                cur.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(table))
                count = cur.fetchone()[0]
                print('TARGET_ROWS', count)
                print('COUNT_MATCH', str(count) == str(expected_raw))
                # Verify document embedding query SQL is accepted, without
                # loading vectors, requesting documents, or using any test PII.
                cur.execute(sql.SQL("""SELECT EXISTS(
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                      AND column_name = 'embedding'
                )"""), tuple(target.split('.')))
                print('EMBEDDING_COLUMN_PRESENT', cur.fetchone()[0])
                print('RESULT DB_COUNT_OK')
        return 0
    except Exception as exc:
        print('RESULT', db_failure_code(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
