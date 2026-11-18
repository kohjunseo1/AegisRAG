"""Pinned KoE5 local-only adapter using the frozen V6 manifest contract."""
import math
import threading
from settings import MANIFEST
from .contracts import ContractError

class KoE5Encoder:
    def __init__(self, *, device='cpu',model=None):
        cfg=MANIFEST['koe5']
        if (cfg['revision']!='237b580aea358e3042fcf5baf78124c33c3afd16' or cfg['dimension']!=1024
                or cfg['query_prefix']!='query: ' or cfg['passage_prefix']!='passage: '
                or cfg['max_seq_length']!=512 or cfg['normalize_embeddings'] is not True):
            raise ContractError('EMBEDDING_CONTRACT_MISMATCH')
        if model is None:
            from huggingface_hub import snapshot_download
            from sentence_transformers import SentenceTransformer
            local=snapshot_download(cfg['repo'],revision=cfg['revision'],local_files_only=True)
            model=SentenceTransformer(local,device=device,trust_remote_code=False,local_files_only=True)
        if model.get_sentence_embedding_dimension()!=1024:raise ContractError('EMBEDDING_DIMENSION_MISMATCH')
        model.max_seq_length=512
        self.model,self.cfg,self._lock=model,dict(cfg),threading.Lock()

    def encode_query(self,prepared):
        # Before local embedding, require explicit prepared/approved-only input.
        prepared.require_approved(key_tag=prepared.key_tag,policy_version=prepared.policy_version,input_kind='query')
        with self._lock:
            rows=self.model.encode([self.cfg['query_prefix']+prepared.sanitized_text],normalize_embeddings=True,
                                   convert_to_numpy=True,show_progress_bar=False,batch_size=1)
        if len(rows)!=1 or len(rows[0])!=1024:raise ContractError('INVALID_EMBEDDING')
        values=tuple(float(v) for v in rows[0])
        if any(not math.isfinite(v) for v in values) or abs(sum(v*v for v in values)-1)>.005:
            raise ContractError('INVALID_EMBEDDING')
        return values
