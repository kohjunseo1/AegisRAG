# AegisRAG — 실전1·2 고성능 통합 최소실험 명령서

> 목표: **기존 우리 V6와 팀원 Hybrid의 고성능 부분만 선택·통합**해, 런타임 NER **1개 또는 최대 2개**로 Strict F1·가명처리 안전성·READY 처리율·P95 지연을 함께 최적화한다. 4팀 원본을 같은 조건의 외부 기준선으로 사용한다. **처음부터 재구축·모든 조합 재추론·새 파인튜닝·새 아키텍처 개발 금지.** 결과가 안 나오면 측정 근거와 실패 원인을 보고, 원본 최선안을 선택한다.
>
> 확인한 기준: 우리 `kohjunseo1/AegisRAG` `feature/unified-v1` HEAD `172aaa68a21f0aa8d39e647642a652281aad825f`; 팀원 `8bitHermitcrab/privacy-rag-chatbot` `feature/detect` HEAD `242543bf0eae91aa3d655227a747aa128707368e`. 4팀 `BBuSang/likelion-AI-4team` 실행 시 SHA 기록. 세 원본, frozen HMAC/key/canonicalizer, 운영 DB, main 불변.

## 0. 시작 전에 읽을 자료·예산 (반드시)

- 이전 측정 재사용: `reports/unified_v1/selection/{step1_raw.csv,step1_raw_by_label.csv,step1_raw_paired.csv,raw_resources.json,team_original_ablation.csv,team_protection_ablation.csv,step1_fusion.csv,actual_emitted_hmac.csv,frozen_parity_and_cost.json,retrieval_same_input.csv,e2e_gate_diagnostics.csv}` 및 `docs/unified_v1/FINAL_BASELINE_DECISION.md`. 실측 Raw snapshot은 `~/.cache/aegisrag/unified_v1/private/selection/`의 입력·모델·코드 hash 일치 시에만 재생. 없으면 **해당 후보만** 재추론; cache time을 inference latency라 부르지 말 것.
- 원본 로직: 우리 `step1_pii_detector/src/pii_detector/{detector.py,structural_policy.py}`, `step2_privacy_rag/app/{privacy_gate.py,document_preparation.py,local_pseudonymizer.py}`; 팀원 `starter/{pii_pipeline.py,detect_boost.py,hybrid_detector.py,model_ensemble.py}`; 4팀 `실전프로젝트1/{pii_detector.py,make_union.py,postprocess.py}`와 `preds/combined/rules+atonlee_holdout.jsonl`.
- 최대 **2개의 서로 다른 NER 체크포인트/요청**, 문서·질의·독립 검사까지 숨겨 실행되는 모델/forward 횟수를 모두 보고. 규칙·Kiwi 등은 비용/오류 계측. 학습·증류·LLM 원문 외부 전송·운영 DB 쓰기·push·대규모 리팩터링 금지.
- **한 단계에서 의사결정한 후에만 다음으로 이동**. 실패한 재현 단계는 BLOCKED 기록하고 기존 증거로 가능한 분석을 계속 수행. Markdown 보고서 하나 + 필수 집계 CSV 3~4개만 남긴다.

## 1. 이미 검증된 사실과 우선 가설

| Raw 모델, A 187문서/912 Gold | TP/FP/FN | Strict F1 | 근거 |
|---|---|---:|---|
| A.X | 902/42/10 | **.9720** | PERSON 558/560, ADDRESS 44/52 (주소 FP13), PHONE 147/147, ACCOUNT 41/41 |
| Veil | 893/59/19 | .9582 | **주소 47/52, FP6** — A.X 대비 주소 유리 |
| KLUE | 797/172/115 | .8474 | Raw 낮음. 그러나 팀원 Boost 후 최종과 다르므로 단독 폐기 금지 |
| KoELECTRA | 903/67/9 | .9596 | 주소 47/52, FP10, 비교적 짧은 warm 추론 |

