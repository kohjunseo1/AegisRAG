# Phase status — 2026-11-19

All work in feature/unified-v1 worktree. No external inference, production DB access or writes, environment upgrades, pushes/PRs. P6 excluded.

## P0 — IMPLEMENTED / regression PASS / real-model parity BLOCKED

- Full blueprint read; current worktree mismatch resolved by using existing unified worktree. Source SHA and assets inspected; origin fetched without main update. Team reference clean and read-only.
- Baseline using existing Aegis Python: Step1 31 tests (1 skip), Step2 183 tests, root 29 tests: PASS. Default Python's missing psycopg failure resolved by selecting existing interpreter, without package changes.
- Commands: `PYTHONPATH=step1_pii_detector:step1_pii_detector/src $PY -m unittest discover -s step1_pii_detector/tests -v`; `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s step2_privacy_rag/tests -v`; `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests -v`.
- `$PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag/bin/python` throughout this report.
- KLUE files hashed; KoELECTRA MISSING. Dependency drift documented. Separate temporary venv compatibility probe; no installed dependency changed. No real model performance measured.
- Source/model/dependency details in SOURCE_MAP.md, MODEL_ASSET_CHECKLIST.md, DEPENDENCY_COMPATIBILITY.md. Production DB fingerprint not read: BLOCKED (no isolated DB).

## P1 — IMPLEMENTED / MOCK_PASS (8 tests); live adapter parity BLOCKED

- New opt-in package: immutable contracts, explicit input kind, seven labels, four raw source adapters, own2/team2/quality4 + leave-one-out registry, strict source/config/text replay with no fallback, config rejection of live API/mask-only auto-ingest.
- Local-only HF loader verifies artifact hashes and mapping, supports fast tokenizer overflow windows; local subprocess IPC validates request/fingerprint/config and enforces timeout. Existing backends can be injected as pinned raw predictors; no global setting mutation.
- `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests/unified_v1 -v`: 8 PASS. Unicode decoder, missing/incorrect assets, missing profiles, concurrent requests, strict replay, safe error behavior covered.
- `$PY -m unified_v1 --profile quality4 --synthetic` (same PYTHONPATH): MOCK/LOCAL_ONLY, not real inference. Non-synthetic CLI leaves all models unavailable and exits 2.
- P0 compatibility probe: tokenizer local load PASS; model load ImportError/BLOCKED. No model substitution/install. Candidate replay is volatile only; persistent sensitive cache deliberately not enabled (optional in specification).
- No commits yet; changes remain reviewable in worktree. No external inference/DB access/production edits.

## P2 — IMPLEMENTED / MOCK_PASS (15 cumulative tests)

- Added StructuralSentinel, contextual name candidates, union coverage, conflict reasons, ProtectionPlan and V6GateAdapter. Frozen Gate and existing V6 modules unchanged.
- Baseline coverage and every new raw/structural/contextual interval survive fusion; unresolved label/boundary cases remove HMAC eligibility and force REVIEW. Missing detector sources force BLOCKED. New unvalidated candidates never become HMAC solely through voting/confidence.
- Preparation calls frozen pseudonymizer, covers residual masks, returns LOCAL_PREPARED only for local processing; approval remains NOT_APPROVED. Mask-only auto-ingest OFF.
- New cases: emoji/codepoint offsets, split/rare/compound/short names, partial addresses/postcode, business IDs, overlapping labels, long-line tail, bad spans, baseline mask preservation, missing models and frozen Gate adapter.
- Command: `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests/unified_v1 -v`: 15 PASS (7 new). No live model/DB/API or latency measurement. No V6 changes required.

## P3 — IMPLEMENTED / MOCK_PASS (20 cumulative tests); production-key parity BLOCKED

- New mention/owned-anchor contracts and local explicit-field extraction. Name lookup is a distinct identity namespace; frozen PERSON token does not assert identity. Entity key uses separate versioned namespace, no numeric alias.
- Conservative resolver requires two independently typed, explicitly owned validated attributes and same lookup name, rejects globally shared identifiers and same-name conflicts. Missing/changed/conflicting/single identifiers, roles and mere co-occurrence remain UNRESOLVED.
- Frozen make_hmac_token/canonicalize/pseudonymize_local called unchanged. Seven-label 96-bit/prefix parity, label separation, document/query consistency and Region/address idempotence verified with synthetic key only.
- `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests/unified_v1 -v`: 20 PASS (5 new), including homonyms, shared phone, changed phone, no anchors, explicit ownership and key mismatch.
- Actual HMAC key/DB parity, real identity quality/latency not measured. Production secrets never read. Existing V6 unlinked_mentions not modified.

## P4 — IMPLEMENTED / MOCK_PASS (26 cumulative tests); DB/KoE5 BLOCKED

- Common approved-only in-memory document index, insert-once fingerprint, explicit experiment approval receipts tied to sanitized text/version/corpus. No ACL claim. Mask-only requires explicit experimental opt-in; no automatic admission.
- own_v6: dense candidate pool then HMAC count/Region/dense; team_v3: ALL+Region/ALL/ANY/Region/Dense groups; hybrid_v1: candidate union and exact identity priority/conflict exclusion. Fake dense scores clearly separate from KoE5. Document-only mode rejects unsupported chunk inputs.
- Frozen common scorer reports eligible vs total denominators, neutral scoring vs actual top5, hit1/5, recall5, MRR, confusable1/5 and Gate state counts; index fingerprint is reproducible.
- IsolatedMigration generates only UUID-namespaced test schema/table/GIN plans. Runtime execution blocked until isolation is independently verified; no connection/DSN opened. Production identifiers cannot be migration targets.
- `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests/unified_v1 -v`: 26 PASS (6 new). Tests cover ALL/ANY/Region/dense union, identity conflict, dedup, version/approval/mask-only/receipt rejection, full denominator and pre-embedding Gate.
- Real Postgres/KoE5 metrics, production DB count/fingerprint and 85/88 corpus evaluation NOT_RUN/BLOCKED; no private holdout read. Synthetic evaluator test uses 1 eligible / 3 total, never presented as 85/88 real performance.

