# Pure fixture from read-only reference 242543bf0eae91aa3d655227a747aa128707368e.
def _merge(groups, k):
    """앞 그룹이 우선. 문서별로 처음 나온(가장 우선순위 높은) 청크 하나만 남김"""
    seen, out = set(), []
    for rows in groups:
        for r in rows:
            if r["doc_id"] not in seen:
                seen.add(r["doc_id"])
                out.append(r)
                if len(out) == k:
                    return out
    return out
