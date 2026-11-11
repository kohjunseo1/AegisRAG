# 실험 이력과 V6 기준선

이 문서는 정리 전 로컬 문서·평가 산출물에 근거한 기록입니다. 기존 main은 Step1까지만 추적했고 Step2는 미추적 로컬 구현이었습니다. 과거 Git 커밋은 보존합니다. 구현 교체·모델 재학습·검색/Gate 정책 튜닝·REVIEW 해결은 이번 범위에 포함하지 않습니다. 비공개 문서·Gold·검색 결과는 Git에 보관하지 않습니다.

## Step1: 최초 평가, 사후 분석, 현재 모델

| 데이터 | 정책/단계 | Strict F1 | Mask Recall | 해석 |
|---|---|---:|---:|---|
| Official Test, 200문서 | 최초 동결 Hybrid(05) | **99.23%** | **99.97%** | 저장 예측 및 Gold로 재채점한 기준선 |
| Official Holdout, 187문서 | **최초** 동결 Hybrid(05) | **97.73%** | **99.48%** | Holdout 오류를 보기 전 성능 |
| Official Holdout | 기존 조건부 우편번호 보정 | **98.27%** | **100.00%** | Holdout을 본 후의 **사후 개선** |
| Official Test | 현재 저장소 주소 검증 + 신규 2규칙, 저장 예측 재채점 | **99.36%** | **99.97%** | 오프라인 후처리 결과; 실제 detector 추론 아님 |
| Official Holdout | 신규 2규칙만, 저장 예측 재채점 | **98.80%** | **99.48%** | 주소 보정 미포함, 사후 분석 |
| Official Holdout | 현재 저장소 주소 검증 + 신규 2규칙, 저장 예측 재채점 | **99.3450%** | **100.00%** | 오프라인 후처리 결과; 실제 detector 추론 아님, 사후 분석 |

> **핵심 주의:** 처음 본 Holdout 결과와 Holdout 오류를 분석한 후의 결과는 구별해야 합니다. 위 사후 결과는 새 미공개 데이터에서의 99% 일반화 성능을 입증하지 않습니다. 또한 V9 Full 6규칙의 Holdout 99.89%는 측정된 실험 기록이지만, 과도한 문맥 의존성을 피하기 위해 **배포 정책으로 채택하지 않았습니다**.

위 신규 정책 수치는 명시된 05 저장 예측에 후처리를 적용한 **오프라인 재채점 참고값**입니다. 실제 `create_detector()`/`detect()` 모델 추론으로 재생성한 결과가 아니므로 최종 배포 성능으로 보지 않습니다. 실제 모델 평가 전에는 통합 버전의 99% 달성을 주장하지 않습니다.


최초 blind Test/Holdout 결과와 사후 저장 예측 재채점은 서로 다른 증거입니다. 현재 실제 모델 리비전은 Step1 manifest에 고정되어 있습니다. `standalone_surname_title`, `explicit_internal_extension`만 Strict span에서 정제하고 원래 후보는 mask_intervals로 보호합니다. V9 Full 6규칙의 99.89%는 과거 사후 실험이며 배포 정책으로 채택하지 않았습니다.

## Step2 발전과 실패·한계

