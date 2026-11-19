# Lean 최종 구성 결정 — 2026-11-19

**최종 안전 기준선: B0, 기존 A.X+Veil + Frozen V6/96-bit HMAC + KoE5 + own_v6. 운영 전환 없음. 실험 종료 후 사용자 요청으로 작업 브랜치에만 커밋·push한다.**
C1(KLUE+Boost 한 모델)은 탐지 성능과 비용 측면에서 가장 유망하다. 그러나 이번 공통 typed adapter는 보호 하한·승인 처리율 동등성을 달성하지 못했다. 탐지부 추천 C1과 안전한 E2E 실행 기준선 B0를 구분한다. 새 어댑터를 기본 런타임에 적용하지 않는다.

## 수행 범위와 판정

| 단계 | 결과 |
| --- | --- |
| A | PASS: 동일 scorer, 원본 단계별 attribution, C1/C2 ablation, 두 번째 모델 진단 상한 |
| B | 실험 PASS / 신규 승인 어댑터 채택 FAIL: Mask-Only와 근거 부족 HMAC은 보류 |
| C | 실추론·A 재생 parity·고정 B 평가 PASS; 신규 end-to-end 개선 미입증 |
| D | 합성 smoke·차단 반례·88질의 단일 로컬 검증 실행 완료; 실제 답변 품질 미측정 |

## 동일 조건의 탐지 성능

A는 187문서/912 Gold, Unicode [start,end), 동일 7종 strict micro scorer다. 4팀 파일의 ID·원문·Gold 전부 일치한다. B0 탐지부와 Gate 이후 typed 출력을 같은 단계로 취급하지 않는다.

| 구성 | NER | TP/FP/FN | Strict F1 | 근거/주의 |
| --- | ---: | --- | ---: | --- |
| B0 탐지부 | 2 | 910/10/2 | 0.993450 | fuse→boundary→refine, Gate 전 |
| B0 Gate typed | 2 | 843/7/69 | 0.956867 | READY/보류 정책 반영, Raw 점수와 직접 비교 금지 |
| B1 no_ensemble | 2 | 908/12/4 | 0.991266 | KLUE+KoELECTRA, 글자 확률 앙상블만 없음 |
| B2 holdout 수정 전 | 1 | 892/86/20 | 0.943915 | 고정된 과거 예측 파일 |
| B2 holdout 수정 후 | 1 | 911/8/1 | 0.995085 | holdout 오류를 보고 보정한 노출된 결과 |
| C1 KLUE+Boost | 1 | 907/4/5 | 0.995063 | KoELECTRA/확률 앙상블 완전 제거 |
| C2 AX+주소/번호 | 1 | 910/20/2 | 0.988056 | 개선 없는 이름 보정 제외 |

C1은 B1 대비 exact TP -1, FP -8이며 F1은 0.991266→0.995063이다. KoELECTRA를 켜면 PERSON 신규 exact TP1/FP8, ADDRESS 신규 TP/FP0/0, 원본 검수 후보는 C1 0→B1 17이다. `no_ensemble`을 1모델이라고 부르던 해석을 수정하고, KoELECTRA 미호출을 별도 회귀 테스트했다. C1과 4팀 수정 후 F1 차이는 약 -0.000022다. 이것은 우월성 증명이 아니다.

## Phase A: 실제 손실·규칙 기여

| B0 단계 | TP/FP/FN | F1 |
| --- | --- | ---: |
| AX_raw | 902/42/10 | 0.971983 |
| Veil_raw | 893/59/19 | 0.958155 |
| fuse_labelwise | 905/35/7 | 0.977322 |
| validate_boundaries | 910/30/2 | 0.982721 |
| refine_spans | 910/10/2 | 0.993450 |
| Step1_entities | 910/10/2 | 0.993450 |
| Gate_entities | 843/7/69 | 0.956867 |
| Gate_HMAC | 843/7/69 | 0.956867 |
| prepare_HMAC | 816/7/96 | 0.940634 |