## P5 — IMPLEMENTED / MOCK_PASS (33 cumulative tests); HCX live NOT_RUN_BY_POLICY

- HCX-007/temperature=0 JSON schema adapter reuses frozen V6 schema/outbound/response validators. Context validates approval/version/identity and exact bounded documents before mock transport.
- Independent detector recheck + patterns operates on token-blanked copies preserving original codepoints; malformed/short/unknown or unauthorized 96-bit tokens, residual PII, unavailable guards, schema/citation/region failures block output.
- Only exact MockTransport accepted; NetworkBlockedTransport always rejects. No live transport implementation or API credential loading. Citation support records exact sanitized context ranges; explicitly not a factual/claim faithfulness verdict.
- `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests/unified_v1 -v`: 33 PASS (7 new). Full raw synthetic detection → V6 Gate adapter → HMAC → independent check/approval → in-memory search → HCX schema mock → output check succeeds with socket and urlopen patched to forbid network. Negative cases include pretransport PII/REVIEW and invalid citations/output tokens/truncation.
- P0→P5 functional structure complete. Final hardening/reproducible CLI and regression verification are still part of P5; P6 is not started.

### P5 최종 보강 / 재현성

- Actual key → 기존 key_tag 함수의 일치 검증, input_kind를 ProtectionPlan에 고정, immutable evidence mapping, 모델 라벨 양방향 매핑 검증 추가. 동일 문서 내 별도 Mention 자동 병합 거부, 소유 속성 형식 검증, 반복 이름/띄어쓰기/조사 후보 보호 추가.
- LocalPipeline, query_from_mentions, 독립 로컬 worker, PII-free ExperimentManifest, network-blocked 전체 합성 CLI 추가. 기존 CI 명령을 유지하고 신규 test/E2E 두 step만 추가.
- `--synthetic-e2e` own2/team2/quality4 모두 MOCK_PASS. 각 세 검색 프로파일을 동일한 1개 합성 문서/질의 fixture로 평가하며 실성능으로 해석하지 않는다. 별도 56문서 fixture로 Dense 후보 밖 token union 회수 검증.
- 추가 실패/수정: overflow test double에 forward/argmax signature가 누락되어 2회 실패; 실제 HF 호출 계약에 맞춰 double을 수정한 뒤 PASS. 제품 코드나 보호 계약을 약화시키지 않았다. unittest imported TestCase 중복 수집을 제거하여 테스트 개수 중복 집계 방지.
- 테스트 수 및 최종 검증 결과는 아래 최종 행과 AUTONOMOUS_RUN_REPORT.md를 기준으로 한다. 모든 변경은 커밋 없이 worktree에 보존(로컬 커밋은 선택 사항). P6 미수행.

### 최종 검증 — P5 종료

| 검증 | 결과 |
|---|---|
| 기존 Step1 / Step2 / 루트 | 31 (1 skip) / 183 / 29, 모두 OK |
| 신규 Unified V1 | 41 PASS |
| 전체 | 284개 실행, 283 PASS / 1 SKIP / 최종 FAIL 0 |
| synthetic-e2e own2/team2/quality4 | MOCK_PASS; 네트워크 차단; 실모델 대체 아님 |
| 원본 smoke / synthetic-review / environment-only | PASS |
| 기존 CLI help 5종 | PASS |
| Git history security scan / 신규 29개 파일 패턴 검사 | PASS (알려진 비밀 패턴 검사; 모든 비밀 부재 보증 아님) |
| diff check / 신규 Python AST / 신규 코드 trailing whitespace | PASS |
| 운영 V6/HMAC/Region/lock 변경 | 없음 |
| 실모델 parity·성능 / DB integration | BLOCKED / NOT_MEASURED |
| 실제 HCX·외부 LLM·외부 임베딩 | NOT_RUN_BY_POLICY, 0회 |
| P6 / push / PR / main 병합 | NOT_RUN |

최종 테스트 명령은 P0와 동일한 세 unittest discover 명령 및 `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests/unified_v1 -v`다. 기존 CI의 `holdout_finalizer.py policy`는 소스 파일을 수정할 수 있어 실행하지 않았으며, CI 명령 자체는 보존했다. 운영 DB count/fingerprint는 접근하지 않아 미검증이다. 타 worktree의 기존 변경 목록과 팀원 참조 clean 상태를 종료 시 재확인했다.

요약 보고서: AUTONOMOUS_RUN_REPORT.md. 재현 명령: README.md. 기계 판독 결과: VALIDATION_RESULTS.json. 다음 요청 선행조건: NEXT_ACTIONS.md. **P5에서 종료, P6 미수행.**

## 후속 GitHub 게시 요청

최초 자율 실행 종료 후 사용자가 `feature/unified-v1`의 커밋 및 GitHub push를 명시적으로 요청했다. 위 미커밋/미push 기록은 최초 실행 종료 시점의 상태다. 이번 후속 작업은 통합 코드·테스트·문서·CI 변경의 브랜치 게시만 포함하며, PR 생성/main 병합/P6는 포함하지 않는다.