- A.X+Veil **raw union** 905/84/7; 4모델 union 905/288/7. KLUE/KoELECTRA 추가 **신규 exact TP 0**은 union의 한계일 뿐 *경계 선택*에 무가치하다는 뜻은 아님. 912 Gold 전부 Raw 후보와 **부분 겹침**이 있으므로 개발셋에서는 후보 생성보다 경계·타입·오탐 의사결정이 주된 과제. 겹침을 자동 복구 가능한 정답으로 취급 금지.
- Frozen V6의 **최종 typed** 843/7/69·F1 .9569·READY177/187은 **Raw와 다른 단계**이므로 `AX에서 59개가 Gate에 의해 삭제` 등 원인 미확인 단정 금지. 함수별 attribution 필요.
- 팀원 원본 `no_ensemble` F1 **.9913**(908/12/4), `default` .9880, `no_boost` .9659. **`no_ensemble`은 RoBERTa 글자 확률 추가만 끈 것, KLUE+KoELECTRA 2 NER는 그대로**! 따라서 **KLUE+Boost·KoELECTRA 제거(1모델)** 성능은 아직 별도 측정되지 않았고 최우선 실험.
- 팀원 Hybrid 출력의 **제안 Gold 완전 보호는 910/912** vs Frozen V6 제안 912/912. 이 수치를 실제 READY 안전성으로 혼동 금지. 4팀 규칙+atonlee(1NER)는 holdout **최종 F1 .9951**이지만 **그 holdout 오류를 보고 수정**했으며 별도 hard-eval F1 약 .8655. 동일 A 데이터/SHA/스코어러로 비교 전 무조건 우수하다고 주장 금지.
- KoELECTRA에는 평가용 7종 외 추가 민감 라벨도 있지만, **7종 Strict F1 개선과 분리**하고 위험 신호로만 검토. 증거 없이 다른 라벨을 주민번호·계좌 HMAC으로 변환하지 말 것.
- 원본 Hybrid·4팀의 품질 향상은 모델 수보다 `주소 경계 보정`, `이름 호칭/조사·강한 필드 문맥`, `번호 오탐 필터`, `문서 내부 재등장` 등 **저비용 후처리**에 큰 부분이 있다. 4팀의 모든 어휘 차단 목록을 무작정 복사하면 OOD 이름 미탐 위험.

**우선 가설:** `고성능 NER 1개 + 유형별 검증/경계·문맥 보정`으로 충분한지 먼저 보고, **두 번째 모델의 순증 TP·오탐 감소·실제 보호 이득이 입증될 때만** 비용을 지불한다. 4팀의 코드 복제가 아니라 **서로 다른 타입별 증거에 따른 span 선택 + 안전한 HMAC 의사결정 분리**가 차별점 후보.

## 2. 비교할 후보 — 최소화

**원본 기준선 (수정 금지)**
- `B0`: Frozen V6, A.X+Veil (2NER).
- `B1`: 팀원 원본 Hybrid **no_ensemble**, KLUE+KoELECTRA+Boost (2NER).
- `B2`: 4팀 원본 규칙+atonlee+후처리 (1NER). 원본 예측 파일과 입력/Gold가 정확히 같으면 재추론 없이 동일 scorer 사용.

**최소 신규 실험**
- `C1` **우선**, 1NER: 팀원 **KLUE+Boost+후처리**, `ENSEMBLE=False`, **KoELECTRA 완전히 제거**. 기존 cached `P.model_predict`를 `P.detect`→`post_filter`와 같은 호출 순서로 재생. **왜:** `no_boost`와 default의 차이는 크지만 **KoELECTRA 자체의 순기여는 아직 미측정**. 모델 하나로 .99 근처를 재현할 수 있는지 먼저 확인.
- `C2` **우선**, 1NER: **A.X + 범용 경계·문맥·형식 보정**. 원본 A.X Raw을 보존하고 팀원 `detect_boost`·4팀 `postprocess`에서 일반화 가능한 *순수 함수/검증 원리만* 이식. 먼저 조사/호칭, 주소 상세·우편번호, 번호 필드 미끼의 개별 ablation. **왜:** A.X Raw F1 .972이고 Gold 완전 미탐보다 정확한 경계·후보 선택 오류가 중요.
- `C3` **조건부**, 2NER: `C2 + Veil`, **동일 Resolver**, 특히 ADDRESS 정확한 타입·경계에 도움이 되는지. Veil 주소 47/52 FP6으로 근거 있음.
- `C4` **추가 근거 있을 때만**, 2NER: `C2 + KoELECTRA`, **동일 Resolver**. 비용은 빠를 수 있지만 주소 FP10/이름 FP52라 바로 우선 아님. **C3보다 의미 있는 추가 경계 복구/성능-속도 이득이 기존 snapshot에서 관찰될 때만** 구현.

