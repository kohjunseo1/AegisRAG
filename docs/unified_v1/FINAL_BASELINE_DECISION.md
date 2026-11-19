# 최종 베이스라인 선정 — 2026-11-19

**추천: 기존 `own2(AX+Veil) + Frozen V6 Gate/96-bit HMAC + KoE5 + own_v6`를 유지한다.** 신규 모델/Hybrid의 높은 탐지 F1만으로 자동 적재·송신을 승격하지 않는다. 최종 실행 후보는 **F0 × own_v6 한 개**이며 HCX는 로컬 Mock만 실행했다. 운영 전환은 하지 않았다.

## 단계별 판정

| 단계 | 판정 | 근거 |
| --- | --- | --- |
| P0 | PASS | 입력 SHA·ID·Gold offset·모델 pin·참조 SHA·환경 고정 |
| P1 | PASS | 4개 단독을 포함한 총 15개 Raw union, 동일 실제 모델 snapshot |
| P2 | PASS | 원본 Hybrid 8옵션, F0~F5, A 선택 잠금 후 B 고정 검증 및 실제 가명처리 parity |
| P3 | PASS / SQL 청크 통합 BLOCKED | 원본 Top-5 85/85 일치, 실제 DB Top-10 및 격리 메모리 187/427청크 비교 |
| P4 | 구현·측정 PASS / 차단 원인 검수 BLOCKED | 88질의 단계 진단, 177문서 독립 검사, lookup 정합성·CLARIFY 보완 |
| P5 | 로컬 Mock PASS / 운영·실답변 품질 미검증 | 선택 구성 합성 인덱스·Mock 및 비용 비교 완료; 여기서 종료 |

## 입력·재현 조건

- 코드 시작 SHA `01313ab6c68874fee1fd7e4d689a6399ae993e66`, 읽기 전용 팀원 참조 SHA `242543bf0eae91aa3d655227a747aa128707368e`. 실험은 로컬에서 수행했다. 이후 사용자 요청에 따라 작업 브랜치에 결과를 커밋·push한다. 실행 코드의 최종 파일 SHA는 `EXPERIMENT_MANIFEST.json`에 별도로 기록한다.
- A: **187문서 / 912 Gold, seen_dev**. 내부 완전 중복 1건을 제거하지 않아 기존 분모를 유지했다. 실제 모델 snapshot은 186개 고유 원문에 대해 재사용했다.
- B: **171문서 / 1,439 Gold**, SHA `52bca604b38cf06d1445c57b9e48ad96a7654fed73dfcd0734a8eaa5df58b42f`. 과거 노출 이력이 있는 분포 이동 검증용이며 **신규 blind가 아니다**. A/B 완전 원문 중복은 0건이다. 원문 길이 중앙값/최대값은 A 199/373, B 644/1,570 코드포인트다.
- A 평가 후 `SELECTION_LOCK.json`에 F0를 고정하고 B에서는 F0만 평가했다. B 결과에 맞춘 모델·임계값·탐지 규칙 변경은 없다. B의 최종 가명처리 교차검증 중 평가기의 REVIEW/BLOCKED 분류 오류 2건을 발견해 원본 `prepare()` 상태로 고쳤다. READY·탐지 점수·HMAC 출력은 바뀌지 않았다. 이는 계측 수정이며, 관찰 이력은 감추지 않는다.
- C: 동일 187행/88질의, frozen 가명 질의 READY 85·DEFERRED 3. 실제 DB fingerprint는 전후 `10c6ee54c842a9cbf4bcd59295b00664`로 동일하다. DB 벡터의 KoE5 provenance는 기존 manifest 선언이며 DB 내부 attestation은 없다. 신규 로컬 벡터는 고정 KoE5로 실계산했다.
- Gold는 채점에만 사용했다. 모델/정책/검색/청크/embedding에는 원문·승인된 가명 입력 및 실행 메타데이터만 전달한다. 원문·Gold·원시 span·매핑은 Git에 기록하지 않았다. 상세 캐시는 Git 외부 `~/.cache/aegisrag/unified_v1/private/selection/`에 디렉터리 0700/파일 0600으로 보관한다.
- 기존 Conda 환경·V6·main·HMAC·운영 DB·참조 저장소·requirements를 변경하지 않았다. 별도 `/tmp` 모델 worker 환경에 Kiwi 0.22.2/모델 0.22.1만 추가했다.

