# Safe diagnosis of held Blind documents

**Run BEFORE DB ingestion.** The original 171-document baseline found 12 Gate not ready, 7 pipeline exceptions and 2 extra projected review outcomes. These are not necessarily Veil model errors. The generic SAFE_PIPELINE_FAILURE deliberately hides exception details.

Local diagnostic, from project root:

    python scripts/diagnose_blind_holds.py --input step2_privacy_rag/local_eval/blind_v2/blind_eval.jsonl.zip --report step2_privacy_rag/local_eval/blind_v2/pii_timing_reasons_v1.json

This replays only the held cases using the same local model, frozen Step1 result, name protection policy and Privacy Gate. It logs ONLY the case index, allowlisted error category, stage, exception type, module filename/line and decision codes. It NEVER prints PII, raw text, token, secret, traceback content or unfiltered exception message. No database calls and no HCX calls.

BLIND_SAFE_FAILURE: why a previous SAFE_PIPELINE_FAILURE happened.
BLIND_SAFE_GATE_DIAG: a Gate not-ready reason (allowlisted).
BLIND_HELD_CASE: present state and name-link counts.
BLIND_HELD_DIAG_SUMMARY: reasons and non-deterministic state differences.

Do not bulk-convert BLOCKED to REVIEW or force approval. Both are not-indexable and the existing review UI exposes both. Only source correction followed by successful detector + Gate + pseudonymization is eligible. If the cause is an implementation bug, correct the underlying logic and rerun the benchmark.

This is a replay of raw texts; the later dataset_intake stage adds a doc-type prefix, so always verify actual staging results independently.