| 단계 | 근거와 관찰 | 최종 처리 |
|---|---|---|
| 초기 Gate/HMAC | Step1 READY가 외부 송신 허가를 의미하지 않음. Veil-only 인물을 업무 용어/직함/불확실 후보로 구분 | Gate V4와 fail-closed 계약 유지 |
| 이름 V1/V2 | 합성 93건, 알려진 Holdout 9건 진단. 말미 `이`와 조사 구분, 직함·호칭 증거의 충돌 | 탐지 경계를 임의 축소하지 않음 |
| V3/V3.1 | 93+51=144 합성 사례. 복수 라벨에서 이름 제안이 잘못 배제되던 문제 수정 | 실행 중인 V3.1 의존성 유지 |
| V4 Pareto | 기존 144건 PERSON 문자 보호율 0.94159→0.99115; 추가 정답 문자 28, PERSON 밖 60. 신규 51건 0.92593→1.0; 추가 정답 14, 밖 21 | 정보 손실과 보호율을 분리; 전체 PII F1로 해석 금지 |
| V5 정책 | 명시 이름 필드·long/short·모호함 검토. 추가 합성 78건도 AI 생성 반례이며 독립 실무 평가가 아님 | `v5_privacy_first` 및 non-linked handoff V2 유지 |
| non-linked handoff | 보호할 구간과 동일인 HMAC 입력을 분리. 모호한 이름은 추가 보호하지만 동일인 연결을 주장하지 않음 | 미해결 언급 질의는 보류, 원래 REVIEW를 승격하지 않음 |
| 검색 V5 | KoE5 1024d + HMAC/Hybrid/lexical/BGE 비교. CPU reranker의 지연과 후보 상한을 함께 분석 | BGE/adaptive 후보는 최종 온라인 경로에서 제외 |
| Adaptive/Batch | 과거 후보 Oracle@20 79/88. 이는 그 후보집합의 Hit@1 상한이며 신규 질의 일반화 보장이 아님 | HMAC tier 우선의 근거와 latency 범위 기록 |
| V6 주소/Region | address canonicalization V2.2와 Region alias 키, HMAC 일치 개수→Region→Dense 재정렬 | 최종 앱과 평가 경로 유지 |
| E2E/UI | 중복 합성 UI·chat UI·live 후보를 거쳐 final MVP. 문서 잔여 전화·메일·번호형 문자열 송신 전 마스킹, 질의 잔여 PII는 보류 | final MVP API를 단일 앱으로 이관 |

기존 V4 기록에서 알려진 Holdout 9건 중 7문서가 1자 추가 보호됐으나, 그 문자가 Gold 이름이라는 결론은 그 로그만으로 내릴 수 없습니다. 동일 표면형을 같은 사람으로 연결하는 것도 보장하지 않습니다. V5 grammar/minmask 변형은 비교용이며 최종 기본 정책을 대체하지 않았습니다. 추가 마스킹·가명처리는 재식별 위험이나 모든 기밀정보의 부재를 인증하지 않습니다.

## V6 동결 기준

| 항목 | 기존 기록 |
|---|---:|
| 전체 문서 / 자동 처리 / REVIEW | 187 / 177 / 10 |
| 질의 READY / REVIEW / BLOCKED | 85 / 2 / 1 |
| 평가 가능 질의 | 81 |
| Hit@1 | 73/81 |
| Hit@5 | 79/81 |
| Recall@5 | 0.8802 |
| MRR | 0.9340 |
| 근접 동점 | Q059 Top-5 집합 동일, 4/5위 교환 가능 |
| 과거 합성 HCX 기록 | 4/4 (과거 BASELINE_FREEZE.md 기록; 이번 실측과 구분) |

Hit@5는 적어도 하나의 정답을 찾은 질의 비율이고 Recall@5는 다중 Gold 회수율의 질의별 평균입니다. 177/187은 처리율이며 PII 정확도가 아닙니다. Holdout을 반복 분석한 선택 편향 가능성이 있습니다. 인용 ID의 존재·지역 일치 확인은 문장별 사실성을 증명하지 않습니다.

## 2026-11-10 재현 패키지 정리

