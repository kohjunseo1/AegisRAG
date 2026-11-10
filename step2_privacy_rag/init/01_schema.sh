#!/usr/bin/env bash
set -euo pipefail

: "${RAG_PASSWORD:?Missing RAG_PASSWORD}"
: "${RAG_READONLY_PASSWORD:?Missing RAG_READONLY_PASSWORD}"
: "${VAULT_PASSWORD:?Missing VAULT_PASSWORD}"

if [[ ! "${EMBED_DIM:-}" =~ ^[0-9]+$ ]]; then
    echo "EMBED_DIM must be an integer" >&2
    exit 1
fi

psql -X -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" <<'SQL'

\getenv dbname POSTGRES_DB
\getenv ingest_pass RAG_PASSWORD
\getenv readonly_pass RAG_READONLY_PASSWORD
\getenv vault_pass VAULT_PASSWORD
\getenv dim EMBED_DIM

BEGIN;

-- 1. pgvector 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. 역할별 DB 계정 생성
CREATE ROLE rag_ingest LOGIN PASSWORD :'ingest_pass';
CREATE ROLE rag_readonly LOGIN PASSWORD :'readonly_pass';
CREATE ROLE vault_user LOGIN PASSWORD :'vault_pass';

REVOKE CONNECT ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname"
    TO rag_ingest, rag_readonly, vault_user;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- 3. 저장 영역 분리
CREATE SCHEMA rag AUTHORIZATION rag_ingest;
CREATE SCHEMA vault AUTHORIZATION vault_user;

REVOKE ALL ON SCHEMA rag FROM PUBLIC;
REVOKE ALL ON SCHEMA vault FROM PUBLIC;
GRANT USAGE ON SCHEMA rag TO rag_readonly;

-- 4. 가명 문서 및 임베딩 테이블
CREATE TABLE rag.chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    chunk_idx INTEGER NOT NULL,
    doc_type TEXT,
    masked_text TEXT NOT NULL,
    tokens TEXT[] NOT NULL DEFAULT '{}',
    embedding vector(:dim) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE rag.chunks OWNER TO rag_ingest;
GRANT SELECT ON rag.chunks TO rag_readonly;

CREATE INDEX chunks_tokens_gin
    ON rag.chunks USING GIN (tokens);

CREATE INDEX chunks_embedding_hnsw
    ON rag.chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX chunks_doc_id
    ON rag.chunks (doc_id);

-- 5. 원문 개인정보 매핑 테이블
CREATE TABLE vault.token_map (
    token TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    normalized TEXT NOT NULL,
    originals TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE vault.token_map OWNER TO vault_user;

COMMIT;
SQL