Step1의 TP 910→Gate 843은 strict typed 출력의 변화다. 실제 코드의 REVIEW/경계/라벨/HMAC 제외를 `A_error_taxonomy.csv`로 추적했다. FN69를 69개 실제 누출로 해석하지 않는다. 원본 V6 제안 Gold 완전 보호 912/912와 실제 READY177의 Gold843/843은 기존 검증을 재사용한다.

| AX 규칙 단독 | TP/FP/FN | F1 |
| --- | --- | ---: |
| AX_raw | 902/42/10 | 0.971983 |
| C2_only_person | 902/42/10 | 0.971983 |
| C2_only_address | 910/29/2 | 0.983252 |
| C2_only_numbers | 902/33/10 | 0.976719 |
| C2 | 910/20/2 | 0.988056 |
| C2_off_person | 910/20/2 | 0.988056 |
| C2_off_address | 902/33/10 | 0.976719 |
| C2_off_numbers | 910/29/2 | 0.983252 |

주소 보정: raw AX 대비 TP +8, FP -13. 번호 형식/문맥: TP 변화 0, FP -9. 이름 보정: 이 A에서 변화 0으로 기본 C2에서 제외. 각 단독/하나씩 off의 recovered/lost TP·removed/created FP는 집계 CSV에서 문서별로 재현한다. Gold·인물 목록은 정책 함수 인자로 받지 않는다.

C2가 남긴 FN 2개는 PERSON 경계 문제이고 주소 Gold 52개는 모두 exact 복구했다. Veil과 KoELECTRA 모두 raw AX 대비 새 주소 exact TP3이나, C2 보정 이후 추가 주소 이득은 입증되지 않았다. Veil 신규 주소 FP2 vs KoELECTRA6, PERSON 추가 exact TP0/0이다. 이 값은 정답을 본 진단 상한이며 runtime resolver 점수로 쓰지 않는다. 따라서 C3/C4·cascade·15조합 재실험·새 모델 다운로드는 진행하지 않았다. 두 번째 모델은 현재 공통 어댑터의 이름 승인/Mask-Only 병목을 해결한다는 근거가 없다.

## Phase B: 공통 typed-plan/HMAC 실제 로컬 준비

모든 원본 탐지 출력을 같은 어댑터로 평가했다. B0도 Gate 이전 Step1 typed 후보를 입력한다. 원본 승인 정책과 공통 어댑터 결과는 별개다. B1도 원본 KLUE+KoELECTRA raw를 mask 입력에 포함했다(standalone Hybrid의 최종 span 보호910/912와 구분). B2는 승인된 최종 예측 파일만 있어 raw의 추가 마스크 근거는 미확보다. B1 raw 누락을 최종 집계에서 보완한 계측 수정은 C1/C2 정책·선택을 바꾸지 않는다. 원본 96-bit HMAC/canonicalizer/token namespace를 호출하며 원본 키·V6 파일은 변경하지 않았다.

| 공통 어댑터 입력 | 제안 Gold 보호 | LOCAL_PREPARED/REVIEW/BLOCKED | 실제 READY Gold 보호 | 실제 HMAC TP/FP | F0 마스크 대비 미보호 코드포인트 |
| --- | --- | --- | --- | --- | ---: |
| B0 | 912/912 | 14/173/0 | 0/0 | 0/0 | 108 |
| B1 | 912/912 | 9/178/0 | 27/27 | 27/0 | 179 |
| B2 | 911/912 | 15/172/0 | 27/27 | 27/0 | 208 |
| C1 | 911/912 | 18/169/0 | 27/27 | 27/0 | 200 |
| C2 | 912/912 | 23/164/0 | 27/27 | 27/0 | 140 |