- 정리 전 `.env`·Compose·init 파일 해시, 전체 파일 목록, 미추적 Step2까지 포함한 권한 제한 로컬 복구 스냅샷을 저장소 밖에 보관했습니다. 민감 데이터는 로컬 원위치에 보존합니다.
- AST import 분석에서 최종 앱의 로컬 의존 모듈 14개, 최종 실행 경로 밖 Python 모듈 73개를 확인했습니다. 회귀·재현에 필요한 테스트와 의존성을 먼저 이관했습니다. V1/V3/V4 명칭 일부는 실제 최종 의존성이므로 app에 남습니다.
- `app/`, 단일 CLI, 별도 content-addressed 재현 적재, 모델 SHA 고정, 익명 지연 계측, 인용 문서만 접는 데스크톱 UI를 구성했습니다.
- 최초 평가 이관에서 전체 Gold를 분모로 하면 Recall@5 0.8791이 나왔습니다. 저장된 기존 dense 순위와 실제 DB 양쪽에서 이를 확인했습니다. 적재 가능한 Gold 교집합을 분모로 하면 기록된 V6 기준 0.8802가 재현됩니다. 평가기는 두 정의를 함께 출력합니다. 과거 평가기의 채점 전용 neutral 제외도 보존하지만 이 데이터의 차이 원인은 아닙니다. 검색 순위·Gate·모델·키를 튜닝하지 않았습니다.
- 실모델로 187문서·88질의를 재준비한 결과, 기존 V6 artifact와 documents/queries/Gold/key tag/counts가 모두 동일했습니다. 177 LOCAL_PREPARED/10 REVIEW, 질의 85 LOCAL_PREPARED/2 REVIEW/1 BLOCKED를 재확인했습니다.
- 깨끗한 Python 3.11.13 venv에서 requirements.lock 전체 설치 및 `pip check`가 성공했습니다. 호스트는 macOS x86_64이며 MPS 검증을 주장하지 않습니다.
- Step1 회귀 30건 중 29건 통과, 과거 release notebook 부재로 reference parity 1건은 실행 불가입니다. 현재 실모델 준비 검증과 구분합니다.
- Step2 회귀 67건 통과. 여기에 기존 assertion 묶음 가명처리 18건/Gate 15건/Veil 정책 44건을 unittest에서 실제 호출하도록 연결했습니다.

최종 DB/검색/HCX·UI·Git 검사 결과는 아래 검증 기록에 추가합니다.

## 기존 문서 증거 목록

아래 파일은 정리 전 문서의 SHA256입니다. 전체 원본은 로컬 복구 스냅샷에 보관하고 실행 안내는 REPRODUCIBILITY.md로 통합했습니다. 공개 공유되는 기록에는 원문·Gold·비밀키를 넣지 않았습니다.

