# AegisRAG Unified V1 자율 실행 결과

**P0 → P5 기본 코드 구조 구현 완료. Synthetic/Mock 검증 통과. 실모델 통합 성능 검증은 BLOCKED이며 P6는 수행하지 않았다.**

작업일: 2026-11-19, Asia/Seoul. 구현 위치: `/Users/gojunseo/AegisRAG-unified-v1`, `feature/unified-v1`. IDE가 열려 있던 `/Users/gojunseo/AegisRAG`의 미커밋 변경은 건드리지 않았다. 기준 HEAD는 `644ab9d56cd24d5386c739712488299448940d26`, 팀원 참조는 `242543bf0eae91aa3d655227a747aa128707368e`다. 변경은 로컬 worktree에 남겼고 커밋/push/PR/병합하지 않았다.

## 구현과 단계별 상태

| 단계 | 구현 완료 사항 | 검증 상태 | 실제 환경 한계 |
|---|---|---|---|
| P0 | 환경/소스 SHA, 모델 자산 해시, V6 기준 회귀, 별도 venv 호환 PoC | 환경 진단 완료, 기존 회귀 PASS | KLUE 토크나이저 로딩만 PASS; 모델 ImportError. KoELECTRA MISSING |
| P1 | 공통 계약, 4 raw Adapter, 7개 프로파일, strict volatile replay, 오프라인 HF overflow, 로컬 IPC worker, 검증된 설정/CLI | IMPLEMENTED / MOCK_PASS | 실제 원본 예측 동등성 BLOCKED. 다운그레이드/공개 모델 대체 없음 |
| P2 | Sentinel/문맥·반복 이름 후보, Evidence-preserving Fusion, ProtectionPlan, 원본 Gate adapter | IMPLEMENTED / MOCK_PASS | 모호한 새 후보/라벨 충돌은 REVIEW, 필수 모델 장애 BLOCKED |
| P3 | 원본 96-bit HMAC/Region 계약, Mention/OwnedAnchor, 보수적 resolver, UNRESOLVED 질의 차단 | IMPLEMENTED / MOCK_PASS | 합성 키 golden parity만 검증. 실제 운영 키/개체 품질 미측정 |
| P4 | 승인된 가명 문서 인메모리 인덱스, own_v6/team_v3/hybrid_v1, 동일 scorer, 격리 migration 계획 | IMPLEMENTED / MOCK_PASS | Postgres/KoE5 integration BLOCKED. migration 실행 자체 차단 |
| P5 | HCX-007 schema, Context, outbound/output 독립 recheck, MockTransport, 인용 ID/근거 범위 검사, 전체 합성 CLI/manifest | IMPLEMENTED / MOCK_PASS | HCX live NOT_RUN_BY_POLICY. 실제 사실성/응답 성능 미검증 |

상세 변경·검증 이력은 [PHASE_STATUS.md](PHASE_STATUS.md), 코드 실행법은 [README.md](README.md)에 있다. 전체 코드의 진입점은 `step2_privacy_rag/app/unified_v1/`이며 기존 V6 서비스 기본값은 바꾸지 않았다.

## 검증 결과

| 검사 | 결과 |
|---|---|
| 기존 Step1 테스트 | 31개 실행, 30 PASS / 1 SKIP |
| 기존 Step2 테스트 | 183 PASS |
| 기존 루트 테스트 | 29 PASS |
| 신규 Unified V1 테스트 | 41 PASS |
| 총계 | **284개 실행, 283 PASS / 1 SKIP / 최종 FAIL 0** |
| 원본 synthetic smoke / synthetic-review | PASS |
| 신규 own2/team2/quality4 전체 합성 E2E | MOCK_PASS, 네트워크 차단 |
| 기존 diagnose 환경 검사 / CLI help 5종 | PASS |
| Git 이력 보안 검사 / diff whitespace / 신규 Python syntax | PASS |

명령과 기계 판독 결과: [VALIDATION_RESULTS.json](VALIDATION_RESULTS.json). 기존 Step1 SKIP은 원본 reference release notebook을 찾을 수 없어 parity 비교를 생략한 것이며 신규 실모델 PASS로 계산하지 않았다. 최초 기본 Python 실행의 psycopg 누락 오류는 기존 Aegis Python 선택으로 해결했다. 신규 overflow 테스트 double의 signature 오류 2건도 수정 후 재실행하여 PASS했다. 이력은 PHASE_STATUS에 남겼다.

Git 보안 검사는 추적 파일/HEAD 이력을 대상으로 했다. 아직 untracked인 신규 코드·테스트·문서는 별도 정적 패턴 검사로 확인했다. 원본 CI 명령은 유지했고 신규 test/E2E step만 추가했다. `holdout_finalizer.py policy`는 V6 소스를 쓸 수 있어 이번 자율 실행에서는 호출하지 않았다. 관련 단위 테스트와 smoke는 통과했다.