## P1 — Raw NER: Gate와 분리

Unicode `[start,end)` 및 라벨이 모두 같은 strict micro 점수다. 15개 조합에 Gate를 적용해 다른 조합을 BLOCKED시키지 않았다. 모델 출력은 한 번 취득해 union/채점에서 공유했다.

| 모델 | TP | FP | FN | F1 |
| --- | --- | --- | --- | --- |
| ax | 902 | 42 | 10 | 0.9720 |
| veil | 893 | 59 | 19 | 0.9582 |
| klue | 797 | 172 | 115 | 0.8474 |
| koelectra | 903 | 67 | 9 | 0.9596 |
| ax+veil | 905 | 84 | 7 | 0.9521 |
| ax+klue | 902 | 201 | 10 | 0.8953 |
| ax+koelectra | 905 | 97 | 7 | 0.9457 |
| veil+klue | 904 | 223 | 8 | 0.8867 |
| veil+koelectra | 905 | 112 | 7 | 0.9383 |
| klue+koelectra | 904 | 228 | 8 | 0.8845 |
| ax+veil+klue | 905 | 242 | 7 | 0.8791 |
| ax+veil+koelectra | 905 | 135 | 7 | 0.9273 |
| ax+klue+koelectra | 905 | 250 | 7 | 0.8757 |
| veil+klue+koelectra | 905 | 271 | 7 | 0.8669 |
| ax+veil+klue+koelectra | 905 | 288 | 7 | 0.8599 |

AX+Veil 대비 KLUE/KoELECTRA 추가 strict TP는 각각 **0/0**, 추가 FP는 **158/51**이다. 4모델 공통 strict FN은 7개이며 Gold와 아예 겹치지 않는 완전 미탐은 0개다. 경계·타입 오류를 완전 미탐과 혼동하지 않는다. Raw AX 단독이 가장 높은 F1이지만, AX 단독으로 기존 Gate 안전 신호를 대체하는 동등성은 검증하지 않았으므로 운영 기본값으로 승격하지 않았다.

전체 `step1_raw.csv`, 라벨별 `step1_raw_by_label.csv`, 문서별 `step1_raw_paired.csv`, 모델 간 기여/합의 `raw_resources.json`을 남겼다. 새로운 독립 세트에 대한 통계적 우월성 주장은 하지 않는다.

## P2 — Fusion·보호·HMAC

| 세트/정책 | strict F1 | TP/FP/FN | Gold 완전 개체 보호 | READY/REVIEW/BLOCKED |
| --- | --- | --- | --- | --- |
| A/F0 | 0.9569 | 843/7/69 | 912/912 | 177/10/0 |
| A/F1 | 0.9610 | 850/7/62 | 912/912 | 49/138/0 |
| A/F2 | 0.9880 | 908/18/4 | 910/912 | 0/187/0 |
| A/F3 | 0.9569 | 843/7/69 | 912/912 | 145/42/0 |
| A/F4 | 0.9569 | 843/7/69 | 912/912 | 156/31/0 |
| A/F5 | 0.9569 | 843/7/69 | 912/912 | 169/18/0 |
| B/F0 | 0.9470 | 1339/50/100 | 1439/1439 | 155/14/2 |

이 표의 strict 예측은 정책이 내놓은 typed 개체를 전체 문서에서 채점한다. **보호율은 REVIEW/BLOCKED의 제안 경계도 포함하므로 실제 송신·적재 성공률이 아니다.** READY 문서만의 Gold 보호는 A 843/843, B 1,268/1,268이며, 이는 주어진 Gold에 한정한 검증이다. 알려지지 않은 개인정보 부재를 증명하지 않는다.