실제 READY의 Gold 분모가 작다. 0/0도 성공적인 이름 가명처리로 해석하지 않는다. 보호 하한의 코드포인트 차이는 알려진 Gold 외 과보호도 포함하므로 실제 누출 개수와 다르지만, 하한 동등성을 주장할 근거도 되지 못한다. REVIEW preview는 원본 pseudonymizer로 로컬 생성해 형식 검증했으며 LOCAL_PREPARED·검색 적재·송신 성공으로 세지 않았다. 확정되지 않은 후보는 마스크로만 보존하고 보류한다.

원본 B0 native: READY177 / REVIEW10 / BLOCKED0; 실제 READY HMAC TP816/FP7, 원본 prepare parity177/177. 이것은 기존 실측의 재사용이다. FP는 Gold strict 타입/경계 오차이며 암호 함수 불일치와 다르다. 이번 공통 adapter는 B0조차 READY를 크게 줄였으므로 교체 채택 FAIL이다. C1 탐지기가 본질적으로 가명처리에 부적합하다고 단정하지 않는다.

## 통계·실추론 비용

문서 단위 bootstrap 2,000회(seed20261119), 아래는 각 후보 − B2의 strict micro F1 차이 95% percentile CI다. A는 노출된 개발셋이며 CI는 분포 이동·미라벨 PII 안전성 보증이 아니다.

| 후보 | paired win/tie/loss | F1 delta CI |
| --- | --- | --- |
| B0 | 1/176/10 | [-0.007257, 0.003659] |
| B1 | 0/175/12 | [-0.009263, 0.001157] |
| B2 | 0/187/0 | [0.000000, 0.000000] |
| C1 | 0/183/4 | [-0.005285, 0.004940] |
| C2 | 1/166/20 | [-0.012973, -0.001090] |

추론과 별도 측정한 공통 Resolver/HMAC preview 비용(187문서, 실제 forward 없음):

- C1: Resolver P50/P95 0.200/0.412ms, HMAC preview 0.124/0.256ms.
- C2: Resolver P50/P95 0.214/0.431ms, HMAC preview 0.127/0.265ms.

위 값은 탐지 latency와 따로 잰 순수 함수 비용이다. preview는 승인 성과가 아니며 P95를 단순 합산해 전체 SLO로 주장하지 않는다. B0 추론 표는 native Gate/prepare까지 포함하며 C1/C2/B1은 탐지+원본 후처리까지만 포함한다. 모델/규칙 내부 시간 개별 분리는 미계측이며 통합 탐지 구간과 공통 Resolver/HMAC 비용을 구분한다.

동일 Mac CPU·동일 A/B 문서 순서, torch/OMP/MKL 2 threads. B0 ONNX intra/inter-op도 2로 고정했다. 후보는 순차 측정하므로 시스템 부하/열 상태까지 통제한 교차 실험은 아니다. 기존 캐시 replay ms를 추론 시간으로 쓰지 않았다. cold는 합성 첫 요청 로딩 1회 관측, warm은 다음 실제 corpus 요청이다. RSS는 worker process peak로 allocator/로딩 비용을 포함한다.

| 구성/데이터 | P50/P95 ms | RSS MiB | cold load ms | 실제 NER forward 합계 |
| --- | --- | ---: | ---: | --- |
| C1/A(187) | 94.72/154.10 | 510.05 | 9554.24 | {"klue": 195} |
| C1/B(171) | 281.84/454.09 | 528.79 | 9895.78 | {"klue": 437} |
| C2/A(187) | 260.63/398.52 | 792.30 | 8795.26 | {"ax": 195} |
| B1/A(187) | 130.59/220.48 | 559.02 | 9646.81 | {"klue": 195, "koelectra": 195} |
| B0/A(187) | 539.82/1164.52 | 2209.25 | 23692.14 | {"veil": 187, "ax": 195} |
| B0/B(171) | 2641.26/4531.09 | 2041.13 | 21766.57 | {"veil": 673, "ax": 745} |

