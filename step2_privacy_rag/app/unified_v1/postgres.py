"""Read-only legacy 187-row index reuse. No migration/re-ingest/DDL/DML API."""
from contextlib import contextmanager
from dataclasses import dataclass,replace
from pathlib import Path
from types import SimpleNamespace
import math
from settings import db_config,MANIFEST
from dataset_preparation import fixed_key,key_tag
from .contracts import ContractError,PreparationResult
from .retrieval import TOKEN,REGION,ENTITY,DOCID,validate_receipt,rank_documents

TABLE='rag.aegis_live_docs'
REQUIRED={'doc_id':'text','masked_text':'text','tokens':'text[]','region_keys':'text[]',
          'key_tag':'text','embedding':'vector(1024)'}

@dataclass(frozen=True,repr=False)
class LegacyDocument:
    doc_id:str
    preparation:object
    regions:tuple
    entity_ids:tuple=()
    name_lookup_keys:tuple=()

class ExistingIndexReader:
    def __init__(self,env_file, *, expected_rows=187,expected_key_tag=None,connector=None,
                 corpus_id='legacy_v6_local',policy_version='evidence_v2',entity_ids_by_doc=None):
        env_file=Path(env_file)
        if not env_file.is_absolute() or not env_file.is_file():raise ContractError('ENV_FILE_REQUIRED')
        if connector is None:
            import psycopg
            connector=psycopg.connect
        self._connector=connector
        self._db=db_config(env_file)
        self._db['options']='-c default_transaction_read_only=on -c statement_timeout=30000'
        self.key_tag=expected_key_tag or key_tag(fixed_key(env_file))
        self.expected_rows=expected_rows;self.policy_version=policy_version;self.corpus_id=corpus_id
        self.index_version='legacy_v6_readonly_koe5';self._fingerprint=None
        self.sidecar_entries={}
        self.entity_ids_by_doc=dict(entity_ids_by_doc or {})
        if any(not DOCID.fullmatch(did) or any(not ENTITY.fullmatch(e) for e in ids)
               for did,ids in self.entity_ids_by_doc.items()):raise ContractError('INVALID_ENTITY_SIDECAR')

    @contextmanager
    def _cursor(self):
        try:
            with self._connector(**self._db) as conn:
                with conn.cursor() as cur:
                    cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    cur.execute('SHOW transaction_read_only')
                    if cur.fetchone()[0]!='on':raise ContractError('DATABASE_READ_ONLY_REQUIRED')
                    yield cur
        except ContractError:raise
        except Exception:raise ContractError('DATABASE_READ_FAILED') from None

    def _validate(self,cur):
        cur.execute("SELECT a.attname,format_type(a.atttypid,a.atttypmod) FROM pg_attribute a WHERE a.attrelid='rag.aegis_live_docs'::regclass AND a.attnum>0 AND NOT a.attisdropped")
        types=dict(cur.fetchall())
        if any(types.get(k)!=v for k,v in REQUIRED.items()):raise ContractError('DATABASE_SCHEMA_MISMATCH')
        cur.execute('SELECT count(*),count(DISTINCT doc_id),count(DISTINCT key_tag),min(key_tag) FROM rag.aegis_live_docs')
        count,unique,tags,tag=cur.fetchone()
        if count!=self.expected_rows or unique!=count or tags!=1 or tag!=self.key_tag:
            raise ContractError('DATABASE_COUNT_OR_KEY_MISMATCH')
        cur.execute("SELECT md5(string_agg(md5(row(doc_id,masked_text,tokens,region_keys,key_tag,embedding)::text),'' ORDER BY doc_id)) FROM rag.aegis_live_docs")
        digest=cur.fetchone()[0]
        if self._fingerprint is not None and digest!=self._fingerprint:raise ContractError('DATABASE_CONTENT_CHANGED')
        cur.execute("SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE schemaname='rag' AND tablename='aegis_live_docs' AND indexdef ILIKE '%USING gin%tokens%')")
        gin=cur.fetchone()[0]
        return dict(rows=count,key_matches=True,schema_matches=True,row_fingerprint=digest,token_gin_present=gin,
                    configured_embedding_revision=MANIFEST['koe5']['revision'],
                    embedding_provenance='LEGACY_V6_MANIFEST_DECLARED_NOT_DB_ATTESTED',read_only=True)

    def metadata(self):
        with self._cursor() as cur:result=self._validate(cur)
        self._fingerprint=result['row_fingerprint']
        return result

    def snapshot_documents(self):
        with self._cursor() as cur:
            meta=self._validate(cur)
            cur.execute('SELECT doc_id,masked_text FROM rag.aegis_live_docs ORDER BY doc_id')
            rows=[dict(doc_id=did,masked_text=text) for did,text in cur.fetchall()]
        self._fingerprint=meta['row_fingerprint']
        return rows

    def attach_sidecar(self,sidecar,key):
        from .sidecar import validate_sidecar
        if self._fingerprint is None:self.metadata()
        payload=validate_sidecar(sidecar,index_fingerprint=self._fingerprint,key=key,key_tag=self.key_tag)
        self.sidecar_entries=payload['entries']
        self.entity_ids_by_doc={d:tuple(r['entity_ids']) for d,r in self.sidecar_entries.items()}

    @property
    def ids(self):
        with self._cursor() as cur:
            self._validate(cur)
            cur.execute('SELECT doc_id FROM rag.aegis_live_docs ORDER BY doc_id')
            ids=tuple(r[0] for r in cur.fetchall())
            if any(not DOCID.fullmatch(d) for d in ids):raise ContractError('INVALID_LEGACY_ID')
            return frozenset(ids)

    @property
    def index_fingerprint(self):return self._fingerprint

    def retrieve(self,query, *, encoder,profile='hybrid_v1',top_k=5):
        if profile not in {'own_v6','team_v3','hybrid_v1'} or type(top_k) is not int or not 1<=top_k<=5:raise ContractError('INVALID_RETRIEVAL_PROFILE_OR_LIMIT')
        if query.identity_unresolved:raise ContractError('CLARIFY_UNRESOLVED_IDENTITY')
        validate_receipt(query.preparation,query.approval,key_tag=self.key_tag,policy_version=self.policy_version,
                         input_kind='query',corpus_id=self.corpus_id)
        vector=encoder.encode_query(query.preparation)
        if len(vector)!=1024 or any(not math.isfinite(float(v)) for v in vector):raise ContractError('INVALID_EMBEDDING')
        vec='['+','.join(format(float(v),'.9g') for v in vector)+']'
        tokens=sorted(set(TOKEN.findall(query.preparation.sanitized_text)))
        regions=list(query.regions)
        if any(not REGION.fullmatch(r) for r in regions):raise ContractError('INVALID_REGION_KEY')
        with self._cursor() as cur:
            metadata=self._validate(cur)
            # ALL/ANY/Region plus all 187 dense rows preserves frozen V6 semantics.
            cur.execute('''WITH token_all AS (
                SELECT doc_id FROM rag.aegis_live_docs WHERE key_tag=%s AND tokens @> %s::text[] AND cardinality(%s::text[])>0
            ), token_any AS (
                SELECT doc_id FROM rag.aegis_live_docs WHERE key_tag=%s AND tokens && %s::text[]
            ), region_hits AS (
                SELECT doc_id FROM rag.aegis_live_docs WHERE key_tag=%s AND region_keys && %s::text[]
            ), dense AS (
                SELECT doc_id FROM rag.aegis_live_docs WHERE key_tag=%s ORDER BY embedding <=> %s::vector,doc_id LIMIT %s
            ), candidates AS (
                SELECT doc_id FROM token_all UNION SELECT doc_id FROM token_any UNION SELECT doc_id FROM region_hits UNION SELECT doc_id FROM dense
            ) SELECT d.doc_id,d.masked_text,d.tokens,d.region_keys,d.embedding <=> %s::vector AS distance
              FROM rag.aegis_live_docs d JOIN candidates c USING(doc_id) WHERE d.key_tag=%s ORDER BY distance,d.doc_id''',
                        (self.key_tag,tokens,tokens,self.key_tag,tokens,self.key_tag,regions,self.key_tag,vec,self.expected_rows,vec,self.key_tag))
            rows=cur.fetchall()
        self._fingerprint=metadata['row_fingerprint']
        docs=[];scores={}
        for did,text,stored_tokens,reg,distance in rows:
            if (not DOCID.fullmatch(did) or did in scores or not isinstance(text,str)
                    or set(stored_tokens)!=set(TOKEN.findall(text)) or any(not REGION.fullmatch(r) for r in reg)
                    or not math.isfinite(float(distance))):raise ContractError('INVALID_LEGACY_ROW')
            # Local read permission does not fabricate remote corpus approval.
            prep=SimpleNamespace(sanitized_text=text,approval_state='LEGACY_READ_ONLY')
            side=self.sidecar_entries.get(did,{})
            if side and side['masked_fingerprint']!=__import__('hashlib').sha256(text.encode()).hexdigest():raise ContractError('SIDECAR_CONTENT_MISMATCH')
            docs.append(LegacyDocument(did,prep,tuple(reg),tuple(self.entity_ids_by_doc.get(did,())),tuple(side.get('name_lookup_keys',()))))
            scores[did]=-float(distance)
        ranked=rank_documents(query,docs,scores,profile=profile,top_k=top_k,index_version=self.index_version,
                              key_tag=self.key_tag,policy_version=self.policy_version,dense_pool_limit=self.expected_rows)
        return tuple(replace(row,index_protection_policy='legacy_v6',query_protection_policy=self.policy_version) for row in ranked)
