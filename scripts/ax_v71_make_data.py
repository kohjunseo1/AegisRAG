#!/usr/bin/env python3
"""V7.1 seven-type pilot data builder. Depends on existing ax_v7_make_data.py.

Reuses reviewed V7 Korean-name/address supervision, adds independent English
names, mixed records, and formatted numbers. No A/B/Hard403 file is read.
Train/Dev are synthetic developmental data, NOT blinded evaluation evidence.
"""
from __future__ import annotations
import argparse, hashlib, json, random, re, statistics
from collections import Counter
from pathlib import Path
import ax_v7_make_data as v7

TYPES = ('person','address','phone_number','account_number','card_number',
         'resident_registration_number','email')
ROMAN = {'김':'KIM','박':'PARK','이':'LEE','최':'CHOI','정':'JUNG','조':'CHO',
         '강':'KANG','윤':'YOON','임':'LIM','한':'HAN','신':'SHIN','오':'OH',
         '권':'KWON','남궁':'NAMGUNG','황보':'HWANGBO'}
GIVEN = {'서준':'SEOJUN','도윤':'DOYUN','민서':'MINSEO','지민':'JIMIN',
         '현서':'HYUNSEO','준서':'JUNSEO','지현':'JIHYUN','수아':'SUA',
         '유진':'YUJIN','채린':'CHAERIN','예지':'YEJI','나연':'NAYEON',
         '재민':'JAEMIN','지호':'JIHO','태윤':'TAEYOON','서연':'SEOYEON',
         '하윤':'HAYOON','시우':'SIWOO','소윤':'SOYOON','민준':'MINJUN'}
FIELDS_T = ('인적사항 중', '신규 상담 기록의', '계약 변경 신청의', '금융 고객 자료의')
FIELDS_D = ('상담 이관 기록상', '요청 서류에서 확인된', '접수자 검토표의', '보험 업무 확인란의')
LABEL_CUES = {
    'person': ('이름','본인 성명','고객 영문명'),
    'address': ('상세 주소','우편물 발송 주소'),
    'phone_number': ('연락처','고객 전화번호','휴대전화'),
    'account_number': ('수취 계좌번호','입금계좌','계좌 정보'),
    'card_number': ('결제 카드번호','본인 카드번호','카드 확인 정보'),
    'resident_registration_number': ('주민등록번호','주민번호 확인값'),
    'email': ('이메일 주소','회신용 메일'),
}
TRAIN_ENDINGS = ('내부 검토 후 접수 상태를 갱신합니다.','관련 문서는 신청서와 함께 확인합니다.',
                 '이후 처리 단계는 검토 결과에 따라 결정합니다.')
DEV_ENDINGS = ('다음 담당자가 자료를 다시 대조합니다.','문서 확인 후 안내 여부를 결정합니다.',
               '요청한 변경 내용은 별도의 절차로 확인합니다.')
NEGATIVE = (('일반 항목 제목','주소'),('날씨 문장의 일반 명사','하늘'),
            ('간식 재료의 명칭','김'),('업무 조직 명칭','지원팀'),('제품명','나래알림'),
            ('접수 기록의 관리용 항목','문의번호'))


def pool(split):
    full=v7.NAME_POOLS[split]['full_name']
    out=[]
    for name in full:
        surname=next((x for x in sorted(ROMAN,key=len,reverse=True) if name.startswith(x)),None)
        if surname and name[len(surname):] in GIVEN:
            out.append((name,ROMAN[surname],GIVEN[name[len(surname):]]))
    if not out:raise ValueError('NO_ROMAN_NAME_POOL')
    return out


def roman(rng, split):
    ko,surname,given=rng.choice(pool(split))
    sep=rng.choice((' ','  ','-'))
    en=(surname+sep+given) if rng.random()<.5 else (given+sep+surname)
    return ko,en


def invalid_card(rng):
    base=rng.choice(('412962','532648','490723','548827'))+''.join(str(rng.randrange(10)) for _ in range(9))
    def check(x):
        vals=[int(c) for c in reversed(x)]
        return sum(((v*2-9) if v*2>9 else v*2) if i%2 else v for i,v in enumerate(vals))%10
    candidate=base+next(str(i) for i in range(10) if check(base+str(i)) != 0)
    sep=rng.choice(('-',' ','.',''))
    return sep.join(candidate[i:i+4] for i in (0,4,8,12))


