# Dependency compatibility

P0 read-only inspection on 2026-11-19, Intel macOS:

- Default `python3`: Python 3.10.4, no torch/transformers/psycopg. First regression attempt: Step1 31 (1 skip), root 29 pass; Step2 fails 11 imports/subprocesses due to missing psycopg. This is an environment failure, not a code regression.
- Existing Aegis interpreter: `/Users/gojunseo/opt/anaconda3/envs/aegisrag/bin/python`, Python 3.11.13, torch 2.2.2, transformers **5.22.2**, tokenizers **0.23.3**, numpy 1.26.4, onnxruntime 1.23.2. Actual transformers/tokenizers differ from requirements.lock (4.55.4/0.21.4); no changes made.
- Separate temporary venv `/tmp/aegis-unified-team-poc` created with system-site-packages for a read-only compatibility probe. No existing package installed/upgraded. This is a compatibility probe using inherited installed packages, not proof of the original team environment.
- Team requirements report torch 2.11.0/transformers 5.20.2; checkpoint config records transformers **5.21.0**. Training/runtime parity needs clarification and original synthetic predictions.
- Unified contracts/mock tests use standard library plus existing V6 modules. No new runtime dependency is required. Local subprocess adapter permits an independently provisioned team interpreter, without changing Aegis environment.

PoC result: local fast tokenizer loaded successfully; AutoModel loading failed with ImportError. Transformers 5 runtime backend requirements are incompatible with installed torch 2.2.2. No inference run, no prediction equivalence/latency claimed. IPC implementation is used as the independent runtime integration boundary; provisioning a compatible model environment remains BLOCKED.
