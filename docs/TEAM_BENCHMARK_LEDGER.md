# 개인정보 보호형 RAG — 팀별 성능 비교 원장

- 최초 기록: 2026-11-24 (KST)
- 관리 브랜치: `experiment/team-v7-integration`
- 목적: 실전1 탐지 / 실전2 검색 / 종합 E2E의 비교 근거를 보존하고, 동일 데이터셋의 최신 검증 결과를 추가한다.
- 상태: Legacy 및 V7 통합 실측 완료, V7 이전 strict/coverage 집계 재현, 타 팀 공개 수치 참조. 미측정은 이 비교 원장에서 확인된 수치 없음(실제 실행 여부는 별개).
- **비교 원칙:** 같은 원본 Gold/라벨 정의/분모/평가 단계만 직접 비교한다. 최신 버전과 최고 공개 수치를 별도 행으로 기록한다. mask union과 최종 반환 span의 커버리지를 구분한다. 이전 실험은 새 통합 실측으로 대체하지 않는다. 타 기기의 latency를 배수 비교하지 않는다. Gold/원문/가중치/비밀은 커밋하지 않는다.

## 데이터셋 등록

| ID | 문서 | Gold entity | SHA-256 / 비고 |
|---|---:|---:|---|
| A187 | 187 | 912 | 7ccf27589971ed0911756e527faf475144ab996f6d06416b4a167bda51fda822 |
| B171 | 171 | 1439 | 52bca604b38cf06d1445c57b9e48ad96a7654fed73dfcd0734a8eaa5df58b42f |
| Hard403 | 403 | 1958 | 2497c882307e604a9359bf463c485138fe29c0ea62e9e2360338dd23c006e4ab |
| Synth Fresh | 1775 | 미측정 | 4팀 자체 확인셋; 우리 모델 미평가, 최종 설정 동결 후 평가 |
| Holdout2 | 미확정 | 미확정 | 신규 멘토 평가셋; 도착 전 모델/규칙/임계값/평가 계약 동결 |
| Test200 | 200 | 1165 (3팀 보고) | 원본 일치 여부 별도 검증, 반복 개발/회귀용 |

A187/B171/Hard403는 모두 기존에 분석된 회귀 자료이며 신규 독립 holdout이 아니다. 타 팀 A187/Hard403 공개 수치는 동일 명칭 및 문서 수 기준 참고; 파일 SHA와 scorer 일치 확인 전 엄밀한 paired 결과로 해석 금지.

## 실전1 — A187

| 팀 / 버전 | Strict P | Strict R | Strict F1 | Mask Rec | TP/FP/FN | Mean ms | P95 ms | Cold ms | 근거 |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| 3팀 Split V2 원본 Gold (이전) | .9881 | .9989 | .9935 | 1.0000 | 미측정 | 7.4 | 미측정 | 미측정 | T3-REPORT |
| 3팀 최신 provisional V2 (AD-05 수정 Gold 5경계) | .9902 | .9989 | .9945 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | T3-REPORT; 직접 비교 불가 |
| 4팀 실전1 v10 | .9913 | .9989 | .9951 | .9999 | 미측정 | 35.8 | 미측정 | 미측정 | T4-HOLD |
| 4팀 종합 최신 | 미측정 | 미측정 | .995 (보고 F1, 반올림) | .999 | 미측정 | 미측정 | 미측정 | 미측정 | T4-FINAL |
| 5팀 V6 2모델 (이전 기록) | 미측정 | 미측정 | .993450 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | OWN-HIST |
| 5팀 Legacy 4모델 실측 | .933128 | .994518 | .962845 | 1.000000 | 907/65/5 | 538.82 | 1037.64 | 미측정 | OWN-LEG |
| V7 Onepass Raw (이전) | 0.960680 | 0.991228 | .975715 | .997383 | 904/37/8 | 미측정 | 미측정 | 미측정 | OWN-V7 |
| V7 Onepass Core Postal Join Guard (이전) | 0.996711 | 0.996711 | .996711 | 1.000000* | 909/3/3 | 미측정 | 미측정 | 미측정 | OWN-V7 |
| V7 Onepass Core 통합 실측 | .996711 | .996711 | .996711 | 1.000000 | 909/3/3 | 242.92 | 326.45 | 미측정 | OWN-V7-LIVE |

