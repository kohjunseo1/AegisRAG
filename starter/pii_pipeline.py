"""탐지 + 가명처리 모듈 (실전 2 · 팀 버전)

탐지기 = 실전 1에서 선정한 'KLUE-RoBERTa-small v1 (CPU 학습) + 후처리 + 규칙 기반 정규식'
         → 실전 1 노트북 ②-5 의 `trim+regex` 결과와 같은 순서로 동작하도록 옮김
           1) 모델 예측 (models/roberta_small_v1, CPU)
           2) PERSON_FIX : 한 글자 이름 제거, '윤서랑'·'윤서야' → '윤서'
           3) trim       : 앞뒤 공백·괄호, 이름 뒤 조사·호칭 제거
           4) post_union : NUM_VALIDATE → 정규식 숫자형 우선(NUM_REGEX_PRIORITY) + 주소 보강 → PHONE_EXT
가명처리 = 라벨별 정규화 → HMAC-SHA256(HMAC_KEY) 앞 8자리 → <PREFIX_xxxxxxxx>

스타터와의 약속 (바꾸지 말 것)
  detect(text)              -> [{"label", "start", "end"}]          원문 기준 [start, end)
  pseudonymize(text, spans) -> (masked_text, records)
      records: [{"label", "start", "end", "original", "normalized", "token"}]
  토큰은 TOKEN_PATTERN 에 맞음.  같은 값 → 문서·질의를 가리지 않고 항상 같은 토큰.

필요 파일
  starter/rules_detector.py   실전 1 ① 노트북의 규칙 탐지기 (그대로 복사)
  models/roberta_small_v1/    실전 1 ②-5 노트북이 저장한 모델 폴더 (경로는 .env 의 PII_MODEL_DIR 로 변경 가능)
"""
import hashlib
import hmac
import os
import re
from pathlib import Path

from . import config
from .detect_boost import enhance
from .rules_detector import detect as rule_detect

TOKEN_PATTERN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")
IS_DEMO = False
BACKEND = os.getenv("PII_BACKEND", "legacy")
if BACKEND not in ("legacy", "v7_onepass"):
    raise ValueError("UNKNOWN_PII_BACKEND")

# ---------------------------------------------------------------- 설정 (실전 1 v1 노트북과 동일)
MODEL_DIR = Path(os.getenv("PII_MODEL_DIR", str(config.ROOT / "models" / "roberta_small_v1")))
NUM_THREADS = int(os.getenv("PII_NUM_THREADS", "2"))   # 실전 1 측정 조건과 같은 CPU 코어 수
NUM_REGEX_PRIORITY = True
PERSON_FIX = True
NUM_VALIDATE = True
PHONE_EXT = True
# 실전 2에서 추가한 옵션 (실험 v5 에서 켜고 끄며 비교)
COUNTRY_CODE_FIX = True   # '+82 10-…' 을 모델이 '10-…' 까지만 잡은 경우 '+82 ' 까지 넓히고 전화번호로 교정
LINK_GIVEN_NAMES = True   # 같은 문서 안에서 '지영'(이름만)과 '최지영'이 함께 나오면 같은 토큰으로 묶음 (후보가 하나일 때만)
# 종합 단계에서 추가한 옵션 (.env 로 끄고 켜며 전후 비교)
BOOST_ADDRESS = os.getenv("PII_BOOST_ADDRESS", "1") == "1"   # 주소 경계 보정 + 지번 주소 추가
BOOST_PERSON = os.getenv("PII_BOOST_PERSON", "1") == "1"     # 강한 문맥 규칙 이름 합치기 + 외자·이름만 + 문서 내 전파
HYBRID = os.getenv("PII_HYBRID", "0") == "1"                # koelectra-pii 이름 보조 (hybrid_detector.py, 모델 다운로드 필요)
ENSEMBLE = os.getenv("PII_ENSEMBLE", "1") == "1"             # 모델 글자별 확률 + 약한 규칙 후보 검증 (임계값은 tune_ensemble 로)

LABELS7 = ["person", "phone_number", "email", "address",
           "resident_registration_number", "account_number", "card_number"]
NUM_LABELS = {"phone_number", "email", "resident_registration_number", "account_number", "card_number"}

# ================================================================ 1. 모델 예측
_tok = _model = None


