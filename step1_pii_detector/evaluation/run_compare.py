"""개인정보 탐지 결과 채점기 (run_compare.py, 표준 라이브러리만 사용).

탐지기는 각자 구현하고, 결과를 아래 형식의 jsonl로 저장해 채점합니다.

예측 파일 형식 (문서 1건 = 1줄)
    {"id": "D0002", "entities": [{"label": "person", "start": 53, "end": 57}, ...], "ms": 12.3}
    - label (7종 밖 라벨은 P/R에서 제외되고 오류 CSV에만 오탐으로 남음): person, phone_number, email, address, resident_registration_number, account_number, card_number
    - start/end: 원문 기준 글자 위치, [start, end) 반열린 구간 (파이썬 슬라이스와 같음)
    - ms: (선택) 이 문서 처리 시간(밀리초). 있으면 평균을 표에 표시
    - 정답 파일에 있는 문서가 예측 파일에 없으면 '아무것도 못 찾음'으로 채점

사용법
    # 여러 시스템 비교 (이름=파일)
    python run_compare.py --gold test.jsonl --pred rules=preds/rules_test.jsonl --pred model=preds/model_test.jsonl

    # 같은 시스템을 여러 번 돌린 결과로 일관성 측정 (첫 파일로 성능 채점)
    python run_compare.py --gold dev.jsonl --pred llm=run1.jsonl,run2.jsonl,run3.jsonl

지표
    strict   : 라벨·시작·끝이 모두 정답과 같아야 정답
    partial  : 라벨이 같고 위치가 겹치면 정답 (경계 오류 허용, 예측 하나는 정답 하나에만 매칭)
    mask_rec : 라벨과 무관하게, 정답 개인정보 글자 중 예측 구간이 덮은 글자 비율 (가려졌는가)

어려운 사례 표
    정답 파일의 hard_cases(문서 태그)와 person 개체의 form(full/spaced/given)을 사용합니다.
    Holdout 보강 태그(card/id/account/address_format_variant, single_char_given_name,
    compound_surname)는 태그가 붙은 문서 안에서 해당 변형 개체만 골라 재현율을 계산하며,
    정답 파일에 그 태그가 없으면 표에 나오지 않습니다.
"""
import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from itertools import combinations

LABELS = ["person", "phone_number", "email", "address",
          "resident_registration_number", "account_number", "card_number"]


# ------------------------------------------------------------------ 입출력
def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_gold(path):
    docs = load_jsonl(path)
    for d in docs:
        d["gold"] = [e for e in d["entities"] if e["label"] in LABELS]
        for e in d["gold"]:
            assert d["text"][e["start"]:e["end"]] == e["text"], (d["id"], e)
    return docs


def load_pred(path, docs):
    """정답 문서 순서에 맞춘 예측 목록과 처리 시간 목록을 돌려줌."""
    by_id = {r["id"]: r for r in load_jsonl(path)}
    preds, ms, bad = [], [], Counter()
    for d in docs:
        r = by_id.get(d["id"], {"entities": []})
        ents = []
        for e in r.get("entities", []):
            s, t = int(e["start"]), int(e["end"])
            if e.get("label") not in LABELS:
                bad["7종 밖 라벨(P/R 채점 제외, 오류 CSV에는 오탐으로 기록)"] += 1
            if not (0 <= s < t <= len(d["text"])):
                bad["위치 범위 오류(제외)"] += 1
                continue
            ents.append({"label": e.get("label"), "start": s, "end": t, "text": d["text"][s:t],
                         "confidence": e.get("confidence", "")})
        preds.append(ents)
        if "ms" in r:
            ms.append(float(r["ms"]))
    missing = sum(d["id"] not in by_id for d in docs)
    if missing:
        bad["예측 파일에 없는 문서(빈 예측 처리)"] = missing
    for k, v in bad.items():
        print(f"  ⚠ {os.path.basename(path)}: {k} {v}건")
    return preds, (sum(ms) / len(ms) if ms else None)


