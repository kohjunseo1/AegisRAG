"""Aggregate same-input protection comparison. Never export text/Gold/spans."""
from .contracts import ContractError,LABELS

def protection_metrics(plan,gold):
    if any(g['label'] not in LABELS or type(g['start']) is not int or type(g['end']) is not int
           or not 0<=g['start']<g['end'] for g in gold):raise ContractError('INVALID_GOLD_SPAN')
    strict={(c.label,c.start,c.end) for c in plan.strict_entities}
    hmac_keys={(c.label,c.start,c.end) for c in plan.entities_for_hmac}
    candidates=strict|hmac_keys|{(c.label,c.start,c.end) for c in plan.unresolved_mentions}
    expected={(g['label'],g['start'],g['end']) for g in gold}
    tp=len(strict&expected);fp=len(strict-expected);fn=len(expected-strict)
    protected={i for a,b in plan.mask_intervals for i in range(a,b)}
    goldchars={i for _,a,b in expected for i in range(a,b)}
    return dict(strict_f1=2*tp/(2*tp+fp+fn) if tp+fp+fn else 1.0,
                strict_fp=fp,strict_fn=fn,gold_entities=len(expected),
                protected_gold_chars=len(protected&goldchars),gold_chars=len(goldchars),
                character_protection_rate=len(protected&goldchars)/len(goldchars) if goldchars else 1.0,
                completely_missed=sum(not protected.intersection(range(a,b)) for _,a,b in expected),
                hmac_confirmation_rate=len(hmac_keys)/len(candidates) if candidates else None,
                hmac_rate_denominator='UNIQUE_TYPED_AND_UNRESOLVED_CANDIDATES',
                protection_status=plan.protection_status,
                gate_state='BLOCKED' if plan.protection_status=='BLOCKED' else 'REVIEW' if plan.review_reason_codes else 'READY')

def compare_plans(old,new,gold, *, dataset_role='synthetic'):
    return dict(dataset_role=dataset_role,old=protection_metrics(old,gold),new=protection_metrics(new,gold),
                source='SAME_INPUT_RAW_CANDIDATES',latency='NOT_MEASURED')
