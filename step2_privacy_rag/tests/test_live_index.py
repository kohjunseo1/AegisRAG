"""Synthetic-only tests for one-table live index behavior."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core import LivePipeline
from live_index import incremental_upsert, validate_approved_batch, promote_snapshot, LIVE_TABLE
from smoke import FIXTURE_KEY
from dataset_preparation import key_tag


class LiveIndexContracts(unittest.TestCase):
    def setUp(self):
        self.tag = key_tag(FIXTURE_KEY)
        self.row = dict(doc_id='S001', text='합성 교육 공지입니다.', region_keys=[],
                        tokens=[], approval_state='AUTO_READY')

    def test_approved_batch_rejects_review_pending_and_residual_pii(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'batch.json'
            original = {k: self.row[k] for k in ('doc_id', 'text', 'region_keys', 'approval_state')}
            obj = dict(format='aegisrag-live-approved-v1',
                       hmac_key_tag=self.tag, documents=[original])
            with patch('live_index.fixed_key', return_value=FIXTURE_KEY):
                p.write_text(json.dumps(obj))
                self.assertEqual(validate_approved_batch(p, Path('/fake/.env'))[0]['doc_id'],'S001')
                original['approval_state'] = 'REVIEW_PENDING'
                p.write_text(json.dumps(obj))
                with self.assertRaisesRegex(ValueError, 'UNAPPROVED_DOCUMENT'):
                    validate_approved_batch(p, Path('/fake/.env'))
                original['approval_state'] = 'HUMAN_APPROVED'
                original['text'] = '전화 010-1234-5678'
                p.write_text(json.dumps(obj))
                with self.assertRaisesRegex(ValueError, 'RESIDUAL_PII_REQUIRES_REVIEW'):
                    validate_approved_batch(p, Path('/fake/.env'))

    def cursor_conn(self, existing, old_rows=187):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        # 1) exists, 2) count/distinct/key, 3) selected rows.
        cur.fetchone.side_effect = [('rag.aegis_live_docs',), (old_rows,1,self.tag)]
        cur.fetchall.return_value = existing
        return conn

    @patch('live_index.encode_docs')
    def test_unchanged_doc_does_not_reembed(self, encoder):
        conn = self.cursor_conn([('S001',self.row['text'],[],[])])
        result = incremental_upsert(conn,[self.row],self.tag,confirmed=True)
        self.assertEqual(result, dict(inserted=0,updated=0,unchanged=1,
                                      previous_rows=187,resulting_rows=187))
        encoder.assert_not_called()

    @patch('live_index.encode_docs')
    def test_new_document_embedded_once(self, encoder):
        encoder.return_value = [[0.0]*1024]
        conn = self.cursor_conn([])
        result = incremental_upsert(conn,[self.row],self.tag,confirmed=True)
        self.assertEqual(result['inserted'],1)
        self.assertEqual(result['resulting_rows'],188)
        encoder.assert_called_once()

    @patch('live_index.encode_docs')
    def test_modified_doc_updated_once(self, encoder):
        encoder.return_value = [[0.0]*1024]
        conn = self.cursor_conn([('S001','옛 문서',[],[])])
        result = incremental_upsert(conn,[self.row],self.tag,confirmed=True)
        self.assertEqual(result['updated'],1)
        self.assertEqual(result['inserted'],0)
        encoder.assert_called_once()

    def test_snapshot_confirmation_required(self):
        with self.assertRaisesRegex(ValueError, 'CONFIRM_LIVE_PROMOTION'):
            promote_snapshot(MagicMock(), 'rag.aegis_repro_11d26bb4db0f23360bdb',
                             187,self.tag,'WRONG')

    def test_promote_renames_without_copying_or_indexing(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            ('rag.aegis_repro_11d26bb4db0f23360bdb',None),
            (187,187,1,self.tag)
        ]
        outcome = promote_snapshot(conn,'rag.aegis_repro_11d26bb4db0f23360bdb',
                                   187,self.tag,'PROMOTE_TO_'+LIVE_TABLE)
        self.assertEqual(outcome,'PROMOTED_WITHOUT_COPY')
        sql_commands = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
        self.assertNotIn('TRUNCATE',sql_commands)
        self.assertNotIn('DROP TABLE',sql_commands)

    def test_live_index_accepts_growth(self):
        p = LivePipeline.__new__(LivePipeline)
        p.index_mode = 'live'; p.expected_rows = 187
        self.assertTrue(188 >= p.expected_rows)


if __name__ == '__main__':
    unittest.main()