# ------------------------------------------------------------------ 채점
def overlap(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def key(e):
    return (e["label"], e["start"], e["end"])


def prf(tp, n_pred, n_gold):
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gold if n_gold else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def count(docs, preds):
    """라벨별 카운트 (원본 scoring.score와 같은 정의).
    - strict_tp : 라벨·시작·끝 완전 일치
    - partial_tp: 같은 라벨끼리 겹치면 1:1로 짝지음 (예측 하나는 정답 하나에만 쓰임)
    - chars / chars_masked: 정답 글자 수 / 라벨 무관하게 어떤 예측으로든 덮인 글자 수
    7종 밖 라벨 예측은 pred 수에 넣지 않음 (P/R 계산 제외)"""
    c = defaultdict(Counter)
    for d, P in zip(docs, preds):
        G = d["gold"]
        pkeys = {key(p) for p in P}
        for lab in LABELS:
            g = [x for x in G if x["label"] == lab]
            p = [x for x in P if x["label"] == lab]
            k = c[lab]
            k["gold"] += len(g)
            k["pred"] += len(p)
            k["strict_tp"] += sum(key(x) in pkeys for x in g)
            used = set()
            for x in g:
                for j, y in enumerate(p):
                    if j not in used and overlap(x, y):
                        used.add(j)
                        k["partial_tp"] += 1
                        break
            for x in g:
                cov = set()
                for y in P:
                    cov.update(range(max(x["start"], y["start"]), min(x["end"], y["end"])))
                k["chars"] += x["end"] - x["start"]
                k["chars_masked"] += len(cov)
    return c


def summarize(docs, preds):
    c = count(docs, preds)
    t = Counter()
    for v in c.values():
        t.update(v)
    sp, sr, sf = prf(t["strict_tp"], t["pred"], t["gold"])
    pp, pr, pf = prf(t["partial_tp"], t["pred"], t["gold"])
    return {"strict_p": sp, "strict_r": sr, "strict_f1": sf,
            "partial_p": pp, "partial_r": pr, "partial_f1": pf,
            "mask_rec": t["chars_masked"] / t["chars"] if t["chars"] else 0.0}, c


HARD_TAGS = ("josa_attached", "spaced_name", "given_only", "phone_format_variant", "homonym")

# [추가] Holdout 보강 태그: 태그가 붙은 문서 안에서, 해당 변형에 해당하는 개체만 골라 재현율을 계산
COMPOUND_SURNAMES = ("남궁", "제갈", "선우", "황보", "독고", "사공", "서문")
VARIANT_TAGS = {
    "card_format_variant": ("card_number",
                            lambda e: not re.fullmatch(r"9410-\d{4}-\d{4}-\d{4}", e["text"])),
    "id_format_variant": ("resident_registration_number", lambda e: "-" not in e["text"]),
    "account_format_variant": ("account_number",
                               lambda e: not re.fullmatch(r"\d{3}-\d{6}-\d{2}", e["text"])),
    "address_format_variant": ("address", lambda e: True),
    "single_char_given_name": ("person",
                               lambda e: e.get("form") != "given" and len(e["text"].replace(" ", "")) == 2),
    "compound_surname": ("person",
                         lambda e: e.get("form") != "given" and e["text"].startswith(COMPOUND_SURNAMES)),
}


def hard_case_table(docs, preds):
    """원본 hard_case_recall: 태그 문서의 이름(phone_format_variant는 전화번호) partial 재현율.
    [추가] 항목: 이름 표기 형태별 재현율, 미끼 태그 문서의 오탐 수."""
    out = {}
    for tag in HARD_TAGS:
        lab = "phone_number" if tag == "phone_format_variant" else "person"
        hit = n = 0
        for d, P in zip(docs, preds):
            if tag not in d.get("hard_cases", []):
                continue
            for g in d["gold"]:
                if g["label"] == lab:
                    n += 1
                    hit += any(p["label"] == lab and overlap(g, p) for p in P)
        out[f"{tag} ({lab})"] = (hit / n if n else None, n, "recall")
    for tag, (lab, want) in VARIANT_TAGS.items():
        hit = n = 0
        for d, P in zip(docs, preds):
            if tag not in d.get("hard_cases", []):
                continue
            for g in d["gold"]:
                if g["label"] == lab and want(g):
                    n += 1
                    hit += any(p["label"] == lab and overlap(g, p) for p in P)
        if n:
            out[f"{tag} ({lab})"] = (hit / n, n, "recall")
    for form in ("full", "spaced", "given"):
        n = hit = 0
        for d, P in zip(docs, preds):
            for g in d["gold"]:
                if g["label"] == "person" and g.get("form") == form:
                    n += 1
                    hit += any(p["label"] == g["label"] and overlap(g, p) for p in P)
        out[f"[추가] person form={form}"] = (hit / n if n else None, n, "recall")
    for tag in ("decoy_company_phone", "decoy_words", "decoy_numbers", "no_pii"):
        sel = [(d, P) for d, P in zip(docs, preds) if tag in d.get("hard_cases", [])]
        if sel:
            fp = sum(1 for d, P in sel for p in P if not any(overlap(g, p) for g in d["gold"]))
            out[f"[추가] {tag} 오탐수"] = (fp, len(sel), "fp")
    return out


def ctx(text, s, e, w=15):
    return (text[max(0, s - w):s] + "【" + text[s:e] + "】" + text[e:e + w]).replace("\n", " ")


def error_rows(docs, preds, name):
    """원본과 같은 유형: 경계 오류 / 라벨 오류(→예측라벨) / 미탐 / 오탐."""
    rows = []
    for d, P in zip(docs, preds):
        G, T = d["gold"], d["text"]
        for g in G:
            same = [p for p in P if p["label"] == g["label"] and overlap(g, p)]
            if any((p["start"], p["end"]) == (g["start"], g["end"]) for p in same):
                continue  # 정확 일치
            other = [p for p in P if overlap(g, p)]
            if same:
                typ, p = "경계 오류", same[0]
            elif other:
                typ, p = f"라벨 오류(→{other[0]['label']})", other[0]
            else:
                typ, p = "미탐", None
            rows.append([name, d["id"], typ, g["label"], g["text"],
                         p["text"] if p else "", p["confidence"] if p else "", ctx(T, g["start"], g["end"])])
        for p in P:
            if not any(overlap(g, p) for g in G):
                rows.append([name, d["id"], "오탐", p["label"], "", p["text"], p["confidence"],
                             ctx(T, p["start"], p["end"])])
    return rows


def consistency(runs):
    """반복 실행 간 문서별 span 집합의 평균 Jaccard, 결과가 완전히 같은 문서 비율."""
    jac, same = [], 0
    for doc_runs in zip(*runs):
        sets = [{key(p) for p in r} for r in doc_runs]
        vals = [len(x & y) / len(x | y) if x | y else 1.0 for x, y in combinations(sets, 2)]
        jac.append(sum(vals) / len(vals))
        same += all(s == sets[0] for s in sets)
    return sum(jac) / len(jac), same / len(jac)


# ------------------------------------------------------------------ 실행
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True, help="정답 jsonl (dev/test)")
    ap.add_argument("--pred", action="append", required=True,
                    help="이름=예측.jsonl  (반복 실행은 이름=r1.jsonl,r2.jsonl,r3.jsonl)")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    docs = load_gold(a.gold)
    os.makedirs(a.out, exist_ok=True)
    print(f"문서 {len(docs)}건, 정답 개체 {sum(len(d['gold']) for d in docs)}개")

    systems, ms, cons = {}, {}, {}
    for arg in a.pred:
        name, _, paths = arg.partition("=")
        if not paths:
            name, paths = os.path.splitext(os.path.basename(arg))[0], arg
        runs = []
        for p in paths.split(","):
            P, t = load_pred(p, docs)
            runs.append(P)
            ms.setdefault(name, t)
        systems[name] = runs[0]
        if len(runs) > 1:
            cons[name] = consistency(runs)

    # 전체
    hdr = (f"{'시스템':<14}| {'strict P':>8}{'R':>7}{'F1':>7} | {'partial P':>9}{'R':>7}{'F1':>7}"
           f" | {'mask_rec':>8} | {'ms/문서':>8}")
    print("\n=== 전체 성능 ===\n" + hdr + "\n" + "-" * len(hdr))
    rows, counts = [], {}
    for k, P in systems.items():
        s, counts[k] = summarize(docs, P)
        t = ms.get(k)
        rows.append([k] + [round(v, 4) for v in s.values()] + [round(t, 1) if t else ""])
        print(f"{k:<14}| {s['strict_p']:>8.3f}{s['strict_r']:>7.3f}{s['strict_f1']:>7.3f} | "
              f"{s['partial_p']:>9.3f}{s['partial_r']:>7.3f}{s['partial_f1']:>7.3f} | "
              f"{s['mask_rec']:>8.3f} | {(f'{t:.1f}' if t else '-'):>8}")

    # 라벨별
    print("\n=== 라벨별 partial 재현율 (정밀도) ===")
    print(f"{'라벨':<30}" + "".join(f"{k:>16}" for k in systems))
    lab_rows = []
    for lab in LABELS:
        line, row = f"{lab:<30}", [lab]
        for k in systems:
            c = counts[k][lab]
            p, r, _ = prf(c["partial_tp"], c["pred"], c["gold"])
            line += f"{r:>9.3f} ({p:.2f})"
            row += [round(r, 4), round(p, 4)]
        print(line)
        lab_rows.append(row)

    # 어려운 사례
    print("\n=== 어려운 사례 (재현율 또는 오탐 수) ===")
    hc = {k: hard_case_table(docs, P) for k, P in systems.items()}
    first = next(iter(hc.values()))
    print(f"{'항목 (대상 수)':<34}" + "".join(f"{k:>14}" for k in systems))
    hc_rows = []
    for item, (_, n, kind) in first.items():
        vals = [hc[k][item][0] for k in systems]
        fmt = (lambda v: f"{v:.3f}" if v is not None else "-") if kind == "recall" else str
        print(f"{item + f' ({n})':<34}" + "".join(f"{fmt(v):>14}" for v in vals))
        hc_rows.append([item, n] + vals)

    for k, (j, same) in cons.items():
        print(f"\n{k} 일관성: 평균 Jaccard {j:.3f}, 결과가 완전히 같은 문서 {same:.1%}")

    # 저장
    def write(fname, header, data):
        with open(os.path.join(a.out, fname), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(data)

    write("summary.csv", ["system", "strict_p", "strict_r", "strict_f1", "partial_p", "partial_r",
                          "partial_f1", "mask_rec", "ms_per_doc"], rows)
    write("per_label.csv", ["label"] + [f"{k}_{m}" for k in systems for m in ("recall", "precision")], lab_rows)
    write("hard_cases.csv", ["item", "n"] + list(systems), hc_rows)
    write("consistency.csv", ["system", "mean_jaccard", "identical_doc_ratio"],
          [[k, round(j, 4), round(s, 4)] for k, (j, s) in cons.items()])
    for k, P in systems.items():
        write(f"errors_{k}.csv", ["system", "doc_id", "error_type", "label", "gold_text",
                                  "pred_text", "confidence", "context"], error_rows(docs, P, k))
    if len(systems) > 1:
        names, dis = list(systems), []
        for i, d in enumerate(docs):
            for g in d["gold"]:
                caught = [k for k in names
                          if any(p["label"] == g["label"] and overlap(g, p) for p in systems[k][i])]
                if 0 < len(caught) < len(names):
                    dis.append([d["id"], g["label"], g["text"], "|".join(caught), ctx(d["text"], g["start"], g["end"])])
        write("disagreements.csv", ["doc_id", "label", "gold_text", "caught_by", "context"], dis)
    print(f"\n결과 저장: {a.out}/")


if __name__ == "__main__":
    main()