## 실전1 — B171

| 팀 / 버전 | Strict P | Strict R | Strict F1 | Mask Rec | TP/FP/FN | Mean ms | P95 ms | Cold ms | 근거 |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| 3팀 최신 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | T3-REPORT |
| 4팀 최신 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | T4-FINAL |
| 5팀 V6 2모델 (이전 기록) | 미측정 | 미측정 | .976109 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | OWN-HIST |
| 5팀 Legacy 4모델 실측 | .906667 | .992356 | .947578 | .999839 | 1428/147/11 | 2615.76 | 6331.99 | 미측정 | OWN-LEG |
| V7 Onepass Raw (이전) | 0.957305 | 0.997220 | .976855 | 1.000000 | 1435/64/4 | 미측정 | 미측정 | 미측정 | OWN-V7 |
| V7 Onepass Core Postal Join Guard (이전) | 0.962441 | 0.997220 | .979522 | 1.000000* | 1435/56/4 | 미측정 | 미측정 | 미측정 | OWN-V7 |
| V7 Onepass Combined Guard (이전, 미선정) | 0.969595 | 0.997220 | .983213 | 1.000000* | 1435/45/4 | 미측정 | 미측정 | 미측정 | OWN-V7 |
| V7 Onepass Core 통합 실측 | .962441 | .997220 | .979522 | 1.000000 | 1435/56/4 | 803.05 | 1361.81 | 미측정 | OWN-V7-LIVE |

## 실전1 — Hard403

| 팀 / 버전 | Strict P | Strict R | Strict F1 | Mask Rec | TP/FP/FN | Mean ms | P95 ms | Cold ms | 근거 |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| 3팀 최신 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | T3-REPORT |
| 4팀 v8 | .7874 | .7737 | .7805 | .9332 | 미측정 | 46.1 | 미측정 | 미측정 | T4-V8 |
| 4팀 v9 | .8708 | .8504 | .8605 | .9636 | 미측정 | 33.9 | 미측정 | 미측정 | T4-V9 |
| 4팀 v10 | .8752 | .8560 | .8655 | .9641 | 미측정 | 36.8 | 미측정 | 미측정 | T4-HARD |
| 4팀 종합 추가 학습 v4 | 미측정 | 미측정 | 미측정 | .964 | 미측정 | 미측정 | 미측정 | 미측정 | T4-FINAL; 이름 R=.940 |
| 4팀 종합 최종 이름 보충 모델 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | T4-FINAL |
| 5팀 V6 2모델 (이전 기록) | 미측정 | 미측정 | .920555 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | OWN-HIST |
| 5팀 Legacy 4모델 실측 | .833173 | .885087 | .858346 | .990618 | 1733/347/225 | 343.81 | 501.75 | 미측정 | OWN-LEG |
| V7 Run3 Core Guard (이전 510-token) | 0.891262 | 0.937692 | .913888 | .992791* | 1836/224/122 | 미측정 | 미측정 | 미측정 | OWN-V7; 신규 exact loss 4 |
| V7 Onepass Raw (이전) | 0.887310 | 0.924923 | .905726 | .991678 | 1811/230/147 | 미측정 | 미측정 | 미측정 | OWN-V7 |
| V7 Onepass Core Postal Join Guard (이전) | 0.891016 | 0.926966 | .908636 | .991890* | 1815/222/143 | 미측정 | 미측정 | 미측정 | OWN-V7; 신규 exact loss 0 |
| V7 Onepass Combined Guard (이전, 미선정) | 0.890748 | 0.924413 | .907268 | .991890* | 1810/222/148 | 미측정 | 미측정 | 미측정 | OWN-V7; 신규 exact loss 5 |
| V7 Onepass Core 통합 실측 | .891016 | .926966 | .908636 | .991890 | 1815/222/143 | 261.02 | 509.90 | 미측정 | OWN-V7-LIVE |

* mask union: 원본 탐지 후보 + 보정 후보의 문자 커버리지. 실제 가명처리 출력 보호를 입증하지 않는다. Legacy Mask Rec은 최종 반환 span의 커버리지. Legacy 누락 문자 A187 0/7642, B171 2/12459, Hard403 177/18866. 완전 미탐 0/0/23. V7 이전 summary는 raw cache 기반, CPU 속도 비교 아님.

