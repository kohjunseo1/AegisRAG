# AegisRAG 실전 1·2 평가 현황 및 팀 비교 (2026-11-12)

> 범위: 합성 Holdout, AegisRAG 작업 브랜치의 로컬 CPU 실행 로그, 공개된 다른 팀의 GitHub 실험 문서. **최종 챗봇 답변 정확도나 새로운 질의 일반화 성능은 아직 측정하지 않았음.**

## AegisRAG 확인된 지표

| 항목 | 결과 | 분모/조건 |
|---|---:|---|
| Step1 Official Test (최초 동결 정책) Strict F1 | 99.23% | 200문서, 초기 평가 |
| Step1 Official Test (최초 동결 정책) Mask Recall | 99.97% | 초기 평가 |
| Step1 Official Holdout (최초 동결 정책) Strict F1 | 97.73% | 187문서, 최초 Holdout 평가 |
| Step1 Official Holdout (최초 동결 정책) Mask Recall | 99.48% | 최초 Holdout 평가 |
| Step1 사후 개선 Holdout Strict F1 / Mask Recall | 99.345% / 100.00% | 저장 예측 오프라인 재채점, **Holdout 확인 이후**, 실모델 온라인 점수 아님 |
| 문서 DB | 187/187 | 177 자동 처리 + 10 검토 후 적재 |
| 온라인 질의 Gate | 85 READY / 2 REVIEW / 1 BLOCKED | 88개 |
| Hit@1 / Hit@5 (전체 질의) | 77/88 = 87.50% / 83/88 = 94.32% | Deferred 3건을 0점으로 처리 |
| MRR (전체 질의) | 0.9051 | 88질의 |
| Hit@1 / Hit@5 (검색 가능 질의) | 77/85 = 90.59% / 83/85 = 97.65% | 85질의 |
| Recall@5 / MRR (검색 가능 질의) | 0.8848 / 0.9371 | 85질의, neutral 문서는 채점 때만 제외 |
| Confusable@5 | 27/85 = 31.76% | 실제 Top-5에 혼동 문서 하나 이상 존재할 비율 |
| 검색 로컬 CPU latency | mean 819.67ms, p50 717.03ms, p95 1,637.05ms | 85 READY, 모델 preload 후, HCX 제외 |
| 모델 cold start + preload | 42,182.71ms | 한 번의 로컬 실행 |

**현재 실측 오류:** Q001/Q015/Q033은 Gate 보류/차단, Q055(첫 정답 41위)·Q065(8위)는 Top-5 실패. 29개 혼동 후보 존재 질의 중 27개는 Top-5에 혼동 후보가 포함됨. 외부 HCX-007 네 건의 *공개 합성 문서* 시나리오는 `smoke --hcx-live`로 실제 API 호출 검증됨. 운영 인덱스에 연결한 웹의 ANSWER/CLARIFY 품질 및 인용 사실성은 아직 정량 평가되지 않음.

## 공개 자료 기반 팀 비교 (실전 2, 88질의)

| 지표 | AegisRAG (이번 온라인 Gate 포함) | 우리 팀원 ① (8bitHermitcrab v3_recheck) |
|---|---:|---:|
| Hit@1 | 0.875 | 0.773 |
| Hit@5 | 0.943 | 0.920 |
| MRR | 0.905 | 0.832 |
| Recall@5 | 0.8848 (85 READY만) | 0.822 (88질의) |
| 혼동 | Confusable@5 0.3176 | Confuse@10 0.538 |

**주의:** Recall과 Confuse는 정의·분모가 달라 순수 비교 불가. 토큰 정책·모델·문서 처리·중립문서 채점 규칙·각자의 Holdout 반복 튜닝도 다름. 공동 채점기로 모든 run을 동일 기준에 재채점하기 전에는 모델 우열의 확정 결과가 아님.