def _load_model():
    global _tok, _model
    if _model is None:
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer
        if not MODEL_DIR.exists():
            raise FileNotFoundError(f"모델 폴더가 없습니다: {MODEL_DIR}  (.env 의 PII_MODEL_DIR 확인)")
        torch.set_num_threads(NUM_THREADS)
        tok = AutoTokenizer.from_pretrained(str(MODEL_DIR))
        model = AutoModelForTokenClassification.from_pretrained(str(MODEL_DIR)).eval()
        # The pinned RoBERTa checkpoint was saved with torchscript=True.
        # Eager inference expects ModelOutput.logits, not a TorchScript tuple.
        # Match the original benchmark's runtime-only config normalization.
        model.config.torchscript = False
        model.config.return_dict = True
        _tok, _model = tok, model
    return _tok, _model


def _chunks(text, max_chars=350):
    """줄 단위로 묶어 (시작위치, 조각) — 학습 때와 같은 방식"""
    out, start, buf = [], 0, ""
    for line in text.splitlines(keepends=True):
        if buf and len(buf) + len(line) > max_chars:
            out.append((start, buf))
            start += len(buf)
            buf = ""
        buf += line
    if buf:
        out.append((start, buf))
    return out


def _tags_to_spans(offsets, tags, off=0):
    spans, cur = [], None
    for (s, e), tag in zip(offsets, tags):
        if s == e:
            continue
        if tag == "O":
            if cur:
                spans.append(cur)
                cur = None
            continue
        p, lab = tag.split("-", 1)
        if cur and cur["label"] == lab and p == "I" and off + s <= cur["end"] + 1:
            cur["end"] = off + e
        else:
            if cur:
                spans.append(cur)
            cur = {"label": lab, "start": off + s, "end": off + e}
    if cur:
        spans.append(cur)
    return spans


def model_predict(text):
    import torch
    tok, model = _load_model()
    id2label = model.config.id2label
    spans = []
    with torch.no_grad():
        for off, piece in _chunks(text):
            enc = tok(piece, return_offsets_mapping=True, truncation=True, max_length=512, return_tensors="pt")
            offsets = enc.pop("offset_mapping")[0].tolist()
            enc.pop("token_type_ids", None)
            ids = model(**enc).logits[0].argmax(-1).tolist()
            spans += _tags_to_spans(offsets, [id2label[i] for i in ids], off)
    return spans


# ================================================================ 2. 후처리 (실전 1 노트북에서 그대로 옮김)
TRAIL = re.compile(r"(님|씨)?(께서|에게|이랑|랑|이가|이는|이를|이의|은|는|이|가|을|를|의|께|와|과|도|만|아|야)?$")
COMPOUND = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")
CALL = re.compile(r"(아|야|이랑|랑)$")


def _strip_josa(frag):
    m = TRAIL.search(frag)
    if not m or not m.group(0):
        return frag
    core = frag.replace(" ", "")
    if len(core) >= (5 if core.startswith(COMPOUND) else 4):
        return frag[:-len(m.group(0))]
    return frag


def _trim(text, sp):
    s, e = sp["start"], sp["end"]
    while s < e and text[s] in " \t\n,.()":
        s += 1
    while e > s and text[e - 1] in " \t\n,.():":
        e -= 1
    if sp["label"] == "person":
        frag = text[s:e]
        e -= len(frag) - len(_strip_josa(frag))
    return {**sp, "start": s, "end": e} if e > s else None


def _fix_person(text, spans):
    names = [text[s["start"]:s["end"]].replace(" ", "") for s in spans if s["label"] == "person"]
    out = []
    for s in spans:
        if s["label"] == "person":
            core = text[s["start"]:s["end"]].replace(" ", "")
            if len(core) < 2:
                continue
            m = CALL.search(core)
            if m and len(core) - len(m.group(0)) == 2:
                given = core[:2]
                if any(n != core and len(n) >= 3 and n.endswith(given) for n in names):
                    s = {**s, "end": s["end"] - len(m.group(0))}
        out.append(s)
    return out


def _union(model_spans, rule_spans):
    if not NUM_REGEX_PRIORITY:
        out = list(model_spans)
        for r in rule_spans:
            if r["label"] != "person" and not any(r["start"] < m["end"] and m["start"] < r["end"] for m in out):
                out.append(r)
        return sorted(out, key=lambda x: x["start"])
    rx = [r for r in rule_spans if r["label"] in NUM_LABELS]
    kept = [m for m in model_spans if not any(m["start"] < r["end"] and r["start"] < m["end"] for r in rx)]
    out = kept + rx
    for r in rule_spans:
        if r["label"] == "address" and not any(r["start"] < m["end"] and m["start"] < r["end"] for m in out):
            out.append(r)
    return sorted(out, key=lambda x: x["start"])


