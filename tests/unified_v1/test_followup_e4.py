from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
from unified_v1.contracts import ContractError
from unified_v1.postgres import ExistingIndexReader,REQUIRED
from unified_v1.retrieval import PreparationResult,approve_experiment,QuerySpec
from unified_v1.database import IsolatedMigration

class ReadOnlyTests(unittest.TestCase):
    def reader(self,path):
        cur=MagicMock();conn=MagicMock();connector=MagicMock()
        connector.return_value.__enter__.return_value=conn
        conn.cursor.return_value.__enter__.return_value=cur
        def execute(sql,params=None):
            if sql.startswith('SHOW'):cur.fetchone.return_value=('on',)
            elif 'FROM pg_attribute' in sql:cur.fetchall.return_value=list(REQUIRED.items())
            elif 'count(*)' in sql:cur.fetchone.return_value=(187,187,1,'synthetic')
            elif 'string_agg' in sql:cur.fetchone.return_value=('stable-fingerprint',)
            elif 'pg_indexes' in sql:cur.fetchone.return_value=(False,)
            elif sql.startswith('WITH token_all'):cur.fetchall.return_value=[('d','합성 절차',[],[],.1)]
        cur.execute.side_effect=execute
        return ExistingIndexReader(path,expected_key_tag='synthetic',connector=connector),cur,connector

    def test_sql_read_only_pool_and_no_remote_approval_fabrication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'.env';path.write_text('')
            reader,cur,connect=self.reader(path)
            before=reader.metadata()
            p=PreparationResult('LOCAL_PREPARED','LINKED','NOT_APPROVED','합성 안내','synthetic','evidence_v2','query',coverage_verified=True)
            p,a=approve_experiment(p,corpus_id=reader.corpus_id,checker=lambda t:True)
            encoder=MagicMock();encoder.encode_query.return_value=[1.0]+[0.0]*1023
            for profile in ['own_v6','team_v3','hybrid_v1']:
                rows=reader.retrieve(QuerySpec(p,a),encoder=encoder,profile=profile)
                self.assertEqual(rows[0].doc_id,'d');self.assertEqual(rows[0].approval_state,'LEGACY_READ_ONLY')
            self.assertTrue(reader.metadata()['read_only'])
            self.assertIn('default_transaction_read_only=on',connect.call_args.kwargs['options'])
            statements=[call.args[0] for call in cur.execute.call_args_list]
            self.assertTrue(all(s.startswith(('SET TRANSACTION','SHOW ','SELECT ','WITH ')) for s in statements))
            self.assertTrue(any('tokens @>' in s and 'tokens &&' in s and 'region_keys &&' in s for s in statements))
            self.assertFalse(any(s.startswith(('CREATE','INSERT','UPDATE','DELETE','ALTER','DROP','TRUNCATE')) for s in statements))

    def test_readonly_and_key_mismatch_block_before_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'.env';path.write_text('')
            reader,cur,connector=self.reader(path)
            original=cur.execute.side_effect
            def wrong(sql,params=None):
                original(sql,params)
                if 'count(*)' in sql:cur.fetchone.return_value=(187,187,1,'wrong')
            cur.execute.side_effect=wrong
            with self.assertRaisesRegex(ContractError,'KEY_MISMATCH'):reader.metadata()

    def test_isolated_vector_schema_never_targets_production(self):
        migration=IsolatedMigration('aegis_unified_test_'+'f'*32)
        self.assertTrue(any('vector(1024)' in s for s in migration.statements()))
        self.assertEqual(sum('GIN' in s for s in migration.statements()),2)
        conn=MagicMock()
        with self.assertRaises(ContractError):migration.execute_isolated(conn,expected_database='pii_rag',production_database='pii_rag',confirm=migration.namespace)
        conn.cursor.assert_not_called()
