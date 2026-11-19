"""Raw adapters; no policy mutation, public model fallback, or network loaders."""
from __future__ import annotations
from pathlib import Path
import hashlib
import inspect
import json
import os
import re
import subprocess
import threading
import uuid
from .contracts import Candidate, ContractError, DetectionEvidence, LABELS, PROFILES, fingerprint, config_hash, EXTRA_SENSITIVE_TYPES, EXTRA_LABELS

ALIASES = {'NAME': 'person', 'PERSON': 'person', 'PHONE': 'phone_number',
           'EMAIL': 'email', 'ADDRESS': 'address', 'ZIPCODE': 'address',
           'RRN': 'resident_registration_number', 'ACCOUNT': 'account_number',
           'CARD': 'card_number'}
ALIASES.update({s.upper(): s for s in LABELS})

def map_label(tag, ignore=()):
    if tag == 'O':
        return None
    name = re.sub(r'^[BIESLU][-_]', '', tag).upper()
    if name in ignore:
        return None
    if name not in ALIASES:
        raise ContractError('UNKNOWN_MODEL_LABEL')
    return ALIASES[name]

def validate_assets(path, trusted_root, expected_hashes):
    p, root = Path(path), Path(trusted_root).resolve()
    if not p.is_absolute() or not p.resolve().is_relative_to(root):
        raise ContractError('MODEL_PATH_FORBIDDEN')
    if not p.is_dir():
        raise ContractError('MODEL_MISSING')
    required = {'config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors'}
    if not required <= set(expected_hashes):
        raise ContractError('MODEL_MANIFEST_INCOMPLETE')
    for name, expected in expected_hashes.items():
        f = p / name
        if not f.resolve().is_relative_to(p.resolve()) or not f.is_file():
            raise ContractError('MODEL_ARTIFACT_MISMATCH')
        h = hashlib.sha256()
        with f.open('rb') as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(part)
        if h.hexdigest() != expected:
            raise ContractError('MODEL_ARTIFACT_MISMATCH')
    return p

class RawAdapter:
    def __init__(self, source_id, revision, predictor=None):
        if source_id not in PROFILES['quality4'] or not revision:
            raise ContractError('INVALID_ADAPTER')
        self.source_id, self.revision, self.predictor = source_id, revision, predictor
        self._lock = threading.Lock()

    def predict(self, text):
        if self.predictor is None:
            raise ContractError('MODEL_MISSING')
        with self._lock:
            raw = self.predictor(text)
        output = []
        for row in raw:
            c = Candidate(self.source_id, self.revision, row['label'], row['start'], row['end'],
                          fingerprint(text), confidence_raw=row.get('prob'),
                          confidence_semantics='uncalibrated_span_mean' if row.get('prob') is not None else None,
                          evidence_codes=('RAW_MODEL',)+tuple(row.get('evidence_codes',())), window_id=row.get('window_id'))
            output.append(c.validate(text))
        return tuple(output)

class AxAdapter(RawAdapter):
    def __init__(self, revision, predictor=None):
        super().__init__('ax', revision, predictor)

class VeilAdapter(RawAdapter):
    def __init__(self, revision, predictor=None):
        super().__init__('veil', revision, predictor)

class KlueAdapter(RawAdapter):
    def __init__(self, revision, predictor=None):
        super().__init__('klue', revision, predictor)

class KoElectraAdapter(RawAdapter):
    def __init__(self, revision, predictor=None):
        super().__init__('koelectra', revision, predictor)

