"""Offline behavioral parity against the project's preserved reference implementation."""
import ast
import base64
import importlib.util
import json
import sys
import unittest
import zlib
from pathlib import Path

from pii_detector.detector import PIIDetector
from pii_detector.structural_policy import mask_union

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_NOTEBOOK = ROOT.parent / "notebooks" / "06D_Final_Release_Validation_AutoDrive.ipynb"


def load_reference_detector():
    notebook = json.loads(REFERENCE_NOTEBOOK.read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        source = "".join(cell.get("source", []))
        if "EMBEDDED_FILES =" not in source:
            continue
        tree = ast.parse(source)
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "EMBEDDED_FILES"
                    for target in node.targets)
        )
        embedded = ast.literal_eval(assignment.value)
        raw = zlib.decompress(base64.b64decode(embedded["pii_detector_final.py"]))
        path = ROOT / "tests" / "_reference_detector_for_test.py"
        path.write_bytes(raw)
        try:
            spec = importlib.util.spec_from_file_location("_reference_detector_for_test", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module.PIIDetector
        finally:
            path.unlink(missing_ok=True)
            sys.modules.pop("_reference_detector_for_test", None)
    raise AssertionError("Could not locate the reference implementation in its release notebook")


def synthetic_backends():
    def ax(text):
        spans = []
        values = [("person", "김민수"), ("phone_number", "010-1234-5678"),
                  ("phone_number", "5854"), ("phone_number", "1588-0000")]
        if text.startswith("김 과장"):
            values.append(("person", "김"))
        if text.startswith("이 팀장"):
            values.append(("person", "이"))
        if text.startswith("홍 부장"):
            values.append(("person", "홍"))
        for label, value in values:
            start = text.find(value)
            if start >= 0:
                spans.append({"label": label, "start": start, "end": start + len(value)})
        return spans

    def veil(text):
        value = "서울특별시 강남구 한빛로 9, 2층"
        start = text.find(value)
        return ([{"label": "address", "start": start, "end": start + len(value)}]
                if start >= 0 else [])

    return ax, veil


@unittest.skipUnless(
    REFERENCE_NOTEBOOK.is_file(),
    "Reference release notebook is unavailable; parity comparison skipped",
)
class TestReferenceDetectorParity(unittest.TestCase):
    def test_synthetic_predictions_match_reference(self):
        Original = load_reference_detector()
        ax, veil = synthetic_backends()
        original = Original(ax, veil, parallel=False)
        packaged = PIIDetector(ax, veil, parallel=False)
        cases = [
            "배송지: (05299) 서울특별시 강남구 한빛로 9, 2층",
            "고객 김민수의 연락처는 010-1234-5678입니다.",
            "주소: 05299 서울특별시 강남구 한빛로 9, 2층",
            "김 과장님에게 연락",
            "이 팀장은 회의 중",
            "홍 부장 안내",
            "김민수 과장님",
            "김과장 연락",
            "김\n과장님",
            "내선: 5854",
            "대표전화: 1588-0000",
            "일반 안내 문장입니다.",
        ]
        try:
            for text in cases:
                with self.subTest(text=text):
                    left, right = original.detect(text), packaged.detect(text)
                    self.assertEqual(left["status"], right["status"])
                    self.assertEqual(left["issues"], right["issues"])
                    self.assertEqual(left.get("review_required"), right["review_required"])
                    baseline = {tuple(e[k] for k in ("label", "start", "end"))
                                for e in left["entities"]}
                    revised = {tuple(e[k] for k in ("label", "start", "end"))
                               for e in right["entities"]}
                    dropped = baseline - revised
                    events = {(e["label"], e["start"], e["end"])
                              for e in right["structural_policy_events"]}
                    self.assertEqual(dropped, events)
                    self.assertLessEqual(revised, baseline)
                    self.assertEqual(right["mask_intervals"],
                                     mask_union(left["entities"], right["entities"], text))
                    self.assertEqual(left.get("postal_expansions"),
                                     right["postal_expansions"])
        finally:
            original.close()
            packaged.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