## 실전1 — Test200 / Synth Fresh / Holdout2

| 데이터셋 | 팀 / 버전 | Strict P | Strict R | Strict F1 | Mask Rec | Mean ms | 비고 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Test200 | 3팀 provisional V2 | 1.0000 | 1.0000 | 1.0000 | 미측정 | 미측정 | 반복 개선 자료 |
| Test200 | 4팀 v10 rules+atonlee | .9757 | .9974 | .9864 | 1.0000 | 43.3 | T4-TEST |
| Test200 | 5팀 V7 최종 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | |
| Synth Fresh | 3팀 최신 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | |
| Synth Fresh | 4팀 v10 | .9227 | .9233 | .9230 | .9835 | 32.1 | T4-FRESH |
| Synth Fresh | 4팀 종합 이름 보충 | 미측정 | 미측정 | 미측정 | .9858 | 미측정 | 이름 R=.9743, 미탐84 |
| Synth Fresh | 5팀 V7 종합 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 설정 동결 후 평가 |
| Holdout2 | 3팀 최신 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | |
| Holdout2 | 4팀 최신 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | |
| Holdout2 | 5팀 최종 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미수령 |

## 실전2 / 종합 — A187, 질의88

| 팀 / 버전 | 분모 | Hit@1 | Hit@5 | MRR | 비고 |
|---|---|---:|---:|---:|---|
| 3팀 E5-small (선정) | 88 | .784 | .932 | .845 | T3-SLIDES |
| 3팀 bge-m3 | 88 | .830 | .943 | .873 | T3-SLIDES |
| 3팀 종합 최신 | 미측정 | 미측정 | 미측정 | 미측정 | 공개 Git README 미기재 |
| 4팀 실전2 최종 | 88 | .943 | 미측정 | .966 | T4-SLIDES/README |
| 4팀 종합 최신 | 88 | .977 | 1.000 | .989 | LLM 재순위 포함, 인용 적중 .989, 입력/원답변/최종답변 유출 0/0/0 (T4-FINAL) |
| 5팀 실전2 발표 | 88 | .773 | .920 | .832 | OWN-SLIDES |
| 5팀 V6 개선 (전체) | 88 | .875 | .9432 | .9051 | OWN-HIST; READY85만의 점수와 구분 |
| 5팀 기존 팀 통합 (코드 주석 기록) | 88 | 미측정 | 1.000 | .918 | OWN-MERGE; 원본 실행 로그 미확인 |
| 5팀 V7 통합 실전2 | 88 | 미측정 | 미측정 | 미측정 | 예정 |
| 5팀 종합 최종 / Holdout2 | 미확정 | 미측정 | 미측정 | 미측정 | 예정 |

추후 검색 전후 rerank, coverage, query latency, 인용 적중/정밀도, 유출, identity 쪼개짐/합쳐짐, READY/REVIEW/BLOCKED를 구분해서 추가한다. 외부 API 모델과 검색 단계가 다르면 그대로 명시한다.

## 출처 / 재현 기록

