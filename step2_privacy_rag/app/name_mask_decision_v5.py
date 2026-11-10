"""AegisRAG V5: read-only selective name-boundary protection study.

No gold, document ID, source text from a private dataset, or per-case exceptions are
used to decide. Does NOT change detector.status, release data, or make HMAC tokens.
This module purposely treats non-identity-linked masking as a *contract proposal*.
"""
from __future__ import annotations
from collections import Counter
import re
from name_boundary_policy_v3_1 import propose, baseline_masks, merge_intervals, _has_batchim
from name_mask_pareto_v4 import chars, valid_result, run_policy as v4_run

POLICIES = (
    'baseline',
    'v4_skip_short',
    'v5_minmask_abstain',
    'v5_grammar_abstain',
    'v5_privacy_first',
)

# Explicit *fields* are stronger evidence than morphology, but they are not a
# proof of identity or that two people with the same surface name are equal.
# Do not include generic roles like "담당자" without a colon: too noisy.
_FIELD_RE = re.compile(
    r'(?:^|[\n\r;|])\s*'
    r'(?:성명|고객\s*성명|고객명|이름|성함|예금주|신청인\s*이름|'
    r'담당\s*직원\s*성명)\s*[:：]\s*([가-힣]{2,6})'
    r'(?=$|[\s,;/|)])', re.M)


def _explicit_names(text: str) -> set[str]:
    return {m.group(1) for m in _FIELD_RE.finditer(text)}


def _particle_cue(text: str, cand: tuple[int, int]) -> bool:
    """Grammar signals *risk*, NEVER certifies that 이 is a particle.

    True terminal-이 names can be followed by the same surface sequence.
    Therefore this may only trigger VIRTUAL abstention, not auto-release.
    """
    s, end = cand
    if end - s < 3 or text[end-1] != '이' or not _has_batchim(text[end-2]):
        return False
    if end == len(text) or not text[end].isspace():
        return False
    return re.match(r'\s+[가-힣]{1,12}(?:\s|[,.!?]|$)', text[end:]) is not None


def _classify(text, p, explicit):
    s, t = p.candidate_full_span
    long_name, short_name = text[s:t], text[s:t-1]
    direct_long, direct_short = long_name in explicit, short_name in explicit
    inferred_long = p.action == 'EVIDENCE_LONG_PROPOSED'
    inferred_short = p.action == 'EVIDENCE_SHORT_PROPOSED'
    # A contrary explicitly filled field is stronger than a title occurrence,
    # but explicit evidence for both must always remain ambiguous.
    if direct_long and direct_short:
        return 'CONFLICT', 'explicit_conflict'
    if direct_long and not inferred_short:
        return 'LONG', 'explicit_long'
    if direct_short and not inferred_long:
        return 'SHORT', 'explicit_short'
    if (direct_long and inferred_short) or (direct_short and inferred_long):
        return 'CONFLICT', 'evidence_conflict'
    if inferred_long:
        return 'LONG', 'title_long'
    if inferred_short:
        return 'SHORT', 'title_short'
    return 'AMBIGUOUS', ('weak_particle_cue' if _particle_cue(text, (s,t))
                         else 'insufficient_evidence')


def decide(text: str, r: dict, policy: str) -> dict:
    """Returns safe counterfactual masking + virtual abstention, not authorization.

    Only candidate suffix chars outside existing masks are considered.
    If the interval overlaps a non-PERSON detected entity, no expansion is made
    and any READY item becomes virtual abstention rather than being presumed safe.
    """
    if policy not in POLICIES:
        raise ValueError('unknown_policy')
    valid_result(text, r)
    if policy in ('baseline','v4_skip_short'):
        x = v4_run(text,r,policy)
        return {**x, 'abstain_reasons':{}, 'decision_counts':{},
                'identity_auto_linked':0,'not_deployed':True}

    original = baseline_masks(text,r)
    original_chars = chars(original)
    extra=[]
    dropped=set()
    decisions=Counter(); reasons=Counter()
    explicit = _explicit_names(text)
    for p in propose(text,r):
        if p.protect_span is None:
            continue
        s,t=p.protect_span
        gap=set(range(s,t))-original_chars
        if not gap:
            continue
        if any(e['label']!='person' and s < e['end'] and e['start'] < t
               for e in r['entities']):
            dropped.update(gap)
            reasons['other_pii_overlap']+=1
            continue
        cls,why = _classify(text,p,explicit)
        decisions[f'{cls}:{why}']+=1
        if cls == 'LONG':
            extra.append((s,t))
        elif cls == 'SHORT':
            pass  # Valid independent evidence: don't swallow grammatical particles.
        elif policy=='v5_privacy_first':
            extra.append((s,t))
        elif policy=='v5_grammar_abstain' and not _particle_cue(text,(s,t)):
            extra.append((s,t))
        else:
            dropped.update(gap)
            reasons['unresolved_boundary']+=1

    masks=merge_intervals(original+extra,len(text))
    new=chars(masks)
    if not original_chars <= new:
        raise ValueError('baseline_protection_reduced')
    uncovered_candidate=dropped-new
    abstain = r['status']=='READY_FOR_PSEUDONYMIZATION' and bool(uncovered_candidate)
    if abstain:
        reasons['ready_virtual_abstention'] += 1
    return {
        'mask_intervals': masks,
        'added': new-original_chars,
        'would_abstain':abstain,
        'auto_eligible':r['status']=='READY_FOR_PSEUDONYMIZATION' and not abstain,
        'candidate_chars_deferred':len(uncovered_candidate),
        'abstain_reasons':dict(reasons),
        'decision_counts':dict(decisions),
        'identity_auto_linked':0,
        'not_deployed':True,
    }


def score(text: str, gold: list[tuple[int,int]], r: dict, group: str) -> dict:
    """Gold is supplied ONLY to evaluation; decide() has no gold arguments."""
    g=chars(gold)
    rows={}
    for p in POLICIES:
        x=decide(text,r,p)
        covered=chars(x['mask_intervals'])
        added=x['added']
        rows[p]={
            'gold_chars':len(g),
            'covered_gold':len(covered&g),
            'missing_gold':len(g-covered),
            'added_gold':len(added&g),
            'added_non_gold':len(added-g),
            'auto_eligible':int(x['auto_eligible']),
            'auto_uncovered':int(x['auto_eligible'] and bool(g-covered)),
            'virtual_abstain':int(x['would_abstain']),
            'auto_gold_chars':len(g) if x['auto_eligible'] else 0,
            'auto_covered_gold':len(g&covered) if x['auto_eligible'] else 0,
            'deferred_candidate_chars':x.get('candidate_chars_deferred',0),
        }
    return {'group':group,'policies':rows}


def summarize(rows:list[dict]) -> dict:
    out={}
    for p in POLICIES:
        r=[x['policies'][p] for x in rows]
        d={k:sum(v[k] for v in r) for k in (
            'gold_chars','covered_gold','missing_gold','added_gold','added_non_gold',
            'auto_eligible','auto_uncovered','virtual_abstain','auto_gold_chars',
            'auto_covered_gold','deferred_candidate_chars')}
        d['all_person_char_coverage']=round(d['covered_gold']/d['gold_chars'],5) if d['gold_chars'] else None
        d['auto_subset_person_char_coverage']=(round(d['auto_covered_gold']/d['auto_gold_chars'],5)
                                                 if d['auto_gold_chars'] else None)
        out[p]=d
    return out