**하지 말 것:** 15조합 재실행, KLUE+Veil 등 무근거 조합, 별도 새 NER 다운로드, Kiwi 기본 장착, 파인튜닝부터 실행, 모든 팀 코드를 살리는 통합. 결과가 B1/B2가 최고면 그 원본 탐지부를 채택하고 **우리 안전한 HMAC·RAG 계약**에 연결 가능해야 한다.

## 3. Phase A — 원본 결과와 성능 손실의 실제 원인 (우선 실행)

1. **동일 평가 계약:** 원문 SHA/Gold SHA/7종/Unicode `[start,end)`/scorer/문서-ID 정렬. 4팀 `preds/combined/rules+atonlee_holdout.jsonl`과 우리 A 입력이 불일치하면 수치 비교 `NOT_COMPARABLE`; 원본 재실행 가능할 때만 같은 scorer 사용. 4팀 holdout 수정 **전/후** 구분. Original detector의 **Raw typed span**끼리 비교하고, 보호/Gate/HMAC·READY를 섞지 말 것.
2. **Frozen V6 단계별 추적:** `A.X·Veil Raw → fuse_labelwise → validate_boundaries → refine_spans → Step1 entities → Gate entities → Gate entities_for_pseudonymization → original prepare/HMAC`. 각 단계 TP/FP/FN와 오류 이동의 원인(`DROP/BOUNDARY/LABEL/HMAC_NOT_CONFIRMED/REVIEW`)별 집계. `FN69`를 **실제 미보호 69건**이라고 주장 금지.
3. **팀원 핵심 Ablation:** B1(2모델)과 C1(1모델)의 **KoELECTRA만 끈 차이**를 정확히 실행/재생해 `TP/FP/FN, PERSON·ADDRESS 오류, REVIEW 후보` 확인. `no_ensemble`을 `no_koelectra`와 혼동하는 회귀 테스트 작성.
4. **경계 보정 기여 검증:** A.X에 (a) 이름 조사·호칭, (b) 주소 구조·상세주소, (c) 번호 타입·미끼 문맥을 **하나씩** 적용해 후보별 `recovered TP, lost TP, removed FP, created FP` 기록. Gold·이름 목록을 실제 정책 입력으로 사용하는 규칙 금지.
5. **두 번째 모델 후보 상한:** 기존 Raw snapshot에서 `AX+Veil` vs `AX+KoELECTRA`의 *정확 후보 추가*와 *경계 후보 다양성*을 계산. Oracle은 오직 진단 상한(정답을 보고 선택하면 실서비스 성능 아님). **C3/C4 승격 여부를 이 데이터로 결정**.

**산출물:** `A_same_scorer.csv`, `A_error_taxonomy.csv`, `A_second_model_value.csv`. 원문 포함 오류 상세는 Git 외부 비공개.

## 4. Phase B — 최소 Typed Span Resolver, 다른 기능 붙이지 말 것

**목적:** V6 F3/F4처럼 기존 typed 후보를 그대로 두고 REVIEW만 증가시키는 것이 아니라, **증거가 충분한 경우 실제 typed 경계·라벨을 고르는 통합**.