| 정리 전 문서 | SHA256 |
|---|---|
| `step2_privacy_rag/README.md` | `3eb74fdc3c38f93ae8435631ff65e1fd861f26bda7686628a5855d1fd851debe` |
| `step2_privacy_rag/README_V3_1_ko.md` | `c86576618b0a8bf1f4f95c563bf386a6505e0adb0c8686c29438ace094b211ea` |
| `step2_privacy_rag/README_V3_ko.md` | `165cdb85de9e53c665817ef0769513772618c6e0615c48d6c4054447344455fd` |
| `step2_privacy_rag/README_V4_ko.md` | `77300b58523076389f22208e6912d853e139fceaa5b6fdaa2281da6b36ca1d30` |
| `step2_privacy_rag/README_V5_ko.md` | `a8c93d4ad13792d429a635d2a2c4d675df176738afa31959f943bf146fe141a0` |
| `step2_privacy_rag/README_check_team_hmac_contract.txt` | `5c32fedcbf78cfaf6c1608a6b7073b16dda1241327869a9a64d0edc07ce91ad1` |
| `step2_privacy_rag/README_coverage_ko.txt` | `f5cabded0f3377d889879805c177f9b50bf366f7865e3e9d6b7a00656081c691` |
| `step2_privacy_rag/README_gate_v4.txt` | `7abc1721b6d6adf654b6f060db62527de2b57b5c352b25e2bc7caa4fa297b6e5` |
| `step2_privacy_rag/README_ko.md` | `f0257590b35a3a1990bdfec81d024529ab0cfdd004853bb2293c138319ec18b5` |
| `step2_privacy_rag/README_ko.txt` | `bd27c4e5b3dfcc0d7fa196ff04090ee08c8eba8c9ff91675f6b88403767df545` |
| `step2_privacy_rag/README_local_pseudonymizer_v1.txt` | `ad36f4d200677d15badc30167df75ea25e0ee04173cbe1a3ea29a0e239d0825c` |
| `step2_privacy_rag/README_veil_policy.txt` | `62331b828f95056c228b8159dc9cddac5fab7d5b8aab7f71819725790e9b64d9` |
| `step2_privacy_rag/experiments/e2e_local_v1/README_ko.md` | `a3ceb835c0747ef6704caab2861030869fba7a7dd80cd4357cd3437bc98610a8` |
| `step2_privacy_rag/experiments/e2e_local_v1/aegisrag_v6_chat_mvp/MVP_FREEZE_AND_CLEANUP.md` | `1f6f0de508f5aa00dd9b433c661afb6c5ba84c8a314909d86448c316aa6ebe83` |
| `step2_privacy_rag/experiments/e2e_local_v1/aegisrag_v6_chat_mvp/README.md` | `8ee9366628651eed4e7bf0d68d6af6394719ce6e462472e6476577bd13b4dbbb` |
| `step2_privacy_rag/experiments/e2e_local_v1/aegisrag_v6_final_mvp/BASELINE_FREEZE.md` | `875e78a329877b3b291ed1e1a2624ba34ff1042b00a4325f44d0781cd5b14217` |
| `step2_privacy_rag/experiments/e2e_local_v1/aegisrag_v6_final_mvp/README.md` | `971a77d79acd41ed149c7db529571b8a9143e292109c925debf1a74bdc1ea0d4` |
| `step2_privacy_rag/experiments/e2e_local_v1/aegisrag_v6_live_chat/README.md` | `8e6d4e6db5fd37186bb4509e226cc7403687df21bc652d51998535f94e41a958` |
| `step2_privacy_rag/experiments/e2e_local_v1/aegisrag_v6_ui_mvp/README.md` | `bb517b7a2a9c0782cd2246cffabf9748f204525ff30c0d89f5f4930bef9bc1ab` |
| `step2_privacy_rag/experiments/retrieval_pg_v1/README_adaptive_v1_ko.md` | `1ad2fc145d77bc6c62bcb472ab337608cb42b4de6afa126d06a015d27050864a` |
| `step2_privacy_rag/experiments/retrieval_pg_v1/README_batch_v2_ko.md` | `c40e00349c787a3bd31ddbc9268af3236a4b416c8b6433d4ec58aba443ea81a9` |
| `step2_privacy_rag/experiments/retrieval_pg_v1/README_hotfix_ko.md` | `b7e2f30e9cd7ffc22e6e9aceeb299f4e6f28cdaf377dce210e49f20650597cb7` |
| `step2_privacy_rag/experiments/retrieval_pg_v1/README_ko.md` | `6f93d89b249f42ad5b38c3d08e5eb79af064f064b95203ff427cb9f08d8d3b9d` |
| `step1_pii_detector/README.md` | `307f594025ef13350ab5ec6c23951f126c37378c08e50a0b297fcd399ba2d60a` |
| `step1_pii_detector/STRUCTURAL_POLICY_VALIDATION.md` | `4ca5fb61244cd0ec6f1aee8d0a4ff44061e48129f12d7d5b1ead9d77a4234080` |

## 정리 범위

실험 구현·중복 UI·구 README/합성 사례 파일 139개를 작업 트리에서 제거하고 저장소 밖 로컬 복구 영역으로 이동했습니다(이관 원본 포함, 캐시 제외). `local_eval/`, `.env`, 모델 캐시, DB/볼륨은 보존했습니다. Step1의 기존 structural validation 문서는 이력 보고서의 기준선·정책 설명으로 통합했으며 원본은 기존 Git 이력에도 남습니다.

- 보안 검사에서 작업 시작 당시 생성된 로컬 `refs/codex/turn-diffs/.../base` 캡처에 미추적 private 평가 파일이 들어 있음을 확인했습니다. 프로젝트 main 이력과는 분리되어 있고 `.env` 비밀값 패턴은 검출되지 않았습니다. 로컬 도구 캡처는 보존하고, 일반 push 대상 HEAD/index를 별도 검사합니다. mirror/도구 ref push는 하지 않습니다.
- 최초 실제 합성 HCX 실행에서 “제가 신청한 교육” 질문은 CLARIFY 대신 INSUFFICIENT_EVIDENCE로 보류됐습니다. 명시적으로 두 교육 중 대상을 선택하지 않은 합성 질문으로 구분 검증했으며 프롬프트/모델 파라미터/검색 정책은 변경하지 않았습니다. 원래 질문의 두 보류 분류 차이는 남는 한계입니다.

