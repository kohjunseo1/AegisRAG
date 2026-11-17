"""Single-table live index: promotion and approved-only incremental upserts.

This deliberately does not handle raw or REVIEW_PENDING documents. The local
administrator must first generate locally pseudonymized, revalidated records.
It does not authorize external transfer or act as evidence of safe PII coverage.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core import PII
from dataset_preparation import fixed_key, key_tag
from hcx import redact_residual_pii
from reproduction import encode_docs, vector
from settings import table_identifier

LIVE_TABLE = 'rag.aegis_live_docs'
SNAPSHOT = re.compile(r'rag\.aegis_repro_[0-9a-f]{20}\Z')
DOC_ID = re.compile(r'[A-Za-z0-9_-]{1,60}\Z')
REGION = re.compile(r'region_v1_[0-9a-f]{24}\Z')
ALLOWED_APPROVAL = {'AUTO_READY', 'HUMAN_APPROVED'}


def validate_approved_batch(path: Path, env_file: Path) -> list[dict]:
    """Reject raw and unapproved documents; never print their content."""
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        raise ValueError('INVALID_APPROVED_BATCH_FILE') from None
    if (not isinstance(payload, dict) or
            set(payload) != {'format', 'hmac_key_tag', 'documents'} or
            payload['format'] != 'aegisrag-live-approved-v1' or
            payload['hmac_key_tag'] != key_tag(fixed_key(env_file))):
        raise ValueError('INVALID_APPROVED_BATCH')
    rows = payload['documents']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError('INVALID_APPROVED_BATCH_SIZE')
    ids = set()
    clean = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'doc_id', 'text', 'region_keys', 'approval_state'}:
            raise ValueError('INVALID_APPROVED_DOCUMENT')
        did, text, regions = row['doc_id'], row['text'], row['region_keys']
        if not isinstance(did, str) or not DOC_ID.fullmatch(did) or did in ids:
            raise ValueError('INVALID_APPROVED_DOC_IDS')
        ids.add(did)
        if row['approval_state'] not in ALLOWED_APPROVAL:
            raise ValueError('UNAPPROVED_DOCUMENT')
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise ValueError('INVALID_APPROVED_TEXT')
        if not isinstance(regions, list) or not all(isinstance(k, str) and REGION.fullmatch(k) for k in regions):
            raise ValueError('INVALID_APPROVED_REGIONS')
        # Covered common PII patterns are a conservative block, not a privacy certificate.
        _, residues = redact_residual_pii(text)
        if any(residues.values()):
            raise ValueError('RESIDUAL_PII_REQUIRES_REVIEW')
        clean.append({'doc_id': did, 'text': text,
                      'region_keys': sorted(set(regions)),
                      'tokens': sorted(set(PII.findall(text)))})
    return clean


def promote_snapshot(conn, source: str, expected_rows: int, tag: str, confirm: str) -> str:
    """Rename the existing physical table in one transaction. No copy/re-embedding."""
    from psycopg import sql

    if not isinstance(source, str) or not SNAPSHOT.fullmatch(source):
        raise ValueError('INVALID_LIVE_SOURCE')
    if source == LIVE_TABLE or confirm != 'PROMOTE_TO_' + LIVE_TABLE:
        raise ValueError('CONFIRM_LIVE_PROMOTION')
    if type(expected_rows) is not int or expected_rows <= 0:
        raise ValueError('INVALID_EXPECTED_ROWS')
    table = table_identifier(source)
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (LIVE_TABLE,))
        cur.execute('SELECT to_regclass(%s), to_regclass(%s)', (source, LIVE_TABLE))
        original, live = cur.fetchone()
        if live is not None:
            raise ValueError('LIVE_TABLE_ALREADY_EXISTS')
        if original is None:
            raise ValueError('SOURCE_TABLE_NOT_FOUND')
        cur.execute(sql.SQL('SELECT COUNT(*), COUNT(DISTINCT doc_id), COUNT(DISTINCT key_tag), MIN(key_tag) FROM {}').format(table))
        count, unique_ids, num_tags, stored_tag = cur.fetchone()
        if count != expected_rows or unique_ids != count or num_tags != 1 or stored_tag != tag:
            raise ValueError('SOURCE_INTEGRITY_FAILED')
        cur.execute(sql.SQL('ALTER TABLE {} RENAME TO {}').format(
            table, sql.Identifier('aegis_live_docs')))
        # The old immutable content-addressed snapshot comment cannot describe mutable data.
        cur.execute(sql.SQL('COMMENT ON TABLE {} IS {}').format(
            table_identifier(LIVE_TABLE), sql.Literal(
                'aegis-live-v1; seeded_from=' + source + '; initial_rows=' + str(count))))
    return 'PROMOTED_WITHOUT_COPY'


def incremental_upsert(conn, rows: list[dict], tag: str, device: str = 'cpu', confirmed: bool = False) -> dict:
    """Only changed approved docs are embedded, then atomically upserted."""
    from psycopg import sql
    if not confirmed:
        raise ValueError('CONFIRM_LIVE_UPSERT')
    table = table_identifier(LIVE_TABLE)
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (LIVE_TABLE,))
        cur.execute('SELECT to_regclass(%s)', (LIVE_TABLE,))
        if cur.fetchone()[0] is None:
            raise ValueError('LIVE_TABLE_NOT_INITIALIZED')
        cur.execute(sql.SQL('SELECT COUNT(*), COUNT(DISTINCT key_tag), MIN(key_tag) FROM {}').format(table))
        count, tag_count, stored_tag = cur.fetchone()
        if count <= 0 or tag_count != 1 or stored_tag != tag:
            raise ValueError('LIVE_HMAC_KEY_MISMATCH')
        ids = [d['doc_id'] for d in rows]
        cur.execute(sql.SQL('''SELECT doc_id, masked_text, tokens, region_keys
                              FROM {} WHERE doc_id = ANY(%s)''').format(table), (ids,))
        old = {did: (text, sorted(tokens), sorted(region_keys))
               for did, text, tokens, region_keys in cur.fetchall()}
        changed = [d for d in rows if old.get(d['doc_id']) != (
            d['text'], d['tokens'], d['region_keys'])]
        inserted = sum(d['doc_id'] not in old for d in changed)
        if changed:
            embeddings = encode_docs({'documents': changed}, device)
            values = [(d['doc_id'], d['text'], d['tokens'], d['region_keys'],
                       tag, vector(embedding))
                      for d, embedding in zip(changed, embeddings)]
            if len(values) != len(changed):
                raise ValueError('EMBEDDING_COUNT_MISMATCH')
            cur.executemany(sql.SQL('''INSERT INTO {}
                (doc_id, masked_text, tokens, region_keys, key_tag, embedding)
                VALUES (%s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (doc_id) DO UPDATE SET
                    masked_text = EXCLUDED.masked_text,
                    tokens = EXCLUDED.tokens,
                    region_keys = EXCLUDED.region_keys,
                    key_tag = EXCLUDED.key_tag,
                    embedding = EXCLUDED.embedding''').format(table), values)
        return {'inserted': inserted, 'updated': len(changed) - inserted,
                'unchanged': len(rows) - len(changed), 'previous_rows': count,
                'resulting_rows': count + inserted}
