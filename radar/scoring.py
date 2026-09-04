# -*- coding: utf-8 -*-
"""규칙 기반 프리필터 점수. 값싸게 후보를 좁히고 LLM 실패 시 폴백으로 쓴다.

여기서 나오는 점수는 rule_score다. v2의 fit_score(구조화 요구사항 매칭 기반)와
역할이 다르다: rule은 프리필터·폴백, fit은 최종 판정.
"""


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


def passes_prefilter(item, cfg):
    """규칙 프리필터 통과 여부 → (통과bool, 점수, 부분점수).

    제외 키워드에 걸리거나, 문턱 미달이거나, 역할 점수가 0이면 탈락.
    """
    if title_excluded(item, cfg["filter"]):
        return False, 0.0, None
    sc, parts = rule_score(item, cfg)
    if sc < cfg["scoring"]["rule_threshold"] or parts["role"] == 0.0:
        return False, sc, parts
    return True, sc, parts