F1의 정확한 상태는 HMAC 확정 49 / Mask-only 대기 70 / REVIEW 68이다. Mask-only 70개를 자동 적재 가능 문서로 세지 않았다. F0 READY 177개도 독립 검사 후 로컬 적격은 **76개**, 검사 차단은 **101개**다. 실제 DB 쓰기는 0회이고 외부 corpus 적재 승인을 부여하지 않았다.

### 팀원 원본 재현·ablation

| 옵션 | TP | FP | FN | strict F1 | 검수 후보 |
| --- | --- | --- | --- | --- | --- |
| default | 908 | 18 | 4 | 0.9880 | 17 |
| no_ensemble | 908 | 12 | 4 | 0.9913 | 17 |
| no_boost | 892 | 43 | 20 | 0.9659 | 17 |
| drop_unagreed | 908 | 18 | 4 | 0.9880 | 17 |
| num_union | 908 | 18 | 4 | 0.9880 | 17 |
| name_loose | 908 | 18 | 4 | 0.9880 | 25 |
| name_none | 908 | 18 | 4 | 0.9880 | 26 |
| with_kiwi | 906 | 30 | 6 | 0.9805 | 16 |

원본 `pii_pipeline.detect`, `model_ensemble.person_probs`, `hybrid_detector.combine`, `HFDetector.predict`를 참조 파일 변경 없이 실행했다. RoBERTa의 `torchscript=True`에 따른 tuple 반환만 worker 안에서 `torchscript=False, return_dict=True`로 맞췄고 실제 logits 동일성을 확인했다. 옵션은 프로세스 시작 시 한 번 고정하고 요청 중 전역 플래그를 토글하지 않았다. 기본 원본 경로는 Kiwi 없음, Kiwi 추가도 별도 옵션으로 비교했다. accept/review 0.9/0.5, span/candidate 0.35/0.15를 유지했다.

원본 32-bit HMAC/숫자 공통 토큰은 사용하지 않았다. 원본 ALIEN_ID→RRN 매핑은 탐지 재현의 정책 차이로 드러내며 HMAC 채택 대상으로 사용하지 않았다. 원본 KoELECTRA raw decoder 합성 parity와 overflow 검증은 `koelectra_decoder_parity.json`에 기록했다.

`no_ensemble`이 탐지 F1 0.9913으로 가장 좋았지만 기본/해당 변형 모두 Gold 완전 보호는 910/912였다. 기존 V6의 912/912보다 부족하고, 검수 후보를 자동 마스킹·적재하지 않았다. F2는 **독립 탐지 재현**이며 원본의 다른 가명화/승인 의미를 새 96-bit 승인 계약으로 가장하지 않아 READY 0으로 격리했다.

### 채택·제외 근거

- **채택:** Frozen V6 보호 하한/타입별 원본 canonicalizer/96-bit HMAC, 기존 KoE5/own_v6. A에서 안전 경계와 처리율을 함께 개선한 신규 후보가 없다.
- **상시 3·4모델 union 제외:** 새 strict TP 없이 FP 증가, F1은 0.9521→0.8791/0.8599. evidence_v2 연결도 READY 49/187로 감소했다.
- **원본 Hybrid/boost 자동 채택 보류:** 높은 탐지 F1은 실측했지만 완전 개체 보호 2개 부족, 검수/타입/HMAC 동등성 문제 미해결. no_boost는 F1 0.9659, Kiwi 추가는 0.9805로 기본보다 나빴다. ensemble은 이 A에서 FP를 늘렸으므로 후속 후보에서는 끄는 편이 유망하지만 B로 추가 선택·튜닝하지 않았다.
- **F3 제외:** 기존 HMAC 경계를 유지하고 Hybrid의 라벨 변경·추가를 검수로 보냈으며 READY 145로 감소했다. 라벨만 바꿔 HMAC namespace를 바꾸는 구현은 채택하지 않았다.
- **F4 제외:** 형식·모델 지지 근거를 사용하되 합의만으로 HMAC 확정하지 않았다. 보호 향상 없이 READY 156으로 감소했다.
- **F5 제외:** Gold와 무관한 REVIEW/형식 미보호 신호에서만 보조 모델을 호출한다. marker-free 미탐을 찾아낸다고 주장하지 않는다. READY 169로 기준선 177보다 낮아 승격하지 않았다. 실제 조건부 호출 코드와 187문서 interleaved 비용 실험을 별도로 남겼다.

