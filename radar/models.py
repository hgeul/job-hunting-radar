# -*- coding: utf-8 -*-
"""매칭 결과 모델과 마감일 해석.

match dict는 노트·대시보드·텔레그램이 공유하는 표현이다. 필드를 지우면 셋 다 깨진다.
"""

import datetime as dt

from radar.matching import normalize_structured
from radar.sources import apply_url


def target_info(company):
    """TargetCompany → match/card에 실을 최소 정보. target이 아니면 None."""
    if company is None:
        return None
    return {"id": company.id, "name": company.name, "tier": company.tier}


def build_match(it, rsc, detail, age, llm_out, jd_source, company=None):
    """공고 1건의 match dict 생성(LLM 결과 유무 공통).

    company가 있으면 Target Radar가 잡은 공고다. 점수에 tier를 섞지 않는다
    (PLAN 14절): tier는 알림 우선순위에만 쓴다.

    LLM 응답은 그대로 싣지 않고 `normalize_structured`를 거친다. 표시용 필드
    (reasons·gaps·one_liner)는 구조화 필드(strengths·risks·summary)에서 채우되,
    구식 응답도 계속 받는다(Phase 5 전환기).
    """
    st = normalize_structured(llm_out)
    # LLM 점수는 있을 때만 쓴다. 키가 없거나 숫자가 아니면 규칙 점수로 폴백한다
    # (LLM 이 유효 JSON 을 주면서 score 만 빠뜨려도 실행이 죽지 않아야 한다).
    llm_sc = st["score"]
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
        "llm": llm_sc,
        "score": llm_sc if llm_sc is not None else rsc,
        "verdict": st["verdict"],
        "reasons": st["strengths"] or None,
        "gaps": st["risks"] or None,
        "one_liner": st["summary"] or None,
        # 구조화 매칭(Phase 5). 점수 계산은 아직 하지 않는다(Phase 6).
        "requirements": st["requirements"] or None,
        "hard_blockers": st["hard_blockers"] or None,
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
        "llm": m.get("llm") is not None, "date": today,
        "target": m.get("target"),
    }