class LocalHFPredictor:
    """Instantiate only inside an isolated model worker. Overflow covers long lines.

    This new raw decoder intentionally excludes team post-filtering/merging.
    Real-model legacy parity remains a separate gate, never inferred from mocks.
    """
    def __init__(self, path, trusted_root, hashes, *, ignore_labels=(), max_length=512, stride=64, label_policy='core7'):
        path = validate_assets(path, trusted_root, hashes)
        if not 8 <= max_length <= 512 or not 0 <= stride < max_length - 2:
            raise ContractError('INVALID_WINDOW_CONFIG')
        from transformers import AutoTokenizer, AutoModelForTokenClassification
        self.tok = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
        self.model = AutoModelForTokenClassification.from_pretrained(path, local_files_only=True,
                                                                     trust_remote_code=False).eval()
        if not self.tok.is_fast:
            raise ContractError('FAST_TOKENIZER_REQUIRED')
        self.ignore_labels, self.max_length, self.stride = tuple(ignore_labels), max_length, stride
        if label_policy not in {'core7','koelectra59'}:raise ContractError('UNKNOWN_LABEL_POLICY')
        self.label_policy=label_policy
        self.ignored_label_counts={}
        cfg = self.model.config
        if any(label in ALIASES for label in self.ignore_labels):
            raise ContractError('CANNOT_IGNORE_IN_SCOPE_LABEL')
        mapper=koelectra_label if label_policy=='koelectra59' else lambda tag:map_label(tag,self.ignore_labels)
        self.mapping = {int(k):mapper(v) for k, v in cfg.id2label.items()}
        if not LABELS<=set(self.mapping.values()) or (label_policy=='core7' and set(self.mapping.values())-{None}!=LABELS):
            raise ContractError('MISSING_REQUIRED_LABEL_MAPPING')
        if any(cfg.label2id.get(tag) != int(i) for i,tag in cfg.id2label.items()):
            raise ContractError('INCONSISTENT_LABEL2ID')
        if set(self.mapping) != set(range(cfg.num_labels)):
            raise ContractError('INVALID_LABEL_MAPPING')
        if len(self.tok) > self.model.get_input_embeddings().weight.shape[0]:
            raise ContractError('TOKENIZER_VOCAB_MISMATCH')
        self.allowed = set(inspect.signature(self.model.forward).parameters)

    def __call__(self, text):
        import torch
        enc = self.tok(text, return_offsets_mapping=True, return_overflowing_tokens=True,
                       truncation=True, max_length=self.max_length, stride=self.stride, padding=False)
        out = []
        self.ignored_label_counts={}
        with torch.inference_mode():
            for wi, offsets in enumerate(enc['offset_mapping']):
                batch = {k: torch.tensor([v[wi]]) for k, v in enc.items()
                         if k in {'input_ids', 'attention_mask', 'token_type_ids'} and k in self.allowed
                         and not (k == 'token_type_ids' and getattr(self.model.config, 'type_vocab_size', 2) <= 1)}
                output=self.model(**batch)
                logits=output.logits if hasattr(output,'logits') else output[0]
                ids = logits[0].argmax(-1).tolist()
                tags = [self.model.config.id2label[i] for i in ids]
                if self.label_policy=='koelectra59':
                    for tag in tags:
                        kind=re.sub(r'^[BIESLU][-_]','',tag).upper()
                        if kind in KOELECTRA_NON_SENSITIVE:self.ignored_label_counts[kind]=self.ignored_label_counts.get(kind,0)+1
                    out.extend(decode_koelectra_window(text,offsets,tags,str(wi)))
                else:out.extend(decode_window(text, offsets, tags, self.ignore_labels, str(wi)))
        return out

def decode_window(text, offsets, tags, ignore=(), window_id='0'):
    if len(offsets) != len(tags):
        raise ContractError('DECODE_LENGTH_MISMATCH')
    out, current = [], None
    for (start, end), tag in zip(offsets, tags):
        if start == end:
            continue
        if not 0 <= start < end <= len(text):
            raise ContractError('INVALID_OFFSET')
        label = map_label(tag, ignore)
        if label is None:
            current = None
            continue
        prefix = tag[0] if re.match(r'^[BIESLU][-_]', tag) else 'I'
        if current and label == current['label'] and prefix not in {'B', 'S', 'U'} and start <= current['end'] + 1:
            current['end'] = end
        else:
            current = dict(label=label, start=start, end=end, window_id=window_id)
            out.append(current)
        if prefix in {'E', 'S', 'L', 'U'}:
            current = None
    return out

