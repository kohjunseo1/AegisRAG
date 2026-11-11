"""Public synthetic smoke; fixed key is used only for fabricated fixtures."""
from pathlib import Path
from unittest.mock import patch
import json

from core import LivePipeline, PreparedQuery, PII, order_results
from document_preparation import prepare
from local_pseudonymizer import make_hmac_token
from region_keys import keys_from_query
from hcx import validate_response, OutboundError
from dataset_preparation import key_tag
from reproduction import PIPELINE, target, encode_docs, ingest
from settings import ROOT, db_config, table_identifier

# Public fixture constant. Never used by Holdout preparation or chat.
FIXTURE_KEY = b'AEGISRAG_PUBLIC_SYNTHETIC_ONLY_2026_' * 2


class SyntheticDetector:
    """Deterministic fixture adapter; never advertised as model performance."""
    def detect(self, text):
        return dict(status='READY_FOR_PSEUDONYMIZATION', entities=[], mask_intervals=[], issues=[])
    def ax_predict(self, text):
        return []
    def veil_predict(self, text):
        return []


def main(env_file, device='cpu', write=False, hcx_live=False):
    raw = ['사내 안전교육은 매주 수요일 오전 10시에 진행됩니다.',
           '정보보안교육은 매주 금요일 오후 2시에 진행됩니다.',
           '출장비 문의는 재무팀에 접수합니다. 마감일 정보는 없습니다.']
    if write:
        from dotenv import load_dotenv
        load_dotenv(env_file)
        from pii_detector import create_detector
        with create_detector() as detector:
            outcomes = [prepare('S%03d'%i, txt, detector, FIXTURE_KEY) for i, txt in enumerate(raw)]
    else:
        outcomes = [prepare('S%03d'%i, txt, SyntheticDetector(), FIXTURE_KEY) for i, txt in enumerate(raw)]
    assert all(o.state == 'LOCAL_PREPARED' for o in outcomes)
    docs = [dict(doc_id=o.doc_id, text=o.sanitized_text, region_keys=list(o.region_keys)) for o in outcomes]
    data = dict(format='aegisrag-local-embedding-eval-v1', pipeline=PIPELINE,
                documents=docs, hmac_key_tag=key_tag(FIXTURE_KEY))
    # V2 address normalization, deterministic identity, and regional alias contract.
    assert make_hmac_token(FIXTURE_KEY, 'person', '홍 길동') == make_hmac_token(FIXTURE_KEY, 'person', '홍길동')
    assert set(keys_from_query('충남 아산시', FIXTURE_KEY)) & set(keys_from_query('충청남도 아산시', FIXTURE_KEY))
    blocked = SyntheticDetector()
    blocked.detect = lambda _: dict(status='REVIEW_REQUIRED')
    assert prepare('Q_REVIEW', raw[0], blocked, FIXTURE_KEY).state == 'REVIEW'
    if write:
        import psycopg
        name = target(data)
        vectors = encode_docs(data, device)
        with psycopg.connect(**db_config(env_file)) as conn:
            assert ingest(conn, data, vectors, name, name) in {'CREATED','VERIFIED_EXISTING'}
        with psycopg.connect(**db_config(env_file)) as conn:
            assert ingest(conn, data, vectors, name, name) == 'VERIFIED_EXISTING'
        pipeline = LivePipeline.__new__(LivePipeline)
        pipeline.root = ROOT; pipeline.device = device; pipeline._model = None
        pipeline.table = table_identifier(name); pipeline.expected_rows = len(docs)
        pipeline.db = db_config(env_file); pipeline.tag = key_tag(FIXTURE_KEY); pipeline.timings = {}
        evidence = pipeline.retrieve(PreparedQuery('사내 안전교육은 언제 열리나요?', set(), set()))
        assert evidence[0]['doc_id'] == 'S000'
        # Existing populated table must fail verification and preserve its data.
        wrong = vectors.copy(); wrong[0] *= -1
        try:
            with psycopg.connect(**db_config(env_file)) as conn:
                ingest(conn, data, wrong, name, name)
        except ValueError as exc:
            assert str(exc) == 'REPRO_CONTENT_MISMATCH'
        else:
            raise AssertionError('MISMATCHED_VECTORS_ACCEPTED')
        print('SYNTHETIC_DB_INGEST_SEARCH_IDEMPOTENCE_OK', name)
    else:
        evidence = [dict(d, dense_rank=i+1, tokens=[]) for i, d in enumerate(docs)]
        evidence = order_results(evidence, set(), set())
    for decision, citations in [('ANSWER',['S000']), ('CLARIFY',[]), ('INSUFFICIENT_EVIDENCE',[])]:
        validate_response(dict(decision=decision, answer='언제 신청하셨나요?' if decision == 'CLARIFY' else '수요일입니다.', citations=citations), evidence, False)
    try:
        validate_response(dict(decision='ANSWER', answer='합성', citations=['NO_SUCH_DOC']), evidence, False)
    except OutboundError:
        pass
    else:
        raise AssertionError('INVALID_CITATION_ACCEPTED')
    from app import respond
    from unittest.mock import MagicMock
    pl = MagicMock(); pl.ask_local.return_value = ('REVIEW', None, [])
    with patch('app.call_hcx') as mock:
        assert respond(pl, {'question':'합성 질문'})['decision'] == 'REVIEW_REQUIRED'
        mock.assert_not_called()
    if hcx_live:
        # These values originate only from the public fabricated fixture above.
        from hcx import call_hcx
        from dotenv import load_dotenv
        load_dotenv(env_file)
        cases = [
            ('사내 안전교육은 언제 열리나요?', 'ANSWER', {'S000'}),
            ('안전교육과 정보보안교육은 각각 언제 열리나요?', 'ANSWER', {'S000', 'S001'}),
            ('사내 안전교육인지 정보보안교육인지 아직 정하지 못했어요. 제가 갈 교육의 시작 시간을 하나로 알려줄 수 있나요?', 'CLARIFY', set()),
            ('출장비 환급 신청 마감일은 언제인가요?', 'INSUFFICIENT_EVIDENCE', set()),
        ]
        for number, (question, decision, citations) in enumerate(cases, 1):
            result, _, _ = call_hcx(ROOT, question, evidence, False)
            if result['decision'] != decision or not citations.issubset(result['citations']):
                raise ValueError('SYNTHETIC_HCX_EXPECTATION_MISMATCH')
            print('SYNTHETIC_HCX_CASE_OK', number, decision, flush=True)
    print('SYNTHETIC_GATE_HMAC_REGION_HCX_MOCK_OK')
    if not write:
        print('DB_AND_REAL_MODELS_NOT_TESTED: run smoke --write against a disposable database')
    return 0
