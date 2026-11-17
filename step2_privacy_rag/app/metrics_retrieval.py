"""Evaluation of retrieved document IDs; never touches source texts or DB.

Legacy-compatible scoring ignores neutral documents *while scoring* but also
reports actual unfiltered top-5 used by the production chatbot.
"""
from __future__ import annotations
import math
from statistics import median


def percentile(values, p: float):
    """Nearest-rank percentile (ms); None when there are no measurements."""
    if not values:
        return None
    if not 0 < p <= 100:
        raise ValueError('INVALID_PERCENTILE')
    s = sorted(values)
    return round(s[max(0, math.ceil(len(s) * p / 100) - 1)], 2)


def summarize_score(queries: list[dict], gold: list[dict],
                    index_ids: set[str], ranked_by_qid: dict[str, list[str]]) -> dict:
    ids = {q['qid'] for q in queries}
    if len(ids) != len(queries):
        raise ValueError('DUPLICATE_QID')
    labels = {g['qid']: g for g in gold}
    if len(labels) != len(gold) or set(labels) != ids:
        raise ValueError('GOLD_QID_MISMATCH')
    counters = dict(total=len(queries), eligible=0, deferred=0,
                    no_indexable_gold=0, hit1=0, hit5=0,
                    online_hit1=0, online_hit5=0, confusable_before_gold=0,
                    confusable_hit1=0,
                    confusable_hit5=0, confusable_eligible=0)
    recall = []; recall_full = []; mrr = []; conf_rr = []
    by_type = {}
    for q in queries:
        qid = q['qid']
        g = labels[qid]
        relevant = set(g['relevant_doc_ids'])
        confusable = set(g.get('confusable_doc_ids', []))
        neutral = set(g.get('neutral_doc_ids', []))
        if q['text'] is None:
            counters['deferred'] += 1
            continue
        if not relevant.intersection(index_ids):
            counters['no_indexable_gold'] += 1
            continue
        if qid not in ranked_by_qid:
            raise ValueError('MISSING_QUERY_RANKING')
        raw = ranked_by_qid[qid]
        if len(raw) != len(set(raw)) or not set(raw).issubset(index_ids):
            raise ValueError('INVALID_RANKED_IDS')
        # The effective online top-5 never removes neutral docs.
        raw5 = raw[:5]
        counters['online_hit1'] += int(bool(raw and raw[0] in relevant))
        counters['online_hit5'] += int(bool(set(raw5) & relevant))
        first_gold_raw = next((i for i,d in enumerate(raw,1) if d in relevant), None)
        first_confusable_raw = next((i for i,d in enumerate(raw,1) if d in confusable), None)
        if (first_confusable_raw is not None and first_gold_raw is not None
                and first_confusable_raw < first_gold_raw):
            counters['confusable_before_gold'] += 1
        has_confusable = bool(confusable & index_ids)
        counters['confusable_eligible'] += has_confusable
        ch1 = int(bool(raw[:1] and raw[0] in confusable))
        ch5 = int(bool(set(raw5) & confusable))
        counters['confusable_hit1'] += ch1
        counters['confusable_hit5'] += ch5
        conf_rr.append(next((1 / i for i, d in enumerate(raw, 1) if d in confusable), 0.0))
        # Historical metrics excluded neutral IDs at scoring time only.
        scored = [d for d in raw if d not in neutral]
        h1 = int(bool(scored and scored[0] in relevant))
        h5 = int(bool(set(scored[:5]) & relevant))
        counters['eligible'] += 1
        counters['hit1'] += h1
        counters['hit5'] += h5
        recall.append(len(set(scored[:5]) & relevant) / len(relevant & index_ids))
        recall_full.append(len(set(scored[:5]) & relevant) / len(relevant))
        mrr.append(next((1 / i for i, d in enumerate(scored, 1) if d in relevant), 0.0))
        kind = str(g.get('type', 'unknown'))
        if kind not in by_type:
            by_type[kind] = dict(n=0, hit1=0, hit5=0)
        by_type[kind]['n'] += 1
        by_type[kind]['hit1'] += h1
        by_type[kind]['hit5'] += h5
    n = counters['eligible']
    if n == 0:
        raise ValueError('NO_EVALUABLE_QUERIES')
    return {
        **counters,
        'hit1_rate': round(counters['hit1']/n, 4),
        'hit5_rate': round(counters['hit5']/n, 4),
        # Include deferred/unindexable queries as no-hit for full 88-question
        # system coverage. Do not compare these with eligible-only metrics.
        'online_hit1_rate': round(counters['online_hit1']/n, 4),
        'online_hit5_rate': round(counters['online_hit5']/n, 4),
        'online_full_set_hit1_rate': round(counters['online_hit1']/len(queries), 4),
        'online_full_set_hit5_rate': round(counters['online_hit5']/len(queries), 4),
        'confusable_before_gold_rate': round(counters['confusable_before_gold']/n,4),
        'full_set_hit1_rate': round(counters['hit1']/len(queries), 4),
        'full_set_hit5_rate': round(counters['hit5']/len(queries), 4),
        'full_set_mrr': round(sum(mrr)/len(queries), 4),
        'query_coverage_rate': round(n/len(queries), 4),
        'recall5_indexable': round(sum(recall)/n, 4),
        'recall5_full_gold': round(sum(recall_full)/n, 4),
        'mrr': round(sum(mrr)/n, 4),
        # Lower is better. These are unfiltered production top-5 confusions.
        'confusable_at1_rate': round(counters['confusable_hit1']/n, 4),
        'confusable_at5_rate': round(counters['confusable_hit5']/n, 4),
        'confusable_at5_given_candidate': (
            round(counters['confusable_hit5']/counters['confusable_eligible'], 4)
            if counters['confusable_eligible'] else None),
        'confusable_mrr': round(sum(conf_rr)/n, 4),
        'by_type': {k: {**v, 'hit1_rate': round(v['hit1']/v['n'],4),
                                'hit5_rate': round(v['hit5']/v['n'],4)}
                    for k, v in by_type.items()},
        'note': 'Hit/Recall/MRR exclude neutral IDs only in scoring; confusable metrics use actual unfiltered production rankings.'
    }


