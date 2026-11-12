# AegisRAG 개선 실험 일지

현재 동작 중인 Step1·Step2 V6를 기준선으로 두고 **개선 이유 → 변경 → 동일 조건 비교 → 채택 여부**만 기록합니다. 과거 V1~V6의 상세 과정은 [EXPERIMENT_HISTORY.md](EXPERIMENT_HISTORY.md), 환경/재현 명령은 [REPRODUCIBILITY.md](REPRODUCIBILITY.md)를 참고합니다.

## 공통 기준과 기록 원칙

- **기준선 검색:** 문서 187개 중 자동 적재 177개 / REVIEW 10개; READY 질의 85개, 평가 가능 81개. Hit@1 **73/81**, Hit@5 **79/81**, Recall@5(indexable Gold) **0.8802**, Recall@5(full Gold) **0.8791**, MRR **0.9340**.
- **혼동:** Confuse@10 **0.5345** (혼동 Gold가 정의된 **29개 평가 가능 질의**). 향후 Confuse@5와 인용 단계 혼동률은 별도 산출하며 분모와 정의를 명시합니다.
- **보호:** Step1 A.X·Veil, Gate V4, 24-hex HMAC 토큰 및 Region Key를 기준으로 합니다. 원본/키/토큰/민감 텍스트는 보고서 및 로그에 저장하지 않습니다.
- 각 실험은 새 브랜치 및 필요한 경우 별도 해시 테이블을 사용합니다. 기존 V6 테이블/비공개 평가 데이터/공통 키는 변경하지 않습니다.
- 탐지 결과·REVIEW 분포·개체 보호 범위와 기존 검색 성능을 회귀 검증한 뒤 채택합니다. Holdout 반복 튜닝은 피하고, 새 규칙은 합성/Dev 사례와 반례로 선정한 뒤 고정 기준선에서 검증합니다.
- 지연시간은 **cold/warm**, **로컬 vs HCX 포함 E2E**, **장치/프로세스/입력**, 단계별 **p50/p95**, 메모리 사용량을 구분합니다. 보안·정답률 저하와 속도 개선을 함께 기록합니다.

### 신규 실험 기록 양식

```text
[ID / 날짜 / 상태: 계획·실험중·채택·보류·폐기]
이유·가설:
변경: (브랜치/커밋, 코드 또는 설정 변경)
조건: (데이터·모델·장치·실험 횟수)
전후 결과: (품질·보호·속도, 분모 및 범위)
판정·다음 작업:
```

---

## L-000 · 2026-11-11 · 현재 V6 로컬 Latency 기준선 (측정 완료)

**목적:** 서비스 대기시간의 주요 병목을 확인하고 다음 최적화의 우선순위를 선정.

**조건:** macOS, CPU, 같은 Python 프로세스 내 동일한 비식별 일반 질문 `출장 결과 보고의 목적을 알려줘` 3회 연속 실행. `LivePipeline.ask_local()`; **HCX 외부 API 호출 제외**. 매 회 실제 Step1/Gate→질의 임베딩→177문서 V6 읽기 전용 조회→Top-5 반환. 1회차는 모델 최초 로딩을 포함함.

| 구간 | 1회차 (ms) | 2회차 (ms) | 3회차 (ms) |
|---|---:|---:|---:|
| Step1 생성·탐지·Gate·가명처리 | 38,784.9 | 13,865.6 | 12,455.7 |
| KoE5 임베딩 | 7,829.5 | 235.7 | 242.6 |
| PostgreSQL/PGVector | 100.7 | 41.6 | 52.3 |
| 재정렬 | 0.6 | 0.9 | 0.8 |
| **총 로컬 처리** | **46,715.9** | **14,144.0** | **12,751.6** |

**분석:** 반복 요청 2~3회 평균 총 **13.45초**, Step1 약 **13.16초(97.9%)**. KoE5는 1회차 이후 약 0.24초이며 DB 조회는 약 0.05초. 현재 `core.LivePipeline.prepare()`는 매 요청 `with create_detector()`로 탐지기를 생성·종료하지만, KoE5는 인스턴스에 캐시함. **Step1 반복 생성이 유력한 병목**이나, 단계 안에는 실제 추론과 Gate도 포함되므로 로딩 비용은 미분리 상태. 3회 관측만으로 p50/p95 또는 일반적인 운영 응답시간을 주장하지 않음.

**판정:** 추가 구현 없이 측정값을 고정. 다음 우선순위 **L-001 Step1 생성/추론 분리 계측 및 탐지기 재사용**. DB SQL·KoE5 모델 교체는 현재 우선순위 아님.

## L-001 · 채택(옵션 기능) · Step1 탐지기 재사용

**가설:** A.X·Veil 모델 객체를 프로세스 내 안전하게 재사용하면 반복 요청 지연이 줄어듦.

**실험 설계:**
1. 현재 `step1_ms`를 **탐지기 생성/모델 로딩**, **탐지 추론**, **Gate·HMAC 준비**로 분리 측정. 관측을 위해 중복 탐지를 수행하거나 PII 원문을 로그에 남기지 않음.
2. 기준 구현과 **장수명 탐지기 재사용** 구현을 별도 브랜치로 비교. 탐지기 수명 종료 시 명시적 `close()`; 추론 예외·타임아웃 시 기존 fail-closed 계약 유지, 오류 난 객체를 안전하다고 가정해 재사용 금지.
3. cold 1회 + warm 최소 20회, 일반/이름/주소/번호/REVIEW 합성 질의 및 허가된 비공개 회귀 세트. CPU, 프로세스 조건, 메모리(RSS), `step1_ms`, `embedding_ms`, `db_ms`, `hcx_ms`, `total_ms` 구분.
4. **채택 조건:** 출력 상태·보호 구간·가명 토큰·문서 검색 순위·Step1 테스트·V6 baseline이 같고, 실제 warm p50/p95 개선 및 메모리 사용량이 수용 가능할 것. 속도 개선 수치는 측정 뒤 기록.

**현재 상태:** **실제 CPU 속도·12질의 보호 동등성·합성 회귀 검증 완료; `--reuse-step1` 선택 기능으로 채택**. 기본 실행은 매 요청 생성·종료를 유지하며, 서비스를 상시 재사용 기본값으로 바꾸는 것은 별도의 장시간 운영 검증 후 결정합니다. 실험 브랜치 `perf/l001-step1-detector-reuse`에서만 `--reuse-step1`을 활성화할 수 있고, 기본 실행 경로는 기존처럼 매 요청 생성·종료합니다.

- 변경: `LivePipeline`에 opt-in 탐지기 재사용·명시적 종료, 생성/모델 로딩(`step1_init_ms`)과 Step1 탐지+Gate+가명처리(`step1_process_ms`) 분리 계측, 예외 시 재사용 중단을 추가했습니다. **탐지 추론만의 시간과 Gate 시간은 아직 분리되지 않았습니다.**
- 회귀: 합성 탐지기에서 동일 처리/REVIEW 유지/종료/예외 후 자동 재시도 금지 테스트 추가. 실제 A.X·Veil, 비공개 문서, Mac 16GB 메모리 영향은 별도 실측 필요.
- 독립 프로세스로 비교(기존 DB 읽기 전용, **HCX 미호출**):