C1/C2/B1/B0의 A 실제 출력 대 cached 출력 불일치: {"C1": 0, "C2": 0, "B1": 0, "B0": 0}. C1은 KLUE 한 체크포인트만 호출하고 확률 앙상블 추가 forward도 없다. C2는 AX 한 체크포인트만 호출한다. 원본 창 guard 일치를 위한 Veil tokenizer만 읽으며 Veil 모델은 생성·forward하지 않는다. 긴 문서는 여러 창 때문에 요청당 forward가 1회를 넘는다. KoE5 embedding은 NER 개수와 별도다.

forward 표는 warm corpus의 계측 합계다. 합성 warm-up 요청은 각 후보별 별도1회이며 corpus 합계에서 제외했다. B0 cold ONNX export/검증 과정의 PyTorch tracing forward 횟수는 미계측(BLOCKED)이라 0회로 보고하지 않는다. 요청별 NER 체크포인트 상한은 cold와 독립 검사 모두 AX+Veil 두 개이며 export는 동일 Veil checkpoint다.
B2는 동일 A/Gold/SHA 예측 파일로 점수만 재생했다. 4팀 원본과 revision·asset SHA 일치가 입증된 atonlee 체크포인트가 없어 같은 Mac 실추론 속도는 **BLOCKED**다. 로컬 koelectra_ft5를 동일 원본으로 임의 대체하지 않았다. 다른 컴퓨터의 기존 35.8ms를 속도 배수 계산에 쓰지 않았다.

## B: 고정 후 노출된 분포 이동 검증

A에서 C1을 탐지부 finalist로 고정하고 B에서 한 번 평가했다. B는 171문서/1439 Gold, 이미 노출된 데이터이며 fresh Holdout2가 아니다. B 결과로 규칙·임계값·후보를 조정하지 않았다. C2/B1/B2의 B 점수는 미측정이며 우위를 추정하지 않는다.

같은 B에서 원본 B0의 Gate 전 detector TP/FP/FN 1430/61/9, F1 0.976109. 단일 C1과의 탐지부 동등 비교는 이 값으로 한다.

C1 B: TP/FP/FN **1407/109/32**, F1 **0.952284**; 제안 보호 1430/1439; 공통 어댑터 READY 11, REVIEW 160, BLOCKED 0. 실제 READY Gold 43/43, 실제 HMAC TP/FP 43/0.
기존 B0 B Gate typed F1 .946959는 다른 단계의 출력이므로 C1 Raw/후처리 점수와 우월성 비교하지 않는다. B0 B 실행은 동일 조건의 장문 비용/기준선 준비 계측이며 B 기반 정책 변경에 쓰지 않는다.

## Phase D: 우승 안전 기준선 한 개의 격리 E2E

B0만 연결했다. 문서·질의는 같은 정책/키로 새로 준비하고 독립 검사 통과분만 실제 KoE5에 넣어 인메모리 색인했다. 운영 DB의 과거 가명 문서와 혼용하지 않았다. HCX는 기존 HCX-007 Mock schema 경로다.

합성 3문서 smoke PASS, 정상 Mock 1회, 개인정보/임의 토큰/잘못된 토큰 반례 3개 차단. 실제 187문서 처리: {"WITHHELD": 43, "INDEXED": 144}; 보류 사유 {"RESIDUAL_PII_DETECTED": 33, "FROZEN_REVIEW": 10}.

| 88질의 최초 실패/완료 단계 | 건수 |
| --- | ---: |
| gate | 3 |
| context_recheck | 41 |
| complete | 41 |
| query_recheck | 1 |
| outbound | 1 |
| mock_generation | 1 |