def invalid_rrn(rng):
    source=f'{rng.randrange(100):02d}{rng.randrange(1,13):02d}{rng.randrange(1,29):02d}{rng.choice((1,2,3,4))}'
    source+=''.join(str(rng.randrange(10)) for _ in range(5))
    weights=(2,3,4,5,6,7,8,9,2,3,4,5)
    valid=(11-sum(int(x)*w for x,w in zip(source,weights))%11)%10
    number=source+str((valid+1)%10)
    return number[:6]+rng.choice(('-',' ',''))+number[6:]


def phone(rng):
    prefix=rng.choice(('010','010','010','02','031','051'))
    middle=rng.randrange(200,1000) if prefix=='02' else rng.randrange(1000,10000)
    sep=rng.choice(('-','.',' ',''))
    return f'{prefix}{sep}{middle}{sep}{rng.randrange(1000,10000)}'


def account(rng):
    p=rng.choice(('-',' ',''))
    return p.join((str(rng.randrange(100,999)),str(rng.randrange(1000,9999)),
                   str(rng.randrange(100000,999999))))


def address(rng, split):
    region, district, road = rng.choice((
        ('서울특별시','강남구','가람로'),('부산광역시','해운대구','푸른로'),
        ('대구광역시','달서구','나래길'),('경기도','성남시 분당구','새길로'),
        ('인천광역시','연수구','솔내로'),('충청남도','아산시','고운로')))
    num=rng.randrange(10,500) if split=='train' else rng.randrange(501,899)
    location=f'{region} {district} {road} {num}'
    if rng.random()<.6:location+=f' {rng.randrange(101,120)}동 {rng.randrange(201,906)}호'
    if rng.random()<.55:location=f'{rng.randrange(10000,99999)} {location}'
    return location


def make_value(rng, split, typ):
    if typ=='person':return roman(rng,split)[1]
    if typ=='address':return address(rng,split)
    if typ=='phone_number':return phone(rng)
    if typ=='account_number':return account(rng)
    if typ=='card_number':return invalid_card(rng)
    if typ=='resident_registration_number':return invalid_rrn(rng)
    if typ=='email':return f'{split[0]}{rng.randrange(10000,999999)}@example.test'
    raise ValueError(typ)


def synthetic_row(rng, split, i):
    classes=('person_roman','person_alias','address_zip','phone_number',
             'account_number','card_number','resident_registration_number',
             'email','mixed','mixed','negative')
    family=classes[i%len(classes)]
    text=f'{rng.choice(FIELDS_T if split=="train" else FIELDS_D)} / 업무 참조번호 Z-{rng.randrange(100000,999999)}\n'
    entities=[];verified=[]
    def add(term, label=None, negative=False):
        nonlocal text
        a=len(text);text+=term;b=len(text)
        if label:entities.append({'label':label,'start':a,'end':b})
        if negative:verified.append({'start':a,'end':b,'text':term})
    if family=='negative':
        cue,term=rng.choice(NEGATIVE)
        text+=f'분류 정보: {cue} [';add(term,negative=True)
        text+='] / 해당 내용은 업무 분류에 사용됩니다.\n'
    else:
        types=rng.sample(list(TYPES),rng.randrange(2,6)) if family=='mixed' else [
            'person' if family.startswith('person_') else
            'address' if family=='address_zip' else family]
        for typ in types:
            text+=rng.choice(LABEL_CUES[typ])+': '
            if family=='person_alias':
                korean,english=roman(rng,split)
                add(korean,'person');text+=' (영문 표기: ';add(english,'person');text+=')'
            else:add(make_value(rng,split,typ),typ)
            text+=rng.choice((' / 확인 필요\n','; 변경 요청\n','\n'))
    ends=TRAIN_ENDINGS if split=='train' else DEV_ENDINGS
    for _ in range(rng.choice((0,1,2,3,4,5))):text+=rng.choice(ends)+'\n'
    text+=rng.choice(ends)
    return dict(id=f'{split}_v71_extra_{i:05d}',text=text,entities=entities,
                verified_o_spans=verified,annotation_scope='seven_only',
                case_family=family,label_policy='v71_synthetic_pilot')


def norm(x):return re.sub(r'[^0-9a-zA-Z가-힣]','',x).upper()


