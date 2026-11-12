"""Read-only index diagnostics use only counts and key equality."""
import unittest
from unittest.mock import MagicMock, patch
from core import LivePipeline
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

    def test_wrong_key(self):
        result = self.check((187, 1, 'another-tag'))
        self.assertEqual(result['status'], 'MISMATCH')
        self.assertFalse(result['key_matches'])

if __name__ == '__main__':
    unittest.main()