```bash
# 실험 브랜치 checkout 후 가벼운 사전 점검
python scripts/benchmark_step1_reuse.py --mode baseline --runs 3 --device cpu
python scripts/benchmark_step1_reuse.py --mode reuse --runs 5 --device cpu

# 메모리가 안정적일 때 반복 실험 확대
python scripts/benchmark_step1_reuse.py --mode reuse --runs 20 --device cpu

# 실험용 로컬 챗봇은 명시적 opt-in
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/aegisrag.py run-chat --device cpu --port 8768 --reuse-step1
```

### 2026-11-11 · 실제 Mac CPU 예비 비교 (cold 1회 + warm 3회 / 모드)

- **실행 환경:** 동일 Mac, `aegisrag-retrieval` Conda 환경의 A.X/Veil·KoE5 실모델, 기존 177문서 PGVector. 동일 공개형 일반 질문을 반복했으며 HCX 호출·DB 쓰기 없음.
- **오류 수정:** 처음 실행에 사용된 다른 Conda 환경 `aegisrag`는 torch/transformers 호환성 오류로 중단. 정상 사용하던 `aegisrag-retrieval` 환경에서 재측정함. 해당 오류는 탐지기 재사용 효과 측정 결과에 포함하지 않음.

| 지표 | 기본 방식 | 재사용 방식 |
|---|---:|---:|
| Cold 전체 로컬 | 37,261.2 ms | 36,865.7 ms |
| Warm 전체 로컬 평균 | **14,324.7 ms** | **482.5 ms** |
| Warm Step1 평균 | 14,004.8 ms | 220.4 ms |
| Warm Step1 초기화 평균 | 13,508.5 ms | 0.0 ms |
| Warm 임베딩 평균 | 261.9 ms | 217.6 ms |
| Warm DB 평균 | 57.0 ms | 43.7 ms |
| Peak RSS (별도 프로세스) | 4,196.5 MiB | 2,707.5 MiB |
| Top-5 ID 순위 해시 | `cd22fad63a17f8fe` | 동일 |

**예비 효과:** warm 전체 평균 약 **29.7배 빠름**, 처리시간 약 **96.6% 감소**. 반복 Step1 초기화 약 **13.5초**가 주요 병목이었음. Cold는 큰 변화 없음. Top-5 문서 ID 순서는 해당 질문에서만 일치 확인. 서로 다른 프로세스의 peak RSS는 비교 참고용일 뿐 메모리 감소 보장 아님.

**현재 판정:** **유망 / PR 유지·main 미병합**. 각 모드 warm 3회뿐이라 p95 없음; 이름·주소·REVIEW/BLOCKED 실모델 질의의 보호 결과 parity, 폭넓은 회귀, warm 20회 p50/p95 및 메모리 안정성 검증 후 채택 결정. 특히 현재 baseline 평가 CLI는 준비된 질의를 읽기 때문에 **실시간 탐지기 재사용 동작 자체의 동등성 검증을 대신할 수 없음**.

### 2026-11-11 · Warm 20회 및 테스트 재검증

- **실행:** `--mode reuse --runs 20 --device cpu`; cold 1회 + warm 20회, 동일 일반 질문. 실모델·기존 V6 177문서 읽기 전용 DB, HCX 미호출. 기존 방식의 참조 평균은 앞서 관측한 warm 3회로서 완전히 같은 횟수의 대조 측정은 아님.
- **Cold 로컬 전체:** **35,975.2 ms** (Step1 최초 생성 26,638.6ms, KoE5 초기 임베딩 8,197.6ms).
- **Warm 전체:** **평균 444.9ms / p50 432.0ms / p95 504.0ms** (20회).
- **Warm 세부:** Step1 평균 198.3ms (p95 229.1ms); KoE5 평균 201.8ms (p95 234.6ms); DB 평균 43.8ms (p95 100.4ms); 재정렬 평균 0.8ms.
- **최대 프로세스 RSS:** 2,687.2 MiB; Top-5 순위 해시 `cd22fad63a17f8fe`는 모든 재사용 요청과 기존 기본 방식에 일치. 데이터베이스 쓰기/HCX 호출 없음.
- **예비 비교:** 기존 warm 3회 평균 14,324.7ms 대비 재사용 warm 20회 444.9ms → 약 **32.2배**. 서로 다른 관측 횟수로 계산한 참고 비율이며, 실제 E2E/전체 질의 분포 속도 향상을 뜻하지 않음.
- **테스트 로그:** Step1 30개 중 29개 `ok`, 1개 reference notebook 부재 `skipped`; Step2 71개 각 테스트 `ok` (새 L-001 회귀 4개 포함). 제공된 Step2 로그 마지막의 `OK` 요약 줄은 누락됐으나 개별 71개 성공 기록을 확인함.
- **최종 채택 전 남은 검증:** 모델 재사용과 기본 방식의 **실제 원본 질의 다유형 가명처리·상태·토큰 결과 동등성**, 현재 탐지기/검색 안정성, 운영에서의 메모리 장기 안정성.

**보호 동등성 비교 도구:** `scripts/check_step1_reuse_parity.py`는 실제 비공개 원본 질의를 로컬에서 직접 처리하고, **상태 + 가명처리된 출력/토큰/Region의 키드 지문만** Git 무시 경로에 기록함. 원문·토큰·키는 저장하거나 출력하지 않고, DB/HCX를 호출하지 않음. 서로 독립적인 두 Python 프로세스에서 실행 후 동일 질의 ID의 지문을 비교함.

```bash
python scripts/check_step1_reuse_parity.py --mode baseline --limit 12 \
  --out step2_privacy_rag/local_eval/l001_baseline.json
python scripts/check_step1_reuse_parity.py --mode reuse --limit 12 \
  --out step2_privacy_rag/local_eval/l001_reuse.json
python scripts/check_step1_reuse_parity.py --compare \
  --baseline step2_privacy_rag/local_eval/l001_baseline.json \
  --reuse step2_privacy_rag/local_eval/l001_reuse.json
```

`--limit 12`는 현재 REVIEW/BLOCKED 3개 질의와 Gold 유형별 대표 질의를 우선 선택. 실제 위험 데이터는 로컬에서만 처리하며 외부 반출 금지. 이 비교에서 전체 88개 질의를 다 검증한 것은 아님.

### 2026-11-11 · 12질의 개인정보 처리 동등성 검증

- 기존 방식 `baseline`: **READY 9 / REVIEW 2 / BLOCKED 1**, 총 12개.
- 최적화 방식 `reuse`: **READY 9 / REVIEW 2 / BLOCKED 1**, 총 12개.
- `L001_PARITY_RESULT`: **checked 12 / equal 12 / mismatch_qids []**. 상태와 가명처리 결과(마스킹 텍스트·HMAC 토큰·Region Key)의 비밀키 기반 지문이 모두 동일. 외부 HCX 호출·DB 변경 없음.
- 이 결과는 **대표 12질의 및 20회 동일 일반 질의**에서의 동등성·속도 실측이며 88질의 전체, 모든 모델 오류 조건 또는 장기 서비스 안정성을 보증하지 않음.
- **판정:** L-001 재사용 구현을 `--reuse-step1` 명시적 opt-in 기능으로 채택. `main` 기본값은 계속 `reuse_step1=False`. 실제 개인/업무 데이터의 외부 전송 권한은 별도로 확인해야 함. 전면 기본 활성화는 별도 실험으로 보류.

