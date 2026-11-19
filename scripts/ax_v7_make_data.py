"""Generate labeled, privacy-safe V7 *bootstrap* synthetic training/dev sets.

Only a reproducible starter. Do not claim these limited templates establish
generalization; review annotation policy and add varied, independent, manually
verified examples BEFORE running full fine-tuning. No original user PII used.
Generated rows are seven_only; other 37 classes remain unknown, never forced O.

Example: python scripts/ax_v7_make_data.py --out step2_privacy_rag/local_eval/v7_bootstrap
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
from pathlib import Path

# HIGH-CONFIDENCE bootstrap only: the name is an explicitly labeled full
# person name; the address is an explicit customer delivery/residence address.
# One-character names, given-name-only mentions, surname-only references,
# role-only text, ZIP inclusion and generic location mentions are EXCLUDED
# pending the separate annotation-policy review.
GIVEN_NAMES = (
    "서준", "지우", "도윤", "민서", "유진", "하윤", "시우", "수아",
    "지민", "현서", "준서", "나연", "예린", "예서", "가은", "수빈",
    "은우", "채린", "지현", "준혁", "서연", "재민", "지호", "예지",
    "윤서", "태윤", "서우", "승민", "가윤", "민준", "소윤", "지안",
)
SURNAMES = ("김", "박", "이", "최", "정", "조", "강", "윤", "임", "한", "신", "오", "권")
COMPOUND_SURNAMES = ("남궁", "황보")
REGION_DISTRICTS = (
    ("서울특별시", "강남구"),
    ("부산광역시", "해운대구"),
    ("대구광역시", "달서구"),
    ("인천광역시", "연수구"),
    ("대전광역시", "유성구"),
    ("경기도", "성남시 분당구"),
    ("강원특별자치도", "춘천시"),
    ("충청남도", "아산시"),
    ("전북특별자치도", "전주시 완산구"),
    ("경상남도", "창원시 성산구"),
    ("제주특별자치도", "제주시"),
)
ROADS = ("가람로", "나래길", "솔빛로", "샘터길", "푸른로", "새봄길", "하늘로", "새빛길")
VILLAGES = ("가람동", "새빛동", "샘터동", "나래동", "솔빛동")

# Value disjointness across train/dev is a stronger safeguard than text-only
# deduplication. This is still a small synthetic smoke corpus, NOT a blind set.
NAME_POOLS = {}
for _split in ("train", "dev"):
    pool = []
    for surname in (*SURNAMES, *COMPOUND_SURNAMES):
        for given in GIVEN_NAMES:
            name = surname + given
            bucket = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % 5
            if (bucket == 0) == (_split == "dev"):
                pool.append((surname, given))
    NAME_POOLS[_split] = tuple(pool)
assert all(NAME_POOLS.values())

TRAIN_NAME_TEMPLATES = (
    "고객 성명: {PII}\n접수 상태: 대기",
    "계약 신청인 성명: [{PII}] / 검토 완료",
    "발송 수취인 이름: {PII} / 송장 확인",
    "상담 예약자 성명: {PII}; 접수일은 별도",
    "고객 정보 입력란 | 이름: {PII} | 확인",
    "보험 계약자 성명: ({PII}) / 변경 요청",
    "등록 직원 성명: {PII} / 부서 배정",
    "개인 서명자 이름: [{PII}] / 서명 확인",
)
DEV_NAME_TEMPLATES = (
    "이전 신청서의 신청인 이름은 [{PII}]로 기재되어 있습니다.",
    "상담 명단 / 대상자 성명: {PII}\n상태: 검토 중",
    "주문 관련 개인 수령인: ({PII}) / 처리 완료",
)
TRAIN_ADDRESS_TEMPLATES = (
    "고객 배송지 전체주소: {PII}\n배송 메모: 없음",
    "신청자 거주지 상세주소: [{PII}] / 본인 확인",
    "우편 수령인의 주소: {PII}; 발송 예약",
    "고객의 물품 반송 주소: ({PII}) / 배송팀 검수",
    "거주자 등록 주소: {PII}\n변경 일자: 미정",
    "택배 수취지: [{PII}] / 주소 변경 요청",
)
DEV_ADDRESS_TEMPLATES = (
    "보험계약자 실제 거주 주소: {PII}\n심사 단계: 대기",
    "등록된 개인 배송 주소는 [{PII}]입니다.",
    "고객의 우편물 수신 주소: ({PII}) / 처리 단계: 완료",
)

# Only clearly identified non-PII expressions receive verified O supervision.
# Never mark the whole sentence O in a partially annotated 37-label model.
TRAIN_HARD_NEGATIVES = (
    ("업무서식의 열 제목: [{TERM}] / 값은 비어 있습니다.", "주소"),
    ("부서 분류명: [{TERM}] / 인사 공지입니다.", "고객지원팀"),
    ("상품 카탈로그의 제품명: [{TERM}] / 재고 확인", "솔빛알림"),
    ("문서 제목: [{TERM}] / 내용은 별도로 작성", "구매검토표"),
    ("시스템 메뉴 이름: [{TERM}] / 메뉴 설정", "주소관리"),
    ("장부의 입력 항목명: [{TERM}] / 실제 기재값 없음", "고객성명"),
)
DEV_HARD_NEGATIVES = (
    ("운영 서식의 항목명: [{TERM}] / 데이터는 없습니다.", "주소"),
    ("부서 목록의 팀 이름: [{TERM}] / 소속 분류", "관리지원팀"),
    ("발주 관리의 상품명: [{TERM}] / 재고 목록", "나래알림"),
)


def made_name(rng, i, split="train"):
    surname, given = rng.choice(NAME_POOLS[split])
    # Even with internal spacing these are unequivocally FULL names when a
    # person-name field is explicit. No bare 1-char or given-only mentions.
    return surname + (" " if i % 17 == 0 else "") + given


def made_address(rng, i):
    region, district = rng.choice(REGION_DISTRICTS)
    number = rng.randrange(20, 900)
    if i % 3 == 0:
        base = f"{region} {district} {rng.choice(VILLAGES)} {number}-{rng.randrange(1, 89)}"
    else:
        base = f"{region} {district} {rng.choice(ROADS)} {number}"
    if i % 4 == 0:
        base += f" {rng.randrange(101, 109)}동 {rng.randrange(101, 908)}호"
    # Five-digit postal-code policy remains unresolved. Exclude it until
    # Gold/HMAC boundary rules are explicitly approved.
    return base


def entry(doc_id, template, value):
    if template.count("{PII}") != 1:
        raise ValueError("ONE_PLACEHOLDER_REQUIRED")
    start = template.index("{PII}")
    text = template.replace("{PII}", value)
    label = "address" if "addr" in doc_id else "person"
    ent = {"label":label,"start":start,"end":start+len(value)}
    if text[ent["start"]:ent["end"]] != value:
        raise ValueError("INVALID_UNICODE_OFFSET")
    return {"id":doc_id,"text":text,"entities":[ent],
            "annotation_scope":"seven_only","quality_tier":"high_confidence"}


def generate(seed, n, split):
    rng = random.Random(seed)
    names = TRAIN_NAME_TEMPLATES if split == "train" else DEV_NAME_TEMPLATES
    addresses = TRAIN_ADDRESS_TEMPLATES if split == "train" else DEV_ADDRESS_TEMPLATES
    rows=[]
    for i in range(n):
        if i%2 == 0:
            val = made_name(rng, i + (10001 if split=="dev" else 0), split)
            template = rng.choice(names)
            rows.append(entry(f"{split}_person_{i:05d}", template, val))
        else:
            val = made_address(rng, i + (10001 if split=="dev" else 0))
            template = rng.choice(addresses)
            rows.append(entry(f"{split}_addr_{i:05d}", template, val))
    # Unlike bare seven_only negatives (zero CE supervision), these
    # *audited subspans* contribute an O loss while unknown 37-class
    # regions remain ignored. Never mark an entire partially annotated
    # document O merely because no seven-class Gold was supplied.
    negatives_with_targets = (TRAIN_HARD_NEGATIVES if split == "train"
                              else DEV_HARD_NEGATIVES)
    # Vary benign document context to avoid repeating a tiny set of exact
    # negative sentences dozens of times in a larger bootstrap corpus.
    benign_purposes = ("정기점검", "내부검토", "분기보고", "업무정리", "사전확인",
                       "품질검토", "자료편집", "분류작업", "처리기록")
    benign_sections = ("분류표", "요약문", "업무자료", "참조표", "검토자료",
                       "기록표", "안내서", "상태표", "추가설명")
    for i in range(max(1,n//5)):
        template, term = negatives_with_targets[i % len(negatives_with_targets)]
        marker = "{TERM}"
        start = template.index(marker)
        core = template.replace(marker,term)
        prefix = (benign_purposes[(i//len(negatives_with_targets)) % len(benign_purposes)]
                  + " " + benign_sections[(i//(len(negatives_with_targets)*len(benign_purposes))) % len(benign_sections)]
                  + ": ")
        text = prefix + core
        start += len(prefix)
        rows.append({
            "id":f"{split}_negative_{i:05d}",
            "text":text,
            "entities":[],
            "annotation_scope":"seven_only",
            "quality_tier":"high_confidence",
            "verified_o_spans":[{"start":start,"end":start+len(term),"text":term}],
        })
    rng.shuffle(rows)
    return rows


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out",type=Path,default=Path("step2_privacy_rag/local_eval/v7_bootstrap"))
    cli.add_argument("--train-n",type=int,default=800)
    cli.add_argument("--dev-n",type=int,default=150)
    cli.add_argument("--seed",type=int,default=1234)
    cli.add_argument("--replay-in",type=Path,
                     help="historical official/dev JSONL (never test/holdout); kept local")
    args=cli.parse_args()
    if args.train_n<100 or args.dev_n<30:
        cli.error("train-n>=100 and dev-n>=30 required")
    out=args.out.expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("BOOTSTRAP_OUTPUT_EXISTS: use a fresh directory")
    train=generate(args.seed,args.train_n,"train")
    dev=generate(args.seed+19937,args.dev_n,"dev")
    texts={}
    for split,rows in (("train",train),("dev",dev)):
        for row in rows:
            fingerprint=hashlib.sha256(row["text"].encode()).hexdigest()
            if fingerprint in texts and texts[fingerprint]!=split:
                raise ValueError("TRAIN_DEV_TEXT_COLLISION")
            texts[fingerprint]=split
    replay=None
    if args.replay_in is not None:
        from ax_v7_train import UNSAFE_SPLIT, LABELS7
        src=args.replay_in.expanduser().resolve()
        if not src.is_file() or UNSAFE_SPLIT.search(src.name):
            raise ValueError("REPLAY_SOURCE_MUST_BE_LOCAL_ORIGINAL_TRAIN_OR_OFFICIAL_DEV_NOT_TEST")
        replay=[]
        with src.open(encoding="utf-8-sig") as handle:
            for i,line in enumerate(handle):
                if not line.strip(): continue
                row=json.loads(line)
                text=row.get("text"); entities=row.get("entities")
                if not isinstance(text,str) or not isinstance(entities,list):
                    raise ValueError("REPLAY_BAD_SCHEMA")
                clean=[]
                for e in entities:
                    lab=e.get("label");a=e.get("start");b=e.get("end")
                    if lab not in LABELS7 or type(a) is not int or type(b) is not int or not 0<=a<b<=len(text):
                        raise ValueError("REPLAY_UNKNOWN_7_LABEL_OR_OFFSET")
                    if "text" in e and text[a:b]!=e["text"]:
                        raise ValueError("REPLAY_SUBSTRING_MISMATCH")
                    clean.append({"label":lab,"start":a,"end":b})
                replay.append({"id":str(row.get("id",i)),"text":text,
                               "entities":clean,"annotation_scope":"seven_only"})
        if not replay:
            raise ValueError("REPLAY_EMPTY")
        if {hashlib.sha256(r["text"].encode()).hexdigest() for r in replay} & set(texts):
            raise ValueError("REPLAY_OVERLAPS_SYNTHETIC_DATA")
    out.mkdir(parents=True,exist_ok=True)
    for split,rows in (("train",train),("dev",dev)):
        path=out/(split+".jsonl")
        with path.open("w",encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row,ensure_ascii=False)+"\n")
    if replay is not None:
        with (out/"replay.jsonl").open("w",encoding="utf-8") as f:
            for row in replay:
                f.write(json.dumps(row,ensure_ascii=False)+"\n")
    print("V7_BOOTSTRAP_CREATED",json.dumps({
        "train":len(train),"dev":len(dev),
        "verified_negative_train":sum(bool(x.get("verified_o_spans")) for x in train),
        "verified_negative_dev":sum(bool(x.get("verified_o_spans")) for x in dev),
        "partial_replay_docs":len(replay) if replay is not None else 0,
        "scope":"seven_only","no_real_user_data":True,
        "manual_annotation_review_required":True,
        "independent_blind_holdout":False,
        "out":str(out)
    },ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
