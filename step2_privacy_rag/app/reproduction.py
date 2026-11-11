"""Non-destructive, content-addressed V6 loading and evaluation."""
import hashlib
import json
import re
from pathlib import Path

from core import PII, PreparedQuery, LivePipeline
from dataset_preparation import fixed_key, key_tag, private_write
from settings import ROOT, MANIFEST, db_config, load_embedding_model, table_identifier

PIPELINE = 'step1-v5-nonlinked-v2-gate-v4-address-v2.2-region-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def artifact(path, env_file):
    data = json.loads(path.read_text())
    if data.get('pipeline') != PIPELINE or data.get('format') != 'aegisrag-local-embedding-eval-v1':
        raise ValueError('INVALID_V6_ARTIFACT')
    if data.get('hmac_key_tag') != key_tag(fixed_key(env_file)):
        raise ValueError('HMAC_KEY_MISMATCH')
    docs = data['documents']
    if not docs or len({d['doc_id'] for d in docs}) != len(docs):
        raise ValueError('INVALID_DOCUMENT_IDS')
    for d in docs:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', d['doc_id']) or not isinstance(d['text'], str):
            raise ValueError('INVALID_DOCUMENT')
        if not all(re.fullmatch(r'region_v1_[0-9a-f]{24}', k) for k in d['region_keys']):
            raise ValueError('INVALID_REGIONS')
    return data


def provenance(data):
    # Gold and queries never participate in indexing, naming, or embedding.
    policy_files = sorted((ROOT/'step2_privacy_rag/app').glob('*.py'))
    policy_files += sorted((ROOT/'step1_pii_detector/src/pii_detector').glob('*.py'))
    policy_files += [ROOT/'requirements.lock', ROOT/'config/model_manifest.json']
    code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in policy_files}
    return dict(format='aegisrag-repro-v1', documents_sha256=digest(data['documents']),
                key_tag=data['hmac_key_tag'], pipeline=PIPELINE, embedding=MANIFEST['koe5'],
                step1=json.loads((ROOT/MANIFEST['step1_manifest']).read_text()),
                code_sha256=digest(code), rows=len(data['documents']))


def target(data):
    return 'rag.aegis_repro_' + digest(provenance(data))[:20]


def validate_target(name):
    if not re.fullmatch(r'rag\.aegis_repro_[a-f0-9]{20}', name):
        raise ValueError('WRITE_TARGET_MUST_BE_ISOLATED_REPRO_TABLE')
    return table_identifier(name)


def vector(value):
    return '[' + ','.join(format(float(x), '.9g') for x in value) + ']'


def encode_docs(data, device):
    import numpy as np
    model = load_embedding_model(device)
    vectors = np.asarray(model.encode(['passage: '+d['text'] for d in data['documents']],
        normalize_embeddings=True, convert_to_numpy=True, batch_size=4,
        show_progress_bar=False), dtype=np.float32)
    if vectors.shape != (len(data['documents']), 1024) or not np.isfinite(vectors).all():
        raise ValueError('INVALID_DOCUMENT_EMBEDDINGS')
    return vectors