## 최종 검증 기록

- 깨끗한 venv 설치: 성공; `pip check`: 충돌 없음.
- 실제 A.X/Veil 비공개 준비: 기존 V6와 문서·질의·Gold·키 tag·상태 집계 동일.
- 기존 V6 읽기 전용 재평가: 177문서, READY 85, eligible 81, Hit@1 73/81, Hit@5 79/81, indexable Recall@5 0.8802, full-Gold Recall@5 0.8791, MRR 0.9340.
- 합성 전용 별도 PostgreSQL: 실제 탐지기·KoE5 적재·검색·동일 입력 재검증·벡터 불일치 거부 통과.
- 실제 HCX 합성 요청 4건: ANSWER 단일/다중 인용, CLARIFY, INSUFFICIENT_EVIDENCE 통과. private Holdout은 외부 HCX로 송신하지 않음.
- HTTP API 및 HTML/CSS/JS 제공·Origin 차단, JS 문법 검사 통과. UI의 원문 대화는 브라우저 메모리에만 유지하고 요청 로그에는 남기지 않음.
- Step1 29통과/1 reference notebook 부재 skip, Step2 67통과.
- 기존 `.env`·Compose·init SHA256 동일, 기존 비공개 평가 파일 86개 모두 원본과 바이트 동일.
- push 대상 index/HEAD 이력 보안 검사: 민감 경로·현재 비밀값 검출 0. 로컬 도구 캡처 ref의 private 경로는 별도 보고했으며 push 대상에 포함하지 않음.

## 보존한 V5 비교 실측

로컬 `name_mask_decision_v5_final.json`의 합성 78건(`final`) 집계입니다. Official Test/Holdout F1과 비교하지 않습니다. 다음 정책 비교에서는 auto_uncovered가 남아 있어 완전 보호나 일반화 성공을 주장할 수 없습니다.

| 정책 | PERSON 문자 보호율 | 추가 정답 문자 | 추가 PERSON 밖 문자 | 가상 보류 | 자동 처리 미보호 문자 |
|---|---:|---:|---:|---:|---:|
| `baseline` | 0.88889 | 0 | 0 | 0 | 28 |
| `v4_skip_short` | 0.97598 | 29 | 35 | 0 | 8 |
| `v5_grammar_abstain` | 0.94595 | 19 | 2 | 26 | 8 |
| `v5_minmask_abstain` | 0.94294 | 18 | 0 | 29 | 8 |
| `v5_privacy_first` | 0.97598 | 29 | 23 | 0 | 8 |

과거 non-linked handoff V2 Holdout dry-run 기록: 187건 중 LOCAL_DRYRUN_OK 177/REVIEW_QUEUE 10, Step1 review 9/Gate review 1, 미연결 이름 언급 27, 추가 보호 문자 34, 실제 인덱스 쓰기 0, REVIEW→READY 승격 0. 이는 최종 검색 성능이나 PII 정확도가 아닌 중간 계약 점검 기록입니다.

- 전체 177문서 새 적재를 별도 일회용 PostgreSQL에서 완료했습니다. 새 DB 재평가도 73/81·79/81·0.8802·0.9340으로 통과했고 기존 저장 dense 순위 대비 Top-5 차이는 Q059 한 건의 동일 집합 내 순서 차이뿐입니다. 기존 공통 DB에는 쓰지 않았습니다.
- 가명처리·이름 정책·Gate·Region 10개 모듈과 HCX 모듈은 정리 전 원본과 바이트 단위로 동일함을 확인했습니다.
- 실제 단일 CLI로 챗봇 시작→health→UI 정적 파일 확인을 완료했습니다(검증 포트 18768, 안내 기본 URL은 8768).

- 실제 합성 자유 질의 E2E(질의 A.X/Veil→Gate→공개 fixture DB→HCX→인용)도 ANSWER로 통과했습니다. CPU 새 프로세스 1회 계측: Step1 25,235.97ms, 질의 embedding 7,118.43ms, DB 33.06ms, rerank 0.04ms, HCX 1,207.73ms, total 33,595.31ms. 모델 초기 로딩을 포함한 한 번의 관측이며 p50/p95나 운영 지연이 아닙니다. 주요 비용은 Step1/KoE5 로딩·추론이고 DB 재정렬 비용은 작았습니다. 캐싱/병렬화 정책은 바꾸지 않았습니다.

