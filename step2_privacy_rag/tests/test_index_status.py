"""Read-only index diagnostics use only counts and key equality."""
import unittest
from unittest.mock import MagicMock, patch
import psycopg
from core import LivePipeline, db_failure_code, PipelineError
from settings import table_identifier

class IndexStatus(unittest.TestCase):
    def setUp(self):
        self.pipeline = LivePipeline.__new__(LivePipeline)
        self.pipeline.db = {'host':'127.0.0.1','dbname':'synthetic'}
        self.pipeline.table_name = 'rag.synthetic'
        self.pipeline.table = table_identifier(self.pipeline.table_name)
        self.pipeline.tag = 'secret-tag-not-returned'
        self.pipeline.expected_rows = 187

    def check(self, row):
        with patch('psycopg.connect') as connect:
            connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = row
            status = self.pipeline.index_status()
        self.assertNotIn(self.pipeline.tag, str(status))
        return status

    def test_ready(self):
        result = self.check((187, 1, self.pipeline.tag))
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['rows'], 187)

    def test_wrong_table_count(self):
        result = self.check((177, 1, self.pipeline.tag))
        self.assertEqual(result['status'], 'MISMATCH')

    def test_categorize_postgres_errors_without_private_details(self):
        cases = [
            (psycopg.errors.UndefinedTable, 'V6_DB_TABLE_NOT_FOUND'),
            (psycopg.errors.InsufficientPrivilege, 'V6_DB_ACCESS_DENIED'),
            (psycopg.errors.InvalidPassword, 'V6_DB_AUTH_FAILED'),
            (psycopg.errors.InvalidCatalogName, 'V6_DB_DATABASE_NOT_FOUND'),
            (psycopg.errors.UndefinedColumn, 'V6_DB_SCHEMA_MISMATCH'),
            (psycopg.OperationalError, 'V6_DB_CONNECTION_FAILED'),
        ]
        for klass, expected in cases:
            with self.subTest(klass=klass):
                code = db_failure_code(klass('PRIVATE_PASSWORD_OR_SQL'))
                self.assertEqual(code, expected)
                self.assertNotIn('PRIVATE', code)

    def test_index_status_maps_missing_table_safely(self):
        with patch('psycopg.connect') as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.execute.side_effect = psycopg.errors.UndefinedTable('PRIVATE DATA')
            with self.assertRaisesRegex(PipelineError, '^V6_DB_TABLE_NOT_FOUND$'):
                self.pipeline.index_status()

    def test_wrong_key(self):
        result = self.check((187, 1, 'another-tag'))
        self.assertEqual(result['status'], 'MISMATCH')
        self.assertFalse(result['key_matches'])

if __name__ == '__main__':
    unittest.main()