**후속 검증:** 온디바이스 장기 메모리 안정성, 다양한 동의·검토·차단 질의, 완전한 88질의 동등성, cold 시작 최적화, 실제 HCX 포함 E2E 지연을 추적합니다.

**다음 로컬 실행:**
```bash
python scripts/benchmark_step1_reuse.py --mode reuse --runs 20 --device cpu
PYTHONPATH=step2_privacy_rag/app python -m unittest discover -s step2_privacy_rag/tests -v
PYTHONPATH=step1_pii_detector:step1_pii_detector/src python -m unittest discover -s step1_pii_detector/tests -v
```

실행 결과의 단계별 p50/p95와 peak RSS 및 실제 검색 기준선 재현을 확인한 뒤 채택/폐기를 결정합니다. Mac에서 두 모델과 KoE5 동시 상주가 메모리 압박을 유발하면 즉시 baseline 모드로 복귀합니다.

---

## L-002 · 2026-11-11 · 모델 Preloading (실측 검증·서비스 기본 동작 채택)

**문제:** L-001 warm 평균 444.9ms(p95 504.0ms)까지 개선했지만, 첫 질의는 Step1 및 KoE5 초기화로 약 35.98초 소요. 서비스가 준비된 후 첫 사용자의 대기시간을 줄여야 함.

**가설:** 서버가 HTTP 요청을 받기 전에 A.X·Veil과 KoE5를 로딩하고 **비민감 공개형 합성 문장으로 1회 추론 워밍업**하면 첫 사용자의 질의 처리시간이 기존 warm 처리에 근접할 것. 서버 시작 → 최초 답변의 총시간은 크게 단축되지 않을 수 있음.

**변경 후보 (별도 브랜치 `perf/l002-preload-models`):**
- `LivePipeline.preload_models()`: L-001의 Step1 재사용 옵션을 전제하고 Step1 보호 파이프라인 + KoE5 임베딩을 로컬에서 워밍업. DB/HCX/비공개 문서에는 접근하지 않음. 중복 호출은 추가 로딩 없이 유지.
- `run-chat --reuse-step1 --preload`: 워밍업이 성공한 다음에만 HTTP 서버를 생성하고 `READY` 출력. 워밍업 실패는 안전 코드로 차단, 서버 바인딩 없음.
- `scripts/benchmark_preload.py`: 독립 프로세스에서 `lazy` 대 `preload` 비교. `ready_after_ms`, `first_request_ms`, `startup_through_first_ms`, warm p50/p95, 최대 RSS 및 Top-5 서명 보고. 실험 시 HCX 호출·DB 쓰기 없음.
- 기존 `check_step1_reuse_parity.py`에 `--mode preload` 추가하여 L-001에서 수집한 기준 지문과 동일한 12개 개인질의 상태·가명처리 결과를 비교할 수 있도록 함.

**CI/합성 테스트:** 7가지 신규 테스트(사전로딩 성공·중복 호출·REVIEW·인코딩 오류·HTTP 바인딩 순서·시작 실패·CLI 옵션 결합) 및 기존 회귀 테스트. 비공개 모델/DB 기반의 실제 성능 개선은 CI에서 측정 불가.

**Mac 확인 절차 (별도 프로세스 실행, HCX 미호출):**

```bash
# 활성 Conda 환경: 기존에 정상 실행한 aegisrag-retrieval
python scripts/benchmark_preload.py --mode lazy --runs 5 --device cpu
python scripts/benchmark_preload.py --mode preload --runs 5 --device cpu

# L-001에서 저장한 기준 지문과 새 preloading 처리 결과 동등성
python scripts/check_step1_reuse_parity.py --mode preload --limit 12 \
  --out step2_privacy_rag/local_eval/l002_preload.json
python scripts/check_step1_reuse_parity.py --compare \
  --baseline step2_privacy_rag/local_eval/l001_baseline.json \
  --reuse step2_privacy_rag/local_eval/l002_preload.json
```

**검증 결과:** Mac CPU 단일 질의 기준으로 첫 질문의 로컬 지연이 크게 감소했고, 12개 개인정보 처리 동등성·검색 기준선·CI가 유지됨. warm 평균은 5회 표본에서 389ms → 449ms였으므로 무조건 악화 없음을 증명한 것은 아님. 기본 서비스는 Preloading + Step1 재사용을 켜되, 옵션으로 해제할 수 있도록 함.

### 2026-11-11 · 실제 Mac CPU 검증 결과

```text
비교조건: 동일 Mac, CPU, 별도 프로세스, 모드별 cold 1회 + warm 5회
데이터: 기존 V6 검색 177문서 DB 읽기 전용, 일반 질의 1개
외부 HCX 요청/DB 쓰기 없음
```

| 지표 | Lazy (L-001) | Preload (L-002) |
|---|---:|---:|
| 서버 준비시간 | 1,028.1ms | **38,033.9ms** |
| 첫 요청 로컬 처리 | 46,687.9ms | **436.6ms** |
| 서버 시작~첫 요청 완료 | 47,716.0ms | **38,470.5ms** |
| warm 평균 (각 5회) | 388.9ms | 449.0ms |
| warm p50 | 394.8ms | 418.2ms |
| peak RSS | 2,670.3MiB | 2,675.9MiB |
| Top-5 순위 지문 | `4b95ebd92f75de95` | 동일 |

**해석:** 모델 초기화 비용이 첫 요청에서 서버 시작 단계로 이동함. 첫 요청만 비교하면 약 **106.9배 빠름**; 시작~첫 요청 완료는 약 **19.4% 감소**한 이 관측 사례에서만 해당. Preload 워밍업 구성 요소 Step1 29,573.58ms, KoE5 7,967.94ms. 위 두 결과 모두 동일 입력 반복 5회이므로 p95 추정 불가; 실제 HCX 포함 서비스 응답 성능이나 일반화 성능을 주장하지 않음. 최대 RSS 차이는 5.6MiB로 관측 편차의 범위일 수 있음.

**보안·검색 동등성:**
- L-001 기본 방식 대비 Preload **12/12 상태·가명처리·HMAC 토큰·Region 키 지문 일치**. READY 9, REVIEW 2, BLOCKED 1. Gold 입력을 모델에 제공하지 않음.
- 기존 DB 읽기 전용 재평가: `documents=177, eligible=81, ready_queries=85, Hit@1=73/81, Hit@5=79/81, Recall@5(indexable)=0.8802, Recall@5(full)=0.8791, MRR=0.934`. 이 지표는 준비된 질의 대상 검색 평가이며 Preload 내부 호출 동등성 자체와 구분함.
- GitHub 합성 CI 성공. 로컬 추가 테스트 이력: Step1 29 성공/1 skip, Step2 71 성공(이전 L-001 단계); L-002 신규 테스트는 CI에 포함됨.

