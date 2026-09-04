# -*- coding: utf-8 -*-
"""공고 identity·소스 간 중복 병합.

원칙(PLAN 25절): **잘못 합치는 것이 중복 표시보다 위험하다.**
확신이 낮으면 합치지 않는다. 그래서 회사+제목이 정확히 일치할 때만 병합한다.
"""

import re


def norm_key(item):
    """dedup 키: 회사명 + 제목 정규화(괄호·공백·기호 제거, 소문자)."""
    comp = (item.get("company", {}) or {}).get("name", "")
    title = re.sub(r"\(.*?\)", "", item.get("title", ""))
    t = re.sub(r"[\s\W_]+", "", title).lower()
    c = re.sub(r"[\s\W_]+", "", comp).lower()
    return f"{c}|{t}"


def dedup_across_sources(items):
    """소스 '간' 중복(회사+제목)만 병합. 같은 소스 내 항목은 id로 이미 구분되므로 유지.

    병합 시 마감일 있는 소스 정보를 본체로 삼고, 양쪽 소스 배지를 기록한다.
    """
    out = []
    idx = {}  # norm_key -> out 인덱스
    for it in items:
        it.setdefault("_sources", [it.get("_source", "?")])
        k = norm_key(it)
        j = idx.get(k)
        if j is not None and it.get("_source") not in out[j]["_sources"]:
            keep = out[j]
            merged = list(dict.fromkeys(keep["_sources"] + it["_sources"]))
            if not keep.get("_deadline") and it.get("_deadline"):
                it["_sources"] = merged      # 마감일 있는 쪽을 본체로 교체
                out[j] = it
            else:
                keep["_sources"] = merged
            continue
        out.append(it)
        idx[k] = len(out) - 1
    return out
