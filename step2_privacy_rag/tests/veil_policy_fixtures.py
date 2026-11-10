from collections import Counter
from veil_person_policy import classify_veil_only_person, IGNORE, REDACT, REVIEW
TESTS = [
    ("카드 상품: 봄날 체크카드", "봄날", IGNORE),
    ("카드 상품: 하린 포인트 카드", "하린", IGNORE),
    ("상품명: 하린 신용카드", "하린", IGNORE),
    ("상품: 햇살 체크 카드", "햇살", IGNORE),
    ("카드 상품: 새별 법인카드", "새별", IGNORE),
    ("카드 상품: 다온 선불 카드", "다온", IGNORE),
    ("가람은행에 문의", "가람", IGNORE),
    ("출금 계좌: 가람은행 123", "가람", IGNORE),
    ("정다운은행 서비스", "정다운", IGNORE),
    ("[부고] 심사역 장인상", "장인상", IGNORE),
    ("[부고] 고객센터 조모상", "조모상", IGNORE),
    ("[부고] 안내: 외조부상", "외조부상", IGNORE),
    ("김 과장님이 보고서 작성", "김", REDACT),
    ("박 대리가 출근", "박", REDACT),
    ("최 팀장님이 전화", "최", REDACT),
    ("허 부장이 검토", "허", REDACT),
    ("정 사원, 다음 안내", "정", REDACT),
    ("안 차장께서 확인", "안", REDACT),
    ("고객센터 양 심사역이 확인", "양", REDACT),
    ("리스크팀 서 수석연구원이 참석", "서", REDACT),
    ("고객명: 하린", "하린", REVIEW),
    ("고객명: 김 과장", "김", REVIEW),
    ("성명: 가람은행", "가람", REVIEW),
    ("하린 고객이 카드를 신청", "하린", REVIEW),
    ("하린 고객의 체크카드", "하린", REVIEW),
    ("홍 길동이 입장", "홍", REVIEW),
    ("하린 포인트 카드가 출시", "하린", REVIEW),
    ("카드 상품: 하린 고객의 카드", "하린", REVIEW),
    ("팀장님을 찾는다", "팀장", REVIEW),
    ("김 AI 리드님이 참석", "김", REVIEW),
    ("김 테크리더가 참석", "김", REVIEW),
    ("김 부장급 전문가", "김", REVIEW),
    ("김 부장님이라는 제품명", "김", REVIEW),
    ("그린은행 직원", "그린", IGNORE),
    ("은행명: 그린 은행", "그린", REVIEW),
    ("[부고] 민재님이 방문", "민재", REVIEW),
    ("장인상이란 표현", "장인상", REVIEW),
    ("상품명: 하린", "하린", REVIEW),
    ("하린 적금 상품", "하린", REVIEW),
    ("보험 상품: 하린 평생보험", "하린", REVIEW),
    ("고객명: 하린 체크카드", "하린", REVIEW),
    ("담당: 박 대리님", "박", REDACT),
    ("다음 회의에 신 과장님은", "신", REDACT),
    ("고객: 홍길동", "홍길동", REVIEW),
]


def self_test():
    counts = Counter()
    failed = []
    for index, (text, needle, expected) in enumerate(TESTS, 1):
        i = text.index(needle)
        result = classify_veil_only_person(text, i, i + len(needle))
        counts[result.action] += 1
        if result.action != expected:
            failed.append({"test": index, "expected": expected, "actual": result.action})
        if result.action == REDACT and not (
            result.protection_start == i and result.protection_end is not None and result.protection_end > i + len(needle)
        ):
            failed.append({"test": index, "expected": "valid_protection_interval", "actual": "invalid"})
    print(f"독립 합성 테스트: {len(TESTS)}건, 성공 {len(TESTS)-len({f['test'] for f in failed})}건, 실패 {len({f['test'] for f in failed})}건")
    print("행동 분포:", dict(counts))
    if failed:
        print("실패 상세(원문 없음):", failed)
    return not failed