**채택:** L-002를 로컬 챗봇 시작 시 **기본 모델 사전로딩 및 Step1 재사용**으로 채택. 원래 Lazy 방식은 `--no-preload`, 원래 매번 새 탐지기 생성 방식은 `--no-preload --no-reuse-step1`로 재현 가능. 코드 레벨 `LivePipeline(..., reuse_step1=False)` 기본값은 보존. 단일 프로세스/CPU 기준 검증이며 장시간 메모리·멀티 사용자 운영·HCX E2E 지연은 미검증.

**운영 주의:** 모델 사전로딩은 외부 HCX 안전 인증을 의미하지 않음. 실제 서비스는 기존 외부 전송 승인 조건을 그대로 따르며, 준비 상태는 모델 워밍업 성공을 뜻할 뿐 DB의 현재 연결·내용 검증까지 포함하지 않음.

---

## Q-001 · 2026-11-11 · 문서 REVIEW 안전 처리율 개선 (진단 단계)

**현재 결과:** 전체 187문서 중 177 자동 처리, REVIEW 10문서. 실제 모델의 비공개 문서 진단에서 이름 끝 `이` 경계 문제 `possible_uncovered_name_suffix` **7건**, `review_only` **2건**; Gate의 `unresolved_veil_person` **1건**. 문제의 대부분이 **경계 모호성과 정확한 인물 연결 구분**에 집중됨.

**목표:** 개인정보 보호를 줄이지 않고 REVIEW를 안전하게 줄일 수 있는 일반 규칙을 찾는 것. 문서 ID·이름 문자열에 특화한 규칙을 만들거나 모델 REVIEW를 무조건 READY로 승격하지 않음. 문서 적재 관리자 REVIEW와 온라인 사용자 쿼리 거절/재질문 정책은 별도 작업으로 관리.

**첫 단계 — 원인 상세 진단:** `scripts/diagnose_review_q001.py`는 기존 정책 `propose`, `decide`, Gate 판정을 **읽기 전용**으로 호출하고 사유 코드·추가 보호 제안 문자 수·증거 유형·Veil 정책 분류만 표준 출력. 원문·실제 이름·HMAC·개인정보 구간은 출력/저장하지 않음. 데이터베이스 쓰기·HCX 호출 없음. 시험 입력은 로컬에서만 접근.

```bash
python scripts/diagnose_review_q001.py --ids \
  H0002 H0057 H0072 H0091 H0103 H0105 H0116 H0117 H0119 H0135
```

**후속 설계:** 진단 결과를 바탕으로 Dev/합성 데이터에 '이'가 (a) 실제 이름의 마지막 글자인 사례, (b) 주격 조사인 사례, (c) 역할/직책 연결, (d) 정보 상충, (e) 문서 내 복수 동일 표기 인물 등을 포함하고, 보호 구간 recall·오보호 길이·잘못된 동일인 연결·자동 승인율을 동시에 검증. **Holdout을 임계값 선택/규칙 튜닝용으로 사용하지 않음.** 기존 V6 인덱스 및 HMAC 규격은 계속 고정.

**1차 실제 진단(10문서, 민감 원문 제외):** 7문서 `possible_uncovered_name_suffix`는 각각 마스킹 후보 **1글자 추가**를 제안했으나, 모두 `AMBIGUOUS_PROTECT_ONLY`로 확실한 인물 경계 증거가 없음. 2문서 `review_only`는 신규 보호 글자 **0개**이지만 이름의 마지막 `이` 경계가 여전히 모호함. 9건 전체 자동 처리 승격은 **0건(미구현)**, 정책 성능 개선 수치 미측정.

- H0091은 Step1 READY이나 Gate `unresolved_veil_person`으로 보류. 별도 분류에서는 `kinship_bereavement_word` 사유가 확인됐지만 Gate 정책 카운터는 `ambiguous:1`. `IGNORE_EXTRA_CANDIDATE` 분류는 기존 strict PII 또는 A.X 주소 후보와 충돌할 경우 무효화되므로 충돌의 원인을 재진단해야 함.
- 진단 스크립트에 후보의 `strict_entity_overlap`, `ax_address_overlap`, `ignore_candidate_vetoed` **개수만** 기록하도록 추가(민감 텍스트·오프셋 출력 없음).
- H0091만 재진단:
  `python scripts/diagnose_review_q001.py --ids H0091`

**2차 H0091 진단 (2026-11-11):** 기존 Veil 후보에 `kinship_bereavement_word: 1`이 있으나, `strict_entity_overlap: 1`, `ignore_candidate_vetoed: 1`로 인한 Gate 차단이 확정됨. A.X 주소 겹침은 출력되지 않음(관측된 양수 카운트 없음). Step1 strict PII 개체가 있는 상태에서 Veil의 사전 기반 IGNORE 후보를 바로 무시하면 기존 개인정보를 잘못 해제할 수 있음. **상태 REVIEW 유지.**

**보완 진단:** strict 개인정보의 **라벨 종류**, 후보와 strict 구간의 **포함/부분 겹침 관계**, 후보의 **기존 보호 범위 밖 글자 수**만 추가 집계. 원문·개체 문자열·오프셋·토큰은 출력하지 않음. 부고 친족어와 기존 strict 개체가 일부 겹치는 합성 반례를 추가해, 사전상의 IGNORE가 Step1 strict 보호를 무효화할 수 없음을 회귀 검증.

```bash
python scripts/diagnose_review_q001.py --ids H0091
```

### Q-001 · 로컬 Human Review 대시보드 MVP (Mac 실측 전)

Q-001의 이름 끝 '이' 9건 및 H0091 Veil vs strict PERSON 충돌을 사람에게 확인받기 위한 **로컬 전용 검토 도구**를 실험 브랜치에 추가함. 관리자용 프로덕션 기능이 아님.

- 입력: 지정한 문서 ID만 Git에 없는 local_eval/holdout_labeled.jsonl에서 읽고 실제 Step1/Gate/Veil 탐지 결과를 준비.
- 표시: 오직 127.0.0.1:8771 로컬 브라우저에서 인증 토큰 확인 후 원문/마스킹 범위/문제 후보를 표시. 외부 스크립트/서비스 없음.
- 검토 분류: 이름 경계 LONG_NAME / SHORT_NAME_PLUS_PARTICLE / UNRESOLVED, Veil 후보 PERSON_PII / NON_PERSON_EXPRESSION / UNRESOLVED.
- 로컬 기록: Git 무시 경로 local_eval/q001_review_decisions.jsonl에 문서 ID/후보 구간/원문 SHA256/판단 코드/UTC 시간만 기록. 원문·HMAC 토큰은 저장하지 않음. 파일 권한 0600.
- 의도적 미구현: 이 UI의 분류 결과는 **자동 승인/Step1 REVIEW 해제/HMAC 생성/DB 적재/HCX 호출 권한이 전혀 없음**. 모두 NEEDS_INDEPENDENT_REVALIDATION 상태로만 기록하며, 적용하기 전에 별도의 안전한 검증·교정 파이프라인이 필요함.
- 보안 한계: 개인 Mac의 신뢰된 브라우저 1명 전용. 브라우저 확장/공유 기기/OS 사용자/스크린샷 위협은 방어하지 않으므로 운영 배포 금지. 토큰·원문·화면 캡처는 GitHub/채팅에 공유하지 않음.
- 평가: Holdout 문구/문서 번호를 자동 정책 예외에 사용하지 않음. 수동 진단 근거를 확보한 뒤 일반 규칙은 독립 합성/Dev 사례로 검증.