### 실제 HMAC 검증

| 세트 | 최종 준비 상태 parity | HMAC 출력 parity | 실제 READY HMAC strict TP/FP |
| --- | --- | --- | --- |
| A | 187건 / 불일치 0 | 177건 / 불일치 0 | 816/7 |
| B | 171건 / 불일치 0 | 155건 / 불일치 0 | 1263/36 |

A는 원본 실제 헤드 경로와도 상태 187/187, HMAC 출력 177/177 일치했다. B는 실제 모델 snapshot을 원본 준비/가명화 함수에 재생해 상태 171/171, 출력 155/155를 확인했다. HMAC strict FP는 Gold 경계·라벨 채점상의 오차이며 HMAC 함수 parity와 다른 지표다. 전체 제안 HMAC 수와 실제 READY 출력의 HMAC 수를 분리한 `actual_emitted_hmac.csv`를 따른다.

## P3 — 동일 입력 검색 비교

| 구성 | Hit@1 (85) | Hit@5 (85) | Hit@10 (85) | Hit@5 (88) | MRR@10 (85) | Confusable@5 |
| --- | --- | --- | --- | --- | --- | --- |
| own_v6 | 0.9059 | 0.9765 | 0.9882 | 0.9432 | 0.9368 | 0.3176 |
| team_v3 | 0.9059 | 0.9765 | 0.9882 | 0.9432 | 0.9368 | 0.3176 |
| hybrid_v1 | 0.9059 | 0.9765 | 0.9882 | 0.9432 | 0.9368 | 0.3176 |
| team_groups_chunks600 | 0.9059 | 0.9765 | 0.9882 | 0.9432 | 0.9368 | 0.3176 |
| team_groups_chunks200 | 0.8353 | 0.9529 | 0.9765 | 0.9205 | 0.8902 | 0.3176 |

원본 Top-5 parity는 **85/85**, 실제 187행 fingerprint는 불변이다. 문서용 세 Retriever는 개선이 없으므로 단순한 `own_v6`를 유지한다. 팀원 그룹 순서의 문서용 재현은 원본 청크 v3/기존 API embedding 재현으로 부르지 않는다.

원본 줄 단위 chunk 함수의 600자 설정은 **187문서→187청크**로 실제 분할이 없었다. 이에 Gold를 사용하지 않는 고정 200자 ablation을 추가해 **427청크**를 만들었다. 같은 보호 텍스트·KoE5·그룹 병합·문서 중복 제거로 평가했으나 Hit@5가 **83/85→81/85**로 감소해 제외했다. 지역 메타데이터는 동일 원본 문서에서 상속했고 원본 팀의 region namespace를 재현한 것은 아니다. SQL 새 청크 색인은 만들지 않았다.

| own_v6 대비 | Hit@5 개선 QID 수 | 악화 수 | MRR 개선 수 | 악화 수 |
| --- | --- | --- | --- | --- |
| own_v6 | 0 | 0 | 0 | 0 |
| team_v3 | 0 | 0 | 0 | 0 |
| hybrid_v1 | 0 | 0 | 0 | 0 |
| team_groups_chunks600 | 0 | 0 | 0 | 0 |
| team_groups_chunks200 | 0 | 2 | 4 | 10 |

`retrieval_qid_top10.csv`에 전체 88개의 실제 Top-10/neutral 채점 순위/개별 delta를 보존했다. neutral은 검색 입력에 사용하지 않고 반환 순위에서 채점할 때만 제외한다. scored Hit@k와 실제 online Hit@k를 분리했다. Confusable@k는 적어도 하나 등장한 질의 비율이고 문서 개수는 별도 `confusable_doc_count`다. `legacy_top5_metrics.json`은 기존 Top-5 길이/평가기를 그대로 적용해 Top-10 평가와 혼동하지 않도록 남겼다.

