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

**상태:** 진단 도구 후보 구현. 실제 문서 세부 증거 집계 및 신규 정책 채택 전. 자동 처리율·Mask Recall 개선값 미측정.

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