실행 순서: 현재 Q-001 브랜치 동기화 → conda activate aegisrag-retrieval → python scripts/review_dashboard_q001.py --ids H0002 H0057 H0072 H0091 H0103 H0105 H0116 H0117 H0119 H0135 → 터미널의 로컬 URL 접속/세션 토큰 입력 → 항목 검토 → Ctrl+C 종료.

**검증 상태:** 신규 synthetic CI, 실제 Mac의 10문서 로딩/로컬 화면/판단 기록은 후속 확인 필요. main에 미반영.

### 2026-11-12 · Q-001 REVIEW 판정 수집 및 RET-001 공정 비교 준비

**의사결정 목표:** 오늘 팀 통합 논의 전, ① Human Review 10건의 분류 기록 완결성, ② 기존 V6의 전체 88질의 분모 결과, ③ 팀원 최신 run의 동일 Gold/neutral 계산 결과를 확보. 아직 새 탐지 정책 자동 승격이나 187건 적재를 수행하지 않음.

**신규 로컬 검사:** `scripts/q001_review_audit.py` — 실제 Step1/Veil로 동일 문서 후보를 재생성하여 로컬 JSONL 검토 기록의 ID/원문 SHA-256/구간/선택지를 검사. 문서별 최종 유효 판정 누락 수와 stale 기록 오류 수만 반환. 검토가 전부 끝나더라도 자동 READY/DB/HMAC/HCX 미수행.

**공통 88질의 측정:** `scripts/compare_88_retrieval.py` — 이미 준비된 V6 로컬 가명 문서·질의와 DB를 읽기 전용으로 사용. 질의 88건 **전체**를 분모로 계산(보류 질의는 실패로 기록). 기존 자동 평가 가능 81건 결과도 별도로 출력. 팀원 `ranked_doc_ids` JSONL을 입력받으면 **동일한 Gold와 동일 neutral_doc_ids 제외 규칙**으로 재평가. 평가기에서 neutral을 제거하지 않은 실제 상위 5건 지표도 함께 제공해 편향 여부를 확인. Gold는 검색 호출에 사용하지 않음.

```bash
git pull --ff-only origin exp/q001-review-triage
python scripts/q001_review_audit.py
python scripts/compare_88_retrieval.py \
  --prepared step2_privacy_rag/local_eval/reproduction_20261111/prepared.json \
  --table rag.aegis_koe5_v6_exp1 \
  --ours-out step2_privacy_rag/local_eval/ret001_ours_88.jsonl

# 팀원의 최신 88질의 ranked_doc_ids 결과 파일을 확보한 뒤:
python scripts/compare_88_retrieval.py \
  --prepared step2_privacy_rag/local_eval/reproduction_20261111/prepared.json \
  --table rag.aegis_koe5_v6_exp1 \
  --team-run step2_privacy_rag/local_eval/ret001_team_88.jsonl
```

**주의:** 동일한 채점 코드를 사용해도 탐지기·문서 적재 범위·문서 단위 대 청크 단위 검색의 차이는 그대로 존재한다. 기존 PPT의 Hit@5 100%는 해당 팀원의 최신 실행 파일을 받아 재현하기 전에는 확인된 수치가 아님. 특히 Hit@5는 한 개 정답만 포함해도 성공이므로 Recall@5/Confuse@10 및 neutral 제거 전후를 반드시 같이 제시. Holdout 반복 실험은 독립 Blind 검증을 대신하지 않는다.

### 2026-11-12 · Mac Q-001 Audit + RET-001 88질의 기준선 실측

**Q001_REVIEW_AUDIT:** 기록 12개에서 10개 REVIEW 문서 각각 최신 유효 분류 1개, `review_complete=10`, `review_incomplete=0`, `invalid_event_counts={}`. 모든 문서는 여전히 `still_blocked_from_index=true`. **Human 분류 기록 완료 ≠ 독립 개인정보 안전 검증 완료 ≠ 187문서 적재 승인**. 기록 12개는 재선택/갱신을 포함할 수 있으며 문서당 판단 한 번이라는 뜻은 아님.

**RET001_FAIR_EVAL** (기존 177문서, 모델/DB 불변, 읽기 전용, HCX 미호출, Top-20 수집):

| 항목 | 기존 검색 가능한 81건 | 88질의 전체(보류 7건 0점) |
|---|---:|---:|
| Hit@1 | 73/81 = 90.12% | 73/88 = 82.95% |
| Hit@5 | 79/81 = 97.53% | 79/88 = 89.77% |
| MRR | 0.9339 | 0.8596 |
| Recall@5(full Gold) | 0.8791 | 0.8091 |
| Confuse@10 | 0.5345 | 0.5345* |

`*Confuse@10`은 `confusable_doc_ids`가 정의된 질의에서 Top-10에 포함된 혼동 문서의 개수를 Top-10 문서 수로 나눈 평균이며, 미검색/비해당 질의는 분모에 포함하지 않음. 그러므로 전체 88질의의 보안 유출률 또는 동일인 잘못 병합률을 의미하지 않음.

**전체 Hit@5 실패 QID 9개:** Q001, Q007, Q013, Q015, Q033, Q040, Q045, Q055, Q065. 구조적 원인 집계는 보류 질의 3개, READY이지만 Gold 문서 미적재 4개, 평가 가능한 질의의 실제 검색 실패 2개. **개별 QID의 정확한 원인 분류는 아직 공개 집계로 미확인**. 이를 별도 분석하는 `scripts/ret001_failure_breakdown.py` 추가. 이미 저장한 로컬 `ret001_ours_88.jsonl`과 `prepared.json`으로 읽기 전용 실행하며 원문·HMAC·문서 본문을 출력하지 않음:

```bash
python scripts/ret001_failure_breakdown.py \
  --prepared step2_privacy_rag/local_eval/reproduction_20261111/prepared.json \
  --runs step2_privacy_rag/local_eval/ret001_ours_88.jsonl
```

**해석/통합 논의:** 실전 2 자동 파이프라인(177건, 88질의 전체 성능)과 검수+재검증 Human-assisted 파이프라인(187건 목표)은 별도 결과로 보고해야 함. 전자만으로 실무 운영 안전성을 부정할 수 없지만, 후자도 자동 탐지 성능이라고 주장할 수 없음. 팀원 A의 PPT 88/88 Hit@5, MRR 0.924와 비교할 때 평가기·Gold·neutral 처리·검색 후보, 전체 vs 유효 질의 기준, 실제 Top-5 순위와 재현 입력을 반드시 통일.

