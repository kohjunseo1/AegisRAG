"""Privacy-preserving dataset intake contracts on synthetic data only."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dataset_workflow import (doc_id,stage_dataset,review_corrected,
    export_approved,summary,write_private,read_signed,signed,input_rows)
from document_preparation import Outcome

KEY=b'V'*32
TK='<PERSON_'+'a'*24+'>'

def detector_result(did,text,model,key):
    if '모호' in text:
        return Outcome(did,'REVIEW','GATE_NOT_READY')
    if '금지' in text:
        return Outcome(did,'BLOCKED','STEP1_NOT_READY')
    return Outcome(did,'LOCAL_PREPARED','OK',sanitized_text='검증된 합성문서 '+TK,
                   tokens=(TK,),region_keys=())


class DatasetWorkflowTests(unittest.TestCase):
    def test_namespaced_ids(self):
        self.assertTrue(doc_id('newset','X1',KEY).startswith('newset__'))
        self.assertNotIn('X1',doc_id('newset','X1',KEY))
        self.assertEqual(doc_id('newset','X1',KEY),doc_id('newset','X1',KEY))
        self.assertNotEqual(doc_id('newset','X1',KEY),doc_id('other','X1',KEY))
        self.assertNotEqual(doc_id('newset','X1',KEY),doc_id('newset','X1',b'P'*32))
        self.assertLessEqual(len(doc_id('newset','한글/개별문서',KEY)),60)

    def test_dataset_adapter(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'source.jsonl'
            path.write_text(json.dumps({'uuid':'1','body':'합성자료','title':'교육',
                 'kind':'안내'},ensure_ascii=False)+'\n',encoding='utf-8')
            rows=input_rows(path,id_field='uuid',text_field='body',
                            title_field='title',type_field='kind')
            self.assertEqual(rows[0][0],'1')
            self.assertIn('문서 유형: 안내',rows[0][1])

    @patch('dataset_workflow.prepare',side_effect=detector_result)
    def test_ready_pending_blocked_human_approval(self,_):
        rows=[('A','안전 문서'),('B','모호 문서'),('C','금지 문서')]
        stage=stage_dataset(rows,'newset',object(),KEY)
        self.assertEqual(summary(stage)['ready'],1)
        self.assertEqual(summary(stage)['review_pending'],1)
        self.assertEqual(summary(stage)['blocked'],1)
        self.assertEqual(len(export_approved(stage)['documents']),1)
        did=doc_id('newset','B',KEY)
        with self.assertRaisesRegex(ValueError,'REVISED_TEXT_NOT_GATE_READY'):
            review_corrected(stage,did,'여전히 모호 문서','reviewer1',object(),KEY)
        self.assertEqual(summary(stage)['review_pending'],1)
        review_corrected(stage,did,'수정 후 안전 문서','reviewer1',object(),KEY)
        self.assertEqual(summary(stage)['ready'],2)
        self.assertEqual(len(export_approved(stage)['documents']),2)
        self.assertEqual([d for d in export_approved(stage)['documents']
            if d['doc_id']==did][0]['approval_state'],'HUMAN_APPROVED')

    def test_signed_private_stage_detects_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'local_eval'/'s1'/'stage.json'
            with patch('dataset_workflow.BASE',Path(d)/'local_eval'):
                obj={'format':'aegisrag-dataset-stage-v1',
                     'dataset':'testdata',
                     'hmac_key_tag':__import__('dataset_preparation').key_tag(KEY),
                     'documents':[]}
                write_private(path,signed(obj,KEY))
                self.assertEqual(read_signed(path,KEY)['dataset'],'testdata')
                corrupt=json.loads(path.read_text())
                corrupt['dataset']='forged'
                path.write_text(json.dumps(corrupt))
                with self.assertRaisesRegex(ValueError,'INVALID_STAGE_SIGNATURE'):
                    read_signed(path,KEY)

    @patch('dataset_workflow.prepare',side_effect=detector_result)
    def test_duplicate_id_not_allowed(self,_):
        with self.assertRaisesRegex(ValueError,'COLLIDING_DOCUMENT_IDS'):
            stage_dataset([('A','문서1'),('A','문서2')],'newset',object(),KEY)

if __name__=='__main__':
    unittest.main()
