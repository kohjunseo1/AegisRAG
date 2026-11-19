"""Reviewable isolated migration plans. No DB connection or autonomous writes."""
from dataclasses import dataclass
import re
from .contracts import ContractError

@dataclass(frozen=True)
class IsolatedMigration:
    namespace: str

    def __post_init__(self):
        if not re.fullmatch(r'aegis_unified_test_[0-9a-f]{32}',self.namespace):
            raise ContractError('DATABASE_TARGET_FORBIDDEN')

    def statements(self):
        # Validated schema identifier only. Production rag.* never referenced.
        s=self.namespace
        return (f'CREATE SCHEMA "{s}"',
                f'CREATE TABLE "{s}".documents (doc_id text PRIMARY KEY, masked_text text NOT NULL, '
                "tokens text[] NOT NULL, region_keys text[] NOT NULL, key_tag text NOT NULL, "
                "policy_version text NOT NULL, index_version text NOT NULL, "
                "approval_state text NOT NULL CHECK (approval_state IN ('HUMAN_APPROVED', 'AUTO_APPROVED')), "
                'embedding vector(1024) NOT NULL)',
                f'CREATE INDEX ON "{s}".documents USING GIN(tokens)',
                f'CREATE INDEX ON "{s}".documents USING GIN(region_keys)')

    def execute(self,*args,**kwargs):
        # No verified disposable PostgreSQL environment in this autonomous run.
        raise ContractError('DATABASE_INTEGRATION_BLOCKED_NO_VERIFIED_ISOLATION')

    def execute_isolated(self,conn, *, expected_database,production_database,confirm):
        if (not expected_database or not production_database or expected_database==production_database
                or confirm!=self.namespace):
            raise ContractError('ISOLATED_DATABASE_CONFIRMATION_REQUIRED')
        with conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            if cur.fetchone()[0]!=expected_database:raise ContractError('DATABASE_TARGET_FORBIDDEN')
            cur.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")
            if not cur.fetchone()[0]:raise ContractError('PGVECTOR_REQUIRED')
            for statement in self.statements():cur.execute(statement)
            cur.execute(f"""COMMENT ON SCHEMA "{self.namespace}" IS 'aegisrag_unified_disposable_v1'""")

    def drop_isolated(self,conn, *, expected_database,production_database,confirm):
        if (not expected_database or not production_database or expected_database==production_database or confirm!=self.namespace):
            raise ContractError('ISOLATED_DATABASE_CONFIRMATION_REQUIRED')
        with conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            if cur.fetchone()[0]!=expected_database:raise ContractError('DATABASE_TARGET_FORBIDDEN')
            cur.execute("SELECT obj_description(oid,'pg_namespace') FROM pg_namespace WHERE nspname=%s",(self.namespace,))
            row=cur.fetchone()
            if not row or row[0]!='aegisrag_unified_disposable_v1':raise ContractError('ISOLATED_SCHEMA_PROVENANCE_REQUIRED')
            cur.execute(f'DROP SCHEMA "{self.namespace}" CASCADE')