### 2026-11-12 · RET-001 QID 원인 확정 및 검토 UI 개선

**실측 진단 결과:** 전체 88질의 Hit@5 실패는 9건으로 다음처럼 구분.
- `DEFERRED_QUERY` 3: Q001(name_topic), Q015(role_agent), Q033(value_exact). 각 정답 Gold 1건은 기존 DB에 있음. 문서 REVIEW만 처리해도 질문 처리 보류는 별도 해결 필요.
- `READY_NO_INDEXABLE_GOLD` 4: Q007(name_topic), Q013(name_variant), Q040(address_exact), Q045(address_format_variant). Gold 각 1건, 기존 177문서에 없음. Human-assisted 재적재의 가장 직접적인 효과 측정 대상.
- `RANKING_MISS` 2: Q055(semantic_no_pii, 9 Gold 중 8개가 이미 인덱스에 있지만 저장 Top-20에서 정답 없음), Q065(hybrid_person_content, Gold 5/5 인덱스 존재, 첫 Gold 순위 7위). 두 사례는 문서 적재와 별도로 검색·랭킹 개선이 필요.
- 이번 **QID 9건**과 PERSON 이름/조사 경계 **문서 9건**은 다른 집합.

**팀원 detect 코드의 경계 접근(정적 분석):** `pii_pipeline._strip_josa`는 모델이 탐지한 PERSON 끝에 조사 후보가 있으면 일정 길이 이상에서 잘라내며, `detect_boost.py`는 Kiwi J* 및 호칭·반복 문맥으로 2글자 이름+조사 분할을 제안. 추론 자동화가 가능하지만, 진짜 이름 끝이 '이'인 경계 반례의 HMAC 잘못된 분리 위험도 함께 시험 필요. 현 Q-001 자료에 맞는 고유 이름 별도 예외·Gold 규칙을 배포하지 않음.

**검토 대시보드 개선:** `scripts/review_dashboard_q001.py`의 인증 후 문서 선택을 드롭다운+별도 조회 버튼에서 **스크롤 가능한 10건 목록 및 항목 클릭 시 즉시 원문/후보 표시**로 변경(첫 문서 자동 열기, 모바일 너비 지원). 리뷰 로컬 토큰·판단 기록 포맷·Gate 차단과 DB 미적재 동작은 그대로. 합성 UI 계약 테스트 및 CI 통과, Mac 브라우저 실제 재시연 전. 문서별 판단 결과를 서버가 대시보드 목록에 지속 표시하는 기능은 별도 개선 예정.

**원클릭 서비스 방향(설계, 아직 구현 전):** 최종 서비스는 Chat UI와 관리자 REVIEW 큐를 하나의 백엔드에서 제공하고, 신규 문서 배치를 자동 Step1→Gate→REVIEW/READY로 라우팅. `READY`만 검색 인덱스 반영; `REVIEW`는 검토 큐와 관리자용 링크로 안내하며 일반 사용자 Chat UI에는 원문 및 관리자 경로 미노출. 관리자 검토는 정답 구간/토큰 관계 교정 → 독립 검증 → 신규 데이터 버전 생성 → 승인된 문서만 적재. 실전2 비교 실험은 자동 처리/인간 개입 처리 별도 지표로 보고. 사용자 권한별 복원/ACL·동명이인 Resolver는 종합 과제 기능으로 이후 통합.

**후속 실험:** Human Review로 확정된 10문서를 기존 strict 보호 구간을 축소하지 않고 비연결 마스킹 중심으로 교정하는 **별도 실험 artifact** 생성 → 회귀 확인 → 새 독립 DB 테이블 적재 및 88질의 평가. 실제 검토 분류를 이용한 이 결과는 `Human-assisted`로 명시하며 자동 탐지 성능 향상으로 주장하지 않음. 안전성 검증 없이 검토 판단만으로 Gate를 우회하지 않음.

**다음 정책 시도:** 이름 경계는 추가 보호 자체와 동일 인물 HMAC 연동을 분리하고, 반례를 포함한 합성/Dev 테스트로 보호 범위 누락·과보호·잘못된 연결 여부를 확인. Gate 충돌은 안전한 이유가 확인되기 전 REVIEW를 유지. 문서별 임시 예외 또는 Holdout 특정 사례에만 맞춘 규칙은 채택하지 않음.

**상태:** 원인 분류 및 추가 충돌 진단 구현 완료 / 모델 정책 미변경·실제 처리율 개선 미검증.

---

## Q-001 · 2026-11-12 · Human-assisted 로컬 E2E 통합 후보 (CI 통과 / Mac 실측 전)

### 구현 범위

- `scripts/review_apply_q001.py`: 모든 최신 Human Review 선택을 원문 SHA-256, 후보 위치·유형과 대조. 이름 끝 `이`를 `LONG_NAME` 또는 `SHORT_NAME_PLUS_PARTICLE`로 교정해 올바른 PERSON HMAC 입력 구간을 만든다. **길이가 3글자일 가능성이 높다는 통계적 추정만으로 자동 승인하지 않음.**
- H0091 등 `NON_PERSON_EXPRESSION`: 원래 strict PERSON 중 해당 Veil 추가 후보 내부에 정확히 하나만 있는 경우에 한해, 독립 어휘 규칙이 실제로 경조사 표현을 확인하면 PERSON HMAC 연결을 제거. **기존 strict 보호 마스크는 남겨서 residual redaction 적용**. 원본 A.X/Veil 예측 헤드를 보존한 Privacy Gate V4를 다시 실행해 READY 확인.
- 처음 Step1이 REVIEW였다면 승인 가능한 이름 경계 관련 issue code만 있어야 하며, 나머지 issue 및 새 불명확 후보는 여전히 차단. 기존 모든 Mask 위치는 보존하거나 확장하고 다른 PII/역할 구간을 임의 해제하지 않음.
- `scripts/q001_human_assisted_e2e.py`: 기존 준비된 177문서 artifact와 새로 재검증한 10문서를 모아 187문서 artifact 생성. 동일 HMAC 키/기존 88질의 상태 보존, 신규 Content-addressed 독립 `rag.aegis_repro_...` 실험 테이블에만 적재. 88개 전체를 보류=0점으로 재평가, 85개 READY 내 검색과 분리 표기.
- 관리자 대시보드 `scripts/review_dashboard_q001.py`: `--enable-experimental-indexing` 옵션을 명시한 경우에만 '검토 반영 · 실험 적재 · 88질의 평가' 버튼 활성화. 토큰 인증·명시적 확인 뒤 실험을 실행하고 진행·성공·안전한 에러 코드를 폴링해서 표시. 원문·개인식별자를 API 작업 로그에 출력하지 않음. 완료 메타데이터는 local_eval에만 저장.
- `scripts/run_demo_q001.py`: **하나의 실행 명령으로** localhost 챗봇(기존 177문서)과 관리자 REVIEW 화면 실행. 관리자가 187문서 실험을 성공시켰을 때 챗봇을 새로운 187문서 실험 테이블로 재시작. 외부 HCX 전송은 사용자 명시 설정 `AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES`이 있을 때만 실행 가능. 관리자 토큰은 실행 시마다 새로 생성.