def validate(train,dev):
    total=[]
    for name,rows in (('train',train),('dev',dev)):
        ids=set();texts=set();values={l:set() for l in TYPES}; labels=Counter()
        for row in rows:
            if row['id'] in ids or row['text'] in texts:raise ValueError('DUPLICATE_ID_OR_DOC')
            ids.add(row['id']);texts.add(row['text']);intervals=[]
            for e in row['entities']:
                a,b=e['start'],e['end'];lab=e['label']
                if lab not in TYPES or not (0<=a<b<=len(row['text'])):raise ValueError('INVALID_LABEL_OR_SPAN')
                intervals.append((a,b));values[lab].add(norm(row['text'][a:b]));labels[lab]+=1
            intervals.sort()
            if any(intervals[k][0]<intervals[k-1][1] for k in range(1,len(intervals))):
                raise ValueError('OVERLAPPING_GOLD')
            for e in row.get('verified_o_spans',[]):
                a,b=e['start'],e['end']
                if not 0<=a<b<=len(row['text']) or row['text'][a:b]!=e['text']:
                    raise ValueError('BAD_VERIFIED_O')
                if any(a<e_ and s<b for s,e_ in intervals):raise ValueError('VERIFIED_O_CONFLICT')
        if set(labels)!=set(TYPES):raise ValueError('MISSING_7_LABELS')
        total.append((ids,texts,values,labels,rows))
    left,right=total
    if left[1]&right[1]:raise ValueError('SPLIT_DOC_OVERLAP')
    for label in TYPES:
        if left[2][label]&right[2][label]:raise ValueError('SPLIT_PII_OVERLAP_'+label)
    return {name:{'docs':len(x[4]),'labels':dict(sorted(x[3].items())),
            'verified_O':sum(len(r.get('verified_o_spans',[])) for r in x[4]),
            'median_char_length':statistics.median(len(r['text']) for r in x[4])}
            for name,x in zip(('train','dev'),total)}


def generate(seed,n,split):
    rng=random.Random(seed)
    # Reviewed v7 examples give short full names, particles and lot/road address.
    old=v7.generate(seed,n//2,split)
    for row in old:
        row['id']='v71_'+row['id']
        row['label_policy']='v71_synthetic_pilot'
    new=[synthetic_row(rng,split,i) for i in range(n-len(old))]
    result=old+new;rng.shuffle(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--train-n',type=int,default=3200)
    p.add_argument('--dev-n',type=int,default=480)
    p.add_argument('--seed',type=int,default=7193)
    p.add_argument('--audit-only',action='store_true')
    args=p.parse_args()
    if args.train_n<200 or args.dev_n<100:p.error('train >=200 and dev >=100')
    out=args.out.expanduser().resolve()
    if args.audit_only:
        rows=[[json.loads(s) for s in (out/f'{name}.jsonl').read_text().splitlines() if s.strip()]
              for name in ('train','dev')]
    else:
        if out.exists() and any(out.iterdir()):raise ValueError('OUTPUT_DIRECTORY_NOT_EMPTY')
        train=generate(args.seed,args.train_n,'train')
        # Old curated address values are random; deterministically retry a dev
        # seed if a synthetic surface accidentally collides across splits.
        for attempt in range(24):
            dev=generate(args.seed+24311+attempt*7919,args.dev_n,'dev')
            try:
                validate(train,dev)
            except ValueError as exc:
                if str(exc).startswith(('SPLIT_PII_OVERLAP_','SPLIT_DOC_OVERLAP')):
                    continue
                raise
            rows=[train,dev]
            break
        else:
            raise ValueError('SPLIT_DIVERSITY_EXHAUSTED')
    report=validate(*rows)
    if not args.audit_only:
        out.mkdir(parents=True,exist_ok=True)
        for name,rs in zip(('train','dev'),rows):
            path=out/f'{name}.jsonl'
            with path.open('w',encoding='utf-8') as f:
                for r in rs:f.write(json.dumps(r,ensure_ascii=False)+'\n')
            path.chmod(0o600)
    print('V71_SYNTHETIC_AUDIT_PASS',json.dumps({'report':report,'dir':str(out),
          'gold_unchanged':True,'train_data_excludes_hard403':True,
          'independent_blind_holdout':False},sort_keys=True,ensure_ascii=False))

if __name__=='__main__':main()