- *입력*: 선택된 1 또는 2 NER의 원본 후보 + 규칙 기반 형식/문맥 후보. **모델별 raw softmax 직접 비교 금지**(보정되지 않음). 후보를 Unicode 위치/label/source로 관리. 겹친 후보끼리만 비교.
- *번호·이메일*: 완전한 digit run/구분자, 전화 국가코드, 타입별 형식과 필드 문맥. 13자리라서 무조건 RRN, 임의 숫자를 HMAC으로 확정하는 규칙 금지. checksum 단독으로 개인정보 부정도 금지.
- *주소*: 우편번호·시군구·도로명/지번·건물·동호수를 국소적으로 확인; `가장 긴 경계`가 항상 정답이라고 가정하지 않고 주변 비주소 문구와의 병합 방지.
- *PERSON*: 직함/성명 필드/호칭/서명/동일 문서에서 강하게 확인된 재등장만 긍정 근거; 성씨·사전/형태소만으로 확정·삭제 금지. `이`로 끝나는 실제 이름과 조사 이, 외자·복성, 일반명사 같은 이름은 반례 테스트. 동일 문서 내 후보 추가는 **문서 간 identity linking이 아님**.
- *충돌 해소*: 타입별 구조·문맥과 후보 근거가 충분하면 하나의 정확 typed span 선택; 근거 부족하면 `ABSTAIN → Mask-Only/REVIEW`, 확정 HMAC 금지. **보호용 mask coverage와 HMAC typed span을 서로 다른 출력**으로 유지. 기존 F0 보호 하한 훼손 금지.
- *가명화*: 기존 96-bit HMAC·canonicalizer·토큰 namespace 재사용. 신규 실험용 **모델 독립 typed-plan adapter**로 후보를 검사하되 F0와 팀원 출력을 같은 원칙으로 평가. 기존 V6 Gate가 A.X/Veil을 호출하도록 되어 있으므로 C1/C2에서 이를 **숨겨 호출하지 않는다**. 원본 F0 Gate 실행은 원본 회귀 확인용. 원본 정책과 공통 Adapter 승인 기준이 다르면 각각의 READY 표를 분리해 공정 비교.
- *규칙 추적*: 모든 추가 규칙을 하나씩 off 하는 ablation. `TP 회복 / TP 훼손 / FP 감소 / FP 증가 / Gold 완전보호 / HMAC 오경계` 기록. 규칙이 실제 성능 개선 안 하면 버린다.

**실험 순서:** C1/C2 + 최소 Resolver → 둘 다 약할 때만 C3 → 기존 cache에 정당한 근거가 있으면 C4. B0/B1/B2 원본은 기준. 우승 1~2개만 Phase C.

## 5. Phase C — 실전1 최종 판단: 안전·정확도·속도·일반화

- **동일 개발 데이터 A (187/912)**: Strict P/R/F1, Gold 개체 완전 겹침·정확 일치, 모델/라벨별, 문서별 paired win/tie/loss. 팀원·4팀은 같은 scorer 원본 출력 기준. **정책의 제안 mask 912/912는 실제 보호 성공 아님**.
- **실제 가명처리**: 실제 `LOCAL_PREPARED` 산출 sanitized text의 Gold 완전 보호, 잘못된 HMAC 타입/경계·토큰 일관성, READY/REVIEW/BLOCKED, Mask-Only 미승인 비율, 독립 재검사 통과율. REVIEW/차단의 제안 보호 범위는 별도. 실제 미보호/잘못된 HMAC 증가하는 후보는 탈락.
- **통계적 비교**: 같은 문서 단위 bootstrap CI·paired win/loss를 기록. 작은 F1 차이만으로 Holdout2 우위를 단정하지 말 것.
- **시간**: 모든 후보를 **동일 Mac·동일 입력/순서·동일 스레드**에서 실추론. 모델 forward 수, cold start, 187문서와 긴 문서 171개 warm P50/P95, RSS, 단계별 모델/규칙/Resolver/Gate/HMAC 비용. 기존 KLUE/KoELECTRA 캐시 replay ms를 실제 추론 ms로 사용 금지. 다른 컴퓨터에서 측정된 4팀 35.8ms와 직접 속도 배수 비교 금지.
- **분포 이동 B**: `blind_eval.jsonl` 171문서·1439 Gold는 **이미 노출된 별도 분포 검증**. A에서 후보/임계값 고정 후 B 평가, B로 조정하면 평가용 지위 하락. 4팀 자체 fresh/hard는 label 정의와 중복/생성기 계열 확인 후 보조 스트레스셋으로만. Holdout2는 최종 고정된 상태에서만 한 번 평가.
- **결정**: A 기준 4팀보다 우수하거나 사전에 정한 동등 허용폭(예: F1 차이 .005) 안에서 더 낮은 모델 수/p95와 보호·HMAC 안전성을 보여주는 후보 채택. 단독이 동등하면 **1NER**. 2NER는 추가 TP·FP·보호 또는 OOD 개선이 실제로 비용을 정당화할 때만. **정적 2NER가 이긴 뒤에만 조건부 Cascade 검토**(미호출 시 완전 미탐 위험이 있는지 offline counterfactual 먼저). 187문서 개선이 Holdout2 우월 보증 아님. 파인튜닝은 **이번 작업 제외**; 남은 모델 자체 완전 미탐이 확인된 경우 별도 의사결정.

