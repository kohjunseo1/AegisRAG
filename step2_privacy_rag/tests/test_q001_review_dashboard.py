"""Synthetic Q-001 review UI contract: annotations never authorize data release."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import review_dashboard_q001 as m


def fake_case():
    return {"SYNTH001": {
        "id": "SYNTH001", "source": "[부고] 장인상 고객정보",
        "step1_status": "REVIEW_REQUIRED", "gate_reason": "detector_not_ready",
        "candidates": [
            {"kind": "veil_candidate", "start": 5, "end": 8, "reason": "synthetic"},
            {"kind": "name_boundary", "start": 0, "end": 3, "reason": "synthetic"},
        ], "entities": [], "masks": []}}


class ReviewRecordContracts(unittest.TestCase):
    def test_annotations_never_approve_or_contain_source(self):
        payload = {"id": "SYNTH001", "candidate": 0, "choice": "NON_PERSON_EXPRESSION"}
        r = m.review_record(payload, fake_case())
        self.assertEqual(r["state"], "NEEDS_INDEPENDENT_REVALIDATION")
        self.assertNotIn("장인상", json.dumps(r, ensure_ascii=False))
        self.assertNotIn("source", r)
        self.assertEqual(r["human_classification"], "NON_PERSON_EXPRESSION")
        self.assertEqual(r["kind"], "veil_candidate")

    def test_review_ui_uses_scrollable_clickable_document_queue(self):
        self.assertIn('id="queue"', m.HTML)
        self.assertIn('max-height:66vh;overflow:auto', m.HTML)
        self.assertIn('button.onclick=()=>loadCase(info.id)', m.HTML)
        self.assertIn('if(entries.length)await loadCase(entries[0].id)', m.HTML)
        self.assertNotIn('id="open"', m.HTML)
        self.assertNotIn('id="ids"', m.HTML)

    def test_review_ui_restores_current_choices_and_ignores_stale_records(self):
        m.LOCAL.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=m.LOCAL) as directory:
            journal=Path(directory)/"reviews.jsonl"
            a=m.review_record({"id":"SYNTH001","candidate":0,
                               "choice":"UNRESOLVED"},fake_case())
            m.persist(journal,a)
            b=m.review_record({"id":"SYNTH001","candidate":0,
                               "choice":"NON_PERSON_EXPRESSION"},fake_case())
            m.persist(journal,b)
            choices=m.saved_review_choices(fake_case(),journal)
            self.assertEqual(choices[("SYNTH001",0)],"NON_PERSON_EXPRESSION")
            self.assertNotIn(("SYNTH001",1),choices)
            stale=dict(b,source_digest="f"*64)
            m.persist(journal,stale)
            self.assertEqual(m.saved_review_choices(fake_case(),journal)[("SYNTH001",0)],
                             "NON_PERSON_EXPRESSION")

    def test_review_queue_displays_empty_state_and_reviewed_count(self):
        self.assertIn("현재 확인할 REVIEW 문서가 없습니다.", m.HTML)
        self.assertIn("현재 확인할 문서 없음",m.HTML)
        self.assertIn('id="review-summary"',m.HTML)
        self.assertIn('entries.filter(x=>x.review_complete).length',m.HTML)
        self.assertIn("관리자 인증 및 검토 목록 조회 중",m.HTML)
        self.assertIn("분류 기록은 HMAC·적재 승인이 아닙니다",m.HTML)

    def test_admin_http_login_returns_all_reviewed_documents(self):
        m.LOCAL.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=m.LOCAL) as directory:
            journal=Path(directory)/"review_journal.jsonl"
            cases={}
            for index in range(10):
                key=f"H{index:04d}"
                base=next(iter(fake_case().values()))
                cases[key]={**base,"id":key}
                record=m.review_record({"id":key,"candidate":0,
                    "choice":"NON_PERSON_EXPRESSION"},cases)
                m.persist(journal,record)
            server=ThreadingHTTPServer(("127.0.0.1",0),BaseHTTPRequestHandler)
            server.RequestHandlerClass=m.create_handler(
                cases,"temporary-synthetic-token",server.server_port,journal,
                allow_experiment=True)
            thread=Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                uri=f"http://127.0.0.1:{server.server_port}/api/cases"
                with self.assertRaises(HTTPError) as denied:
                    urlopen(uri,timeout=3)
                self.assertEqual(denied.exception.code,403)
                request=Request(uri,headers={
                    "X-Local-Review-Token":"temporary-synthetic-token"})
                with urlopen(request,timeout=3) as response:
                    payload=json.load(response)
                    self.assertEqual(response.status,200)
                self.assertEqual(len(payload["cases"]),10)
                self.assertTrue(payload["experimental_enabled"])
                self.assertTrue(all(x["review_complete"] is False
                                    for x in payload["cases"]))
                # Each synthetic case has TWO candidates. Verify that a
                # single recorded decision does not mark it complete.
                for key in cases:
                    entry=m.review_record({"id":key,"candidate":1,
                        "choice":"LONG_NAME"},cases)
                    m.persist(journal,entry)
                with urlopen(request,timeout=3) as response:
                    payload=json.load(response)
                self.assertEqual(sum(x["review_complete"] for x in payload["cases"]),10)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_review_token_auto_login_and_explicit_admin_action(self):
        self.assertIn('A-Za-z0-9_-', m.HTML)
        self.assertIn('elem("login").click()', m.HTML)
        self.assertIn('if(!Array.isArray(data.cases))', m.HTML)
        self.assertIn('loginBusy=true', m.HTML)
        self.assertIn('id="run-e2e"', m.HTML)

    def test_wrong_target_action_rejected(self):
        with self.assertRaises(ValueError):
            m.review_record({"id": "SYNTH001", "candidate": 0,
                             "choice": "SHORT_NAME_PLUS_PARTICLE"}, fake_case())

    def test_extra_keys_and_fake_status_rejected(self):
        payload = {"id": "SYNTH001", "candidate": 0, "choice": "PERSON_PII",
                   "state": "READY_FOR_PSEUDONYMIZATION"}
        with self.assertRaises(ValueError):
            m.review_record(payload, fake_case())

    def test_unknown_id_and_boolean_index_rejected(self):
        with self.assertRaises(ValueError):
            m.review_record({"id": "OTHER", "candidate": 0, "choice": "UNRESOLVED"},
                            fake_case())
        with self.assertRaises(ValueError):
            m.review_record({"id": "SYNTH001", "candidate": True, "choice": "UNRESOLVED"},
                            fake_case())

    def test_local_private_journal_permissions(self):
        m.LOCAL.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=m.LOCAL) as directory:
            out = Path(directory) / "reviews.jsonl"
            record = m.review_record({"id":"SYNTH001","candidate":1,"choice":"UNRESOLVED"},
                                     fake_case())
            m.persist(out, record)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(saved["state"], "NEEDS_INDEPENDENT_REVALIDATION")
            self.assertNotIn("고객정보", out.read_text(encoding="utf-8"))

    def test_nonprivate_paths_rejected(self):
        with self.assertRaises(ValueError):
            m.private_path(ROOT / "README.md")

    def test_unauthed_or_cross_origin_requests_rejected(self):
        handler = m.create_handler(fake_case(), "session-secret", 8771,
                                   m.DEFAULT_JOURNAL if hasattr(m, "DEFAULT_JOURNAL")
                                   else m.LOCAL/"q001_review_decisions.jsonl")
        req = object.__new__(handler)
        req.headers = {"Host": "127.0.0.1:8771",
                       "X-Local-Review-Token": "invalid",
                       "Origin": "http://evil.example"}
        self.assertFalse(req.valid_origin())
        self.assertFalse(req.authenticated())
        req.headers["Origin"] = "http://127.0.0.1:8771"
        self.assertTrue(req.valid_origin())


if __name__ == "__main__":
    unittest.main()
,m.HTML)
        self.assertIn('elem("login").click()',m.HTML)
        self.assertIn('if(!Array.isArray(data.cases))',m.HTML)
        self.assertIn('loginBusy=true',m.HTML)
        self.assertIn('id="run-e2e"',m.HTML)

    def test_wrong_target_action_rejected(self):
        with self.assertRaises(ValueError):
            m.review_record({"id": "SYNTH001", "candidate": 0,
                             "choice": "SHORT_NAME_PLUS_PARTICLE"}, fake_case())

    def test_extra_keys_and_fake_status_rejected(self):
        payload = {"id": "SYNTH001", "candidate": 0, "choice": "PERSON_PII",
                   "state": "READY_FOR_PSEUDONYMIZATION"}
        with self.assertRaises(ValueError):
            m.review_record(payload, fake_case())

    def test_unknown_id_and_boolean_index_rejected(self):
        with self.assertRaises(ValueError):
            m.review_record({"id": "OTHER", "candidate": 0, "choice": "UNRESOLVED"},
                            fake_case())
        with self.assertRaises(ValueError):
            m.review_record({"id": "SYNTH001", "candidate": True, "choice": "UNRESOLVED"},
                            fake_case())

    def test_local_private_journal_permissions(self):
        m.LOCAL.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=m.LOCAL) as directory:
            out = Path(directory) / "reviews.jsonl"
            record = m.review_record({"id":"SYNTH001","candidate":1,"choice":"UNRESOLVED"},
                                     fake_case())
            m.persist(out, record)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(saved["state"], "NEEDS_INDEPENDENT_REVALIDATION")
            self.assertNotIn("고객정보", out.read_text(encoding="utf-8"))

    def test_nonprivate_paths_rejected(self):
        with self.assertRaises(ValueError):
            m.private_path(ROOT / "README.md")

    def test_unauthed_or_cross_origin_requests_rejected(self):
        handler = m.create_handler(fake_case(), "session-secret", 8771,
                                   m.DEFAULT_JOURNAL if hasattr(m, "DEFAULT_JOURNAL")
                                   else m.LOCAL/"q001_review_decisions.jsonl")
        req = object.__new__(handler)
        req.headers = {"Host": "127.0.0.1:8771",
                       "X-Local-Review-Token": "invalid",
                       "Origin": "http://evil.example"}
        self.assertFalse(req.valid_origin())
        self.assertFalse(req.authenticated())
        req.headers["Origin"] = "http://127.0.0.1:8771"
        self.assertTrue(req.valid_origin())


if __name__ == "__main__":
    unittest.main()