class SubprocessPredictor:
    """Local stdio IPC; one process/request, bounded input, timeout, no shell/secrets.

    Worker executable/script must be trusted absolute local paths. Worker must
    return request_id + original fingerprint + config hash; stderr is discarded.
    """
    def __init__(self, command, configuration_hash, timeout=30, max_bytes=1048576):
        if not command or any(not Path(p).is_absolute() or not Path(p).is_file() for p in command):
            raise ContractError('IPC_COMMAND_FORBIDDEN')
        self.command, self.configuration_hash = tuple(command), configuration_hash
        self.timeout, self.max_bytes = timeout, max_bytes

    def __call__(self, text):
        rid = uuid.uuid4().hex
        req = dict(request_id=rid, text=text, fingerprint=fingerprint(text), config_hash=self.configuration_hash)
        data = json.dumps(req).encode()
        if len(data) > self.max_bytes:
            raise ContractError('IPC_REQUEST_TOO_LARGE')
        env = {k: v for k, v in os.environ.items() if k in {'PATH', 'SYSTEMROOT', 'TMPDIR'}}
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
        try:
            result = subprocess.run(self.command, input=data, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=env, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise ContractError('MODEL_TIMEOUT') from None
        except Exception:
            raise ContractError('IPC_FAILED') from None
        if result.returncode or len(result.stdout) > self.max_bytes:
            raise ContractError('IPC_FAILED')
        try:
            obj = json.loads(result.stdout)
            if (obj['request_id'] != rid or obj['fingerprint'] != req['fingerprint']
                    or obj['config_hash'] != self.configuration_hash):
                raise ValueError
            return obj['candidates']
        except Exception:
            raise ContractError('IPC_RESPONSE_MISMATCH') from None

class CandidateReplay:
    """Volatile, immutable replay; no disk/raw-text retention and no miss fallback."""
    def __init__(self):
        self._entries = {}
        self._lock = threading.Lock()

    def put(self, text, input_kind, source, revision, configuration_hash, candidates):
        for c in candidates:
            c.validate(text)
            if c.source_id != source or c.source_revision != revision:
                raise ContractError('CACHE_SOURCE_MISMATCH')
        with self._lock:
            self._entries[(fingerprint(text), input_kind, source, revision, configuration_hash)] = tuple(candidates)

    def get(self, text, input_kind, source, revision, configuration_hash):
        with self._lock:
            key = (fingerprint(text), input_kind, source, revision, configuration_hash)
            if key not in self._entries:
                raise ContractError('CACHE_MISS')
            return self._entries[key]

class DetectionEngine:
    def __init__(self, profile, adapters, configuration=None):
        if profile not in PROFILES:
            raise ContractError('UNKNOWN_PROFILE')
        if any(source != adapter.source_id for source,adapter in adapters.items()):
            raise ContractError('ADAPTER_SOURCE_MISMATCH')
        self.profile, self.adapters = profile, dict(adapters)
        self.configuration_hash = config_hash({'profile': profile, 'configuration': configuration or {},
                                               'revisions': {s:a.revision for s,a in adapters.items()}})

    def detect(self, text, *, input_kind, request_id=None):
        if input_kind not in {'document', 'query'} or len(text) > 1000000:
            raise ContractError('INVALID_INPUT_KIND_OR_SIZE')
        raw, status = {}, {}
        for source in PROFILES[self.profile]:
            adapter = self.adapters.get(source)
            if adapter is None or adapter.predictor is None:
                status[source], raw[source] = 'UNAVAILABLE', ()
                continue
            try:
                raw[source] = adapter.predict(text)
                status[source] = 'OK'
            except ContractError as error:
                status[source] = ('TIMEOUT' if str(error) == 'MODEL_TIMEOUT' else
                                  'UNAVAILABLE' if str(error) == 'MODEL_MISSING' else 'ERROR')
                raw[source] = ()
            except Exception:
                status[source], raw[source] = 'ERROR', ()
        return DetectionEvidence(input_kind, request_id or uuid.uuid4().hex, raw, status, (),
                                 self.configuration_hash, fingerprint(text), self.profile)


class PersistentPredictor(SubprocessPredictor):
    """One resident offline worker, serialized framed requests; faults never retry."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._process=None
        self._request_lock=threading.Lock()
        self._faulted=False
        self.last_metrics={}

    def _stop(self,graceful=False):
        if self._process is not None:
            if self._process.poll() is None:
                if graceful:
                    self._process.stdin.close()
                    try:self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:self._process.kill()
                else:self._process.kill()
            self._process.wait()
            for stream in [self._process.stdin,self._process.stdout]:
                if stream:stream.close()
            self._process=None

    def close(self):
        with self._request_lock:
            self._faulted=True
            self._stop(graceful=True)

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def __call__(self,text, *, sources=None,diagnostic_legacy=False,input_kind='document'):
        import selectors
        import time
        rid=uuid.uuid4().hex
        req=dict(request_id=rid,text=text,fingerprint=fingerprint(text),config_hash=self.configuration_hash)
        if sources is not None:req['sources']=list(sources)
        if diagnostic_legacy:req.update(diagnostic_legacy=True,input_kind=input_kind)
        data=json.dumps(req).encode()+b'\n'
        if len(data)>self.max_bytes:raise ContractError('IPC_REQUEST_TOO_LARGE')
        if not self._request_lock.acquire(timeout=self.timeout):raise ContractError('MODEL_TIMEOUT')
        try:
            if self._faulted:raise ContractError('WORKER_FAULTED')
            if self._process is None:
                env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','TMPDIR'}}
                env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
                self._process=subprocess.Popen(self.command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL,env=env,bufsize=0)
            deadline=time.monotonic()+self.timeout
            # Nonblocking writes avoid hanging on a full pipe when a worker faults.
            output=bytearray();sent=0
            with selectors.DefaultSelector() as selector:
                os.set_blocking(self._process.stdin.fileno(),False)
                selector.register(self._process.stdin,selectors.EVENT_WRITE)
                selector.register(self._process.stdout,selectors.EVENT_READ)
                while b'\n' not in output:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise ContractError('MODEL_TIMEOUT')
                    events=selector.select(remaining)
                    if not events:raise ContractError('MODEL_TIMEOUT')
                    for key,mask in events:
                        if mask & selectors.EVENT_WRITE:
                            n=os.write(key.fd,data[sent:sent+65536]);sent+=n
                            if sent==len(data):selector.unregister(self._process.stdin)
                        else:
                            part=os.read(key.fd,min(65536,self.max_bytes+1-len(output)))
                            if not part:raise ContractError('WORKER_EXITED')
                            output.extend(part)
                            if len(output)>self.max_bytes:raise ContractError('IPC_RESPONSE_TOO_LARGE')
            obj=json.loads(output)
            if (obj['request_id']!=rid or obj['fingerprint']!=req['fingerprint']
                    or obj['config_hash']!=self.configuration_hash):
                raise ContractError('IPC_RESPONSE_MISMATCH')
            if obj.get('error'):
                code=obj['error']
                raise ContractError(code if code in {'MODEL_MISSING','MODEL_TIMEOUT','MODEL_ARTIFACT_MISMATCH'} else 'WORKER_MODEL_FAILED')
            self.last_metrics=dict(obj.get('metrics',{}))
            self.last_diagnostics=dict(obj.get('diagnostics',{}))
            return obj['candidates']
        except Exception as error:
            self._faulted=True;self._stop()
            if isinstance(error,ContractError):raise
            raise ContractError('IPC_FAILED') from None
        finally:self._request_lock.release()


# Out-of-scope organizational/temporal/quantity labels are diagnosed, not silently unknown.
KOELECTRA_NON_SENSITIVE=frozenset({'DATE','TIME','QUANTITY','POSITION','DEPARTMENT','ORGANIZATION'})
KOELECTRA_CORE={'NAME':'person','PHONE':'phone_number','EMAIL':'email','ADDRESS':'address','ZIPCODE':'address',
                'RRN':'resident_registration_number','CARD_NUMBER':'card_number','ACCOUNT_NUMBER':'account_number'}

def koelectra_label(tag):
    if tag=='O':return None
    kind=re.sub(r'^[BIESLU][-_]','',tag).upper()
    if kind in KOELECTRA_CORE:return KOELECTRA_CORE[kind]
    if kind in EXTRA_SENSITIVE_TYPES:return 'extra_sensitive:'+kind
    if kind in KOELECTRA_NON_SENSITIVE:return None
    raise ContractError('UNKNOWN_KOELECTRA_LABEL')

def decode_koelectra_window(text,offsets,tags,window_id='0'):
    """Match team raw BIO decoder, before merge_adjacent/to_ours/trim policy."""
    if len(offsets)!=len(tags):raise ContractError('DECODE_LENGTH_MISMATCH')
    out=[];cur=None
    for (start,end),tag in zip(offsets,tags):
        if start==end:continue
        if not 0<=start<end<=len(text):raise ContractError('INVALID_OFFSET')
        while start<end and text[start].isspace():start+=1
        if start==end:continue
        label=koelectra_label(tag)
        if label is None:cur=None;continue
        kind=re.sub(r'^[BIESLU][-_]','',tag).upper()
        pre=tag[0] if re.match(r'^[BIESLU][-_]',tag) else 'I'
        if cur and cur['_kind']==kind and pre not in {'B','S','U'}:cur['end']=end
        else:
            cur=dict(label=label,start=start,end=end,window_id=window_id,_kind=kind,
                     evidence_codes=('ORIGINAL_LABEL_'+kind,)+(('EXTRA_SENSITIVE_CANDIDATE',) if label in EXTRA_LABELS else ()))
            out.append(cur)
        if pre in {'E','S','L','U'}:cur=None
    return [{k:v for k,v in row.items() if k!='_kind'} for row in out]
