# -*- coding: utf-8 -*-
"""공고 identity·소스 간 중복 병합.

원칙(PLAN 25절): **잘못 합치는 것이 중복 표시보다 위험하다.**
확신이 낮으면 합치지 않는다. 그래서 회사+제목이 정확히 일치할 때만 병합한다.
"""

import re

from radar.util import log


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


def exact_key(item):
    """엄격 키: 회사 + 제목 **원문** + 경력범위 + 근무지.

    두 가지를 `norm_key` 와 다르게 본다.

    1. **괄호 안을 살린다.** `norm_key` 는 소스 간 표기차를 흡수하려고 괄호를 지우는데,
       같은 소스 안에서 그러면 서로 다른 공고가 뭉개진다. 실측(2026-09-07): 한 증권사가
       `Backend Engineer (Market Data)`·`(Trading Platform)`·`(검색)` 등 11건을 올렸고
       느슨한 키로는 전부 하나가 된다. 괄호가 공고를 구분하는 정보다.
    2. **경력범위·근무지를 키에 넣는다.** 제목까지 같아도 요구 경력이 다르면 다른
       공고다. 실측: 제목만으로 접으면 42개 그룹에서 서로 다른 메타가 합쳐진다
       (`경력 3~7` 과 `5~15` 처럼). 잘못 합치는 것이 중복 표시보다 위험하다(PLAN 25절).

    직무 태그(depthTwos)는 넣지 않는다. 같은 공고를 플랫폼이 여러 카테고리에 올려
    태그만 달라지는 경우가 많아, 그걸 키에 넣으면 재게시가 안 접힌다.
    """
    canon = item.get("_canonical_company")
    if canon:
        c = str(canon).lower()
    else:
        comp = (item.get("company", {}) or {}).get("name", "")
        c = re.sub(r"[\s\W_]+", "", comp).lower()
    t = re.sub(r"[\s\W_]+", "", item.get("title", "")).lower()
    career = f"{item.get('careerMin')}~{item.get('careerMax')}"
    regions = ",".join(sorted(item.get("regions") or []))
    return f"{c}|{t}|{career}|{regions}"


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


def _merge_into(keep, it):
    """부족한 필드만 채운다. 값을 덮어쓰지 않는다."""
    keep["_sources"] = list(dict.fromkeys(keep["_sources"] + it["_sources"]))
    for f in _FILLABLE:
        if not keep.get(f) and it.get(f):
            keep[f] = it[f]


def dedup_across_sources(items):
    """중복 병합. 두 단계로 나눈다.

    1. **재게시**(회사 + 제목 + 경력범위 + 근무지 일치): 소스가 같든 다르든 접는다.
       같은 공고가 여러 직무 카테고리에 올라와 별도 id 를 받는 일이 흔하다.
       접지 않으면 중복마다 LLM 호출이 나가고 같은 공고가 여러 번 알림된다.
    2. **소스 간 표기차**(느슨한 키 일치, 소스는 다름): 기존 동작.
       단 느슨한 키 하나에 서로 다른 공고가 여럿 걸려 있으면 **병합하지 않는다**.
       어느 쪽에 붙일지 정할 근거가 없고, 잘못 합치는 것이 중복 표시보다 위험하다
       (PLAN 25절).

    **먼저 온 항목의 id를 유지한다.** 예전에는 마감일이 있는 쪽으로 본체를 통째로
    교체했는데, 그러면 살아남는 id가 바뀐다. 이미 `notified` 로 기록된 공고가
    새 id를 갖게 되어 **같은 공고를 다시 알리는** 사고가 난다.
    """
    # 느슨한 키가 서로 다른 공고 여럿을 가리키는지 미리 센다.
    loose_variants = {}
    for it in items:
        loose_variants.setdefault(norm_key(it), set()).add(exact_key(it))
    ambiguous = {k for k, v in loose_variants.items() if len(v) > 1}

    out = []
    exact_idx = {}   # exact_key -> out 인덱스
    loose_idx = {}   # norm_key  -> out 인덱스
    repost = 0
    for it in items:
        it.setdefault("_sources", [it.get("_source", "?")])
        ek = exact_key(it)
        j = exact_idx.get(ek)
        if j is not None:
            _merge_into(out[j], it)
            repost += 1
            continue
        lk = norm_key(it)
        j = loose_idx.get(lk)
        if (j is not None and lk not in ambiguous
                and it.get("_source") not in out[j]["_sources"]):
            _merge_into(out[j], it)
            continue
        out.append(it)
        exact_idx[ek] = len(out) - 1
        loose_idx.setdefault(lk, len(out) - 1)
    if repost:
        log(f"  · 같은 공고 재게시 {repost}건 병합"
            f"(회사·제목·경력·근무지 동일). {len(items)} → {len(out)}건")
    return out
