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

SYLLABLES = (
    "가","나","다","라","마","바","사","아","자","차",
    "하","별","솔","린","민","현","유","준","서","지",
    "윤","정","태","도","율","온","수","희","빈","아",
)
SURNAMES = ("김","박","이","최","정","조","강","윤","임","한","남궁","황보")
REGIONS = (
    "서울특별시","부산광역시","대구광역시","인천광역시",
    "대전광역시","경기도","강원특별자치도","충청남도",
    "전북특별자치도","경상남도","제주특별자치도",
)
ROADS = ("가람로","나래길","솔빛로","샘터길","푸른로","새봄길","하늘로","새빛길")
VILLAGES = ("가람동","새빛동","가상리","샘터동","나래리","솔빛동")
TRAIN_NAME_TEMPLATES = (
    "고객 성명: {PII} / 확인란에 서명했습니다.",
    "상담 요청자 {PII}에게 접수 사실을 전달했습니다.",
    "상담 기록에는 {PII}씨가 신청했다고 적혀 있습니다.",
    "승인 안내를 받은 사람은 {PII}입니다.",
    "처리 담당자가 기록한 신청인: {PII}",
    "수취인 {PII}께서 수정 요청을 제출했습니다.",
    "이름을 {PII}으로 변경해 주십시오.",
    "문의 글의 작성자({PII})와 확인했습니다.",
)
DEV_NAME_TEMPLATES = (
    "접수 현황을 확인한 뒤 신청자 {PII}에게 안내했습니다.",
    "실무 기록의 요청인 표기는 {PII}였습니다.",
    "검토 결과 {PII}님이 처음 접수한 건이었습니다.",
)
TRAIN_ADDRESS_TEMPLATES = (
    "주소: {PII} / 배송 정보",
    "고객이 알려 준 주소는 {PII}입니다.",
    "서류에 기재된 배송지는 {PII}였습니다.",
    "신규 수령 장소는 {PII}로 등록합니다.",
    "반송 대상 주소({PII})를 확인했습니다.",
    "변경 주소를 {PII}라고 전달했습니다.",
)
DEV_ADDRESS_TEMPLATES = (
    "우편 발송에 사용할 주소: {PII}",
    "주소 변경 내역에는 {PII}가 적혀 있었습니다.",
    "서류 수취 위치가 {PII}로 신고됐습니다.",
)
NEG_TRAIN = (
    "김 과장은 다음 회의의 일정을 정했습니다.",
    "요청 접수 번호는 단순 업무용 번호입니다.",
    "직원과 상담사는 업무 내용을 확인했습니다.",
    "이름이라는 항목에는 값이 입력되지 않았습니다.",
    "주소라는 항목에는 상세 내용이 없습니다.",
    "부산 지역은 이번 검토의 대상이 아닙니다.",
    "담당 부서는 서류의 형식만 검토했습니다.",
)
NEG_DEV = (
    "서울 지역의 일반적인 서비스 동향을 논의했습니다.",
    "이름과 직책이라는 제목만 있는 빈 표였습니다.",
    "검토자와 담당자는 결재 단계에서 협의했습니다.",
)


def made_name(rng, i):
    n = (1 if i%11 == 0 else 2 if i%4 == 0 else 3 if i%5 else 4)
    parts = [rng.choice(SYLLABLES) for _ in range(n)]
    if n == 1:
        return parts[0]
    if n == 2 and i%3 == 0:
        return rng.choice(SURNAMES) + parts[0]
    if n >= 3:
        full = rng.choice(SURNAMES) + "".join(parts[:n-1])
    else:
        full = "".join(parts)
    if i%7 == 0 and len(full) > 2:
        full = full[0] + " " + full[1:]
    if i%17 == 0 and len(full)>2:
        full = full[:-1] + "이"
    return full


def made_address(rng, i):
    region = rng.choice(REGIONS)
    code = rng.randrange(10, 950)
    unit = rng.randrange(1, 95)
    if i%3 == 0:
        base = region + " " + rng.choice(VILLAGES) + " " + str(code) + "-" + str(unit)
    else:
        base = region + " " + rng.choice(ROADS) + " " + str(code)
        if i%4 == 0:
            base += " " + str(unit) + "동 " + str(rng.randrange(101, 902)) + "호"
    if i%13 == 0:
        base = "(" + str(rng.randrange(10000,99999)) + ") " + base
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
    return {"id":doc_id,"text":text,"entities":[ent],"annotation_scope":"seven_only"}


def generate(seed, n, split):
    rng = random.Random(seed)
    names = TRAIN_NAME_TEMPLATES if split == "train" else DEV_NAME_TEMPLATES
    addresses = TRAIN_ADDRESS_TEMPLATES if split == "train" else DEV_ADDRESS_TEMPLATES
    negatives = NEG_TRAIN if split == "train" else NEG_DEV
    rows=[]
    for i in range(n):
        if i%2 == 0:
            val = made_name(rng, i + (10001 if split=="dev" else 0))
            template = rng.choice(names)
            rows.append(entry(f"{split}_person_{i:05d}", template, val))
        else:
            val = made_address(rng, i + (10001 if split=="dev" else 0))
            template = rng.choice(addresses)
            rows.append(entry(f"{split}_addr_{i:05d}", template, val))
    # Hard negatives are seven_only (no O labels). Their function is
    # for manual review and future complete annotation, NOT supervised FP suppression.
    for i in range(max(1,n//10)):
        rows.append({"id":f"{split}_negative_{i:05d}",
                     "text":negatives[i%len(negatives)],
                     "entities":[],"annotation_scope":"seven_only"})
    rng.shuffle(rows)
    return rows


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out",type=Path,default=Path("step2_privacy_rag/local_eval/v7_bootstrap"))
    cli.add_argument("--train-n",type=int,default=800)
    cli.add_argument("--dev-n",type=int,default=150)
    cli.add_argument("--seed",type=int,default=1234)
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
    out.mkdir(parents=True,exist_ok=True)
    for split,rows in (("train",train),("dev",dev)):
        path=out/(split+".jsonl")
        with path.open("w",encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row,ensure_ascii=False)+"\n")
    print("V7_BOOTSTRAP_CREATED",json.dumps({
        "train":len(train),"dev":len(dev),
        "scope":"seven_only","no_real_user_data":True,
        "manual_annotation_review_required":True,
        "independent_blind_holdout":False,
        "out":str(out)
    },ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