- ① [실전2 재채점 기록](https://github.com/8bitHermitcrab/privacy-rag-chatbot/blob/feature/detect/deliverables/retrieval_eval_v3.md), [버전 비교](https://github.com/8bitHermitcrab/privacy-rag-chatbot/blob/feature/detect/deliverables/retrieval_versions.md). 로컬·API 임베딩, token tier 검색, 앙상블 탐지/인물 해소 등의 비교 버전이 있음.
- ② [nlp_like_lion_chatbot_03](https://github.com/taewoo26/nlp_like_lion_chatbot_03): 탐지기 테스트 strict F1(Veil + Kiwi split) 0.9768 및 인물 사전/역할 매핑 구현. 공개 root README에는 최종 88질의 검색 점수가 미측정으로 남아 있어 동일 근거의 순위 비교 불가능.
- ③ [likelion-AI-4team](https://github.com/BBuSang/likelion-AI-4team): 이름/역할/문서 유형과 질의 보강, 결과선택·출력 검사 및 E2E 답변 평가 코드가 있음. 동일한 기준으로 재현 가능한 최종 수치를 확인하지 못했으므로 전체 우열 판단 불가.

## 현재 웹 질문이 부족한 근거를 반환하는 이유 구분

1. **질문이 말뭉치 범위를 벗어남**: 합성 금융 문서 187건에 경조사/복리후생/출장비 규정이 없다면 정상적인 INSUFFICIENT_EVIDENCE.
2. **검색 실패**: 정답 문서가 DB에 있지만 Top-5에 오지 않음(Q055/Q065처럼). HMAC/의미 검색 후보 재선정이 필요.
3. **HCX 선택·답변 실패**: 관련 문서가 Top-5에 있어도 모델이 대상/질문 사실을 연결하지 못하거나 이름 단독 질문에 CLARIFY하지 못함. 프롬프트/근거 품질 개선 필요.

웹 **개발자 상세**에는 검색 후보 5건의 `doc_id`, `hmac_matches`, `region_matches`, `dense_rank`를 별도로 출력함. **후보 문서 ≠ HCX가 인용한 문서 ≠ Gold 정답 문서**. 민감 본문은 브라우저 개발자 상세로 새로 노출하지 않음. 실제 문서 텍스트 확인은 `--interactive --show-masked-evidence` 및 로컬 데이터 탐색기로 제한.

## 개선 우선순위 및 완료 기준

1. **현재 서비스 측정(코드 변경 최소)**: 187문서에 실제로 있는 *정답 가능한 질문* 10건, 없는 질문 5건, 인물만 입력한 모호 질의 5건을 고정 평가표로 만들고 후보 문서·HCX 결정·인용 및 전체 지연시간 기록. 골드 미제공 질문은 수작업 검증 필요.
2. **질의 Gate 개선**: Q001/Q015/Q033 + `오가은 정보`의 원인별 최소 로컬 fallback/CLARIFY. 결코 REVIEW를 강제로 READY로 바꾸거나 다른 사람 토큰으로 매핑하지 않음. 신규 질의 20건에서도 보안 회귀 검사.
3. **검색 품질 개선**: Q055/Q065, 혼동 후보가 정답보다 위인 5건의 토큰 매칭·문서 유형·근거 적합성 분석. Dense-only, token-only, hybrid boost를 동일 질문/인덱스에서 ablation. BM25와 Cross-Encoder는 실제 비교 후 도입.
4. **답변 평가**: 실행한 HCX만 대상으로 answer correctness, evidence-hit, citation precision, unnecessary abstention, leakage, cold/warm p95 기록. 기존 검색 Hit@5를 답변 정확도라고 표현하지 않음.
5. **팀 간 공통 비교**: 같은 질문 집합·Gold·Hit 정의·모델 워밍업 조건·CPU·LLM·Top-K. 팀 DB는 격리, `--reset`을 공유 DB에서 실행 금지.

## 재현

```bash
python scripts/verify_v6_e2e.py --eval-raw
python scripts/verify_v6_e2e.py --interactive --diagnose-gate --show-masked-evidence
```

실제 웹: `AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES python scripts/aegisrag.py run-chat --device cpu --port 8768`; 모델은 사전 로딩, DB 조회는 읽기 전용. 실제 HCX 호출은 비용이 발생함.

**작성 시점 확인 한계:** 다른 팀의 공개 README/실험 문서와 핵심 코드를 확인했으나 각 코드의 로컬 실행 또는 공통 환경 재채점은 수행하지 않음. 본 문서의 수치는 사용자 실측 로그·각 팀 문서에 표시된 값을 정확하게 구분함.
