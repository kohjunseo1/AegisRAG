import json, os
from importlib.resources import files
from pathlib import Path
from dotenv import load_dotenv
from .detector import PIIDetector

def create_detector(manifest_path=None, cache_dir=None, token=None):
    root=Path(__file__).resolve().parents[2]
    load_dotenv(root.parent/'.env',override=False)
    load_dotenv(root/'.env',override=False)
    if manifest_path is None:
        manifest_text = files('pii_detector').joinpath('model_manifest.json').read_text(encoding='utf-8')
    else:
        manifest_text = Path(manifest_path).read_text(encoding='utf-8')
    manifest=json.loads(manifest_text)
    token=token or os.environ.get('HF_TOKEN')
    if not token:
        raise RuntimeError('HF_TOKEN is required. Set it in the environment or an approved .env file.')
    # Defer heavyweight model dependencies and any HF activity until credentials exist.
    from .backends import load_backends

    ax,veil,guard=load_backends(manifest,token,cache_dir)
    return PIIDetector(ax,veil,token_guard=guard,parallel=True,max_chars=20000,max_windows=150,timeout_seconds=60)
