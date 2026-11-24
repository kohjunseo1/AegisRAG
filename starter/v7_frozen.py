"""Generated from frozen V7 sources by tools/vendor_v7.py; no model or Gold data."""
from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Iterable

SOURCE_SHA256 = {'scripts/ax_v7_train.py': 'fcf00acf514e18b07787210459d740a0162b507cd4b652f9988a52417282bacb', 'scripts/ax_v7_eval.py': '77624c6bf43c07e53cab545e380b460c97de630ccd5aee985867b68f1ebf84ee', 'scripts/v7_chunk_ablation_v2.py': 'ed9d90c180d9d4c6e4bacf1a81cd2b046eabe80af07563c12b34af5f2219139e', 'step1_pii_detector/src/pii_detector/detector.py': '7068847cf7cd673acee07a48eeb80c79d012400a4ad710ee212590f8280abee0', 'step1_pii_detector/src/pii_detector/structural_policy.py': '5cbd304e1a14fc2756c1b3d120a91a9a632214485fe4002d806e86183bb12872', 'scripts/v7_final_postpolicy_probe.py': '4495b13ceae0b10049144546805174d8b38c1277c2ec3696d67c41b515cccd2c', 'scripts/v7_exit_gate.py': 'b8d915a1e0e8e3d83ccf35a6506b9c07c95becc5cdc2d2f9b735ff0881ecf586', 'scripts/v7_structural_fp_ablation.py': 'bead6f181eb2f1de09109c5748fe5201b5b80b488c914be0652a6470653e8421'}
# Source: scripts/ax_v7_train.py

ALIASES = {
    "person": ("PERSON", "NAME", "FULL_NAME", "KOREAN_NAME", "PERSON_NAME", "PRIVATE_PERSON"),
    "phone_number": ("PHONE", "PHONE_NUMBER", "PHONE_NUM", "MOBILE", "MOBILE_PHONE", "TELEPHONE", "PRIVATE_PHONE"),
    "email": ("EMAIL", "E_MAIL", "EMAIL_ADDRESS", "PRIVATE_EMAIL"),
    "address": ("ADDRESS", "STREET_ADDRESS", "HOME_ADDRESS", "POSTAL_ADDRESS", "PRIVATE_ADDRESS"),
    "resident_registration_number": ("RRN", "RESIDENT_REGISTRATION_NUMBER", "RESIDENT_NUMBER", "RESIDENT_ID", "KOREAN_RRN", "JUMIN"),
    "account_number": ("ACCOUNT", "ACCOUNT_NUMBER", "ACCOUNT_NUM", "BANK_ACCOUNT", "BANK_ACCOUNT_NUMBER"),
    "card_number": ("CARD", "CARD_NUMBER", "CARD_NUM", "CREDIT_CARD", "CREDIT_CARD_NUMBER", "DEBIT_CARD", "PAYMENT_CARD"),
}

def load_fast_tokenizer(model_dir):
    """Match frozen Step1 backend compatibility for older A.X tokenizer_config."""
    import shutil
    import tempfile
    from transformers import AutoTokenizer, PreTrainedTokenizerFast
    try:
        tok = AutoTokenizer.from_pretrained(
            str(model_dir), use_fast=True, local_files_only=True)
    except ValueError as ex:
        if "TokenizersBackend" not in str(ex):
            raise
        try:
            tok = PreTrainedTokenizerFast.from_pretrained(
                str(model_dir), local_files_only=True)
        except Exception:
            with tempfile.TemporaryDirectory(prefix="ax-v7-tokenizer-") as tmp:
                tmp_dir=Path(tmp)
                for file in Path(model_dir).iterdir():
                    if file.is_file() and file.suffix not in (".safetensors",".bin"):
                        shutil.copy2(file,tmp_dir/file.name)
                config=tmp_dir/"tokenizer_config.json"
                if not config.is_file():
                    raise ValueError("TOKENIZER_COMPAT_CONFIG_MISSING")
                meta=json.loads(config.read_text(encoding="utf-8"))
                meta["tokenizer_class"]="PreTrainedTokenizerFast"
                config.write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8")
                tok=AutoTokenizer.from_pretrained(str(tmp_dir),use_fast=True,local_files_only=True)
    if not tok.is_fast:
        raise ValueError("FAST_TOKENIZER_REQUIRED_FOR_UNICODE_OFFSETS")
    return tok

# Source: scripts/ax_v7_eval.py