- T3-REPORT: [3팀 탐지 모델 코드 최종 보고서](https://github.com/taewoo26/nlp_like_lion_chatbot_03/blob/4d154106999ea7ddf7904cffa7d859503c20cbd7/docs/탐지%20모델%20코드%20최종%20보고서.md), 원본 Split V2 표 605–609행, provisional 755–760행. 최신 Gold의 우편번호 경계 5개 수정. `develop`과 `JMY` HEAD 동일. [인수 안내](https://github.com/taewoo26/nlp_like_lion_chatbot_03/blob/4d154106999ea7ddf7904cffa7d859503c20cbd7/docs/detector_handoff.md).
- T4-FINAL: [4팀 종합 README §13](https://github.com/BBuSang/likelion-AI-4team/blob/336504b6d3a3257dbe6a1c2f128e1c0e67a6f7c2/최종프로젝트/README.md#13-평가-결과), A187 F1=.995, Hard403 .964는 Mask Recall, Synth Fresh .9858도 Mask Recall. 최신 Hard strict F1은 공개 확인 못함.
- T4-HOLD / T4-HARD / T4-TEST / T4-FRESH: [4팀 v10 결과 디렉터리](https://github.com/BBuSang/likelion-AI-4team/tree/336504b6d3a3257dbe6a1c2f128e1c0e67a6f7c2/실전프로젝트1/results), 각각 final_holdout, final_synth_hard_eval, final_test, final_synth_fresh의 summary.csv.
- T4-V8 / T4-V9: 같은 결과 디렉터리 version_history/v8 또는 v9/synth_hard_eval.csv.
- T3-SLIDES: 제공된 3팀.pdf, 9–10쪽 (bge-m3/E5-small 비교). T4-SLIDES: 제공된 4팀.pdf, 10쪽. OWN-SLIDES: 제공된 실전_2_발표_RAG5팀.pdf, 9쪽. 발표자료 원본 재배포하지 않음.
- OWN-HIST: 이전 비교 계획 STEP1_FINAL_COMPARISON_PLAN.md 및 실전2 88질의 기록. 원본 재확인 후 상세 해시/카운트 보강, 현재는 이전 기록.
- OWN-LEG: 2026-11-24 사용자 로컬 실측 로그, `tools/benchmark_step1.py`, 코드 a6daa00 이후, CPU Intel Mac/Python3.11.13/torch2.2.2/transformers4.55.4/ONNX Runtime1.23.2, PyTorch/ORT 각각 2 threads, 데이터셋별 fresh process, fallback/cache 차단. 원본 aggregate JSON은 outputs/step1/legacy_{dataset}_{run_id}.json. Cold start 수치는 전체 실행 JSON 확보 후 기입. 별도 5문서 smoke cold=34163.94ms (이전 thread 설정; 본표 미사용).
- OWN-V7: 이전 사용자 제공 aggregate `summary(9).json` (v7_context_compare.py). Model SHA256 978a1c8a7100d2c2411ce797c72cb08b235dcf150f77d3003e4f05881bd8bd1a (Onepass 로컬 확인). 510-token/128-overlap, Raw 및 정책 비교. 원본 집계의 privacy_gate=not_run. 후처리 코드와 체크포인트 일치 확인 후 통합 측정으로 별도 행 추가.
- OWN-MERGE: [팀 통합 merge 코드 주석](https://github.com/kohjunseo1/AegisRAG/blob/f8d7caf2268efbfe34d90dde8f0d0d6ebf3fcbf5/starter/detect_merge.py).

## 결정 및 갱신 방법

- 우선 후보: V7 Final Onepass + Core Postal Join Guard. Combined는 Hard403에서 신규 exact loss 5개로 보류. 추가 학습 중단, 실제 코드/가명처리 계약 검증 우선. V6/Legacy는 비교/rollback 경로 유지.
- 실전1 개발 종료 목표: A187 F1>=.9951 / mask=1, B171 F1>.976109 / mask=1, Hard403 F1>=.90 / mask>=.990618, 후처리 추가 노출 없음, 동일 기기 속도 개선. Gate 미검증 상태는 배포 승인 아님.
- 다음 갱신: 양쪽 전체 실행 JSON에서 cold start/peak RSS 및 소스 해시 추가. 모델별 최적 CPU thread 설정은 별도 짧은 tuning 후 표기; 기존 controlled 결과 유지. Step2 gate의 실제 마스킹/외부 전송과 false READY 검증은 별도 단계.
- 타 팀 업데이트: pinned commit, 경로, 데이터 해시(가능하면), 날짜, 평가 정책을 기록하여 해당 행을 추가한다. 미측정을 추정으로 채우지 않는다.
- Holdout2 수령 전 모델/정책/임계값/스코어러/환경/커밋을 고정한다. 최종 평가 후 수정이 필요하면 해당 세트를 회귀 자료로 재분류하고 별도 신규 확인셋을 사용한다.

## 2026-11-24 V7 통합 실측 추가 (OWN-V7-LIVE)

사용자 로컬 BENCHMARK_COMPLETE 로그, 개인 브랜치 8b17cf6 이후, 동일 원본 Gold 해시 및 CPU 2스레드 비교. 정책 core_postal_join_guard_v1_audited. 이전 raw 및 core TP/FP/FN과 최종 점수 모두 재현. 모델 학습 없음. Mask Rec은 최종 typed span coverage이고 실제 외부 전송 유출 검사와 다름.

| Dataset | Raw TP/FP/FN | Raw F1 | Raw mask | Typed leak chars | Typed fully uncovered | Typed partly uncovered only | Union proxy recall | Review docs |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A187 | 904/37/8 | .975715 | .997383 | 0 | 0 | 0 | 1.000000 | 32/187 |
| B171 | 1435/64/4 | .976855 | 1.000000 | 0 | 0 | 0 | 1.000000 | 8/171 |
| Hard403 | 1811/230/147 | .905726 | .991678 | 153 | 33 | 21 | .991890 | 19/403 |

- Review reasons: A187 bereavement_kinship_role 6, event_status_nominal 10, explicit_internal_extension 9, name_suffix:review_only 3, postal:review_overlap 4; B171 standalone_surname_title 8; Hard403 name_suffix:review_only 20, postal:review_unlabeled_prefix 4. Counts are candidate events, not review recall.
- Legacy fully uncovered Hard403=23 vs V7=33, despite fewer missed chars (177 -> 153) and strict FN (225 -> 143). Complete miss is an independent risk; the 19 review documents do not establish capture of those 33 entities. No deployment approval. Keep Legacy/V6 fallback as explicit evaluation/rollback only, not automatic fail-open.
- Decision: freeze V7 weights and Core candidate for Step2 development, do not further tune on exposed A187/B171/Hard403 merely for F1. Audit complete misses by label and whether document review catches them, and evaluate independent structural verification, gate false READY and actual release coverage before final deployment. No new Gold inspection for runtime. A187 remains primary scoring reference; Holdout2 remains untouched.
- Mean speedup Legacy/V7: A187 2.22x, B171 3.26x, Hard403 1.32x. Hard403 P95 501.75 -> 509.90 ms (slight regression); cold start and RSS pending actual saved aggregate JSON.
- Honorific policy: 3팀 develop report confirms 성+호칭 exclusion as policy; 4팀 final README line 111 explicitly detects one-character given names with 님/씨 (e.g. 린님/진씨) and excludes surname+job-title; do not blanket-remove all one-character names followed by 님/씨. Our Core retained; Combined remains unselected. Context/identity and ambiguity review in Step2.


## 2026-11-24 V7 residual audit and REVIEW scenarios

Source: user-provided AUDIT_AGGREGATE output from tools/audit_v7_residuals.py (51f380d). Raw/typed counts and current review counts reproduced. No Gold edited; no model or runtime policy changed. Offline scenario only, not Gate deployment approval.

| Dataset | Current review docs | Audit-only suppression | Also resolve postal | Also flag ambiguous honorific | Uncovered docs (current / proposed reviewed) |
|---|---:|---:|---:|---:|---|
| A187 | 32 | 7 | 3 | 3 | 0 (0 / 0) |
| B171 | 8 | 0 | 0 | 2 | 0 (0 / 0) |
| Hard403 | 19 | 19 | 19 | 21 | 41 (0 / 2) |

A187: 25 suppression events overlap no Gold (10 status, 9 extension, 6 bereavement); 4 postal overlap events all resolved by join; remaining 3 name-suffix review docs. B171: 8 surname-title suppression events overlap no Gold; 11 ambiguous honorific predictions across 2 docs. These scenarios retain protection; audit-only classification does not itself authorize unmasking. No newly uncovered Gold characters from raw to typed in any corpus.

Hard403: 33 complete misses across 27 docs (person 29, address 2, phone 1, RRN 1); 21 partial misses; 89 fully covered strict FN (81 same-label boundary, 8 wrong-label). Total 153 uncovered chars across 41 docs. Current review catches 0/41 leaky docs and 0/33 complete-miss entities. Honorific scenario catches 2/41 docs and 3/33 entities via document-level signals, with 0 direct overlap with the missed spans. 39 leaky docs remain unreviewed. Complete-miss policy flags: 4 possible surname-title, 2 ambiguous surname/given-name honorific, 27 no identified policy conflict. These are heuristic candidates, not adjudicated Gold errors. Preserve original Gold; any policy-aligned evaluation requires independent consistent full-corpus annotation review. Do not exclude FN selectively.

Next: separate auto audit events from unresolved human-review queue without changing typed spans or reducing protection; inspect private residual context locally; test independent structural/field validation and name ambiguity signals against missed-span and false-review metrics. Keep V7 weights frozen while evaluating lightweight safeguards. Do not interpret current REVIEW rate as actual false READY for external transfer; no production gate run.