합성 테스트는 오버랩·경계/라벨 충돌, 띄어쓰기/복성/외자/이름 뒤 조사, 부분 주소·우편번호·업무 ID, 긴 한 줄의 마지막 토큰, 모델 오류·missing·IPC timeout·요청 혼선·캐시 미스, HMAC golden 7종·키 tag 불일치, 동명이인/공유 번호/전화 변경/소유 근거 부재, 승인/버전 불일치, 후보 union·동일인 충돌·문서 중복, 잘못된 인용·임의/짧은 토큰·잔여 PII·잘린 토큰·출력 검사 장애를 검증했다.

## 실측 여부와 BLOCKED

**실모델 탐지 F1, 누출률, 실제 검색 Hit/MRR, E2E 답변 정확성, latency p50/p95, load time, peak RSS를 측정하지 않았다.** CLI 검색 지표는 동일한 1개 합성 문서/질의 fixture의 계약 검사다. 별도 56문서 합성 fixture는 Dense pool 밖 token 후보 회수를 검사한다. 이를 실제 85/88 또는 171/187 데이터 성능과 비교하지 않는다. experiment manifest는 mock 상태, NOT_MEASURED timing, null RSS, KoE5 NOT_RUN, HCX NOT_RUN_BY_POLICY를 명시한다.

- **KLUE:** 4개 파일/해시와 15 BIO 라벨 확인, 로컬 fast tokenizer 로딩 PASS. 모델 로딩 ImportError로 추론/원본 parity BLOCKED. 발신자 해시·예상 예측도 미제공이다.
- **KoELECTRA:** 실제 ft5 가중치 MISSING. team2/quality4의 실모델 READY는 불가능하다. 합성 실행의 Mock Adapter와 명확히 구분했다.
- **환경:** 기존 Python 3.11.13/torch 2.2.2는 확인했으나 실제 Transformers 5.22.2/tokenizers 0.23.3이 lock과 다르다. 기존 환경을 설치/업그레이드/복구하지 않았다. 임시 venv의 inherited-package PoC는 원래 팀원 환경 재현 증거가 아니다.
- **DB/KoE5:** 격리 DB와 vector 모델 실행 미검증. 운영 DB를 열지 않았으므로 실제 행 수/key tag/전후 row fingerprint도 검증하지 않았다. DB_PASS로 보고하지 않는다.
- **HCX:** live 호출 0회. Mock E2E만 수행했다. 인용 ID와 제공된 가명 근거 범위의 유효성만 검사하며 claim-level 사실성 검증을 했다고 주장하지 않는다.

## 보존한 안전 경계

기존 main/타 worktree/팀원 참조/운영 V6 코드/requirements.lock/HMAC 정규화와 토큰 길이를 변경하지 않았다. `.env`와 운영 키·원문/Gold·운영 DB를 신규 작업에서 사용하지 않았다. 운영 DB 연결·쓰기, 실제 HCX/외부 LLM/외부 임베딩, GitHub push/PR/main 병합은 수행하지 않았다. `git fetch origin`은 기준 확인용으로만 실행했다.

실험 승인 receipt는 가명 본문 fingerprint·버전·corpus에 묶이며 로컬 호출자 계약이다. ACL이나 암호학적 권한 증명은 구현하지 않았다. Mask-only 자동 적재 OFF, persistent private cache 비활성, document-only index 유지다. 불확실한 후보는 보호 범위를 줄이지 않고 REVIEW/UNRESOLVED로 남긴다.

## 다음 날 필요한 작업

1. 실제 KoELECTRA ft5와 팀원 체크섬·합성 예상 raw 출력·환경/공유 조건 확보.
2. 기존 환경을 보존한 새 모델 환경에서 KLUE/A.X/Veil 로딩·원본 parity 및 충분한 warm 성능 측정.
3. 폐기 가능한 격리 Postgres와 KoE5 통합 검증, 필요한 경우 별도 범위의 운영 DB 읽기 전용 보존 확인.
4. 구현 diff와 합성 결과 검토 후 후속 실측 범위를 결정. 실제 HCX 호출·새 Blind·최종 운영 프로파일·P6·PR/병합은 별도 요청 대상.

세부 선행조건: [NEXT_ACTIONS.md](NEXT_ACTIONS.md). 이번 실행은 **P5에서 종료**한다.

## 후속 GitHub 게시 요청

최초 자율 실행 종료 후 사용자가 `feature/unified-v1`의 커밋 및 GitHub push를 명시적으로 요청했다. 위 미커밋/미push 기록은 최초 실행 종료 시점의 상태다. 이번 후속 작업은 통합 코드·테스트·문서·CI 변경의 브랜치 게시만 포함하며, PR 생성/main 병합/P6는 포함하지 않는다.
