"""Configuration adapters; never mutate the team's environment or credentials."""
from pathlib import Path
import json
import os
import re

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / 'config/model_manifest.json').read_text())


def settings(env_file=None):
    from dotenv import dotenv_values
    values = dotenv_values(env_file or ROOT / '.env')
    return lambda name, default='': os.environ.get(name) or values.get(name) or default


def db_config(env_file=None):
    get = settings(env_file)
    host = get('DB_HOST', 'localhost')
    return dict(host='127.0.0.1' if host == 'localhost' else host,
                port=int(get('DB_PORT', '5433')), dbname=get('POSTGRES_DB', 'pii_rag'),
                user=get('DB_USER', 'rag_ingest'), password=get('RAG_PASSWORD'),
                sslmode=get('DB_SSLMODE', 'prefer'), connect_timeout=8)


def table_identifier(name):
    from psycopg import sql
    if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}\.[a-z_][a-z0-9_]{0,62}', name):
        raise ValueError('INVALID_TABLE_IDENTIFIER')
    return sql.Identifier(*name.split('.'))


def load_embedding_model(device='cpu'):
    from sentence_transformers import SentenceTransformer
    cfg = MANIFEST['koe5']
    model = SentenceTransformer(cfg['repo'], revision=cfg['revision'], device=device,
                                trust_remote_code=False)
    model.max_seq_length = cfg['max_seq_length']
    if model.get_sentence_embedding_dimension() != cfg['dimension']:
        raise ValueError('INVALID_MODEL_DIMENSION')
    return model
