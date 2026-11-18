# Unified V1 source map

2026-11-19. Worktree: `/Users/gojunseo/AegisRAG-unified-v1`, branch `feature/unified-v1`.
Own base and fetched origin/main: `644ab9d56cd24d5386c739712488299448940d26`.
Read-only team reference: `242543bf0eae91aa3d655227a747aa128707368e` (`feature/detect`).
No reset/rebase/main update. IDE worktree has unrelated pending changes and is untouched.

| New module | Read-only source / rationale |
|---|---|
| `unified_v1/contracts.py`, `adapters.py` | Blueprint §4; own `pii_detector/backends.py` raw predict functions; team `starter/pii_pipeline.py:model_predict`, `hf_detector.py:decode`. New isolated runtime, explicit mapping and overflow windows; no team global settings or policy imports. |
| `unified_v1/protection.py` | Own `privacy_gate.py`, `gate_validation.py`, `local_pseudonymizer.py` remain authoritative. Add conservative adapter and union coverage. Team `detect_boost.py` is architectural reference only. |
| `unified_v1/identity.py` | Own frozen label-scoped HMAC; team `person_resolver.py` concept reviewed; conservative new resolver avoids role/name-only merging. |
| `unified_v1/retrieval.py` | Own `live_index.py`, team `starter/search.py` ranking reference. New in-memory common corpus interface, no SQL copied into production. |
| `unified_v1/generation.py` | Own `hcx.py` SCHEMA, outbound and response validation. Only explicit mock transport, no live transport. |

No team source files or model weights copied. Team root has no explicit LICENSE file: redistribution rights and model sharing terms remain unverified; no release/publish authorized. Existing attribution remains intact. All model, DB and HCX live claims require independent evidence.

Additional implementation files: `worker.py` adds network-blocked local stdio process isolation; `pipeline.py` connects explicit-kind preparation and unresolved query policy; `experiment.py`/`synthetic.py` provide new mock-only reproducibility orchestration. `.github/workflows/synthetic.yml` retains all existing steps and appends Unified V1 synthetic tests and E2E. No team source redistribution or production runtime switch.