def ingest(conn, data, vectors, name, confirmation):
    """Atomic insert-once. Same input verifies existing rows; never replaces rows."""
    from psycopg import sql
    import numpy as np
    table = validate_target(name)
    if name != target(data) or confirmation != name:
        raise ValueError('CONFIRM_CONTENT_ADDRESSED_TARGET')
    meta = json.dumps(provenance(data), sort_keys=True)
    expected = [(d['doc_id'], d['text'], sorted(set(PII.findall(d['text']))),
                 d['region_keys'], data['hmac_key_tag'], vector(v))
                for d, v in zip(data['documents'], vectors)]
    if len(expected) != len(data['documents']):
        raise ValueError('EMBEDDING_COUNT_MISMATCH')
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (name,))
        cur.execute('SELECT to_regclass(%s)', (name,))
        exists = cur.fetchone()[0] is not None
        if exists:
            cur.execute('SELECT obj_description(to_regclass(%s))', (name,))
            if cur.fetchone()[0] != meta:
                raise ValueError('EXISTING_TABLE_PROVENANCE_MISMATCH')
        else:
            cur.execute(sql.SQL('''CREATE TABLE {} (
                doc_id text PRIMARY KEY, masked_text text NOT NULL, tokens text[] NOT NULL,
                region_keys text[] NOT NULL, key_tag text NOT NULL, embedding vector(1024) NOT NULL
            )''').format(table))
            cur.executemany(sql.SQL('INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s::vector)').format(table), expected)
            cur.execute(sql.SQL('COMMENT ON TABLE {} IS {}').format(table, sql.Literal(meta)))
            cur.execute(sql.SQL('GRANT SELECT ON {} TO rag_readonly').format(table))
        cur.execute(sql.SQL('SELECT doc_id,masked_text,tokens,region_keys,key_tag,embedding::text FROM {} ORDER BY doc_id').format(table))
        rows = cur.fetchall()
        ordered = sorted(expected)
        if len(rows) != len(ordered):
            raise ValueError('REPRO_ROW_COUNT_MISMATCH')
        for got, want in zip(rows, ordered):
            if got[:5] != want[:5] or not np.array_equal(
                np.asarray(json.loads(got[5]), dtype=np.float32),
                np.asarray(json.loads(want[5]), dtype=np.float32)):
                raise ValueError('REPRO_CONTENT_MISMATCH')
    return 'VERIFIED_EXISTING' if exists else 'CREATED'


def evaluate(data, table, env_file, device='cpu', expected_baseline=False):
    pipeline = LivePipeline(ROOT, device, env_file)
    pipeline.db = db_config(env_file)
    pipeline.secret = fixed_key(env_file)
    pipeline.tag = key_tag(pipeline.secret)
    pipeline.table = table_identifier(table)
    pipeline.expected_rows = len(data['documents'])
    ids = {d['doc_id'] for d in data['documents']}
    gold_rows = data['gold_for_evaluation_only']
    qs = data['queries']
    if len({q['qid'] for q in qs}) != len(qs) or {q['qid'] for q in qs} != {g['qid'] for g in gold_rows} or len(gold_rows) != len(qs):
        raise ValueError('INVALID_GOLD_QUERY_IDS')
    gold = {g['qid']: set(g['relevant_doc_ids']) for g in gold_rows}
    neutral = {g['qid']: set(g.get('neutral_doc_ids', [])) for g in gold_rows}
    hits1 = hits5 = 0
    recalls = []; full_gold_recalls = []; reciprocal = []; runs = {}
    for q in qs:
        relevant = gold[q['qid']]
        if q['text'] is None or not relevant.intersection(ids):
            continue
        rows = pipeline.retrieve(PreparedQuery(q['text'], set(PII.findall(q['text'])), set(q['region_keys'])), top_k=None)
        ranked = [r['doc_id'] for r in rows]
        runs[q['qid']] = ranked
        # Preserve the historical evaluator: neutral documents are excluded ONLY while scoring.
        ranked = [did for did in ranked if did not in neutral[q['qid']]]
        hits1 += bool(ranked and ranked[0] in relevant)
        hits5 += bool(set(ranked[:5]) & relevant)
        found = len(set(ranked[:5]) & relevant)
        # V6 baseline recall measures recall among indexable Gold documents.
        recalls.append(found / len(relevant.intersection(ids)))
        full_gold_recalls.append(found / len(relevant))
        reciprocal.append(next((1/i for i, d in enumerate(ranked, 1) if d in relevant), 0))
    n = len(recalls)
    if n == 0:
        raise ValueError('NO_EVALUABLE_QUERIES')
    report = dict(eligible=n, hit1=hits1, hit5=hits5, recall5=round(sum(recalls)/n, 4),
                  mrr=round(sum(reciprocal)/n, 4), recall5_full_gold=round(sum(full_gold_recalls)/n, 4), documents=len(ids), ready_queries=sum(q['text'] is not None for q in qs))
    if expected_baseline:
        expected = dict(eligible=81, hit1=73, hit5=79, recall5=.8802, mrr=.9340, documents=177, ready_queries=85)
        if {k: report[k] for k in expected} != expected:
            raise ValueError('BASELINE_METRICS_MISMATCH:'+json.dumps(report, sort_keys=True))
    return report, runs