사유: `{"FROZEN_REVIEW": 2, "CLARIFY_UNVERIFIED_IDENTITY_CANDIDATES": 41, "RESIDUAL_PII_DETECTED": 1, "OUTBOUND_TRUNCATED_TOKEN": 1, "FROZEN_BLOCKED": 1, "GENERATION_OR_SCHEMA_FAILED": 1}`. 실제 검색 가능한 경로의 Hit@5 분자/전체 분모는 64/88, 반환 Top5만 측정했다. 검색 전 차단 질의를 성공으로 세지 않았다. Context/Identity 실패를 탐지 F1로 해석하지 않는다.

독립 재검사는 선택한 AX+Veil의 raw 출력과 기존 구조 검사를 사용한다. 별도 KLUE/KoELECTRA를 숨겨 호출하지 않는다. 실제 추가 forward {"veil": 333, "ax": 405}, 요청/재사용 {"prepare_real_requests": 92, "guard_requests": 815, "guard_real_requests": 241, "guard_snapshot_hits": 574, "mock_calls": 43, "prepare_A_real_snapshot_reuse": 187}. 동일 가명 입력의 run 내 정확 snapshot 재사용을 명시했으며 cache hit를 추론 latency로 보고하지 않는다.

88질의 혼합 경로 지연 P50/P95 911.67/2330.97ms. guard cache·최초 실패 단계가 섞인 진단값이며 전체 성공 응답 SLO/속도 개선 주장으로 쓰지 않는다.

차단 원인 오프라인 재구성: 토큰 절단1질의에서 bounded 문서2개의 보호 토큰이 잘리는 것을 확인했다. 원본 hcx의 1500자 절단 이후 ContextBuilder가 전송 전에 막았다. 스키마 실패1건은 지역 불일치 top1을 고정 Mock이 인용해 REGION_MISMATCH_CITATION으로 거절된 것이다. 기존 합성 regression 두 경로도 PASS이며 검사 해제로 통과시키지 않았다. 원본 hcx/V6를 변경하지 않고 별도 토큰 안전 excerpt/Mock citation 과제로 남겼다. 88질의를 반복하지 않았다.

Mock transport는 성공41회+스키마 실패1회+합성1회=43회다. 최초 계수에서 빠진 스키마 실패 호출은 기록된 terminal stage와 코드 경로로 보정했고 finally에서 실패 호출도 세도록 계측을 수정했다. 유료 HCX 호출은 0회다.

따라서 과거 quality4 guard+운영 DB의 10/88과는 guard 구성·색인 대상이 다르며 단순 통과율 개선으로 주장하지 않는다. Guard/Identity/출력 검사를 해제하지 않았다. 검증된 소유 관계가 없는 동명이인 후보는 그대로 CLARIFY한다. 실제 사실성·정답성·비공개 개인정보 잔류 여부의 수동 검수는 미완료다.

## 채택·폐기·BLOCKED

- 유지: 원본 Frozen V6 보호 계약/96-bit HMAC/typed namespace, KoE5/own_v6, 독립 잔여정보 검사·보수적 Identity.
- 탐지부 후속 추천: C1 한 모델. B1보다 FP8 감소, 모델1개 감소. A 보호 부족1개는 PHONE이며 미승인 후보는 PERSON400, RRN31, CARD9(잔여 Mask-Only 구간 별도)이다. 안전한 승인 어댑터 개선 전에는 운영 교체하지 않는다.
- C2 주소/번호 순수 함수는 실측 유효. PERSON 보정은 A 이득0이라 제외. 무조건 이 삭제·성씨/어휘 차단 목록·최장 경계 선택을 신규 정책에 추가하지 않았다.
- 공통 adapter 채택 FAIL: 문맥 근거 부족과 Mask-Only 보류로 READY 손실, F0 보호 하한 동등성 실패. 처리율을 높이기 위한 자동 승인/일반어 whitelist 없음.
- C3/C4/cascade 미승격: 단일 C1 탐지 F1은 B2와 동등 허용폭 내, C2 주소는52/52이며 추가 후보가 현재 승인 병목을 해결할 증거 없음.
- BLOCKED: 4팀 같은 Mac live latency / 최소 필요: 원본 atonlee checkpoint와 revision·asset SHA. 새 NER 다운로드는 이번 범위 밖.
- BLOCKED: 새 비노출 Holdout2 일반화 / 최소 필요: 잠금 이후 별도 출처·중복 검증한 미공개 labeled set. 현재 B를 fresh blind로 재명명하지 않음.
- BLOCKED: 실제 답변 사실성·미라벨 잔여 PII 수동 검수 / 최소 필요: Git 외부 진단과 평가자 검수. 실제 HCX 유료 호출은 실행하지 않음.

