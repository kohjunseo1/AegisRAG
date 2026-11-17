"""Read-only retrieval alternatives; never invoke HCX, alter Gate or modify DB.

The frozen V6 order is the baseline. Alternatives operate ONLY within the
same verified HMAC/region tier, using pseudonymized text in local memory.
No Gold labels or QIDs are consulted by the rankers.
"""
from __future__ import annotations

from collections import Counter
import math
import re
import unicodedata

PROTECTED = re.compile(r"<[A-Z][A-Z0-9_]*_[0-9a-f]{24}>|\[REDACTED[A-Z0-9_]*\]")
NON_WORD = re.compile(r"[^가-힣a-z0-9]+")


def grams(text: str) -> frozenset[str]:
    """Character 2/3-grams, omitting identity HMACs and redaction markers."""
    plain = unicodedata.normalize("NFKC", PROTECTED.sub(" ", text)).lower()
    words = NON_WORD.sub(" ", plain).split()
    return frozenset(w[i:i+n] for w in words for n in (2, 3)
                     for i in range(max(0, len(w)-n+1)))


def make_lexical_index(rows: list[dict]) -> tuple[dict, dict]:
    """Build document frequencies from current masked candidates only."""
    by_id = {r["doc_id"]: grams(r["text"]) for r in rows}
    df = Counter(g for gset in by_id.values() for g in gset)
    idf = {g: math.log((len(rows)+1)/(n+1)) + 1 for g, n in df.items()}
    return by_id, idf


def lexical_coverage(query: str, doc_grams: frozenset[str], idf: dict) -> float:
    query_grams = grams(query)
    denom = sum(idf.get(g, 0) for g in query_grams)
    if not denom:
        return 0.0
    return sum(idf.get(g, 0) for g in query_grams if g in doc_grams) / denom


def lexical_rerank(rows: list[dict], prepared_query: str, weight: float = .15,
                   index: tuple[dict, dict] | None = None) -> list[dict]:
    """RRF-shaped dense rank with bounded IDF overlap bonus, within safety tiers."""
    if not 0 <= weight <= 1:
        raise ValueError("INVALID_LEXICAL_WEIGHT")
    by_id, idf = index if index is not None else make_lexical_index(rows)
    score = {
        r["doc_id"]: 1 / (60 + r["dense_rank"]) +
        weight * lexical_coverage(prepared_query, by_id[r["doc_id"]], idf) / 60
        for r in rows
    }
    return sorted(rows, key=lambda r: (
        -r["hmac_matches"], -r["region_matches"], -score[r["doc_id"]],
        r["dense_rank"], r["doc_id"]
    ))


def _jaccard(first: frozenset[str], second: frozenset[str]) -> float:
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def coverage_select(rows: list[dict], *, k: int = 5, pool_size: int = 20,
                    penalty: float = .15, doc_grams: dict | None = None) -> list[dict]:
    """Greedy evidence diversity for top K, same HMAC/region tier precedence.

    Never demotes a higher HMAC/region match below a lower tier within selection.
    Retains the baseline first hit. Remaining documents keep baseline order.
    """
    if k < 1 or pool_size < k or not 0 <= penalty <= 2:
        raise ValueError("INVALID_COVERAGE_PARAMS")
    if len(rows) <= 1:
        return list(rows)
    pool = list(rows[:pool_size])
    by_id = doc_grams if doc_grams is not None else {
        r["doc_id"]: grams(r["text"][:900]) for r in pool
    }
    chosen = [pool.pop(0)]
    while len(chosen) < k and pool:
        highest = max((r["hmac_matches"], r["region_matches"]) for r in pool)
        eligible = [r for r in pool if (r["hmac_matches"], r["region_matches"]) == highest]
        def objective(r):
            overlap = max(_jaccard(by_id[r["doc_id"]], by_id[z["doc_id"]])
                          for z in chosen)
            return (1/(60+r["dense_rank"]) - penalty*overlap/60,
                    -r["dense_rank"], r["doc_id"])
        best = max(eligible, key=objective)
        chosen.append(best)
        pool.remove(best)
    selected = {r["doc_id"] for r in chosen}
    return chosen + [r for r in rows if r["doc_id"] not in selected]