**산출물:** `C_pareto.csv`(model_count, models, strict TP/FP/FN, P/R/F1, Gold 완전 보호(계획/실제 READY 분리), HMAC 오류, READY/REVIEW/BLOCKED, p50/p95/RSS, OOD delta, 선택 이유).

## 6. Phase D — 실전1 우승 구성 하나만 실전2 E2E 연결

1. 선정된 실제 NER 구성 하나 → 새 typed plan/HMAC → 실제 KoE5 → **격리 인메모리 인덱스** → 현재 Retriever → HCX **Mock**. 정상 합성질의와 개인정보 차단 반례를 실제 코드로 검증(외부 API 없음).
2. 가명 정책/토큰이 바뀌었으면 **과거 DB의 187개 가명 문서와 혼용 금지**. 같은 구성으로 문서·질의를 새로 준비한 인메모리 인덱스에서 평가. 기존 DB는 read-only 그대로.
3. Smoke가 성공하면 기존 **88질의** 한 번만 로컬 검증: Gate/Query recheck/Identity/Retrieval/Context/Mock/Output recheck 최초 실패·정상 처리수 보고. 현재 10/88은 탐지 성능이 아니라 독립 Guard·Identity 차단을 포함. 단지 통과율을 높이기 위해 보안검사 해제하거나 동명이인 무근거 연결 금지.
4. 실제 HCX 유료 호출·원문 외부 전송·운영 DB INSERT·UI 전체 재구축 금지. Step2 실패 원인을 독립 과제로 남기고 Step1 성능 선정과 혼동하지 않는다.

## 7. 제출/중단 기준

- **최종 보고서 하나:** `docs/unified_v1/LEAN_FINAL_DECISION.md`에 `B0/B1/B2/C1/C2/(선택시 C3/C4)`의 정직한 동등 평가 표, 채택·폐기한 원리/함수와 delta, 실제 모델 호출 수/속도, A/B 차이, HMAC 안전성, 격리 E2E 결과, 미완료 blocker, 재현 명령, Git+data+model+scorer SHA 기록.
- **실험 집계 CSV 최대 4개**만 Git에 저장. 원문·Gold·Raw Span·HMAC 키/매핑·개인정보 포함 오류 상세는 `~/.cache/.../private` chmod 0700/0600으로 보관. CI의 `/Users/gojunseo/...` 절대경로 단위 테스트 실패도 최종 완료 전에 독립 fixture 또는 pin checkout으로 수정.
- 불가능하면 `BLOCKED: 원인 / 필요한 최소 파일 또는 권한 / 기존 근거만으로 가능한 결론`을 짧게 보고. 실측 없이 `성능 개선`, `E2E 완료`, `안전 보증`, `Holdout2 일반화 증명` 주장 금지. **추가 모델·모든 조합 재실험·새 프로젝트 구조를 임의로 시작하지 마.**

## 방법론 근거 (연구가 우리 데이터의 점수를 보장하지는 않음)

- Microsoft Presidio 공식 Analyzer: 유형별 PatternRecognizer·validation·context enhancer·NER 조합 https://microsoft.github.io/presidio/analyzer/
- Calibrating Structured Output Predictors (ACL 2020): 엔터티 수준 보정, 모델 Raw confidence 직접 비교의 한계 https://aclanthology.org/2020.acl-main.188/
- The Art of Abstention (ACL 2021): 불확실할 때 보류하는 선택적 판단 https://aclanthology.org/2021.acl-long.84/
- Guidance-Based Prompt Data Augmentation for Domain NER (ACL 2024): 장기 학습시 문맥 다양화, **이번 통합에서 학습 금지** https://aclanthology.org/2024.acl-short.61/
