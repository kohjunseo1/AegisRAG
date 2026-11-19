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

# CURATED V7 synthetic PILOT, not the final large-scale training corpus.
# "High confidence" is defined by explicit PERSON fields/anchored references
# or full personal address fields, not by token shape alone.
# A disjoint reference "surname + title" is routed to ROLE_REFERENCE logic,
# NEVER mislabeled as a known HMAC person or supervised O.
#
# An unanchored one-character token (e.g. "박") is *not* a labeled person.
# An anchored value in "신청인 이름: 솔" is a valid supervised PERSON example.
GIVEN_NAMES = (
    "서준", "지우", "도윤", "민서", "유진", "하윤", "시우", "수아",
    "지민", "현서", "준서", "나연", "예린", "예서", "가은", "수빈",
    "은우", "채린", "지현", "준혁", "서연", "재민", "지호", "예지",
    "윤서", "태윤", "서우", "승민", "가윤", "민준", "소윤", "지안",
    "하늘", "보라", "한결", "가람", "나래",
)
ONE_SYLLABLE_GIVEN = ("솔", "린", "봄", "별", "온", "율", "담", "결", "빛", "찬", "민", "현", "수")
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

# Hash the entire PERSON surface form so no exact PERSON value is shared
# between train and dev, even across different case families.
def belongs_to_split(value, split):
    bucket = int(hashlib.sha256(value.replace(" ", "").encode()).hexdigest()[:8],16) % 5
    return (bucket == 0) == (split == "dev")


def make_person_pools(split):
    pools = {
        "full_name": [s+g for s in (*SURNAMES,*COMPOUND_SURNAMES) for g in GIVEN_NAMES],
        "short_full_name": [s+g for s in SURNAMES for g in ONE_SYLLABLE_GIVEN],
        "given_only_explicit": list(GIVEN_NAMES),
        "single_name_explicit": list(ONE_SYLLABLE_GIVEN),
        "repeated_name_particle": [s+g for s in SURNAMES for g in GIVEN_NAMES],
    }
    return {key: tuple(v for v in vals if belongs_to_split(v,split))
            for key,vals in pools.items()}