### 2026-11-12 · 실제 Mac preview 성공 / 챗봇·관리자 분리 문제 진단

**사용자 실측:** `Q001_HUMAN_ASSISTED_E2E` `state=REVALIDATED_LOCAL`, `documents=187`, `baseline_doc_count=177`, `revalidated_review_count=10` (`name=9,veil=1`), `mask_coverage_non_decreasing=true`, `all88_queries_unchanged=true`, `db_written=false`, `planned_table=rag.aegis_repro_cc02f91c215583fa591a`. **187문서 실제 DB 적재·검색 평가는 아직 하지 않았음.**
**사용자 챗봇 관찰:** `Gate=READY`, `HMAC tokens=1`, `Region keys=0`, `HCX-007 INSUFFICIENT_EVIDENCE` for generic name question. 이는 질의 가명화·HCX 응답을 확인할 뿐 HMAC이 DB에서 매치됐는지 또는 올바른 문서가 Top-5인지 증명하지 않음. 서버 실행 로그만으로 챗봇이 새 187문서 테이블을 사용했다고 볼 수 없음(기본은 기존 177문서).
**보완:** `step2_privacy_rag/app/app.py` health에 `expected_index_rows`, `index_kind` 추가; 채팅 진단에 안전한 Top-5 문서 ID/검색 수/HMAC 일치 문서 수 추가. `app.js`에서 이 데이터를 개발자 상세에 표시. `scripts/check_demo_q001.py`는 인증 정보 없이 두 로컬 서버 포트(8768 챗봇, 8771 관리자)를 각각 점검. `scripts/run_demo_q001.py`는 기존 서버의 포트 충돌을 명시적으로 차단하고 관리자 서버 실제 응답을 확인한 뒤 챗봇 모델을 실행. 대시보드는 관리자 세션 토큰 입력+Enter 허용 및 기존 JSONL 리뷰 선택의 복원 기능 추가. 합성 CI 통과 / Mac 재실행 전.
**정확한 아키텍처:** 지금 실험에서는 원래 177문서가 별도 라이브 챗봇에 즉시 검색 가능, REVIEW 10문서는 **10건 전부 최종 분류 후** 새 187문서 실험 테이블을 원자적으로 생성하는 **배치 검증** 경로. 운영 목표는 READY 문서가 즉시 인덱스에 반영되고 REVIEW는 별도 큐에 남으며 **개별 검토된 문서가 안전성 재검증 후 비동기 트랜잭션으로 추가 적재**되는 증분 경로. 이는 현재 구현 완료 기능으로 주장하지 말 것. API 서버 요청이 DB 임베딩 및 긴 평가 작업을 동기 블로킹하지 않고 별도 worker에서 수행해야 한다.

### 2026-11-12 · UI 로그인 공백 및 질문 측 REVIEW 오인 수정

사용자 제공 관찰: 관리자 토큰 입력된 화면에서 REVIEW 목록·원문이 비어 있고 최초 인증 안내가 그대로 표시됨. 이 상태는 **REVIEW 문서 0건을 의미하지 않음**. Q-001 서버는 지정된 10건을 계속 보유하고, 인증된 `/api/cases` 조회 및 `/api/case/{id}` 호출 후 목록이 표시되어야 함. 입력 후 별도 '검토 문서 불러오기' 클릭 또는 Enter 필요. 목록 불러오기 전인 상태, 응답 실패, 조회 결과 0건을 분리하여 메시지 표시하도록 수정.

추가 반영:
- UI에 '판단 기록 n/10, 미분류 m건', 실제 목록 0건이면 '현재 확인할 문서 없음' 표시. 10건이 모두 판단 기록돼도 **독립 재검증·적재 전 문서는 목록에 계속 남음**.
- 상단 문구는 '분류 기록만으로 HMAC/적재 승인이 되는 것이 아니며 별도 실험 승인 실행 시에만 Gate 재검증→신규 DB 적재'로 수정.
- `app.py`에서 질문 전처리 Gate REVIEW는 `review_scope=QUERY_PREPARATION`, `mode=gate`로 분리. 질문이 Gate에서 보류되면 HCX 호출도 Retrieval도 발생하지 않으므로 이전의 'HCX-007' 배지는 오해를 초래함. `app.js`는 `Privacy Gate · 검색 전 보류`로 표시.
- 사용자의 `오가은이 있는 문서 찾아줘`는 **질문 측 이름/조사 경계 REVIEW** 가능성이 있으며 문서 10건 Human Review의 결과로 자동 해제되지 않음. 반대로 `윤선영 정보`에 대한 ANSWER는 기존 챗봇 경로와 HCX 호출이 동작함을 보여줌.
- 사용자가 채팅에 로컬 세션 토큰을 공유한 경우 서버 재시작으로 새 토큰 발급 권장.

**남은 검증:** 관리자 UI에서 최신 런타임 토큰 로그인 후 `판단 기록 10/10` 표시 확인, 실험 버튼을 눌러 DB 새 테이블 생성 시 `EXPERIMENT_INDEXED_AND_EVALUATED` 완료/88질의 실측 확인, 챗봇이 177→187 문서 전환됐는지 `/api/health` 확인. CI는 합성 계약 검사만 수행하므로 실제 Mac E2E 성공을 주장하지 않음.

### 2026-11-12 · 관리 UI 토큰 자동 로그인 + 실제 API 응답 테스트

실제 Mac 공유 로그에서 관리자 `127.0.0.1:8771`와 챗봇 `127.0.0.1:8768` 기동, 챗봇은 **177문서 인덱스** 표시를 확인. 로그에는 E2E `SUCCEEDED`/`EXPERIMENT_INDEXED_AND_EVALUATED` 또는 187문서 검색 전환 기록이 **없음**. 이전 187문서 `REVALIDATED_LOCAL`은 DB 미적재 프리뷰임. 관리자 UI에 토큰 입력만 하고 별도 **검토 문서 불러오기** 클릭 없이 목록이 표시되지 않는 UX 문제를 확인. 저장된 검토 판단 10/10은 서버 상태에서 사라지지 않으나 인증 API 호출 전 브라우저에는 보이지 않음.

**해결:** 관리 UI가 로컬 `token_urlsafe(32)`의 완전한 43자 토큰 입력/붙여넣기를 감지하면 별도 버튼 없이 `/api/cases`를 자동 호출하고 명시적 로딩/오류 메시지 표시. 버튼·Enter 수동 조회도 유지. `loginBusy`로 중복 요청 방지. 인증 후 개별 문서 10건, 판정 완료 n/10, 그리고 실험 E2E 실행 버튼을 표시. 토큰 없는 페이지에서 원문/검토 기록을 반환하지 않음.