def model_label(tag):
    text=str(tag).strip().upper().replace(" ","_").replace("-","_")
    base=re.sub(r"^(B|I|E|S)_","",text)
    matches=[name for name,aliases in ALIASES.items() if base in aliases]
    if len(matches)>1:
        raise ValueError("AMBIGUOUS_LABEL_MAPPING")
    return matches[0] if matches else None

def decode(text, offsets, tags, origin):
    # Frozen A.X Step1 decode_bio semantics: same span union and B/I/E/S.
    spans=[];cur=None
    for (s,e),tag in zip(offsets,tags):
        if int(s)>=int(e): continue
        tag=str(tag)
        if tag in ("O",""):
            if cur:spans.append(cur);cur=None
            continue
        m=re.match(r"^([BIES])[-_](.+)$",tag)
        if not m:
            if cur:spans.append(cur);cur=None
            continue
        part,lab=m.groups()
        label=model_label(tag)
        if label is None:
            if cur:spans.append(cur);cur=None
            continue
        a,b=origin+int(s),origin+int(e)
        if part=="S":
            if cur:spans.append(cur);cur=None
            spans.append({"label":label,"start":a,"end":b})
        elif part=="B" or not cur or cur["label"]!=label or a>cur["end"]+1:
            if cur:spans.append(cur)
            cur={"label":label,"start":a,"end":b}
            if part=="E":spans.append(cur);cur=None
        else:
            cur["end"]=b
            if part=="E":spans.append(cur);cur=None
    if cur:spans.append(cur)
    return [x for x in spans if 0<=x["start"]<x["end"]<=len(text)]

# Source: scripts/v7_chunk_ablation_v2.py

def window_bounds(n:int,cap:int,overlap:int)->list[tuple[int,int]]:
    """Cover every token; keep last window truncated, never pad the text or truncate."""
    if cap<=0 or not 0<=overlap<cap:raise ValueError('BAD_WINDOW_SETTINGS')
    if n<=0:return []
    out=[];start=0
    while start<n:
        end=min(n,start+cap)
        out.append((start,end))
        if end==n:break
        start+=cap-overlap
    return out

