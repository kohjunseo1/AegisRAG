import unittest
from pii_detector.structural_policy import refine_spans, attach_policy
from pii_detector.detector import PIIDetector
from unittest.mock import patch


def span(text, phrase, label):
    s = text.index(phrase)
    return {"label": label, "start": s, "end": s + len(phrase)}


class PolicyTests(unittest.TestCase):
    def test_detector_contract_preserves_mask_union_and_existing_postal(self):
        text = "김 과장님에게\n내선: 5854\n고객센터: 1588-0000\n주소: (05299) 서울특별시 강남구"

        def ax(piece):
            spans = []
            for value, label in (("김", "person"), ("5854", "phone_number"),
                                 ("1588-0000", "phone_number")):
                start = piece.find(value)
                if start >= 0:
                    spans.append({"label": label, "start": start, "end": start + len(value)})
            return spans

        def veil(piece):
            value = "서울특별시 강남구"
            start = piece.find(value)
            return ([{"label": "address", "start": start, "end": start + len(value)}]
                    if start >= 0 else [])

        with PIIDetector(ax, veil, parallel=False) as detector:
            result = detector.detect(text)

        self.assertEqual(result["status"], "READY_FOR_PSEUDONYMIZATION")
        entities = {(e["label"], text[e["start"]:e["end"]]) for e in result["entities"]}
        self.assertIn(("phone_number", "1588-0000"), entities)
        self.assertNotIn(("person", "김"), entities)
        self.assertNotIn(("phone_number", "5854"), entities)
        self.assertTrue(any(text[e["start"]:e["end"]].startswith("(05299)")
                            for e in result["entities"] if e["label"] == "address"))
        self.assertEqual({e["rule"] for e in result["structural_policy_events"]},
                         {"standalone_surname_title", "explicit_internal_extension"})
        for original in ("김", "5854", "1588-0000", "(05299) 서울특별시 강남구"):
            start = text.index(original)
            self.assertTrue(any(i["start"] <= start and i["end"] >= start + len(original)
                                for i in result["mask_intervals"]), original)
        self.assertEqual(result["policy_version"], "aegis_core_v1")

        # Mock Step 2 consumes only the public mask_intervals API. Strict spans
        # removed by policy still get redacted; ordinary detected PII does too.
        redacted = list(text)
        for interval in result["mask_intervals"]:
            for index in range(interval["start"], interval["end"]):
                redacted[index] = "*"
        redacted = "".join(redacted)
        for original in ("김", "5854", "1588-0000", "05299", "서울특별시 강남구"):
            start = text.index(original)
            self.assertEqual(redacted[start:start + len(original)], "*" * len(original))

    def test_structural_policy_failure_is_fail_closed(self):
        with patch("pii_detector.detector.refine_spans", side_effect=RuntimeError("secret")):
            with PIIDetector(lambda t: [], lambda t: [], parallel=False) as detector:
                result = detector.detect("ordinary text")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["issues"][0]["code"], "model_or_postprocess_failure")
        self.assertNotIn("secret", str(result))

    def test_standalone_surname_title(self):
        for t in ("김 과장님에게 연락", "홍 부장 안내", "이 팀장은 회의"):
            with self.subTest(t=t):
                p = span(t, t[0], "person")
                self.assertEqual(refine_spans(t, [p])[0], [])

    def test_actual_name_not_suppressed(self):
        for text, phrase in (("김민수 과장님", "김민수"),
                             ("가명 김하늘이 부장님과", "김하늘이"),
                             ("계약자 이서연", "이서연"),
                             ("김과장 연락", "김"),
                             ("김\n과장님", "김")):
            with self.subTest(text=text):
                p = span(text, phrase, "person")
                self.assertEqual(refine_spans(text, [p])[0], [p])

    def test_extension_only_explicit(self):
        for t in ("내선: 5854", "내선번호: 5854", "담당(내선): 5854"):
            with self.subTest(t=t):
                self.assertEqual(refine_spans(t, [span(t,"5854","phone_number")])[0], [])

    def test_normal_phone_and_identifiers_preserved(self):
        for t, p in (("고객센터: 1588-0000", "1588-0000"),
                     ("대표전화: 02-123-4567", "02-123-4567"),
                     ("내선: 010-1234-5678", "010-1234-5678"),
                     ("접수번호: 5854", "5854"),
                     ("내선 연결: 5854", "5854"),
                     ("내선: 5854 / 고객번호: 1234", "5854")):
            with self.subTest(text=t):
                s = span(t,p,"phone_number")
                self.assertEqual(refine_spans(t,[s])[0],[s])

    def test_mask_preserves_dropped_spans(self):
        t="김 과장님\n내선: 5854\n고객: 김민수"
        raw={"status":"READY_FOR_PSEUDONYMIZATION", "entities":[span(t,"김","person"),span(t,"5854","phone_number"),span(t,"김민수","person")], "issues":[]}
        refined=attach_policy(t,raw)
        self.assertEqual(len(refined["entities"]),1)
        self.assertEqual(refined["entities"][0]["label"],"person")
        covered = {i for e in refined["mask_intervals"] for i in range(e["start"],e["end"])}
        self.assertTrue(all(i in covered for e in raw["entities"] for i in range(e["start"],e["end"])))
        self.assertEqual(raw["entities"][0],span(t,"김","person"))
        self.assertTrue(all("김" not in repr(x) for x in refined["structural_policy_events"]))

    def test_never_upgrades_review_or_blocked(self):
        t="내선: 5854";p=span(t,"5854","phone_number")
        x=attach_policy(t,{"status":"REVIEW_REQUIRED","issues":[{"code":"original"}],"entities":[p]})
        self.assertEqual(x["status"],"REVIEW_REQUIRED");self.assertEqual(len(x["issues"]),1)
        y=attach_policy(t,{"status":"BLOCKED","entities":[]})
        self.assertEqual(y["status"],"BLOCKED")
        self.assertNotIn("mask_intervals",y)

    def test_invalid_offsets_fail_closed(self):
        with self.assertRaises(ValueError):
            attach_policy("김",{"status":"READY_FOR_PSEUDONYMIZATION","entities":[{"label":"person","start":0,"end":2}]})

if __name__ == '__main__':
    unittest.main()
