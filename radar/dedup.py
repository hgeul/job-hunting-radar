# -*- coding: utf-8 -*-
"""공고 identity·소스 간 중복 병합.

원칙(PLAN 25절): **잘못 합치는 것이 중복 표시보다 위험하다.**
확신이 낮으면 합치지 않는다. 그래서 회사+제목이 정확히 일치할 때만 병합한다.
"""

import re


def norm_key(item):
    """dedup 키: 회사 + 제목 정규화(괄호·공백·기호 제거, 소문자).

    회사 쪽은 `_canonical_company`(감시 대상 기업 id)가 있으면 그걸 쓴다.
    같은 회사를 채용 플랫폼은 "예시알파", 공식 채용페이지는 "Example Alpha"로 부르기 때문에
    표기만으로 비교하면 같은 공고가 두 번 나온다. 조사문서 9절의
    `canonical_company_id + normalized_title` 이 이 뜻이다.
    """
    canon = item.get("_canonical_company")
    if canon:
        c = str(canon).lower()
    else:
        comp = (item.get("company", {}) or {}).get("name", "")
        c = re.sub(r"[\s\W_]+", "", comp).lower()
    title = re.sub(r"\(.*?\)", "", item.get("title", ""))
    t = re.sub(r"[\s\W_]+", "", title).lower()
    return f"{c}|{t}"


def canonicalize(items, registry):
    """감시 대상으로 해석되는 회사에 canonical id를 붙인다(dedup 전에 호출).

    해석 안 되는 회사는 그대로 둔다. 억지로 합치지 않는다.
    """
    if registry is None or not len(registry):
        return items
    for it in items:
        c = registry.resolve_item(it)
        if c is not None:
            it["_canonical_company"] = c.id
    return items


# 병합 시 먼저 온 쪽에 비어 있으면 나중 쪽에서 채워오는 필드.
# 값을 덮어쓰지 않는다. 비어 있을 때만 채운다.
_FILLABLE = ("_deadline", "_detail_url", "_created_at",
             "careerMin", "careerMax")


def dedup_across_sources(items):
    """소스 '간' 중복(회사+제목)만 병합. 같은 소스 내 항목은 id로 이미 구분되므로 유지.

    **먼저 온 항목의 id를 유지한다.** 예전에는 마감일이 있는 쪽으로 본체를 통째로
    교체했는데, 그러면 살아남는 id가 바뀐다. 이미 `notified` 로 기록된 공고가
    새 id를 갖게 되어 **같은 공고를 다시 알리는** 사고가 난다. 공식 소스를 켰다
    껐다 하는 것만으로도 id가 왔다갔다한다.

    대신 부족한 필드만 채운다. 마감일 같은 정보는 얻으면서 id는 안정적으로 둔다.
    수집 순서가 플랫폼 먼저라 상태 이력이 있는 플랫폼 id가 자연히 살아남는다.
    """
    out = []
    idx = {}  # norm_key -> out 인덱스
    for it in items:
        it.setdefault("_sources", [it.get("_source", "?")])
        k = norm_key(it)
        j = idx.get(k)
        if j is not None and it.get("_source") not in out[j]["_sources"]:
            keep = out[j]
            keep["_sources"] = list(dict.fromkeys(keep["_sources"] + it["_sources"]))
            for f in _FILLABLE:
                if not keep.get(f) and it.get(f):
                    keep[f] = it[f]
            continue
        out.append(it)
        idx[k] = len(out) - 1
    return out