def summarize_latency(rows: list[dict[str, float]]) -> dict:
    if not rows:
        return {}
    out = {}
    fields = ('total_ms', 'step1_ms', 'embedding_ms', 'db_ms', 'rerank_ms', 'hcx_ms')
    for field in fields:
        vals = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
        if not vals:
            continue
        out[field] = {'n': len(vals), 'mean': round(sum(vals)/len(vals),2),
                      'p50': round(median(vals),2),
                      'p95': percentile(vals,95),
                      'max': round(max(vals),2)}
    return out


def failure_cases(queries: list[dict], gold: list[dict], ranked_by_qid: dict[str, list[str]],
                  index_ids: set[str]) -> list[dict]:
    """QID/type/rank summaries only. No source text or HMAC data is serialized."""
    label = {g['qid']: g for g in gold}
    output = []
    for q in queries:
        g = label[q['qid']]
        reference = set(g.get('relevant_doc_ids', []))
        confusable = set(g.get('confusable_doc_ids', []))
        if q['text'] is None:
            output.append({'qid':q['qid'], 'type':g.get('type','unknown'), 'reason':'DEFERRED'})
            continue
        if not reference.intersection(index_ids):
            output.append({'qid':q['qid'], 'type':g.get('type','unknown'), 'reason':'NO_INDEXABLE_GOLD'})
            continue
        raw = ranked_by_qid[q['qid']]
        # Keep both ranks before any neutral-doc filtering, as actually seen by UI.
        gold_rank = next((i for i,d in enumerate(raw,1) if d in reference),None)
        confuse_rank = next((i for i,d in enumerate(raw,1) if d in confusable),None)
        scored = [d for d in raw if d not in set(g.get('neutral_doc_ids', []))]
        hit1 = bool(scored and scored[0] in reference)
        hit5 = bool(set(scored[:5]) & reference)
        conf_at5 = bool(set(raw[:5]) & confusable)
        if hit1 and not conf_at5:
            continue
        output.append({'qid':q['qid'], 'type':g.get('type','unknown'),
                       'reason': 'MISS_TOP5' if not hit5 else
                                 ('MISS_TOP1' if not hit1 else 'CONFUSABLE_IN_TOP5'),
                       'hit1':hit1, 'hit5':hit5, 'confusable_at5':conf_at5,
                       'first_gold_rank_raw':gold_rank, 'first_confusable_rank_raw':confuse_rank})
    return output