NAME_POOLS = {split: make_person_pools(split) for split in ("train","dev")}
assert all(all(pool for pool in groups.values()) for groups in NAME_POOLS.values())

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
# The first name occurrence is unambiguous due to its explicit named field;
# the second occurrence tests morphological boundaries while referencing the
# same confirmed individual. Both spans are annotated PERSON, particles are NOT.
TRAIN_REPEATED_TEMPLATES = (
    "고객 성명: {PII}\n동일 고객 {PII}이 서류를 제출했습니다.",
    "신청인 이름: {PII}\n기록상 {PII}에게 안내했습니다.",
    "계약자 성명: {PII}\n이후 {PII}은 정보를 수정했습니다.",
)
DEV_REPEATED_TEMPLATES = (
    "수령인 이름: {PII}\n이전 안내에서 {PII}의 서명을 확인했습니다.",
    "본인 성명: {PII}\n확인 과정에서 {PII}을 다시 호출했습니다.",
)
# The grammatical attachment is annotated *outside* PERSON. For names ending
# without final consonant, some forms are awkward, so use neutral endings:
# "에게", "의" are safe after almost any Korean full name.
TRAIN_REPEATED_TEMPLATES = (
    "고객 성명: {PII}\n동일 고객 {PII}에게 안내했습니다.",
    "신청인 이름: {PII}\n재접수 건에서 {PII}의 요청을 확인했습니다.",
    "계약자 성명: {PII}\n이후 {PII}께서는 정보를 수정했습니다.",
)
DEV_REPEATED_TEMPLATES = (
    "수령인 이름: {PII}\n이전 안내에서 {PII}의 서명을 확인했습니다.",
    "본인 성명: {PII}\n확인 과정에서 {PII}에게 다시 문의했습니다.",
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

# Definitely non-PERSON contexts: "하늘이 맑습니다" is a linguistic negative
# control, NOT evidence that V6/A.X actually emitted an FP on that sentence.
# Only the explicitly indicated TERM substring is marked verified O.
TRAIN_HARD_NEGATIVES = (
    ("업무서식의 열 제목: [{TERM}] / 값은 비어 있습니다.", "주소", "field_header"),
    ("부서 분류명: [{TERM}] / 인사 공지입니다.", "고객지원팀", "organization"),
    ("상품 카탈로그의 제품명: [{TERM}] / 재고 확인", "솔빛알림", "product"),
    ("문서 제목: [{TERM}] / 내용은 별도로 작성", "구매검토표", "field_header"),
    ("시스템 메뉴 이름: [{TERM}] / 메뉴 설정", "주소관리", "field_header"),
    ("장부의 입력 항목명: [{TERM}] / 실제 기재값 없음", "고객성명", "field_header"),
    ("날씨 관측 문장: {TERM}이 맑습니다.", "하늘", "common_noun"),
    ("기상 기록에는 {TERM}이 환하게 빛났다고 적혀 있습니다.", "별", "common_noun"),
    ("농업 보고서: 올봄 {TERM}이 조금씩 피었습니다.", "꽃", "common_noun"),
)
DEV_HARD_NEGATIVES = (
    ("운영 서식의 항목명: [{TERM}] / 데이터는 없습니다.", "주소", "field_header"),
    ("부서 목록의 팀 이름: [{TERM}] / 소속 분류", "관리지원팀", "organization"),
    ("발주 관리의 상품명: [{TERM}] / 재고 목록", "나래알림", "product"),
    ("날씨 관측 결과: 오늘은 {TERM}이 흐립니다.", "하늘", "common_noun"),
    ("자연 관찰 일지: {TERM}이 밤에 보입니다.", "별", "common_noun"),
)


def made_name(rng, i, split="train", family="full_name"):
    name = rng.choice(NAME_POOLS[split][family])
    # Only "full_name" gets internal spacing; others are short spans whose
    # exact boundary matters. Field anchoring makes single syllable person
    # supervision precise and auditable.
    if family == "full_name" and i % 17 == 0:
        return name[0]+" "+name[1:]
    return name


def made_address(rng, i):
    region, district = rng.choice(REGION_DISTRICTS)
    number = rng.randrange(20, 900)
    if i % 3 == 0:
        base = f"{region} {district} {rng.choice(VILLAGES)} {number}-{rng.randrange(1,89)}"
    else:
        base = f"{region} {district} {rng.choice(ROADS)} {number}"
    if i % 4 == 0:
        base += f" {rng.randrange(101,109)}동 {rng.randrange(101,908)}호"
    # Postal code inclusion in ADDRESS Gold remains unresolved. Protect its
    # digits via the existing structural sentinel until team label policy
    # specifies whether ZIP must be included in the ADDRESS exact-match span.
    return base


def entry(doc_id, template, value, family):
    """Label *all* confirmed occurrences of PII; never lose repeated spans."""
    marker="{PII}"
    if marker not in template:
        raise ValueError("MISSING_PII_PLACEHOLDER")
    parts=template.split(marker)
    text=parts[0]
    positions=[]
    for tail in parts[1:]:
        start=len(text)
        text += value
        positions.append((start,start+len(value)))
        text += tail
    label="address" if family.startswith("address_") else "person"
    ents=[{"label":label,"start":a,"end":b} for a,b in positions]
    if not all(text[e["start"]:e["end"]]==value for e in ents):
        raise ValueError("INVALID_UNICODE_OFFSET")
    return {"id":doc_id,"text":text,"entities":ents,
            "annotation_scope":"seven_only","quality_tier":"high_confidence",
            "case_family":family,"label_policy":"v7_explicit_person_or_full_address_v2"}


# Four of every ten positives are full addresses; of the six PERSON cases,
# one is a short surname+given, one a given-only 2-character field, one a
# one-character name in a signed explicit field, and one a confirmed repeated
# person mention with particle boundary. Never label surname+role as HMAC PII.
CASE_CYCLE = (
    "full_name", "address_road", "short_full_name", "address_lot",
    "given_only_explicit", "address_detail", "full_name",
    "single_name_explicit", "address_road", "repeated_name_particle",
)


def generate(seed, n, split):
    if split not in ("train","dev"):
        raise ValueError("UNKNOWN_SPLIT")
    rng=random.Random(seed)
    templates_name=TRAIN_NAME_TEMPLATES if split=="train" else DEV_NAME_TEMPLATES
    templates_repeated=TRAIN_REPEATED_TEMPLATES if split=="train" else DEV_REPEATED_TEMPLATES
    templates_address=TRAIN_ADDRESS_TEMPLATES if split=="train" else DEV_ADDRESS_TEMPLATES
    negatives_with_targets=TRAIN_HARD_NEGATIVES if split=="train" else DEV_HARD_NEGATIVES
    rows=[]
    seen_text=set()
    seen_names=set()
    for i in range(n):
        family=CASE_CYCLE[i % len(CASE_CYCLE)]
        for retry in range(500):
            if family.startswith("address_"):
                value=made_address(rng,i+retry+(10001 if split=="dev" else 0))
                # Road/lot are explicit in the payload, not just in metadata.
                if family=="address_road" and not any(road+" " in value for road in ROADS):
                    continue
                if family=="address_lot" and not any(village+" " in value for village in VILLAGES):
                    continue
                if family=="address_detail" and not ("동 " in value and value.endswith("호")):
                    # Force a fully specified apartment suffix for this case.
                    value+=" 103동 204호"
                template=rng.choice(templates_address)
            else:
                value=made_name(rng,i+retry,split,family if family!="repeated_name_particle" else "repeated_name_particle")
                template=rng.choice(templates_repeated if family=="repeated_name_particle" else templates_name)
            row=entry(f"{split}_{family}_{i:05d}",template,value,family)
            if row["text"] in seen_text:
                continue
            seen_text.add(row["text"])
            rows.append(row)
            if family in NAME_POOLS[split]:
                seen_names.add(value.replace(" ",""))
            break
        else:
            raise RuntimeError("CURATED_DATA_DIVERSITY_EXHAUSTED: increase name/template variation")
    benign_purposes=("정기점검","내부검토","분기보고","업무정리","사전확인",
                     "품질검토","자료편집","분류작업","처리기록")
    benign_sections=("분류표","요약문","업무자료","참조표","검토자료",
                     "기록표","안내서","상태표","추가설명")
    for i in range(max(1,n//5)):
        template,term,kind=negatives_with_targets[i%len(negatives_with_targets)]
        start=template.index("{TERM}")
        core=template.replace("{TERM}",term)
        prefix=(benign_purposes[(i//len(negatives_with_targets))%len(benign_purposes)]
                +" "+benign_sections[(i//(len(negatives_with_targets)*len(benign_purposes)))%len(benign_sections)]+": ")
        text=prefix+core
        start+=len(prefix)
        if text in seen_text:
            raise ValueError("CURATED_NEGATIVE_DUPLICATE")
        seen_text.add(text)
        rows.append({"id":f"{split}_negative_{i:05d}","text":text,
                     "entities":[],"annotation_scope":"seven_only",
                     "quality_tier":"high_confidence",
                     "case_family":"negative_"+kind,
                     "label_policy":"v7_explicit_person_or_full_address_v2",
                     "verified_o_spans":[{"start":start,"end":start+len(term),"text":term}]})
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
    # Check document exact fingerprints and PERSON surface overlap; the
    # generator is a dev/test harness, not an independent family-level split.
    train_names={r["text"][e["start"]:e["end"]].replace(" ","")
                 for r in train for e in r["entities"] if e["label"]=="person"}
    dev_names={r["text"][e["start"]:e["end"]].replace(" ","")
               for r in dev for e in r["entities"] if e["label"]=="person"}
    if train_names & dev_names:
        raise ValueError("TRAIN_DEV_PERSON_VALUE_COLLISION")
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
        "family_counts_train":{t:sum(row["case_family"]==t for row in train)
                               for t in sorted({row["case_family"] for row in train})},
        "family_counts_dev":{t:sum(row["case_family"]==t for row in dev)
                             for t in sorted({row["case_family"] for row in dev})},
        "scope":"seven_only","no_real_user_data":True,
        "manual_annotation_review_required":True,
        "independent_blind_holdout":False,
        "out":str(out)
    },ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
