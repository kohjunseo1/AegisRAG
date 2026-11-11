"""Synthetic Q-001 review UI contract: annotations never authorize data release."""
import json
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