PHONE_EXT_RES = [
    re.compile(r"(?<![\d+])\+82[-. ]?(?:\(0\)|0)?[-. ]?(?:1[016789]|2|[3-6][1-5]|70)[-. )]?\d{3,4}[-. ]?\d{4}(?![\d-])"),
    re.compile(r"(?<![\d+.-])82[-. ](?:1[016789])[-. ]\d{3,4}[-. ]\d{4}(?![\d-])"),
    re.compile(r"(?<![\d.-])0(?:2|[3-6][1-5]|70|1[016789])[-. ]\d{7,8}(?![\d-])"),
    re.compile(r"(?<![\d(])\(0(?:2|[3-6][1-5]|70)\)[ ]?\d{3,4}[-. ]?\d{4}(?![\d-])"),
    re.compile(r"(?<![\d.-])050\d?[-. ]?\d{3,4}[-. ]?\d{4}(?![\d-])"),
]
SERVICE_RE = re.compile(r"1[5-9]\d{2}[-. ]?\d{4}")
PHONE_DIGITS = re.compile(r"0(?:1[016789]\d{7,8}|2\d{7,8}|[3-6][1-5]\d{7,8}|70\d{8}|50\d{8,9})")
ACCOUNT_CTX = re.compile(r"계좌|입금|출금|송금|이체|은행|예금|통장|환불|납부|account", re.I)
RRN_SHAPE = re.compile(r"\d{6}\s*[-.]?\s*\d{7}")
CARD_SHAPE = re.compile(r"\d{4}([-. ]?)\d{4}\1\d{4}\1\d{2,4}|\d{4}[-. ]?\d{6}[-. ]?\d{5}")


def _extra_phones(text, taken):
    out = []
    for rx in PHONE_EXT_RES:
        for m in rx.finditer(text):
            s, e = m.start(), m.end()
            if not any(s < te and ts < e for ts, te in taken):
                out.append({"label": "phone_number", "start": s, "end": e})
                taken.append((s, e))
    return out


def _possible_labels(t, before):
    t = t.strip()
    if SERVICE_RE.fullmatch(t):
        return []
    d = re.sub(r"\D", "", t)
    if re.match(r"\+?82\D", t):
        d = "0" + d[2:].lstrip("0")
    out = []
    if PHONE_DIGITS.fullmatch(d):
        out.append("phone_number")
    if RRN_SHAPE.fullmatch(t) or len(d) == 13:
        out.append("resident_registration_number")
    if CARD_SHAPE.fullmatch(t) or 14 <= len(d) <= 16:
        out.append("card_number")
    if 10 <= len(d) <= 16:
        date_like = re.match(r"(19|20)\d{2}(0[1-9]|1[0-2])", d) and len(d) >= 12 and not RRN_SHAPE.fullmatch(t)
        if not date_like or ACCOUNT_CTX.search(before):
            out.append("account_number")
    return out


def _validate_numeric(text, model_spans, rule_spans):
    rx = [r for r in rule_spans if r["label"] in NUM_LABELS]
    out = []
    for m in model_spans:
        if m["label"] not in NUM_LABELS or any(m["start"] < r["end"] and r["start"] < m["end"] for r in rx):
            out.append(m)
            continue
        t = text[m["start"]:m["end"]]
        if m["label"] == "email":
            if "@" in t or " at " in t or "골뱅이" in t:
                out.append(m)
            continue
        ok = _possible_labels(t, text[max(0, m["start"] - 30):m["start"]])
        if not ok:
            continue
        out.append(m if m["label"] in ok else {**m, "label": ok[0]})
    return out


def _post_union(text, model_spans, rule_spans):
    if NUM_VALIDATE:
        model_spans = _validate_numeric(text, model_spans, rule_spans)
    out = _union(model_spans, rule_spans)
    if PHONE_EXT:
        out = sorted(out + _extra_phones(text, [(o["start"], o["end"]) for o in out]), key=lambda x: x["start"])
    return out


# ---------------------------------------------------------------- 실전 2 추가 후처리
CC_LEFT = re.compile(r"\+?82[-. ]?$")


def _fix_country_code(text, spans):
    """'+82 10-0971-7912' 에서 '10-0971-7912' 만 잡힌 숫자형 → '+82 ' 까지 넓히고 phone_number 로 교정.
    이렇게 해야 질의의 '010-0971-7912' 와 같은 정규화 값(01009717912)이 되어 같은 토큰이 됨."""
    out = []
    for s in spans:
        if s["label"] in NUM_LABELS - {"email"}:
            m = CC_LEFT.search(text[max(0, s["start"] - 4):s["start"]])
            if m and re.match(r"1[016789]", text[s["start"]:s["end"]]):
                s = {**s, "start": s["start"] - len(m.group(0)), "label": "phone_number"}
        out.append(s)
    return out


