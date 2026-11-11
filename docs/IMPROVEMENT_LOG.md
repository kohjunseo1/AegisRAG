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

## L-001 · 계획 · Step1 탐지기 재사용

**가설:** A.X·Veil 모델 객체를 프로세스 내 안전하게 재사용하면 반복 요청 지연이 줄어듦.

**실험 설계:**
1. 현재 `step1_ms`를 **탐지기 생성/모델 로딩**, **탐지 추론**, **Gate·HMAC 준비**로 분리 측정. 관측을 위해 중복 탐지를 수행하거나 PII 원문을 로그에 남기지 않음.
2. 기준 구현과 **장수명 탐지기 재사용** 구현을 별도 브랜치로 비교. 탐지기 수명 종료 시 명시적 `close()`; 추론 예외·타임아웃 시 기존 fail-closed 계약 유지, 오류 난 객체를 안전하다고 가정해 재사용 금지.
3. cold 1회 + warm 최소 20회, 일반/이름/주소/번호/REVIEW 합성 질의 및 허가된 비공개 회귀 세트. CPU, 프로세스 조건, 메모리(RSS), `step1_ms`, `embedding_ms`, `db_ms`, `hcx_ms`, `total_ms` 구분.
4. **채택 조건:** 출력 상태·보호 구간·가명 토큰·문서 검색 순위·Step1 테스트·V6 baseline이 같고, 실제 warm p50/p95 개선 및 메모리 사용량이 수용 가능할 것. 속도 개선 수치는 측정 뒤 기록.

**현재 상태:** **CPU 실제 모델·기존 DB 3회 비교에서 큰 개선 확인, 추가 검증 전**. 실험 브랜치 `perf/l001-step1-detector-reuse`에서만 `--reuse-step1`을 활성화할 수 있고, 기본 실행 경로는 기존처럼 매 요청 생성·종료합니다.

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

**다음 로컬 실행:**
```bash
python scripts/benchmark_step1_reuse.py --mode reuse --runs 20 --device cpu
PYTHONPATH=step2_privacy_rag/app python -m unittest discover -s step2_privacy_rag/tests -v
PYTHONPATH=step1_pii_detector:step1_pii_detector/src python -m unittest discover -s step1_pii_detector/tests -v
```

실행 결과의 단계별 p50/p95와 peak RSS 및 실제 검색 기준선 재현을 확인한 뒤 채택/폐기를 결정합니다. Mac에서 두 모델과 KoE5 동시 상주가 메모리 압박을 유발하면 즉시 baseline 모드로 복귀합니다.

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