def winning_ranges(windows:list[tuple[int,int]],n:int)->list[tuple[int,int]]:
    """Midpoint in the overlap; exactly one owner per token, including tails."""
    if not windows:return []
    seams=[]
    for (_,prev_end),(next_start,_) in zip(windows,windows[1:]):
        if next_start>prev_end:raise ValueError('WINDOW_GAP')
        seams.append((prev_end+next_start)//2)
    result=[]
    for i,(start,end) in enumerate(windows):
        left=0 if i==0 else seams[i-1]
        right=n if i==len(windows)-1 else seams[i]
        if left<start or right>end:raise ValueError('WINDOW_KEEP_OUTSIDE_INPUT')
        result.append((left,right))
    if sum(end-start for start,end in result)!=n:raise ValueError('NOT_ALL_TOKENS_OWNED')
    return result

def tokenizer_special_pair(tokenizer):
    """Infer the *actual fast-tokenizer* special IDs from encoded output.

    PreTrainedTokenizerFast may use a Rust post-processor to add CLS/SEP;
    build_inputs_with_special_tokens() can be inherited as a no-op. That is
    not an invalid tokenizer. Validate layout+offsets before constructing
    non-truncated token windows. Only support prefix/suffix one each.
    """
    probe = '김민수가 신청했습니다.'
    plain = tokenizer(probe, add_special_tokens=False, truncation=False,
                      return_offsets_mapping=True)
    full = tokenizer(probe, add_special_tokens=True, truncation=False,
                     return_special_tokens_mask=True, return_offsets_mapping=True)
    ids0, ids1 = plain['input_ids'], full['input_ids']
    expected_mask = [1] + [0]*len(ids0) + [1]
    if (len(ids1) != len(ids0)+2 or full.get('special_tokens_mask') != expected_mask
            or ids1[1:-1] != ids0 or
            full.get('offset_mapping',[None])[1:-1] != plain['offset_mapping']):
        raise ValueError('SPECIAL_TOKEN_LAYOUT_UNSUPPORTED_OR_OFFSET_PARITY_FAIL')
    return int(ids1[0]), int(ids1[-1])

def token_window_predict(text, tokenizer, model, torch, decoder, device,cap:int,overlap:int, special_pair=None):
    """Tokenize ONCE without truncation, infer windows, decode ONE BIO sequence.

    For apples-to-apples use max 512 total incl specials, not native ModernBERT 8192.
    Expected tokenizer form is [CLS] + ids + [SEP]; fail if not applicable.
    """
    if not text:return [],0
    enc=tokenizer(text,add_special_tokens=False,truncation=False,return_offsets_mapping=True)
    ids=enc['input_ids']; offsets=enc['offset_mapping']; n=len(ids)
    if len(offsets)!=n:raise ValueError('TOKEN_OFFSETS_LENGTH_MISMATCH')
    if n==0:return [],0
    if special_pair is None: special_pair=tokenizer_special_pair(tokenizer)
    windows=window_bounds(n,cap,overlap)
    owners=winning_ranges(windows,n)
    selected=[None]*n
    for (start,end),(keep_start,keep_end) in zip(windows,owners):
        piece=ids[start:end]
        inp=[special_pair[0],*piece,special_pair[1]]
        if len(inp)!=len(piece)+2 or len(inp)>512:
            raise ValueError('TOKENIZER_SPECIAL_TOKENS_OR_LENGTH_OVER_512')
        xt=torch.tensor([inp],dtype=torch.long,device=device)
        y=model(input_ids=xt,attention_mask=torch.ones_like(xt)).logits
        if tuple(y.shape[:2])!=(1,len(inp)):
            raise ValueError('MODEL_LOGITS_SHAPE_MISMATCH')
        labels=y[0,1:-1].argmax(dim=-1).tolist()
        if len(labels)!=end-start:raise ValueError('MODEL_TOKEN_COUNT_MISMATCH')
        for idx in range(keep_start,keep_end):
            if selected[idx] is not None:raise ValueError('DUPLICATE_TOKEN_OWNER')
            selected[idx]=labels[idx-start]
    if any(x is None for x in selected):raise ValueError('TOKEN_MISSING_PREDICTION')
    tags=[model.config.id2label[int(index)] for index in selected]
    return decoder(text,offsets,tags,0),len(windows)

# Source: step1_pii_detector/src/pii_detector/detector.py

LABELS = (
    'person','phone_number','email','address','resident_registration_number',
    'account_number','card_number',
)

ADDRESS_HEAD = re.compile(
    r'^(?:서울(?:특별시)?|부산(?:광역시)?|대구(?:광역시)?|인천(?:광역시)?|'
    r'광주(?:광역시)?|대전(?:광역시)?|울산(?:광역시)?|세종(?:특별자치시)?|'
    r'경기(?:도)?|강원(?:특별자치도|도)?|충북|충남|전북|전남|경북|경남|'
    r'제주(?:특별자치도|도)?|충청북도|충청남도|전라북도|전라남도|'
    r'경상북도|경상남도)'
)

PAREN_PREFIX = re.compile(r'(?<![A-Za-z0-9])\([0-9]{5}\)[ \t]{0,3}$')

BARE_PREFIX = re.compile(r'(?<![A-Za-z0-9])(?P<code>[0-9]{5})[ \t]{1,3}$')

NON_POSTAL_FIELD = re.compile(r'(?:상품|고객|접수|거래|주문|계약|카드|송장|사업자)\s*번호\s*[:：]?\s*$')

POSTAL_FIELD = re.compile(r'(?:우편번호|우편\s*코드|postal\s*code)\s*[:：#-]?\s*$', re.I)

ADDRESS_FIELD = re.compile(r'(?:배송지|주소|소재지|거주지|수취인\s*주소)\s*[:：]\s*$')

TRAILING_POSTAL = re.compile(r'^[ \t]{1,3}(?:(?:우편번호|우편\s*코드)\s*[:：]?\s*)?(?:\([0-9]{5}\)|[0-9]{5})(?![0-9])')

ROLE_CUE = re.compile(r'(?:심사역|담당자|직원|상담사|담당|고객)\s*$')

NEXT_CONTEXT = re.compile(r'^\s+(?:조건|문의|안내|처리|확인|신청|완료|등록|동의|심사)')

def span_key(e):
    return (str(e['label']), int(e['start']), int(e['end']))

def overlaps(a, b):
    return int(a['start']) < int(b['end']) and int(b['start']) < int(a['end'])

def normalize_spans(spans, text):
    seen = set()
    out = []
    for e in spans:
        lab, start, end = span_key(e)
        if lab not in LABELS or not (0 <= start < end <= len(text)):
            raise ValueError('invalid_label_or_offset')
        k = (lab, start, end)
        if k not in seen:
            seen.add(k)
            out.append({'label': lab, 'start': start, 'end': end})
    return sorted(out, key=lambda e: (e['start'], e['end'], e['label']))

def validate_boundaries(text, predictions):
    """Conservative postal span expansion. Uncertain strings generate review events, not auto-masking.

    Prefixed parenthesized 5 digits: apply with negative-field / overlap guards.
    Bare 5 digits before address: apply only with an explicit postal or address field.
    Trailing postcodes: review only, because appending them to an ADDRESS span can mask field labels.
    Name ending '이': review only. Never automatically remove a character from a PII span.
    """
    out = normalize_spans(predictions, text)
    events = []
    for i, e in enumerate(out):
        if e['label'] != 'address':
            continue
        a, b = e['start'], e['end']
        if not ADDRESS_HEAD.match(text[a:b]):
            continue
        before = text[max(0, a - 60):a]
        m = PAREN_PREFIX.search(before)
        mode = 'parenthesized' if m else None
        if m is None:
            m = BARE_PREFIX.search(before)
            if m is not None:
                mode = 'bare'
        if m is not None:
            new_a = max(0, a - 60) + m.start()
            earlier = text[max(0, new_a - 35):new_a]
            if NON_POSTAL_FIELD.search(earlier):
                events.append({'rule': 'postal', 'status': 'rejected_nonpostal_field',
                               'start': new_a, 'end': a})
            else:
                field_ok = bool(POSTAL_FIELD.search(earlier) or ADDRESS_FIELD.search(earlier))
                if mode == 'bare' and not field_ok:
                    events.append({'rule': 'postal', 'status': 'review_unlabeled_prefix',
                                   'start': new_a, 'end': a})
                else:
                    candidate = dict(e, start=new_a)
                    if any(overlaps(candidate, other) for j, other in enumerate(out) if j != i):
                        events.append({'rule': 'postal', 'status': 'review_overlap',
                                       'start': new_a, 'end': a})
                    else:
                        out[i] = candidate
                        events.append({'rule': 'postal', 'status': 'applied_' + mode,
                                       'old_start': a, 'start': new_a, 'end': b})
        # At end of a model-recognized address, a five-digit candidate is not blindly attached.
        after = text[b:min(len(text), b + 40)]
        suffix = TRAILING_POSTAL.match(after)
        if suffix is not None:
            events.append({'rule': 'postal', 'status': 'review_trailing_postcode',
                           'start': b + suffix.start(), 'end': b + suffix.end()})
    for e in out:
        if e['label'] != 'person':
            continue
        a, b = e['start'], e['end']
        name = text[a:b]
        if not (3 <= len(name) <= 5 and name.endswith('이')
                and len(name) >= 2 and '가' <= name[-2] <= '힣'
                and (ord(name[-2]) - 0xAC00) % 28 != 0):
            continue
        events.append({'rule': 'name_suffix', 'status': 'review_only',
                       'start': a, 'end': b,
                       'role_cue': bool(ROLE_CUE.search(text[max(0, a-16):a])),
                       'next_cue': bool(NEXT_CONTEXT.match(text[b:b+25]))})
    return normalize_spans(out, text), events

# Source: step1_pii_detector/src/pii_detector/structural_policy.py

TITLE = (r"(?:과장|대리|팀장|부장|차장|사원|주임|책임|선임|수석|대표|이사|"
         r"상무|전무|실장|본부장|센터장|매니저)")

SURNAME_TITLE_AFTER = re.compile(
    rf"^[ \t]+{TITLE}(?:님)?(?:에게|께서|께|은|는|이|가|을|를|과|와|도)?(?=$|[ \t\r\n,.;:!?)\]])"
)

SURNAMES = frozenset(
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민나진지엄채원천방공"
    "현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경사부가복태목형피두감음빈동온호"
)

EXTENSION_VALUE = re.compile(
    r"(?:\b내선(?:번호)?|\(\s*내선\s*\))\s*[:：]\s*([0-9 ]{2,8})(?=\s*$)"
)

def _key(e: dict, text: str) -> tuple[str, int, int]:
    if not isinstance(e, dict):
        raise ValueError("invalid_span_type")
    lab = e.get("label")
    if lab not in LABELS:
        raise ValueError("invalid_span_label")
    s, end = e.get("start"), e.get("end")
    if type(s) is not int or type(end) is not int or not 0 <= s < end <= len(text):
        raise ValueError("invalid_span_offset")
    return lab, s, end

def _line(text: str, start: int, end: int) -> tuple[str, int]:
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    return text[a:len(text) if b < 0 else b], a

def _standalone_surname_title(text: str, start: int, end: int) -> bool:
    word = text[start:end]
    if len(word) != 1 or word not in SURNAMES:
        return False
    # A character within a longer word is never a standalone surname.
    if start > 0 and "가" <= text[start-1] <= "힣":
        return False
    return SURNAME_TITLE_AFTER.match(text[end:end+28]) is not None

def _explicit_internal_extension(text: str, start: int, end: int) -> bool:
    val = text[start:end]
    if not val.isascii() or not val.isdecimal() or not 2 <= len(val) <= 6:
        return False
    line, origin = _line(text, start, end)
    match = EXTENSION_VALUE.search(line)
    if match is None:
        return False
    return origin + match.start(1) <= start and end <= origin + match.end(1)

def refine_spans(text: str, spans: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Return (strict entity spans, audit without source PII strings)."""
    if not isinstance(text, str):
        raise ValueError("invalid_text")
    out, audit, seen = [], [], set()
    for raw in spans:
        lab, start, end = _key(raw, text)
        key = (lab, start, end)
        if key in seen:
            continue
        seen.add(key)
        rule = None
        if lab == "person" and _standalone_surname_title(text, start, end):
            rule = "standalone_surname_title"
        elif lab == "phone_number" and _explicit_internal_extension(text, start, end):
            rule = "explicit_internal_extension"
        if rule:
            audit.append({"rule": rule, "action": "drop_strict_only", "label": lab,
                          "start": start, "end": end})
        else:
            out.append({"label": lab, "start": start, "end": end})
    return sorted(out, key=lambda e: (e["start"], e["end"], e["label"])), audit

def mask_union(original: Iterable[dict], revised: Iterable[dict], text: str) -> list[dict]:
    """Union coverage. Never unmask a character detected by the frozen baseline."""
    intervals = []
    for e in [*original, *revised]:
        _, s, en = _key(e, text)
        intervals.append((s, en))
    intervals.sort()
    merged = []
    for s, en in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], en))
        else:
            merged.append((s, en))
    return [{"start": s, "end": en} for s, en in merged]

# Source: scripts/v7_final_postpolicy_probe.py

def validate_spans(text, spans):
    validated=[]
    for e in spans:
        lab,a,b=e['label'],e['start'],e['end']
        if lab not in {'person','phone_number','email','address','resident_registration_number','account_number','card_number'} or type(a) is not int or type(b) is not int or not 0<=a<b<=len(text):
            raise ValueError('INVALID_SPAN')
        validated.append({'label':lab,'start':a,'end':b})
    return sorted({(e['label'],e['start'],e['end']):e for e in validated}.values(),key=lambda e:(e['start'],e['end'],e['label']))

# Source: scripts/v7_exit_gate.py

POSTAL_CODE = re.compile(r'^[0-9]{5}$')

POSTAL_GAP = re.compile(r'^\)?[ \t]{1,4}$')

def postal_merge(text, spans):
    """Merge two predicted ADDRESS spans if first is a standalone 5-digit Korean
    postcode, second starts with an administrative area, and only ')' + spaces
    separate the spans on same line. Does not consult Gold. Logs counts only.
    """
    spans=validate_spans(text,spans)
    consumed=set();new=[];events=[]
    for i,pc in enumerate(spans):
        if i in consumed:continue
        a,b=pc['start'],pc['end']
        code=text[a:b]
        if pc['label']!='address' or not POSTAL_CODE.fullmatch(code):continue
        if a>0 and text[a-1]=='(':
            left=a-1
        else:
            left=a
        if left>0 and text[left-1].isalnum():continue
        if NON_POSTAL_FIELD.search(text[max(0,left-35):left]):
            events.append({'rule':'postal_join','status':'review_nonpostal_field','start':left,'end':b})
            continue
        for j,ad in enumerate(spans):
            if j==i or j in consumed or ad['label']!='address' or ad['start']<b:continue
            if ad['start']-b>7:continue
            if not POSTAL_GAP.fullmatch(text[b:ad['start']]):continue
            if '\n' in text[left:ad['start']]:continue
            if not ADDRESS_HEAD.match(text[ad['start']:ad['end']]):continue
            candidate={'label':'address','start':left,'end':ad['end']}
            # no third-party entity may be swallowed by a merge
            if any(k not in (i,j) and candidate['start']<x['end'] and x['start']<candidate['end'] for k,x in enumerate(spans)):
                continue
            consumed.add(i);consumed.add(j);new.append(candidate)
            events.append({'rule':'postal_join','status':'merged','start':left,'end':ad['end']})
            break
    out=[s for i,s in enumerate(spans) if i not in consumed]+new
    return validate_spans(text,out),events

# Source: scripts/v7_structural_fp_ablation.py

STATUS_EVENT = re.compile(r'^(?:(?:미|비|불|재))?(?:수신|송신|발송|접수|전달|회신|확인|처리|완료|승인|납부|결제|입금|지급|응답|연결|등록|인증)$')

STATUS_CONTEXT = re.compile(r'(?:알림|안내|통지|문자|메시지|메일|이메일|연락|발송|청구|결제|지급|배송|신청|접수|처리|상태)\s*$')

PERSON_FIELD = re.compile(r'(?:성명|이름|고객명|담당자명|신청인|수취인|연락처명)\s*[:：]?\s*$')

PERSON_CONTINUATION = re.compile(r'^(?:님|씨|께서|에게|한테|고객님|고객|선생님|선생|담당자)(?=$|[^가-힣])')

MOURNING_PREFIX = re.compile(r'^\s*(?:\[\s*부고\s*\]|\[\s*訃告\s*\]|부고\s*[:：])')

JOB_TITLE_BEFORE = re.compile(r'(?:과장|대리|부장|차장|팀장|사원|주임|실장|이사|상무|전무|대표|책임|선임|수석|교수|원장|담당자|사무관|본부장|센터장)(?:님)?\s*$')

KINSHIP = re.compile(r'^(?:부친|모친|조부|조모|외조부|외조모|친조부|친조모|장인|장모|시부|시모|형제|자매|남편|아내|배우자|자녀|아들|딸|빙부|빙모)(?:상)$')

def _before_after(text: str, start: int, end: int):
    # The model span must align to a surface word and not be a substring of a longer word.
    if not (0 <= start < end <= len(text)):
        return None
    if start > 0 and '가' <= text[start-1] <= '힣':
        return None
    if end < len(text) and '가' <= text[end] <= '힣':
        return None
    return text[max(0,start-75):start], text[end:end+30]

def classify_nonperson(text: str, start: int, end: int) -> str | None:
    """Conservative PERSON-only negative evidence, no access to Gold/dataset ID.

    Obituary: role + kinship bereavement noun in obituary header, not a person.
    Status: nominal event after a topic word within a structured record,
    with no direct person-field/honorific cues.
    Unknown: retain original PERSON span (fail-safe) rather than force a drop.
    """
    if not (0 <= start < end <= len(text)):
        raise ValueError('BAD_SPAN')
    surface = text[start:end]
    evidence = _before_after(text,start,end)
    if not evidence:
        # Special case: partial prediction ending immediately before '상', e.g. 장인|상.
        if (end < len(text) and text[end]=='상' and (end+1==len(text) or not ('가' <= text[end+1] <= '힣'))
            and (start==0 or not ('가'<=text[start-1]<='힣'))):
            evidence=(text[max(0,start-75):start],text[end:end+30])
        else:
            return None
    before,after=evidence
    line_start=text.rfind('\n',0,start)+1
    first_break=text.find('\n',0)
    if first_break == -1: first_break=len(text)
    if start<first_break and MOURNING_PREFIX.match(text[:first_break]):
        rel_surface = surface
        if after.startswith('상'):
            rel_surface += '상'
        if KINSHIP.fullmatch(rel_surface) and JOB_TITLE_BEFORE.search(text[line_start:start]):
            if not PERSON_CONTINUATION.match(after):
                return 'bereavement_kinship_role'
    # A status phrase in a table/clause, not a person's name.
    if STATUS_EVENT.fullmatch(surface):
        segment = re.split(r'[|\n\r;]',before)[-1]
        # Must be in explicit event/status syntax, not just a standalone word.
        if STATUS_CONTEXT.search(segment) and not PERSON_FIELD.search(segment):
            if not PERSON_CONTINUATION.match(after):
                return 'event_status_nominal'
    return None

def apply_guard(text: str, spans: list[dict]) -> tuple[list[dict],list[dict]]:
    out=[];events=[]
    for e in spans:
        if e['label']!='person':
            out.append(e);continue
        reason=classify_nonperson(text,int(e['start']),int(e['end']))
        if reason:
            events.append({'label':'person','start':int(e['start']),'end':int(e['end']), 'reason':reason})
        else:out.append(e)
    return out,events
