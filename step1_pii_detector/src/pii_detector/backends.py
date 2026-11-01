"""06D notebook backend implementation: frozen AX BIO and Veil official FP32 ONNX decoder."""
import gc, importlib.util, inspect, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
import torch, onnxruntime
# Torch 2.2 on Python 3.12 exposes torch.compile but raises during ModernBERT
# import. Compilation is an optimization; eager inference preserves the 06D logits.
if sys.version_info >= (3, 12) and tuple(int(x) for x in torch.__version__.split('+')[0].split('.')[:2]) < (2, 3):
    torch.compile = lambda model=None, **kwargs: (lambda fn: fn) if model is None else model
from transformers import AutoModelForTokenClassification, AutoTokenizer, PreTrainedTokenizerFast
from huggingface_hub import snapshot_download
from .loader import load_frozen_repo

def load_backends(manifest, token=None, cache_dir=None):
    global TMP, DEVICE, LABELS, AX_DIR, VEIL_DIR, AX_REPO, AX_SHA, VEIL_REPO, VEIL_SHA, AutoModelForTokenClassification
    from .detector import LABELS
    TMP=Path(tempfile.mkdtemp(prefix="step1-pii-")); DEVICE=torch.device("cuda" if torch.cuda.is_available() else "cpu")
    AX_REPO=manifest["ax"]["repo"]; AX_SHA=manifest["ax"]["revision"]
    VEIL_REPO=manifest["veil"]["repo"]; VEIL_SHA=manifest["veil"]["revision"]
    AX_DIR=load_frozen_repo(AX_REPO,AX_SHA,token,cache_dir); VEIL_DIR=load_frozen_repo(VEIL_REPO,VEIL_SHA,token,cache_dir)
    # ===== 4. A.X 파인튜닝 모델 추론 코드 (03C 원본 그대로) =====
    def load_fast_tokenizer(path):
        try:
            return AutoTokenizer.from_pretrained(str(path),use_fast=True,local_files_only=True)
        except ValueError as ex:
            if 'TokenizersBackend' not in str(ex):raise
            print('A.X TokenizersBackend 호환 처리')
        try:return PreTrainedTokenizerFast.from_pretrained(str(path),local_files_only=True)
        except Exception:pass
        local=TMP/'ax_compat_tokenizer';local.mkdir(exist_ok=True)
        for fp in path.iterdir():
            if fp.is_file() and fp.suffix not in ('.safetensors','.bin'):
                shutil.copy2(fp,local/fp.name)
        cfg=local/'tokenizer_config.json'
        meta=json.loads(cfg.read_text(encoding='utf8'))
        meta['tokenizer_class']='PreTrainedTokenizerFast'
        cfg.write_text(json.dumps(meta,ensure_ascii=False),encoding='utf8')
        return AutoTokenizer.from_pretrained(str(local),use_fast=True,local_files_only=True)

    ax_tok=load_fast_tokenizer(AX_DIR)
    assert ax_tok.is_fast
    ax_net=AutoModelForTokenClassification.from_pretrained(str(AX_DIR),use_safetensors=True,local_files_only=True).to(DEVICE).eval()
    assert ax_net.config.num_labels==37,('A.X 출력층 예상 37',ax_net.config.num_labels)
    chk=ax_tok('김민수의 계좌번호',return_offsets_mapping=True)
    assert max(chk['input_ids'])<ax_net.get_input_embeddings().weight.shape[0], 'A.X tokenizer-vocab 불일치'
    ALIAS={
     'person':['PERSON','NAME','FULL_NAME','KOREAN_NAME','PERSON_NAME','PRIVATE_PERSON'],
     'phone_number':['PHONE','PHONE_NUMBER','PHONE_NUM','MOBILE','MOBILE_PHONE','TELEPHONE','PRIVATE_PHONE'],
     'email':['EMAIL','E_MAIL','EMAIL_ADDRESS','PRIVATE_EMAIL'],
     'address':['ADDRESS','STREET_ADDRESS','HOME_ADDRESS','POSTAL_ADDRESS','PRIVATE_ADDRESS'],
     'resident_registration_number':['RRN','RESIDENT_REGISTRATION_NUMBER','RESIDENT_NUMBER','RESIDENT_ID','KOREAN_RRN','JUMIN'],
     'account_number':['ACCOUNT','ACCOUNT_NUMBER','ACCOUNT_NUM','BANK_ACCOUNT','BANK_ACCOUNT_NUMBER'],
     'card_number':['CARD','CARD_NUMBER','CARD_NUM','CREDIT_CARD','CREDIT_CARD_NUMBER','DEBIT_CARD','PAYMENT_CARD']}
    INV_ALIAS={x.upper():lab for lab,arr in ALIAS.items() for x in arr}
    def map_ax_label(tag):
        s=str(tag).strip().upper().replace(' ','_').replace('-','_')
        return INV_ALIAS.get(re.sub(r'^(B|I|E|S)_','',s))
    assert set(map_ax_label(x) for x in ax_net.config.id2label.values())- {None}==set(LABELS)

    def decode_bio(text,offsets,tags,origin=0):
        spans=[];cur=None
        for (s,e),raw in zip(offsets,tags):
            if int(s)>=int(e):continue
            tag=str(raw)
            if tag=='O' or tag=='':
                if cur:spans.append(cur);cur=None
                continue
            m=re.match(r'^([BIES])[-_](.+)$',tag)
            if not m:
                if cur:spans.append(cur);cur=None
                continue
            part,raw_lab=m.groups();lab=map_ax_label(raw_lab)
            if lab is None:
                if cur:spans.append(cur);cur=None
                continue
            st,en=origin+int(s),origin+int(e)
            if part=='S':
                if cur:spans.append(cur);cur=None
                spans.append({'label':lab,'start':st,'end':en})
            elif part=='B' or not cur or cur['label']!=lab or st>cur['end']+1:
                if cur:spans.append(cur)
                cur={'label':lab,'start':st,'end':en}
                if part=='E':spans.append(cur);cur=None
            else:
                cur['end']=en
                if part=='E':spans.append(cur);cur=None
        if cur:spans.append(cur)
        return [x for x in spans if 0<=x['start']<x['end']<=len(text)]

    def chunk_text(text,max_chars=350):
        results=[];origin=0;buf=''
        for line in text.splitlines(keepends=True):
            if buf and len(buf)+len(line)>max_chars:
                results.append((origin,buf));origin+=len(buf);buf=''
            if len(line)>max_chars:
                if buf:results.append((origin,buf));origin+=len(buf);buf=''
                for i in range(0,len(line),max_chars):results.append((origin+i,line[i:i+max_chars]))
                origin+=len(line)
            else:buf+=line
        if buf:results.append((origin,buf))
        return results

    @torch.inference_mode()
    def predict_ax(text,device=None):
        device=device or DEVICE;spans=[]
        for shift,piece in chunk_text(text):
            enc=ax_tok(piece,return_offsets_mapping=True,return_tensors='pt',truncation=True,max_length=512)
            offsets=enc.pop('offset_mapping')[0].tolist()
            allowed=set(inspect.signature(ax_net.forward).parameters)
            batch={k:v.to(device) for k,v in enc.items() if k in ('input_ids','attention_mask') or (k=='token_type_ids' and k in allowed)}
            logits=ax_net(**batch).logits[0]
            tags=[ax_net.config.id2label[int(x)] for x in logits.argmax(dim=-1).tolist()]
            spans.extend(decode_bio(text,offsets,tags,shift))
        unique={(s['label'],s['start'],s['end']):s for s in spans}
        return sorted(unique.values(),key=lambda e:(e['start'],e['end'],e['label']))

    print('A.X 파인튜닝 최고 모델 로딩 확인:',sum(p.numel() for p in ax_net.parameters()),'parameters')
    # ===== 5. Veil 공식 디코더 (03C 원본 그대로) =====
    from transformers import AutoModelForTokenClassification
    import gc
    veil_tok=load_fast_tokenizer(VEIL_DIR)
    veil_net=AutoModelForTokenClassification.from_pretrained(str(VEIL_DIR),use_safetensors=True,local_files_only=True)
    assert veil_net.config.num_labels==129,('Veil 출력층 예상 129',veil_net.config.num_labels)

    spec=importlib.util.spec_from_file_location('veil_eval_runtime',str(VEIL_DIR/'veil.py'))
    veil_lib=importlib.util.module_from_spec(spec);spec.loader.exec_module(veil_lib)
    Veil=veil_lib.Veil
    V_MAP={'PERSON':'person','PHONE':'phone_number','EMAIL':'email','ADDRESS':'address',
           'RRN':'resident_registration_number','ACCOUNT_NUMBER':'account_number','CARD_NUMBER':'card_number'}
    assert set(V_MAP.values())==set(LABELS)

    class LogitsOnly(torch.nn.Module):
        def __init__(self,net):super().__init__();self.net=net
        def forward(self,input_ids,attention_mask):
            return self.net(input_ids=input_ids,attention_mask=attention_mask).logits

    ONNX_FILE=TMP/'veil_fp32_finetuned.onnx'
    veil_net.eval().cpu()
    example=veil_tok('김민수 전화번호 010-1234-5678',return_tensors='pt')
    ids,mask=example['input_ids'],example['attention_mask']
    wrapper=LogitsOnly(veil_net).eval()
    with torch.no_grad():
        torch.onnx.export(wrapper,(ids,mask),str(ONNX_FILE),input_names=['input_ids','attention_mask'],
            output_names=['logits'],dynamic_axes={'input_ids':{0:'batch',1:'seq'},
            'attention_mask':{0:'batch',1:'seq'},'logits':{0:'batch',1:'seq'}},
            opset_version=17,do_constant_folding=True,**({'dynamo':False} if 'dynamo' in inspect.signature(torch.onnx.export).parameters else {}))
        pt=wrapper(ids,mask).numpy()
    sess=onnxruntime.InferenceSession(str(ONNX_FILE),providers=['CPUExecutionProvider'])
    ort=sess.run(None,{'input_ids':ids.numpy(),'attention_mask':mask.numpy()})[0]
    max_error=float(np.max(np.abs(pt-ort)))
    assert max_error<0.02,f'Veil Export logits mismatch: {max_error}'
    del sess,wrapper,veil_net;gc.collect()
    veil_detector=Veil(str(ONNX_FILE),tokenizer_dir=str(VEIL_DIR),threads=4,merge_adjacent=())

    def normalize_spans(spans,text):
        valid=[];seen=set()
        for e in spans:
            lab=e['label'];s=int(e['start']);en=int(e['end'])
            if lab not in LABELS or not (0<=s<en<=len(text)):raise ValueError(('잘못된 Span',lab,s,en))
            key=(lab,s,en)
            if key not in seen:valid.append({'label':lab,'start':s,'end':en});seen.add(key)
        return sorted(valid,key=lambda e:(e['start'],e['end'],e['label']))

    def predict_veil(text):
        raw=veil_detector.predict(text)
        mapped=[{'label':V_MAP[p['label'].upper()],'start':int(p['start']),'end':int(p['end'])}
                for p in raw if p['label'].upper() in V_MAP]
        return normalize_spans(mapped,text)
    print('Veil FP32 공식 디코더 초기화 · PyTorch/ONNX 최대 로짓 차이:',round(max_error,6))
    def token_guard(piece):
        ax_len=len(ax_tok.encode(piece,add_special_tokens=True,truncation=False))
        veil_len=len(veil_tok.encode(piece,add_special_tokens=True,truncation=False))
        return ax_len<=512 and veil_len<=512

    return predict_ax, predict_veil, token_guard
