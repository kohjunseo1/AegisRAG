from pathlib import Path
from huggingface_hub import snapshot_download

def load_frozen_repo(repo, revision, token=None, cache_dir=None):
    root=Path(snapshot_download(repo_id=repo,revision=revision,token=token,cache_dir=cache_dir,allow_patterns=['best_model/*']))/'best_model'
    for name in ('config.json','model.safetensors','tokenizer_config.json'):
        if not (root/name).is_file(): raise FileNotFoundError(f'Missing pinned model asset: {name}')
    return root