## P4 — 재처리·Identity·E2E 진단

F0로 다시 준비한 READY 문서 **177개 모두 기존 DB의 가명 텍스트와 동일**했다. 현 정책에서 REVIEW인 10개는 새 메모리 인덱스에서 제외했다. 그 결과 Hit@5는 79/85(전체 79/88)로 낮아졌다. 기존 187행을 덮어쓰거나 그 10개를 임의로 READY로 올리지 않는다. 같은 키·정책의 문서/질의 재처리를 실제로 검증한 R3 결과이며 새 SQL 적재는 BLOCKED다.

`identity.py` lookup을 원문 별도 namespace에서 **원본 PERSON 토큰→공통 legacy_lookup_key**로 바꾸고 Identity 정책 버전을 올렸다. 기존 HMAC 토큰/sidecar namespace는 보존했다. 띄어쓰기·복성의 문서/질의 lookup parity, 이름-only 미확정 상태, 검증된 독립 소유 속성 2종, 공유 속성·동명이인·충돌 방어를 테스트했다. `e2e_compare()`에도 실제 lookup/entity/불확실성 신호를 전달하고 DB sidecar를 연결했다. 기존 문서에서 entity 소유 관계를 복원했다고 주장하지 않는다.

최종 F0 × own_v6의 88질의 결과:

| 첫 실패/완료 | 건수 |
| --- | --- |
| Gate | 3 |
| Query 독립 검사 | 4 |
| Context/Identity | 71 |
| Mock 완료 | 10 |

세부 사유는 `RESIDUAL_PII_DETECTED` **31건**, `CLARIFY_UNVERIFIED_IDENTITY_CANDIDATES` **44건**, Frozen REVIEW/BLOCKED 3건이다. source ownership 없는 여러 이름 후보를 동일인이라고 확정하지 않아 CLARIFY한다. 현재 모의 생성 완료는 **10/88**이다. 과거 evidence_v2의 12/88과는 query 준비/identity 경로가 달라 단순 개선률로 주장하지 않는다.

분류 집계는 `{'UNKNOWN': 77, 'NOT_APPLICABLE': 10, 'POLICY_MISMATCH': 1}`이다. 알려진 7종/추가 민감유형 taxonomy 차이 1건은 POLICY_MISMATCH로 기록했고, 나머지는 근거 없는 FALSE_POSITIVE/REAL_RESIDUAL_PII 단정을 피했다. 모델/규칙/라벨/차단 이유는 집계 CSV, 위치·진단 상세는 Git 외부 파일에만 저장했다. 원문 제어 리터럴의 기존 deterministic 처리는 유지했으며 일반어 whitelist는 추가하지 않았다. 실제 잔여 PII 여부의 수동 검수는 **BLOCKED_PENDING_REVIEW**다.

`e2e_gate_diagnostics.csv`는 88질의 모두 처음 실패한 단계를 기록한다. guard가 차단하면 이후 단계는 실행하지 않는다. Mock 경로의 실제 검색 반환은 Top-5이므로 그 경로의 Top-10 지표는 미측정으로 표시했고 P3의 실제 Top-10과 섞지 않았다.

## 비용 실측 범위

고정 짧은 합성 입력에서 모델별 30회 warm 실제 호출:

| 모델 | P50 ms | P95 ms | 호출/초 |
| --- | --- | --- | --- |
| ax | 51.15 | 60.51 | 19.45 |
| veil | 14.13 | 15.44 | 70.38 |
| klue | 16.42 | 23.05 | 58.52 |
| koelectra | 10.57 | 12.51 | 93.83 |