def _drop_overlaps(spans):
    kept, end = [], -1
    for s in sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"]))):
        if s["start"] >= end:
            kept.append(s)
            end = s["end"]
    return kept


# ================================================================ 3. detect
def detect(text, use_model=True):
    """use_model=False 는 모델 없이 규칙 탐지기만 쓰는 비교 실험(B)용
    PII_SPANS_CACHE 가 설정되면 외부 탐지기 결과를 그대로 씀 (starter/span_cache.py, 검색 비교용)
    PII_V6_URL 이 설정되면 하이브리드 + V6 통합 (starter/detect_merge.py), V6 실패 시 하이브리드 단독"""
    return detect_info(text, use_model)[0]


def detect_info(text, use_model=True, *, benchmark_only=False):
    """(spans, info) — info: {"source": cache|hybrid|merge|hybrid_fallback, "review": [...], "v6_status": ...}
    review 는 한쪽 탐지기만 잡은 구간 (위치·라벨만, 원문 없음) → 적재 때 검수 대기열에 기록"""
    if BACKEND == "v7_onepass":
        if not benchmark_only or not use_model:
            raise RuntimeError("V7_E2E_NOT_APPROVED")
        from .v7_onepass import detect_info as detect_v7
        return detect_v7(text)
    from .span_cache import lookup
    cached = lookup(text)
    if cached is not None:
        return _drop_overlaps(cached), {"source": "cache", "review": []}
    hyb = _detect_hybrid(text, use_model)
    from . import v6_client
    if not (use_model and v6_client.enabled()):
        return hyb, {"source": "hybrid", "review": []}
    v6 = v6_client.detect_one(text)
    if v6 is None:
        return hyb, {"source": "hybrid_fallback", "review": []}
    from .detect_merge import merge
    spans, review = merge(hyb, v6["spans"])
    return _drop_overlaps(spans), {"source": "merge", "review": review, "v6_status": v6["status"]}


def _detect_hybrid(text, use_model=True):
    rules = rule_detect(text)
    if not use_model:
        spans = rules + _extra_phones(text, [(r["start"], r["end"]) for r in rules]) if PHONE_EXT else rules
    else:
        sp = model_predict(text)
        if PERSON_FIX:
            sp = _fix_person(text, sp)
        sp = [x for x in (_trim(text, s) for s in sp) if x]
        spans = _post_union(text, sp, rules)
    if COUNTRY_CODE_FIX:
        spans = _fix_country_code(text, spans)
    spans = [{"label": s["label"], "start": s["start"], "end": s["end"]} for s in spans if s["label"] in LABELS7]
    if use_model and ENSEMBLE:          # 모델 확률 × 규칙 후보 앙상블 (model_ensemble.py)
        from .model_ensemble import ensemble_persons, person_probs
        spans = ensemble_persons(text, person_probs(text), spans)
    if BOOST_ADDRESS or BOOST_PERSON:   # 종합 단계 보강: 주소 경계·이름 미탐·문서 내 전파 (detect_boost.py)
        spans = enhance(text, spans, address=BOOST_ADDRESS, person=BOOST_PERSON,
                        kiwi=globals().get("_kiwi"))   # _kiwi 가 있으면 규칙 이름 판정에 고유명사(NNP) 단서 사용
    if use_model and HYBRID:            # RoBERTa × koelectra: 넓게 잡고 근거로 거름 (검수 목록은 detect_with_review)
        from .hybrid_detector import combine, koelectra
        spans = combine(text, spans, koelectra().predict(text), kiwi=globals().get("_kiwi"))[0]
    return _drop_overlaps(spans)


# ================================================================ 4. 정규화 + 가명처리
PREFIX = {"person": "PERSON", "phone_number": "PHONE", "email": "EMAIL", "address": "ADDRESS",
          "resident_registration_number": "RRN", "account_number": "ACCOUNT", "card_number": "CARD"}
