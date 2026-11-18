"""Local explicit-field ownership and conservative identity, separate from coverage."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, replace
import hashlib
import hmac
import re
from local_pseudonymizer import canonicalize, make_hmac_token
from dataset_preparation import key_tag as frozen_key_tag
from .contracts import ContractError, PersonMention, ProtectionPlan, fingerprint

IDENTITY_POLICY = 'anchor_conservative_v1'

@dataclass(frozen=True, repr=False)
class OwnedAnchor:
    key_id: str
    label: str
    owner_mention: str
    source_doc: str
    relation_type: str = 'EXPLICIT_FIELD_OWNER'
    evidence_strength: str = 'OWNED_VALIDATED_FIELD'
    valid_time_optional: str | None = None

@dataclass(frozen=True, repr=False)
class IdentityBundle:
    mentions: tuple[PersonMention, ...]
    anchors: tuple[OwnedAnchor, ...]
    key_tag: str
    policy_version: str = IDENTITY_POLICY

def _opaque_key(key, namespace, value):
    if type(key) is not bytes or len(key) < 32:
        raise ContractError('MISSING_OR_WEAK_KEY')
    payload = b'aegisrag:unified:identity:v1\x00' + namespace.encode() + b'\x00' + value.encode()
    return hmac.new(key,payload,hashlib.sha256).hexdigest()[:24]

def pseudonym_token(key,label,value):
    # No rewritten canonicalizer, prefix or token length.
    return make_hmac_token(key,label,value)[0]

def _valid_attribute(label, value):
    norm=canonicalize(label,value)
    if label=='phone_number':
        return bool(re.fullmatch(r'0[0-9]{8,10}',norm))
    if label=='email':
        return bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[A-Za-z]{2,}',norm))
    if label=='account_number':
        return bool(re.fullmatch(r'[0-9]{8,16}',norm))
    if label=='resident_registration_number':
        if not re.fullmatch(r'[0-9]{13}',norm):return False
        check=(11-sum(int(v)*w for v,w in zip(norm[:12],[2,3,4,5,6,7,8,9,2,3,4,5]))%11)%10
        return int(norm[-1])==check
    return False

def extract_owned_fields(text, plan: ProtectionPlan, *, doc_id, hmac_key, key_tag):
    if (plan.original_input_fingerprint != fingerprint(text) or plan.review_reason_codes
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',doc_id)
            or type(hmac_key) is not bytes or len(hmac_key) < 32 or key_tag != frozen_key_tag(hmac_key)):
        raise ContractError('IDENTITY_INPUT_NOT_VALIDATED')
    names = sorted((e for e in plan.entities_for_hmac if e.label == 'person'),key=lambda e:e.start)
    attrs = [e for e in plan.entities_for_hmac if e.label in {'phone_number','email','resident_registration_number','account_number'}]
    mentions, anchors = [], []
    # Field ownership only: a name field followed by contiguous labeled attribute lines.
    field_names = {'phone_number':{'연락처','전화','전화번호'},'email':{'이메일','email'},
                   'resident_registration_number':{'주민번호','주민등록번호'},'account_number':{'계좌','계좌번호'}}
    for number,name in enumerate(names):
        mid='m_'+_opaque_key(hmac_key,'mention',f'{doc_id}:{name.start}:{name.end}')
        lookup='n_'+_opaque_key(hmac_key,'name_lookup',canonicalize('person',text[name.start:name.end]))
        owned=[]
        line_start=text.rfind('\n',0,name.start)+1
        line_end=text.find('\n',name.end)
        if line_end < 0:line_end=len(text)
        prefix=text[line_start:name.start]
        suffix=text[name.end:line_end]
        explicit=bool(re.fullmatch(r'\s*(?:고객명|성명|이름)\s*[:：]\s*',prefix) and not suffix.strip())
        if explicit:
            position=line_end+1
            while position < len(text):
                end=text.find('\n',position)
                if end < 0:end=len(text)
                line=text[position:end]
                matched=re.fullmatch(r'\s*([가-힣A-Za-z]+)\s*[:：]\s*(.+?)\s*',line)
                if not matched:break
                label=next((lab for lab,labels in field_names.items() if matched[1] in labels),None)
                if label is None:break
                a,b=position+matched.start(2),position+matched.end(2)
                attr=next((e for e in attrs if e.label==label and e.start==a and e.end==b),None)
                if attr and _valid_attribute(label,text[a:b]):
                    kid=pseudonym_token(hmac_key,label,text[a:b])
                    owned.append(kid)
                    anchors.append(OwnedAnchor(kid,label,mid,doc_id))
                position=end+1
        mentions.append(PersonMention(mid,doc_id,lookup,tuple(owned),
                                      ('EXPLICIT_FIELD_OWNER',) if owned else ('NO_OWNERSHIP_PROOF',)))
    return IdentityBundle(tuple(mentions),tuple(anchors),key_tag)

def resolve(bundles, *, hmac_key, key_tag):
    if (type(hmac_key) is not bytes or len(hmac_key) < 32 or key_tag != frozen_key_tag(hmac_key)
            or any(b.key_tag != key_tag or b.policy_version != IDENTITY_POLICY for b in bundles)):
        raise ContractError('IDENTITY_VERSION_OR_KEY_MISMATCH')
    mentions=[m for b in bundles for m in b.mentions]
    anchors=[a for b in bundles for a in b.anchors]
    if len({m.mention_id for m in mentions}) != len(mentions):
        raise ContractError('DUPLICATE_MENTION')
    by_owner=defaultdict(list)
    owner_names={m.mention_id:m.name_lookup_key for m in mentions}
    names_per_anchor=defaultdict(set)
    for a in anchors:
        if a.owner_mention not in owner_names or a.relation_type != 'EXPLICIT_FIELD_OWNER' or a.evidence_strength != 'OWNED_VALIDATED_FIELD':
            raise ContractError('INVALID_ANCHOR_OWNERSHIP')
        by_owner[a.owner_mention].append(a)
        names_per_anchor[a.key_id].add(owner_names[a.owner_mention])
    shared={key for key,names in names_per_anchor.items() if len(names)>1}
    groups=defaultdict(list)
    for m in mentions:
        own=by_owner[m.mention_id]
        if set(m.attribute_key_ids) != {a.key_id for a in own}:
            raise ContractError('ANCHOR_LIST_MISMATCH')
        by_label=defaultdict(set)
        for a in own:
            by_label[a.label].add(a.key_id)
        # At least two independent types; shared anchors and multiple values refuse linkage.
        if (m.name_lookup_key and len(by_label)>=2 and all(len(v)==1 for v in by_label.values())
                and not shared.intersection(m.attribute_key_ids)):
            signature=tuple(sorted((label,next(iter(keys))) for label,keys in by_label.items()))
            groups[(m.name_lookup_key,signature)].append(m)
    linked={}
    for (name,signature),members in groups.items():
        if len(members)<2 or len({m.doc_id for m in members}) != len(members):continue
        # Refuse even otherwise matching subgroup if same-name evidence elsewhere conflicts.
        expected=dict(signature)
        same_name=[m for m in mentions if m.name_lookup_key==name]
        conflict=any(a.label in expected and a.key_id!=expected[a.label]
                     for m in same_name for a in by_owner[m.mention_id])
        if conflict:continue
        eid='e_'+_opaque_key(hmac_key,'person_entity',name+repr(signature))
        for m in members:linked[m.mention_id]=eid
    return tuple(replace(m,entity_id=linked[m.mention_id],link_status='LINKED')
                 if m.mention_id in linked else replace(m,entity_id=None,link_status='UNRESOLVED')
                 for m in mentions)