짧은 합성 요청의 처리율이며 실제 긴 문서 처리량으로 일반화하지 않는다. AX/Veil 메모리는 같은 own2 worker의 peak RSS 약 1,929 MiB를 공유하며 두 번 더하지 않는다. KLUE/KoELECTRA worker peak는 약 490/365 MiB다. cold load 관측값은 own2 약 21.10초, KLUE 9.26초, KoELECTRA 8.65초이며 각 worker 1회 관측으로 cold P95라고 부르지 않는다.

A의 raw snapshot metadata에서 own2 inference P95 511.03ms, KLUE 109.22ms, KoELECTRA 53.34ms, quality4 IPC 전체 P95 664.62ms였다. 중복 문서 1건은 캐시 timing 재사용이다. 원본 Hybrid는 저장된 실제 추론 표본 185개에서 P95 577.69ms였고 ablation replay ms와 비교해 속도 향상을 주장하지 않는다. `team_timing_scope.csv`의 저장된 고유 timing 수는 전체 실행 요청 수와 다르다. 중복 원문 timing이 마지막 캐시 metadata로 덮인 한 건은 실제 추론 분포에서 제외했다.

추가로 모든 모델을 warm/resident 상태로 두고 같은 187문서에서 F0/F5/상시 quality4의 요청 순서를 문서마다 회전한 실제 비용 비교:

| 구성 | P50 ms | P95 ms | 문서/초 |
| --- | --- | --- | --- |
| F5 | 392.02 | 556.69 | 2.50 |
| always_quality4 | 495.41 | 734.99 | 1.94 |
| F0 | 378.97 | 547.81 | 2.57 |

F5의 실제 보조 호출 trigger는 18/187, snapshot 정책과 불일치는 0건이다. cold 비용/메모리 절감 실험은 아니며, 속도 결과가 좋아도 READY 감소를 이유로 F5를 채택하지 않는다. 추가 벤치마크 추론은 P1의 최초 snapshot 취득과 별도다.

SQL/rank P95는 `retrieval_conditions.json`에 프로파일별로 분리했다. 격리 메모리 rank와 실제 SQL 전체 경로의 지연을 동일 조건인 것처럼 비교하지 않는다. 최초 E2E는 캐시/실제 재검사 및 초기 로딩이 섞인 88질의에서 P50 463.96ms / P95 2018.99ms였으며, 이 값은 고정 warm 전체 시스템 SLO가 아니다. 재현 시 이미 존재하는 모델/벡터 캐시에 따라 달라진다.

## P5·테스트·남은 제한

선택 구성 F0 × own_v6로 새 합성 문서 3개를 원본 V6 준비→독립 검사→실제 KoE5→새 메모리 인덱스→Mock HCX/출력 검사까지 실행해 **PASS**했다. 실제 Mock 호출 1회, SQL 쓰기 0회, 유료 HCX 호출 0회다. HCX-007 기존 schema/호출 코드는 재사용 가능하지만 실제 외부 전송은 실행하지 않았다. Mock citation/schema 성공은 주체 정합성·인용 관련성·사실 충실도 성공이 아니며 실제 88질의 답변 품질은 **NOT_MEASURED**다.

최종 회귀: **327 PASS / 1 SKIP / 0 FAIL**. 기존 Step1/Step2/root 테스트와 Unified 테스트를 포함한다. 1 SKIP은 원본 release notebook 부재에 따른 기존 테스트다. 초기 Step1 테스트 import 오류는 PYTHONPATH에 Step1 root를 추가해 해결했고, B 계측 분류 오류도 실제 원본 상태를 따라 수정·회귀 테스트했다. 상세 명령/수는 `validation.json`에 남겼다.

다음 작업은 다음과 같다.

