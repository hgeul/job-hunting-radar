# -*- coding: utf-8 -*-
"""매칭 결과 모델과 마감일 해석.

match dict는 노트·대시보드·텔레그램이 공유하는 표현이다. 필드를 지우면 셋 다 깨진다.
"""

import datetime as dt

from radar.fit import RECOMMENDATION_LABELS, RECOMMENDATIONS, evaluate
from radar.matching import normalize_structured
from radar.sources import apply_url


def rec_rank(m):
    """정렬용 추천 순위. 추천이 없으면(적합도 미산출) 중간에 둔다."""
    rec = m.get("recommendation")
    if rec is None:
        return RECOMMENDATIONS.index("REVIEW")
    return RECOMMENDATIONS.index(rec)


def notify_eligible(m, threshold):
    """알림·🔥·전략 코멘트 대상인가.

    결격으로 SKIP 판정된 공고는 점수가 아무리 높아도 밀어내지 않는다. PLAN 14절의
    "hard blocker 는 score 보다 우선한다"가 판정 필드 안에서만 참이면 의미가 없다.
    노트에는 계속 싣는다(그런 공고가 있었다는 사실 자체는 알아야 한다).
    """
    if m.get("deferred"):
        # LLM 예산 초과로 아직 평가 안 된 공고. 규칙 점수만으로 밀어내지 않는다.
        return False
    rec = m.get("recommendation")
    if rec is not None:
        # 판정이 있으면 판정이 정한다. 점수 문턱은 판정을 못 낸 공고(적합도 미산출)의
        # 폴백일 뿐이다. 실측(2026-09-06): 문턱만 보면 본문 없는 공고 41건이
        # 전부 알림으로 나갔다. 추천/강력추천만 밀어낸다.
        return rec in ("STRONG_APPLY", "APPLY")
    return m["score"] >= threshold


def target_info(company):
    """TargetCompany → match/card에 실을 최소 정보. target이 아니면 None."""
    if company is None:
        return None
    return {"id": company.id, "name": company.name, "tier": company.tier}


def build_match(it, rsc, detail, age, llm_out, jd_source, company=None, cfg=None):
    """공고 1건의 match dict 생성(LLM 결과 유무 공통).

    company가 있으면 Target Radar가 잡은 공고다. 점수에 tier를 섞지 않는다
    (PLAN 14절): tier는 알림 우선순위에만 쓴다.

    LLM 응답은 그대로 싣지 않고 `normalize_structured`를 거친 뒤(Phase 5),
    `fit.evaluate`가 점수와 추천을 코드로 계산한다(Phase 6). LLM이 점수를 직접
    정하지 않는다. 요구사항을 못 받아오면 fit_score가 None이고 규칙 점수로 폴백한다.

    cfg가 없으면 경력·기술·선호 축을 계산할 수 없어 요구사항 축만으로 점수를 낸다
    (스냅샷 하네스처럼 config 없이 부르는 자리를 위해 남겨둔 경로다).
    """
    st = normalize_structured(llm_out)
    ev = evaluate(it, st, cfg)
    fit_sc = ev["fit_score"]
    rec = ev["recommendation"]
    return {
        "id": it["id"],
        "company": it.get("company", {}).get("name", "?"),
        "title": it.get("title", "?"),
        "career": f"{it.get('careerMin','?')}~{it.get('careerMax','?')}년",
        "region": ", ".join(it.get("regions") or []) or "-",
        "depth": ", ".join(it.get("depthTwos") or []) or "-",
        "age": age,
        "url": apply_url(it, detail),
        "jd_source": jd_source,
        "sources": it.get("_sources") or [it.get("_source", "platform")],
        "rule": rsc,
        # 세 값을 분리해 싣는다(PLAN Phase 6): 규칙은 프리필터·폴백, fit은 근거 기반
        # 점수, recommendation은 행동 추천. score는 표시·문턱용 대표값이다.
        "fit": ev["fit"],
        "fit_score": fit_sc,
        "recommendation": rec,
        "recommendation_why": ev["recommendation_why"] or None,
        "career_gap": ev["career_gap"],
        "score": fit_sc if fit_sc is not None else rsc,
        # verdict는 표시용 한국어 라벨이다. 판정의 진실은 recommendation.
        "verdict": RECOMMENDATION_LABELS.get(rec),
        "reasons": st["strengths"] or None,
        "gaps": st["risks"] or None,
        "one_liner": st["summary"] or None,
        "requirements": st["requirements"] or None,
        # 채택된 결격만 싣는다. 반려된 후보는 왜 반려했는지와 함께 따로.
        "hard_blockers": ev["hard_blockers"],
        "blockers_rejected": ev["blockers_rejected"],
        "structured": st["structured"],
        "demoted": st["demoted"],
        # 마감일: 소스가 네이티브로 주면(2차 소스 closedAt) 그걸 우선, 없으면 LLM 추출값.
        "deadline": it.get("_deadline") or st["deadline"],
        "strategy": st["strategy"] or None,
        "target": target_info(company),
        "origin": "target" if company is not None else "discovery",
    }


def deadline_info(deadline):
    """마감일 문자열 → (표시라벨, 남은일수|None, 상태).

    상태: 'date'(구체 날짜) | 'rolling'(상시) | 'unknown'(미상/없음).
    구체 날짜면 D-day 계산(음수면 마감 지남).
    """
    s = (deadline or "").strip()
    if not s or s in ("미상", "unknown"):
        return "미상", None, "unknown"
    if any(t in s for t in ("상시", "채용시", "채용 시", "수시", "충원")):
        return "상시", None, "rolling"
    try:
        d = dt.date.fromisoformat(s[:10])
    except ValueError:
        return s, None, "unknown"
    days = (d - dt.date.today()).days
    md = d.strftime("%m/%d")
    if days < 0:
        return f"마감({md})", days, "date"
    if days == 0:
        return f"오늘마감({md})", 0, "date"
    return f"D-{days} ({md})", days, "date"


def match_card(m, today):
    """대시보드용 카드(state에 축적). 노트에 수록된 공고만 저장한다."""
    return {
        "score": m["score"], "company": m["company"], "title": m["title"],
        "url": m["url"], "verdict": m.get("verdict"),
        "deadline": m.get("deadline"), "one_liner": m.get("one_liner"),
        "career": m["career"], "region": m["region"],
        "sources": m.get("sources"),
        # scored: 적합도를 산출했는가. 옛 카드는 llm 키를 갖고 있어 대시보드가 둘 다 본다.
        "scored": m.get("fit_score") is not None, "date": today,
        "recommendation": m.get("recommendation"),
        "target": m.get("target"),
    }