## 재현·검증·보존

단위 회귀: Unified 95 PASS; 기존 Step1 30 PASS/1 SKIP, Step2 183 PASS, root29 PASS. 합계 **337 PASS / 1 SKIP / 0 FAIL**. Step1 SKIP은 기존 release notebook 부재다. 개인 절대경로에 의존하던 참조 chunk/merge 테스트는 고정 원본 함수의 독립 fixture로 교체했다. B0 ONNX 계측 첫 실행은 positional sess_options 중복 TypeError가 발생해 wrapper를 수정하고 해당 미완료 후보부터 재개했다. 성공한 C1/C2/B1의 실제 측정은 재생 시간으로 바꾸지 않고 원래 기록을 유지했다.

집계 CSV는 정확히 4개: `A_same_scorer.csv`, `A_error_taxonomy.csv`, `A_second_model_value.csv`, `C_pareto.csv`. 상세 Raw/Gold 오류 위치/입력/키/매핑은 Git 밖 private 디렉터리0700·파일0600. 보고서는 이 한 파일만 추가한다. 실제 환경·원본 V6/main/운영 DB/HMAC 키·팀원/4팀 원본은 변경하지 않았다. SQL 쓰기·유료 HCX·외부 개인정보 전송·PR·merge 0회. 실험 중 push는 하지 않았으며, 종료 후 사용자 별도 요청에 따라 feature/unified-v1에만 결과를 커밋·push한다. Phase D 이후 실험을 중단한다.

```bash
cd /Users/gojunseo/AegisRAG-unified-v1
export PYTHONPATH=step2_privacy_rag/app
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
LEAN_MODEL_PY=/tmp/aegis-unified-followup-model-env/bin/python
LEAN_RETRIEVAL_PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag-retrieval/bin/python
"$LEAN_MODEL_PY" -m unified_v1.lean A
"$LEAN_MODEL_PY" -m unified_v1.lean B
"$LEAN_MODEL_PY" -m unified_v1.lean C
"$LEAN_RETRIEVAL_PY" -m unified_v1.lean D --runtime-python "$LEAN_MODEL_PY"
"$LEAN_MODEL_PY" -m unified_v1.lean report
PYTHONPATH=step2_privacy_rag/app:tests/unified_v1 "$LEAN_MODEL_PY" -m unittest discover -s tests/unified_v1 -q
```

위 재현은 기존 A/B 검증 반복이며 새 독립 검증이 아니다. C 중단 후에는 입력·코드·참조·모델 hash가 일치하는 완료된 실제 추론 기록으로만 재개한다. 이번 최초 측정은 계측 wrapper/회귀 정리 전 실측이며 최종 source와 구분한다. 최초 기록에는 이 resume seal이 없어 재현 C는 실제 후보를 다시 측정한다. 전량 새 비용 측정이 필요하면 private C_latency/C_live_*만 별도 백업 후 제거한다. 원문 상세는 터미널에 출력하지 않는다.

### SHA 기록

