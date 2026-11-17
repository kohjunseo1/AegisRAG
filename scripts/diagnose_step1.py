#!/usr/bin/env python3
"""Privacy-safe real Step1 diagnostic in the CURRENT Python environment.

Never prints source input, credentials, model predictions, token values, or
untrusted exception messages. Does not connect to Postgres or send HCX calls.
This is a diagnostic, not a PII recall or data-release certification.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from importlib import metadata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'step2_privacy_rag' / 'app'))
sys.path.insert(0, str(ROOT / 'step1_pii_detector' / 'src'))
SYNTHETIC = '사내 안전교육은 언제 진행되나요?'


def emit(label: str, content: dict) -> None:
    print(label, json.dumps(content, ensure_ascii=False, sort_keys=True), flush=True)


def safe_exception(exc: Exception) -> dict:
    """Allow exception *type* and source line within this repository, nothing else."""
    result = {'error_type': type(exc).__name__}
    try:
        frames = traceback.extract_tb(exc.__traceback__)
        for frame in reversed(frames):
            try:
                rel = Path(frame.filename).resolve().relative_to(ROOT)
            except ValueError:
                continue
            # No line body, no exception message, no absolute local paths.
            result['repo_location'] = f'{rel}:{int(frame.lineno)}'
            break
    except Exception:
        pass
    return result


def inspect_environment() -> None:
    from dotenv import dotenv_values
    env = dotenv_values(ROOT / '.env')
    versions = {}
    for package in ('torch', 'transformers', 'tokenizers',
                    'onnxruntime', 'huggingface-hub', 'python-dotenv', 'sentence-transformers'):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = 'NOT_INSTALLED'
    emit('STEP1_ENVIRONMENT', {
        'python': sys.version.split()[0],
        'conda_env': os.environ.get('CONDA_DEFAULT_ENV') or 'NOT_SET',
        'env_file_exists': (ROOT / '.env').is_file(),
        'hf_token_available': bool(os.environ.get('HF_TOKEN') or env.get('HF_TOKEN')),
        'versions': versions,
    })


def hf_repo_probe() -> int:
    """Read-only metadata check for both pinned org repos. No model downloads."""
    from dotenv import dotenv_values
    from huggingface_hub import HfApi
    from huggingface_hub.utils import RepositoryNotFoundError, RevisionNotFoundError, HfHubHTTPError

    cfg = dotenv_values(ROOT / '.env')
    from_env = bool(os.environ.get('HF_TOKEN'))
    token = os.environ.get('HF_TOKEN') or cfg.get('HF_TOKEN')
    if not token:
        emit('HF_ACCESS', {'auth': 'NO_TOKEN', 'token_source': 'NONE'})
        return 2
    token_source = 'PROCESS_ENV' if from_env else 'DOTENV'
    api = HfApi()
    try:
        api.whoami(token=token)
        auth = 'VALID_TOKEN'
    except Exception as exc:
        auth = ('AUTH_FAILED' if isinstance(exc, HfHubHTTPError)
                else 'AUTH_CHECK_FAILED')
        emit('HF_ACCESS', {'auth': auth, 'token_source': token_source})
        return 2
    emit('HF_ACCESS', {'auth': auth, 'token_source': token_source})
    manifest = json.loads(
        (ROOT / 'step1_pii_detector/src/pii_detector/model_manifest.json'
        ).read_text(encoding='utf-8')
    )
    good = True
    for label in ('ax', 'veil'):
        item = manifest[label]
        repo, revision = item['repo'], item['revision']
        try:
            result = api.model_info(repo_id=repo, revision=revision, token=token)
            if result.sha != revision:
                raise ValueError('REQUIRED_SHA_MISMATCH')
            state = 'PINNED_REVISION_OK'
        except RevisionNotFoundError:
            state = 'REVISION_NOT_FOUND'
        except RepositoryNotFoundError:
            state = 'REPOSITORY_NOT_FOUND_OR_ACCESS_DENIED'
        except HfHubHTTPError:
            state = 'HUB_HTTP_ERROR'
        except Exception:
            state = 'MODEL_INFO_CHECK_FAILED'
        emit('HF_MODEL', {'model': label.upper(), 'repo': repo, 'state': state,
                          'revision_prefix': revision[:8]})
        good = good and state == 'PINNED_REVISION_OK'
    if good:
        print('HF_ORG_MODELS_ACCESS_OK', flush=True)
    return 0 if good else 2


def run_probe() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    detector = None
    try:
        from pii_detector import create_detector
        detector = create_detector()
        emit('STEP1_MODEL_INIT', {'result': 'PASS'})
    except Exception as exc:
        emit('STEP1_MODEL_INIT', {'result': 'FAIL', **safe_exception(exc)})
        return 2

    try:
        for name, predictor in [('AX', detector.ax_predict),
                                ('VEIL', detector.veil_predict)]:
            try:
                spans = predictor(SYNTHETIC)
                if not isinstance(spans, list):
                    raise TypeError('unexpected model output')
                emit('STEP1_BACKEND', {'backend': name, 'result': 'PASS',
                                       'predicted_spans': len(spans)})
            except Exception as exc:
                emit('STEP1_BACKEND', {'backend': name, 'result': 'FAIL',
                                       **safe_exception(exc)})
                return 2

        result = detector.detect(SYNTHETIC)
        status = result.get('status') if isinstance(result, dict) else None
        issues = result.get('issues') if isinstance(result, dict) else []
        safe_codes = sorted({
            str(i.get('code', 'UNKNOWN'))
            for i in issues if isinstance(i, dict) and
            str(i.get('code', '')).replace('_', '').isalnum()
        })
        emit('STEP1_DETECT', {'status': status if status in {
            'READY_FOR_PSEUDONYMIZATION','REVIEW_REQUIRED','BLOCKED'} else 'INVALID_STATUS',
            'issue_codes': safe_codes})
        if status == 'BLOCKED':
            return 2

        from dataset_preparation import fixed_key
        from document_preparation import prepare
        secret = fixed_key(ROOT / '.env')
        local = prepare('Q_LIVE', SYNTHETIC, detector, secret)
        emit('STEP2_LOCAL_PREPARE', {
            'state': local.state,
            'reason': local.reason,
            'unlinked_mentions': int(local.unlinked_mentions),
            'masked_text_exposed': False
        })
        if local.state not in {'LOCAL_PREPARED', 'REVIEW'}:
            return 2
        print('STEP1_LOCAL_PIPELINE_PROBE_OK', flush=True)
        return 0
    except Exception as exc:
        emit('STEP1_PROBE_EXCEPTION', safe_exception(exc))
        return 2
    finally:
        if detector is not None:
            detector.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--environment-only', action='store_true',
                      help='print nonsecret package/config availability without loading models')
    mode.add_argument('--hf-repos', action='store_true',
                      help='validate private org repositories and pinned revisions, no model downloads')
    args = ap.parse_args()
    inspect_environment()
    if args.environment_only:
        return 0
    if args.hf_repos:
        return hf_repo_probe()
    return run_probe()


if __name__ == '__main__':
    raise SystemExit(main())
