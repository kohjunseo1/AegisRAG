# Model assets

No checkpoint or private corpus is staged. Local checks only, no model downloads.

| Source | State | Evidence / unresolved requirement |
|---|---|---|
| KLUE | FILES_PRESENT; parity BLOCKED | roberta; vocab 32000; 15 BIO labels (O + seven types). Four file SHA256 values below; sender-provided checksums and synthetic expected predictions absent. |
| KoELECTRA ft5 | MISSING / UNAVAILABLE | Expected private model folder absent. No public replacement. |
| A.X / Veil | LOCAL_MODEL_NOT_RUN | Pins retained in original manifest; full runtime environment parity unverified. |
| KoE5 | LOCAL_MODEL_NOT_RUN | Original pin/dimension/prefix retained; synthetic ranking uses explicit fake dense scores. |

KLUE file hashes (observed, not sender-verified):

- config.json: `d8d486cc3d59fec1ff109eaf6a60e22b53dc451d4dccb14f94e4b93fcf0b07ac`
- model.safetensors: `05dd28640e2c884007b7c7668ad1e2f770ac2cabe0d31cc9cfe6ac02d2b69b4f`
- tokenizer.json: `200c2fd5a65daf3b67aa532740d3a26736deceaa8b28e82826038aede9377cf0`
- tokenizer_config.json: `fd172592aef04c3c1c5be31fa2072c27ae81bdc4192b4e919ea0db6b43407913`

No four-model E2E/latency/RAM/generalization claim. Missing model prevents team2/quality4 live readiness.

P0 temporary venv probe: `AutoTokenizer(...local_files_only=True)` PASS (`is_fast=True`); `AutoModelForTokenClassification` BLOCKED (ImportError). This is not LOCAL_MODEL_PASS. Original runtime predictions absent; raw parity not measured.
