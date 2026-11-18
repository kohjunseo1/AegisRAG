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
                'embedding real[] CHECK (array_length(embedding, 1) = 1024))',
                f'CREATE INDEX ON "{s}".documents USING GIN(tokens)')

    def execute(self,*args,**kwargs):
        # No verified disposable PostgreSQL environment in this autonomous run.
        raise ContractError('DATABASE_INTEGRATION_BLOCKED_NO_VERIFIED_ISOLATION')