- `git clone --no-local --single-branch --branch main`으로 도구 캡처 ref와 object 저장소 복사 최적화를 제외한 새 clone을 만들었습니다. clone에 `.env`/local_eval이 없음을 확인했고, 잠금 의존성·Step1 편집 설치·pip check·동일 회귀·공개 smoke를 통과했습니다. clone의 **모든 ref** 보안 검사도 민감 경로/토큰 패턴 0건입니다. 원래 작업 저장소에서는 현재 `.env`의 비밀값 8개도 대조 검사했습니다.
- 기존 V6 테이블 행 수 177 및 전체 행 지문은 검증 전후 동일했습니다.

- `.env`가 없는 새 clone에서도 별도 환경 파일의 개인 HF 권한으로 실제 A.X/Veil→고정 KoE5→독립 PGVector 적재/검색/멱등 검증 및 HCX mock을 통과했습니다.
- 검증용으로 새로 만든 Docker 컨테이너/익명 볼륨과 임시 비밀 환경 파일만 정리했습니다. 원래 팀 DB 컨테이너/볼륨은 유지했습니다.

## 2026-11-11 역할에 따른 파일명 정리

파일명의 버전 표기를 제거하고 import·mock patch·실행 스크립트·현재 문서 참조를 함께 변경했습니다. 위의 과거 실험 기록과 원본 파일명은 당시 증거이므로 보존합니다. HMAC domain/key tag, Region 접두사, 정책 ID, 비공개 artifact 형식, CLI 하위 명령과 기존 DB 테이블명은 호환성을 위해 유지합니다.

| 이전 모듈 | 현재 모듈 |
|---|---|
| `local_pseudonymizer_v1.py` | `entity_pseudonymizer.py` |
| `local_pseudonymizer_v2.py` | `local_pseudonymizer.py` |
| `name_boundary_policy_v3_1.py` | `name_boundary_policy.py` |
| `name_mask_decision_v5.py` | `name_protection_policy.py` |
| `name_mask_pareto_v4.py` | `name_masking_strategies.py` |
| `name_v5_nonlinked_handoff_v2.py` | `name_protection_handoff.py` |
| `privacy_gate_v3.py` | `gate_validation.py` |
| `privacy_gate_v4.py` | `privacy_gate.py` |
| `region_keys_v1.py` | `region_keys.py` |
| `safe_e2e_contract_v2.py` | `document_preparation.py` |
| `prepare_v6_retrieval_v1.py` | `dataset_preparation.py` |

관련 테스트 파일명도 같은 역할명으로 바꿨습니다. 코드 파일명과 내용이 재현 적재 provenance 해시에 들어가므로, 이름 변경 후 새 적재의 해시 테이블명은 달라집니다. 기존 테이블을 읽을 때는 기존처럼 `--table` 또는 `AEGISRAG_TABLE`로 지정하며, 기존 DB를 변경하지 않습니다.

이름 정리 검증: Python 소스 41개는 모듈 참조 치환 외 변경 없음, Step2 67건 통과, Step1 29건 통과/reference notebook 부재 1건 skip, 공개 합성 smoke 통과. `.env`·Compose·초기화 스크립트 해시는 변경 전과 동일합니다. Git 추적 파일명의 버전 접미사 0건 및 import/mock 참조 정리를 확인했습니다.

기존 DB를 읽기 전용으로 재평가해 177문서/READY 85/eligible 81, Hit@1 73/81, Hit@5 79/81, indexable Recall@5 0.8802/full-Gold 0.8791, MRR 0.9340 동일성을 확인했습니다. 처음에는 DB 컨테이너가 종료돼 있어 연결 실패했고, 기존 컨테이너를 잠시 시작해 평가한 뒤 원래 종료 상태로 돌렸습니다. DB 설정·데이터는 수정하지 않았습니다.