- 시작 Git: `172aaa68a21f0aa8d39e647642a652281aad825f`
- 팀원 참조: `242543bf0eae91aa3d655227a747aa128707368e`
- 4팀 참조: `df7ef1f0cceeda43b76b57365548d09d92fad7d8`
- A 원문·Gold JSONL: `7ccf27589971ed0911756e527faf475144ab996f6d06416b4a167bda51fda822`
- B 원문·Gold JSONL: `52bca604b38cf06d1445c57b9e48ad96a7654fed73dfcd0734a8eaa5df58b42f`
- 동일 strict scorer(selection.py): `993e75138e8b014175ffe85c3c15cd67d16dd1ae8297d0bde9dfadad557e711a`
- 최종 lean_policy.py: `268bb84e31c8262167e60b2b90152c7e1826d23b5062e0855ed657d6e826a896`
- 최종 lean_worker.py: `fcd3f60fc93eb5e8a8880490675291cd688bbd0878d596c6939c6a40437eb12d`
- 최종 lean.py: `6262bedc2a4590683093b702952e0bf52575f646fb58445c9f01630fee6709e4`
- 모델 실행 환경: torch2.2.2 / transformers4.55.4 / tokenizers0.21.4 / numpy1.26.4, 기존 별도 임시 venv를 재사용. 새 설치·기존 환경 업그레이드 없음.
- KoE5: `nlpai-lab/KoE5@237b580aea358e3042fcf5baf78124c33c3afd16` (1024d/512 tokens/query:·passage:/normalized).

- ax: `{'repo': 'PII-detect/pii-ax-mixed-v1', 'revision': 'c0f99b1dc113befefeff1a3091087d50bc3ea635'}`
- veil: `{'repo': 'PII-detect/pii-veil-mixed-v1', 'revision': 'e6e7d20fa9baa43a938f5f6cc780d3f2c9650fb9'}`
- labels: `['person', 'phone_number', 'email', 'address', 'resident_registration_number', 'account_number', 'card_number']`
- owner: `{'person': 'ax', 'phone_number': 'ax', 'email': 'ax', 'address': 'veil', 'resident_registration_number': 'ax', 'account_number': 'ax', 'card_number': 'ax'}`
- source_stage: `06D_FINAL_RELEASE_VALIDATION`
- detector_module_sha256: `7068847cf7cd673acee07a48eeb80c79d012400a4ad710ee212590f8280abee0`
- frozen_routing: `{'person': 'ax', 'phone_number': 'ax', 'email': 'ax', 'address': 'veil', 'resident_registration_number': 'ax', 'account_number': 'ax', 'card_number': 'ax'}`
- baseline_detector_module_sha256: `c1ba75c57cae037927e6b048e733da66bb8e5d5644ebc57ce5c9d5155b62fe48`
- structural_policy_sha256: `5cbd304e1a14fc2756c1b3d120a91a9a632214485fe4002d806e86183bb12872`
- policy_version: `aegis_core_v1`
- klue/config.json: `d8d486cc3d59fec1ff109eaf6a60e22b53dc451d4dccb14f94e4b93fcf0b07ac`
- klue/model.safetensors: `05dd28640e2c884007b7c7668ad1e2f770ac2cabe0d31cc9cfe6ac02d2b69b4f`
- klue/tokenizer.json: `200c2fd5a65daf3b67aa532740d3a26736deceaa8b28e82826038aede9377cf0`
- klue/tokenizer_config.json: `fd172592aef04c3c1c5be31fa2072c27ae81bdc4192b4e919ea0db6b43407913`
- koelectra/config.json: `6eb2b024f6144b5df04be1304997765aa8518420998ab1f3151a50f570ba0f91`
- koelectra/model.safetensors: `fd2a89454ef29acac263ce3ea3629ee6b5eb513216d46f599462e5b179922abc`
- koelectra/tokenizer.json: `6234fb96e9031ec5e6a83b27ba1ade973e823d5e34a62a49c8d00a20f49e09eb`
- koelectra/tokenizer_config.json: `8a4a9ae2d88ba2638f862c7df7564b38ac4b2849f24c850752a72b9a1e3908b8`