**검증:** 테스트 `test_admin_http_login_returns_all_reviewed_documents`는 실제 localhost HTTPServer에 합성 10건을 바인딩해 토큰 누락 시 403, 토큰 일치 시 10개 조회, 첫 후보만 검토할 때 0/10, 두 번째 후보까지 검토할 때 10/10임을 확인. GitHub CI 통과. 실제 사용자 브라우저·로컬 DB 실험 적재는 아직 재검증 필요.

**다음 실행:** 실행 서버 종료(Ctrl+C) → `git pull --ff-only origin exp/q001-review-triage` → `AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES python scripts/run_demo_q001.py` → 관리자 포트 8771에 새 토큰 붙여넣기 → 10/10 확인 → E2E 실행 승인 → `SUCCEEDED` 및 챗봇 187문서 전환 확인. 토큰을 대화에 공유했을 경우 새로 서버를 시작해 토큰 회전할 것.

### 2026-11-12 · 관리자 UI 버튼 무반응 실제 원인 수정 (Python→JS 렌더링)

**증상:** 관리자 포트 정상, 세션 토큰 사용자가 입력하고 `검토 문서 불러오기` 클릭했으나 문서 목록/상태/실험 버튼 모두 변화 없음. 이전까지 인증/포트 문제가 있다고 추정했으나 실제 원인은 임베디드 JS 구문 오류.

**근본 원인:** `review_dashboard_q001.py`의 `HTML = """..."""` 안에 JS `lines.join("\\n")`이 있음. Python이 일반 triple-quoted 리터럴을 평가할 때 `\\n`을 **실제 줄바꿈 문자**로 변환하여 브라우저로 보내는 JS에 문자열 안의 개행이 생김. 이로 인해 전체 `<script>` 구문 분석 실패, 로그인 버튼 이벤트 리스너도 등록되지 않음. 코드 원문의 JS만 파싱하면 유효하여 오류가 숨겨짐.

**해결:** `HTML = r"""..."""` raw literal로 변경하여 JS 백슬래시 이스케이프 유지. GitHub 테스트에 **Python이 평가한 HTML의 실제 스크립트 문자열** 추출, literal newline regression 및 Node `--check` 구문 검증 추가. 실제 로컬 HTTP 인증/10문서 검토 큐 계약 테스트도 유지. 최신 CI PASS. 브라우저 실측은 재실행 필요.

**실행:** 서버 `Ctrl+C` → `git pull --ff-only origin exp/q001-review-triage` → `AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES python scripts/run_demo_q001.py` → 새 세션 토큰을 대시보드 8771에 붙여넣기 → 판단 기록 10/10 및 실험 버튼 확인. 챗봇은 적재 버튼 실행 전에는 정상적으로 177문서 표시.

### Mac 테스트(기존 main/177문서 테이블 미변경)

```bash
git pull --ff-only origin exp/q001-review-triage
conda activate aegisrag-retrieval

# 1. 보호 교정·HMAC 동등성 사전 점검, DB 쓰기 전혀 없음:
python scripts/q001_human_assisted_e2e.py --mode preview

# 2. 챗봇 + 관리자 UI 실행(별도 두 창에 자동 열기, 브라우저 허용 시):
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES python scripts/run_demo_q001.py

# 3. 127.0.0.1:8771 관리자 UI의 터미널 세션 토큰 입력 → 10건 분류 확인
# 4. '검토 반영 · 실험 적재 · 88질의 평가' 승인 버튼 클릭
# 5. 187문서 신규 독립 DB 생성 / 88질의 결과 표시 / 챗봇 187문서로 재연결
```

실험 용도 CLI 단독 실행도 가능:
```bash
python scripts/q001_human_assisted_e2e.py --mode run \
  --confirm CREATE_ISOLATED_HUMAN_ASSISTED_INDEX --device cpu
```

### 검증·채택 제한

- GitHub 합성 회귀는 이름 마지막 `이`를 3글자 이름+조사/실제 4글자 이름으로 교정했을 때 HMAC이 다른지, 원래 MASK 보존, H0091 부고 오탐 HMAC 제거, 다른 전화번호 보호, 177→187 corpus 합성 생성, 88질의 동일 설정 유지 등을 검사. 실제 Mac 모델/실제 local_eval/pgvector/HCX E2E는 **아직 미실행**.
- 실행 후 `Q001_HUMAN_ASSISTED_E2E` 및 관리자 지표로 실제 187문서 처리 여부, 남은 보류 질의 3개, Hit@1/5·MRR·Recall@5 및 실패 QID를 재확인해야 함. **보류 질의 3개는 이 실험에서 개선하지 않았음**.
- 현재는 *기존 187문서가 고정된 교육용 데이터 배치의 Human-assisted 검증 경로*. 신규 무제한 문서 적재, 사람별 계정/권한, Vault 역변환, 장시간 배포, 사용자별 ACL, 자동 모델 재학습은 아직 지원하지 않음.
- 모델이 모든 개인 식별자를 탐지했다는 보증도 아님. 합성/Dev/새 Blind에서 Mask Recall·False Merge·회귀성 평가를 계속해야 함. 기존 Holdout 10문서에서 검토한 답을 자동 탐지기 일반화 성능으로 주장하지 않음.
- 원본 177문서 `rag.aegis_koe5_v6_exp1`은 재작성/삭제하지 않음. 새로운 실험 테이블은 콘텐츠 버전으로 분리.
- 이름 조사 자동화는 별도 규칙 실험에서 **명시적 이름 필드, 다른 문장 확인, Kiwi J 계열의 일치, 충돌 신호**를 각각 평가. 성씨 + 2/3자 이름 길이만으로 READY 승격 금지.

---

## 후속 개선 백로그 (착수 전)

| 순서 | 영역 | 핵심 검증 |
|---|---|---|
| Q-001 | REVIEW 이름 경계·Veil 후보 | 문서 10건: `possible_uncovered_name_suffix` 7, `review_only` 2, `unresolved_veil_person` 1; 개인정보 보호 및 인물 연결 오류 검증 |
| E-001 | 검색·인용 평가 | Confuse@1/5/10, Citation Precision/Confusion, 답변 보류·근거 적절성; 동일 분모 유지 |
| R-001 | Identity Resolution | 역할·보호 메타데이터 → 보수적 인물 연결 → 재정렬; 추측성 병합 금지 |
| R-002 | 검색·문맥 품질 | 관련 없는 Top-5, 긴 문서 512-token 임베딩/1,500자 HCX 발췌, 청킹·근거 선택 비교 |
| S-001 | 사용자 질의 안전 처리 | REVIEW 질의의 안전한 재질문·로컬 보완; 문서 REVIEW와 분리 |
| A-001 | 관리자 문서 검토 | 합성 REVIEW 큐, 관리자 인증, 보호 구간 수정, 재검증, 승인 후 별도 버전 적재 |
| D-001 | 종합 운영 준비 | 다중 사용자·권한별 검색/복원·출력 재검증·서버 배포·비밀 관리 |

> 이 표의 항목은 **계획**이며, 이미 개선 완료됐다는 뜻이 아닙니다. 변경 전후의 정량 결과만 해당 실험 항목에 추가합니다.