1. Git 외부 진단으로 31개 query/context 차단 및 101개 문서 차단의 실제 잔여 정보/오탐/정책 차이를 검수한다. Gold 부재만으로 일반어 whitelist를 만들지 않는다.
2. Legacy 이름 후보의 명시적 source ownership을 확보하고 동명이인 oracle로 평가한다. 44개 CLARIFY를 단지 처리율을 높이기 위해 없애지 않는다.
3. 원본 Hybrid no_ensemble의 2개 불완전 보호와 검수/HMAC 경계·타입 parity를 해결한 후 새로운 미공개 데이터로 평가한다. 현재 B를 다시 튜닝 세트로 쓰면서 신규 blind라고 부르지 않는다.
4. 확인된 폐기 가능 별도 DB가 있으면 SQL 새 인덱스 통합/청크 성능을 검증한다. 현재는 **BLOCKED_NO_VERIFIED_DISPOSABLE_DB**다. 운영 DB에 schema/GIN을 추가하지 않는다.
5. 실제 HCX/외부 embedding은 **BLOCKED_BY_NO_OPT_IN**이며 이번 작업에서 실행하지 않았다. 사용자 별도 opt-in 이후에도 승인된 합성 1~2회 예산 제한부터 별도로 진행해야 한다. 운영 답변 사실성/개인정보 재노출은 아직 미측정이다.

보존: 기존 V6/main/다른 worktree/참조 저장소/HMAC/운영 DB 불변. 실험 중 운영 DB 쓰기·실제 HCX·비공개 원문 외부 전송·PR·merge는 모두 0회다. GitHub push는 실험 종료 후 사용자 별도 요청에 따라 작업 브랜치에만 진행한다. 최종 추천은 로컬 평가의 안전한 기준선이며 운영 준비 완료 선언이 아니다. P6/운영 전환은 진행하지 않는다.

## 재현 명령

```bash
cd /Users/gojunseo/AegisRAG-unified-v1
AEGIS_MODEL_PY=/tmp/aegis-unified-followup-model-env/bin/python
AEGIS_RETRIEVAL_PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag-retrieval/bin/python
export PYTHONPATH=step2_privacy_rag/app
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false

"$AEGIS_MODEL_PY" -m unified_v1.selection P0
"$AEGIS_MODEL_PY" -m unified_v1.selection P1
"$AEGIS_MODEL_PY" -m unified_v1.selection P2
"$AEGIS_RETRIEVAL_PY" -m unified_v1.selection_retrieval P3 --runtime-python "$AEGIS_MODEL_PY"
"$AEGIS_RETRIEVAL_PY" -m unified_v1.selection_retrieval P4 --runtime-python "$AEGIS_MODEL_PY"
"$AEGIS_MODEL_PY" -m unified_v1.selection_retrieval P4 --indexability-only --runtime-python "$AEGIS_MODEL_PY"
"$AEGIS_RETRIEVAL_PY" -m unified_v1.selection_retrieval P5 --runtime-python "$AEGIS_MODEL_PY"
"$AEGIS_MODEL_PY" -m unified_v1.selection P2 --cascade-benchmark

PYTHONPATH=step2_privacy_rag/app:step1_pii_detector/src:step1_pii_detector "$AEGIS_MODEL_PY" -m unittest discover -s step1_pii_detector/tests -q
PYTHONPATH=step2_privacy_rag/app:step1_pii_detector/src "$AEGIS_MODEL_PY" -m unittest discover -s step2_privacy_rag/tests -q
PYTHONPATH=step2_privacy_rag/app:step1_pii_detector/src "$AEGIS_MODEL_PY" -m unittest discover -s tests -q
PYTHONPATH=step2_privacy_rag/app:tests/unified_v1 "$AEGIS_MODEL_PY" -m unittest discover -s tests/unified_v1 -q
"$AEGIS_MODEL_PY" -m unified_v1.selection_report
```

로컬 데이터 경로는 manifest에 고정돼 있다. 재현은 동일 검증을 반복하며 새 독립 검증이 아니다. `/tmp` 환경은 삭제될 수 있으므로 복원 시 기존 Conda를 업그레이드하지 말고 새 venv에서 manifest의 transformers 4.55.4/tokenizers 0.21.4/hub 0.36.2/numpy 1.26.4/Kiwi 0.22.2를 맞춘다. 모델 가중치·데이터·운영 `.env`를 repo에 복사하지 않는다. 정상 캐시 재사용과 실제 모델 재측정/추가 benchmark를 구분한다.
