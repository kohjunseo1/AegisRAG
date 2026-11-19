from dataclasses import replace
import unittest
from unified_v1.retrieval import IndexedDocument,QuerySpec
from test_p4 import approved,index,TOKEN1,REGION

class RetrievalParityTests(unittest.TestCase):
    def test_own_v6_counts_multiple_region_keys_like_frozen_code(self):
        second='region_v1_'+'d'*24
        ix=index()
        for did,regs in [('both',(REGION,second)),('single',(REGION,))]:
            p,a=approved(TOKEN1);ix.add(IndexedDocument(did,p,a,regs))
        p,a=approved(TOKEN1,'query');q=QuerySpec(p,a,(REGION,second))
        rows=ix.retrieve(q,profile='own_v6',dense_scorer=lambda q,d:{'single':1,'both':0})
        self.assertEqual(rows[0].doc_id,'both')

    def test_drop_requires_disposable_database_and_schema_provenance(self):
        from unittest.mock import MagicMock
        from unified_v1.database import IsolatedMigration
        from unified_v1.contracts import ContractError
        plan=IsolatedMigration('aegis_unified_test_'+'1'*32);conn=MagicMock()
        with self.assertRaises(ContractError):plan.drop_isolated(conn,expected_database='prod',production_database='prod',confirm=plan.namespace)
        conn.cursor.assert_not_called()
        cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect=[('temporary_db',),('unknown_schema',)]
        with self.assertRaisesRegex(ContractError,'PROVENANCE'):plan.drop_isolated(conn,expected_database='temporary_db',production_database='prod',confirm=plan.namespace)
        self.assertFalse(any(str(c.args[0]).startswith('DROP ') for c in cur.execute.call_args_list))

    def test_disposable_schema_creation_comment_is_valid_unescaped_sql(self):
        from unittest.mock import MagicMock
        from unified_v1.database import IsolatedMigration
        plan=IsolatedMigration('aegis_unified_test_'+'2'*32);conn=MagicMock()
        cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect=[('temporary_db',),(True,)]
        plan.execute_isolated(conn,expected_database='temporary_db',production_database='prod',confirm=plan.namespace)
        last=cur.execute.call_args.args[0]
        self.assertEqual(last,f"COMMENT ON SCHEMA \"{plan.namespace}\" IS 'aegisrag_unified_disposable_v1'")
