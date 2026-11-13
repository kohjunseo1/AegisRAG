#!/usr/bin/env python3
"""FIRST-TIME bootstrap for ONLY an isolated AegisRAG blind test database.

Do not use for production or the existing 187-document live DB. Hard guards
require the dedicated local DB name, port, host and exact confirmation phrase.
Inputs are already privacy-approved; no raw/unapproved documents are accepted.
"""
from __future__ import annotations
import argparse,json,re,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'step2_privacy_rag/app'))
CONFIRM='BOOTSTRAP_BLIND_TEST_DB'

def verify_test_target(db:dict,confirmation:str)->None:
    if (confirmation != CONFIRM or db.get('dbname')!='aegis_blind_test'
        or db.get('host') not in ('127.0.0.1','localhost')
        or int(db.get('port',0))!=5544):
        raise ValueError('REFUSE_NON_ISOLATED_TEST_DB')

def bootstrap(input_file:Path,env_file:Path,device:str,confirm:str)->dict:
    from settings import db_config
    from dataset_preparation import fixed_key,key_tag
    from live_index import validate_approved_batch
    from reproduction import encode_docs,vector
    import psycopg
    from psycopg import sql
    db=db_config(env_file)
    verify_test_target(db,confirm)
    rows=validate_approved_batch(input_file,env_file)
    if not rows:raise ValueError('NO_APPROVED_DOCUMENTS')
    tag=key_tag(fixed_key(env_file))
    total_started=time.perf_counter()
    embed_ms=0.0
    with psycopg.connect(**db) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT current_database(), to_regclass(%s)',('rag.aegis_live_docs',))
            current,exists=cur.fetchone()
            if current!='aegis_blind_test' or exists is not None:
                raise ValueError('TEST_DB_MUST_BE_UNINITIALIZED')
            cur.execute('SELECT to_regnamespace(%s)',('rag',))
            if cur.fetchone()[0] is None:
                raise ValueError('TEST_DB_SCHEMA_NOT_READY')
            # Private model invocation is local and happens before any DB DDL.
            embed_started=time.perf_counter()
            vectors=encode_docs({'documents':rows},device)
            embed_ms=(time.perf_counter()-embed_started)*1000
            if len(vectors)!=len(rows):
                raise ValueError('TEST_EMBEDDING_COUNT_MISMATCH')
            cur.execute('''CREATE TABLE rag.aegis_live_docs (
                doc_id TEXT PRIMARY KEY,
                masked_text TEXT NOT NULL,
                tokens TEXT[] NOT NULL DEFAULT '{}',
                region_keys TEXT[] NOT NULL DEFAULT '{}',
                key_tag TEXT NOT NULL,
                embedding vector(1024) NOT NULL
            )''')
            records=[(row['doc_id'],row['text'],row['tokens'],row['region_keys'],
                      tag,vector(v)) for row,v in zip(rows,vectors)]
            cur.executemany('''INSERT INTO rag.aegis_live_docs
                (doc_id,masked_text,tokens,region_keys,key_tag,embedding)
                VALUES (%s,%s,%s,%s,%s,%s::vector)''',records)
            cur.execute('CREATE INDEX idx_blind_tokens ON rag.aegis_live_docs USING gin(tokens)')
            cur.execute('CREATE INDEX idx_blind_region ON rag.aegis_live_docs USING gin(region_keys)')
            cur.execute('SELECT COUNT(*),COUNT(DISTINCT doc_id),COUNT(DISTINCT key_tag) FROM rag.aegis_live_docs')
            count,unique_ids,tags=cur.fetchone()
            if count!=len(rows) or unique_ids!=count or tags!=1:
                raise ValueError('TEST_DB_BOOTSTRAP_INTEGRITY_FAILED')
        # psycopg context commits only when all checks pass.
    total_ms=(time.perf_counter()-total_started)*1000
    return {'database':'aegis_blind_test','port':5544,'table':'rag.aegis_live_docs',
            'inserted':len(rows),'key_tag':tag,'production_187_untouched':True,
            'timings_ms':{'embedding_model_load_and_batch_encoding':round(embed_ms,2),
                          'db_connect_plus_embedding_plus_transaction':round(total_ms,2),
                          'db_transaction_excluding_embedding_approx':round(max(0,total_ms-embed_ms),2)}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path,help='approved batch from dataset_intake export')
    p.add_argument('--env',required=True,type=Path,help='isolated DB env, not root .env')
    p.add_argument('--device',choices=('cpu','mps'),default='cpu')
    p.add_argument('--confirm',required=True)
    a=p.parse_args(argv)
    try:
        result=bootstrap(a.input,a.env,a.device,a.confirm)
        print('BLIND_DB_BOOTSTRAP',json.dumps(result,sort_keys=True),flush=True)
        return 0
    except Exception as exc:
        safe=str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'[A-Z][A-Z0-9_]+',str(exc)) else type(exc).__name__
        print('BLIND_BOOTSTRAP_FAILED',safe,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
