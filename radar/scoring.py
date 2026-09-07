# -*- coding: utf-8 -*-
"""규칙 기반 프리필터 점수. 값싸게 후보를 좁히고 LLM 실패 시 폴백으로 쓴다.

여기서 나오는 점수는 rule_score다. v2의 fit_score(구조화 요구사항 매칭 기반)와
역할이 다르다: rule은 프리필터·폴백, fit은 최종 판정.
"""

import re


def score_role(item, f):
    d2 = set(item.get("depthTwos") or [])
    d1 = set(item.get("depthOnes") or [])
    if d2 & set(f["primary_depth_twos"]):
        return 1.0
    if d2 & set(f["secondary_depth_twos"]):
        return 0.6
    if d1 & set(f["target_depth_ones"]):
        return 0.35
    return 0.0


def score_career(item, years, tol_over):
    cmin = item.get("careerMin")
    cmax = item.get("careerMax")
    if cmin is None and cmax is None:
        return 0.7  # 경력 무관
    cmin = 0 if cmin is None else cmin
    cmax = 100 if cmax is None else cmax
    if cmin <= years <= cmax:
        return 1.0
    if years < cmin:  # 공고가 더 높은 경력 요구
        gap = cmin - years
        if gap <= tol_over:
            return max(0.3, 1.0 - 0.35 * gap)
        return 0.1
    # years > cmax : 내가 더 시니어 (신입~주니어 공고)
    if cmax >= 2:
        return 0.5
    return 0.3


def score_region(item, preferred):
    regions = item.get("regions") or []
    if not regions:
        return 0.8
    if any(any(p in r for p in preferred) for r in regions):
        return 1.0
    return 0.3


def score_employment(item):
    ets = item.get("employeeTypes") or []
    if any("정규" in e for e in ets):
        return 1.0
    if not ets:
        return 0.7
    return 0.4


def score_tech_title(item, prof):
    title = (item.get("title") or "").lower()
    p = sum(1 for t in prof["tech_primary"] if t in title)
    s = sum(1 for t in prof["tech_secondary"] if t in title)
    return min(1.0, 0.5 * p + 0.25 * s)


def _norm_company(name):
    return re.sub(r"[\s\W_]+", "", name or "").lower()


def company_excluded(item, f):
    """제외 회사 목록에 걸리나(부분일치).

    채용 집계·파견 업체가 구인광고를 대량 재게시하는 경우가 있다.
    실측: 한 업체가 하루 27건을 올렸는데 고유 제목이 11개뿐이었고 내용은 채용
    포털 홍보 문구였다. 다른 회사는 제목 하나당 공고 하나다. 그런 제목은 서로
    닮은 데가 없어 키워드로는 절반밖에 못 걸러, 회사 단위로 거른다.

    제목 필터와 달리 **회사명은 사람이 직접 지정**해야 한다. 자동 판정하면
    정상 채용대행사까지 지운다.
    """
    pats = f.get("exclude_companies") or []
    if not pats:
        return False
    c = _norm_company((item.get("company") or {}).get("name"))
    return any(p and _norm_company(p) in c for p in pats)


def title_excluded(item, f):
    title = (item.get("title") or "").lower()
    return any(x.lower() in title for x in f["exclude_title_keywords"])


def rule_score(item, cfg):
    f, prof, sc = cfg["filter"], cfg["profile"], cfg["scoring"]
    parts = {
        "role": score_role(item, f),
        "career": score_career(item, prof["career_years"], sc["career_tolerance_over"]),
        "tech_title": score_tech_title(item, prof),
        "region": score_region(item, f["regions_preferred"]),
        "employment": score_employment(item),
    }
    w = sc["weights"]
    total = sum(parts[k] * w[k] for k in parts) / sum(w.values()) * 100
    return round(total, 1), parts


def discovery_qualified(item, cfg):
    """Discovery Radar 프리필터 → (통과bool, 점수, 부분점수).

    제외 키워드에 걸리거나, 문턱 미달이거나, 역할 점수가 0이면 탈락.
    탈락해도 **점수는 계산해서 돌려준다**: Target Radar가 잡은 공고는 이 문턱을
    건너뛰지만 정렬·폴백용 점수는 여전히 필요하기 때문이다.
    """
    sc, parts = rule_score(item, cfg)
    ok = (not title_excluded(item, cfg["filter"])
          and sc >= cfg["scoring"]["rule_threshold"]
          and parts["role"] != 0.0)
    return ok, sc, parts
