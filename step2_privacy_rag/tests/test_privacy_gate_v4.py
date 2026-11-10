"""Fast deterministic V4 contract tests. No model, no network, no DB."""
from __future__ import annotations

from privacy_gate_v4 import check_privacy_gate, READY
from veil_policy_fixtures import self_test


class Fake:
    def __init__(self, result, ax=(), veil=()):
        self.result = result
        self.ax = list(ax)
        self.veil = list(veil)

    def detect(self, text):
        return self.result

    def ax_predict(self, text):
        return self.ax

    def veil_predict(self, text):
        return self.veil


def span(text, substring, label="person"):
    pos = text.index(substring)
    return {"start": pos, "end": pos + len(substring), "label": label}


def run(text, *, ax=(), veil=(), strict=(), status="READY_FOR_PSEUDONYMIZATION"):
    masks = [{"start": s["start"], "end": s["end"]} for s in strict]
    return check_privacy_gate(Fake({"status": status, "entities": list(strict), "mask_intervals": masks}, ax, veil), text)


def covers(intervals, start, end):
    return all(any(i["start"] <= p < i["end"] for i in intervals) for p in range(start, end))


def main():
    assert self_test(), "policy regression failed"
    count = 0

    for text, candidate, expected in [
        ("카드 상품: 하린 포인트 카드", "하린", READY),
        ("고객명: 하린", "하린", "REVIEW_REQUIRED"),
        ("하린 고객이 카드를 신청", "하린", "REVIEW_REQUIRED"),
        ("가람은행 계좌", "가람", READY),
        ("[부고] 장인상", "장인상", READY),
        ("김 과장님이 검토", "김", READY),
        ("박 대리가 응대", "박", READY),
        ("정 AI리드님", "정", "REVIEW_REQUIRED"),
        ("고객명: 김 과장", "김", "REVIEW_REQUIRED"),
    ]:
        out = run(text, veil=[span(text, candidate)])
        assert out["decision"] == expected, (text, out["reason"])
        if expected == READY and out["veil_policy_counts"].get("role_redacted"):
            assert covers(out["redaction_intervals"], *[span(text, candidate)[k] for k in ("start", "end")])
            assert covers(out["mask_intervals"], *[span(text, candidate)[k] for k in ("start", "end")])
            assert not out["entities_for_pseudonymization"], "Role reference must not get HMAC identity"
        count += 1

    text = "고객 홍 길동님 연락"
    a, b, v = span(text, "홍"), span(text, "길동"), span(text, "홍 길동")
    out = run(text, ax=[a,b], veil=[v], strict=[a,b])
    assert out["decision"] == READY and out["person_merges"] == 1
    assert out["entities_for_pseudonymization"] == [v]
    assert covers(out["mask_intervals"], v["start"], v["end"])
    count += 1

    text = "주소: 서울특별시 강남구 테헤란로 123"
    a = span(text, "서울특별시 강남구 테헤란로", "address")
    out = run(text, ax=[a])
    assert out["decision"] == "REVIEW_REQUIRED" and out["reason"] == "unprotected_ax_address"
    count += 1

    text = "고객명: 하린"
    p=span(text,"하린")
    out = run(text, strict=[p], veil=[p], ax=[p])
    assert out["decision"] == READY and out["entities_for_pseudonymization"] == [p]
    count += 1

    text = "김 과장님"
    # If A.X has an overlapping strict span, the Veil-only rule cannot override it.
    strict_p = span(text, "과장님")
    out = run(text, strict=[strict_p], veil=[span(text, "김")])
    assert out["decision"] == "REVIEW_REQUIRED", out
    count += 1

    out=run("김 과장님", veil=[span("김 과장님", "김")],status="REVIEW_REQUIRED")
    assert out["decision"] == "REVIEW_REQUIRED" and out["reason"] == "detector_not_ready"
    count += 1

    out=check_privacy_gate(Fake({"status":"READY_FOR_PSEUDONYMIZATION", "entities":[], "mask_intervals": []}),None)
    assert out["decision"] == "BLOCKED"
    count += 1

    print(f"Gate V4 계약 테스트: {count}건 PASS; 실제 모델·HMAC·외부 전송 실행 없음")


if __name__ == "__main__":
    main()

import unittest

class LegacyContract(unittest.TestCase):
    def test_contract(self):
        main()