JOSA_TAIL = ("께서", "에게", "이랑", "님", "씨", "은", "는", "이", "가", "을", "를", "의", "랑", "도", "만", "과", "와")
SIDO_SHORT = [("서울특별시", "서울"), ("부산광역시", "부산"), ("대구광역시", "대구"), ("인천광역시", "인천"),
              ("광주광역시", "광주"), ("대전광역시", "대전"), ("울산광역시", "울산"), ("세종특별자치시", "세종"),
              ("경기도", "경기"), ("강원특별자치도", "강원"), ("강원도", "강원"), ("충청북도", "충북"), ("충청남도", "충남"),
              ("전북특별자치도", "전북"), ("전라북도", "전북"), ("전라남도", "전남"), ("경상북도", "경북"),
              ("경상남도", "경남"), ("제주특별자치도", "제주")]


def _digits(v):
    d = re.sub(r"\D", "", v)
    if re.match(r"\s*\+?82\D", v) and d.startswith("82"):   # +82 10-1234-5678 → 01012345678
        d = "0" + d[2:].lstrip("0")
    return d


def normalize(label, value):
    v = value.strip()
    if label == "person":
        v = re.sub(r"\s+", "", v)
        base = 4 if v[:2] in COMPOUND else 3
        for j in JOSA_TAIL:   # 탐지기가 조사까지 잡은 경우만 (이름 길이 보호)
            if v.endswith(j) and len(v) - len(j) >= base:
                return v[:-len(j)]
        return v
    if label in ("phone_number", "resident_registration_number", "account_number", "card_number"):
        return _digits(v)
    if label == "email":
        return v.lower()
    if label == "address":
        for full, short in SIDO_SHORT:
            if v.startswith(full):
                v = short + v[len(full):]
                break
        return re.sub(r"[\s,]", "", v)
    return re.sub(r"\s+", "", v)


def _key():
    if not config.HMAC_KEY:
        raise RuntimeError("HMAC_KEY 가 비어 있습니다 (.env 확인, 팀 전원 같은 값, 커밋 금지)")
    return config.HMAC_KEY.encode()


NUMERIC = ("phone_number", "resident_registration_number", "account_number", "card_number")


def _token(label, normalized):
    # 번호류는 라벨 대신 'num' 으로 해시하고, 접두어도 숫자 모양으로 정함
    # → 모델이 같은 번호를 문서에서는 계좌, 질의에서는 전화번호로 잡아도 같은 토큰이 됨
    if label in NUMERIC:
        ns = "num"
        prefix = "PHONE" if PHONE_DIGITS.fullmatch(normalized) else PREFIX[label]
    else:
        ns, prefix = label, PREFIX[label]
    h = hmac.new(_key(), f"{ns}:{normalized}".encode(), hashlib.sha256).hexdigest()[:8]
    return f"<{prefix}_{h}>"


def pseudonymize(text, spans, override=None):
    """override: {(start, end): token} — 동명이인 구분 등으로 토큰을 바꿀 구간 (person_resolver.overrides)"""
    spans = _drop_overlaps(spans)
    norms = [normalize(s["label"], text[s["start"]:s["end"]]) for s in spans]

    if LINK_GIVEN_NAMES:   # 같은 문서 안에서만, 후보가 정확히 하나일 때만 연결
        fulls = {n for s, n in zip(spans, norms) if s["label"] == "person" and len(n) >= 3}
        for i, (s, n) in enumerate(zip(spans, norms)):
            if s["label"] == "person" and len(n) == 2:
                cands = [f for f in fulls if f.endswith(n)]
                if len(cands) == 1:
                    norms[i] = cands[0]

    out, last, records = [], 0, []
    for s, n in zip(spans, norms):
        token = (override or {}).get((s["start"], s["end"])) or _token(s["label"], n)
        out += [text[last:s["start"]], token]
        last = s["end"]
        records.append({"label": s["label"], "start": s["start"], "end": s["end"],
                        "original": text[s["start"]:s["end"]], "normalized": n, "token": token})
    out.append(text[last:])
    return "".join(out), records


if __name__ == "__main__":   # 동작 확인: python -m starter.pii_pipeline
    import time
    ex = "윤 선영 고객(01003492063)이 추가 한도를 문의함. 윤선영은 서류를 준비함. 문의: 1599-0000"
    t0 = time.perf_counter()
    sp = detect(ex)
    print(f"탐지 {(time.perf_counter() - t0) * 1000:.0f} ms (첫 실행은 모델 로딩 포함)")
    for s in sp:
        print(f"  {s['label']:<15} {ex[s['start']:s['end']]}")
    masked, recs = pseudonymize(ex, sp)
    print("가명처리:", masked)
    q = "윤선영 고객 대출 상담 내역 찾아줘"
    print("질의    :", pseudonymize(q, detect(q))[0])
