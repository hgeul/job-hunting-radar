# -*- coding: utf-8 -*-
"""결정론적 적합도 점수와 추천 판정(Phase 6).

Phase 5 에서 LLM 은 요구사항·근거·결격 후보까지만 내놓게 만들었다. 여기서 그 재료로
**코드가** 점수를 만든다. 같은 입력이면 같은 점수가 나온다(PLAN 12절 완료조건).

세 가지가 여기서 갈린다.

1. **점수(fit_score)**: 요구사항 매칭 55% + 우대 15% + 경력 10% + 기술 10% + 선호 10%.
   가중치는 config 로 바꾼다. 축에 데이터가 없으면 그 축을 빼고 나머지를 재정규화한다
   (없는 축을 0점으로 치면 "정보 없음"이 "미달"로 둔갑한다).

   **앞 두 축만 LLM 근거를 읽고, 뒤 세 축은 공고 데이터로만 계산한다.** 같은 요구사항
   행을 두 축이 읽으면 근거 한 줄이 여러 축에서 만점을 받아 config 가중치가 문서와
   다른 의미가 된다. 뒤 세 축은 규칙 점수와 같은 함수를 쓴다(이미 회귀 테스트가 있고,
   폴백 점수와 같은 잣대를 유지하는 편이 낫다).

   재정규화가 실제로 발동하는 곳은 요구사항 두 축뿐이다. 경력·기술·선호는 데이터가
   없어도 중립값(경력무관 0.7, 지역없음 0.8 등)을 내므로 축이 사라지지 않는다.
2. **추천(recommendation)**: 점수 구간에서 뽑되, 결격·경력차·근거부족이 그 위에 덮어쓴다.
   PLAN 14절이 "점수와 행동 추천을 분리한다"고 한 게 이것이다.
3. **결격(hard blocker)**: LLM 은 후보만 낸다. 채택은 여기서 한다(PLAN 13절).

`UNKNOWN` 은 분모에서 **뺀다**. 정보가 없는 것을 0점(미달)으로도 0.5점(절반 충족)으로도
치지 않는다. 대신 뺀 비율을 `required_coverage` 로 남겨서, 근거가 너무 적으면
추천을 REVIEW 로 눌러 사람이 직접 보게 한다. 점수만 보고 지나치는 것을 막는 장치다.
"""

import re

from radar.scoring import score_career, score_employment, score_region, score_tech_title
from radar.util import log

# UNKNOWN 은 여기 없다. 값이 없다는 뜻이고, 분모에서 빠진다.
MATCH_VALUE = {"FULL": 1.0, "PARTIAL": 0.5, "NONE": 0.0}

AXES = ("required", "preferred", "career", "tech", "preference")

DEFAULT_WEIGHTS = {
    "required": 0.55, "preferred": 0.15, "career": 0.10,
    "tech": 0.10, "preference": 0.10,
}

# 점수 → 추천 구간(PLAN 14절). 경계값은 "이상".
DEFAULT_THRESHOLDS = {"strong_apply": 90, "apply": 80, "review": 70, "archive": 60}

# 필수 요구사항 중 판정이 선 비율이 이 값 **이하**면 추천을 REVIEW 로 누른다.
# 기본 0.5 = "절반 이하만 판정됐으면 사람이 직접 본다". 경계값을 통과로 두면
# 필수의 딱 절반을 모르는 공고가 "추천"으로 나간다.
DEFAULT_MIN_COVERAGE = 0.5

# 필수 요구사항을 이보다 적게 뽑았으면 근거가 얇다고 본다.
# 실측(2026-09-06): 본문이 사실상 빈 공고에서 LLM 이 "경력 무관"·"서울 근무" 같은
# 목록 메타데이터만으로 요구사항 2~3건을 뽑고 전부 FULL 로 판정해 88.2점이 나왔다.
# 비율(coverage)로는 2/2 = 100% 라 안 걸린다. 절대 건수를 같이 봐야 한다.
# 값 5의 근거: 본문이 있는 공고는 8~11건이 나오고 메타데이터만 있는 공고는 2~3건이라
# 그 사이가 비어 있다. jd_source=="thin" 을 쓰지 않는 이유는 아래 주석 참고.
DEFAULT_MIN_REQUIRED_ROWS = 5

# 경력 차이 정책(조사문서 7절). 값은 "내 경력 대비 공고가 더 요구하는 연수".
#   1년(요구 5년 vs 4년) → 그대로 평가
#   2년(요구 6년)        → REVIEW 강등
#   3년+(요구 7년 이상)  → 명시적 senior 역할이면 SKIP 후보
DEFAULT_CAREER_REVIEW_GAP = 2
DEFAULT_CAREER_SKIP_GAP = 3

RECOMMENDATIONS = ("STRONG_APPLY", "APPLY", "REVIEW", "ARCHIVE", "SKIP")

# 노트·대시보드가 쓰는 한국어 라벨. 판정 자체는 위 enum 이 진실이다.
RECOMMENDATION_LABELS = {
    "STRONG_APPLY": "강력추천", "APPLY": "추천", "REVIEW": "검토",
    "ARCHIVE": "보류", "SKIP": "제외",
}

# 제목에 이게 있으면 "명시적 senior 역할"로 본다(경력차 SKIP 판정에만 쓴다).
SENIOR_TITLE_TOKENS = ("senior", "sr.", "staff", "principal", "lead", "시니어",
                       "리드", "책임", "수석", "팀장")

# 결격 후보 중 "경력 연수가 모자람"으로 읽히는 것들. PLAN 13절이 자동 결격을 금지한다.
# 숫자+연수와 경력 단어가 함께 있을 때만 그렇게 본다. "경력"이라는 낱말만으로 거르면
# "신입 전용 공고로 경력자 지원 불가" 같은 **진짜** 결격까지 사라진다(PLAN 13절이
# 신입 전용을 결격 예시로 직접 들고 있다).
_YEARS_RE = re.compile(r"\d+\s*(년|years?)", re.I)
_CAREER_WORDS = ("경력", "연차", "career", "experience")
# 방향이 반대인 배제(내가 너무 시니어라 안 되는 것)는 연수 차이가 아니다.
_ENTRY_ONLY = ("신입 전용", "신입만", "신입 채용", "신입only", "신입 only",
               "경력자 지원 불가", "경력자 불가", "entry level only")


def _entry_only(text):
    s = (text or "").lower().replace(" ", "")
    return any(e.replace(" ", "") in s for e in _ENTRY_ONLY)


_warned = set()


def _warn_once(msg):
    """같은 경고를 공고마다 반복하지 않는다(한 실행에 한 번)."""
    if msg not in _warned:
        _warned.add(msg)
        log(f"  ! {msg}")


def _cfg_block(cfg):
    """config 의 scoring.fit 블록. 없으면 기본값(기존 config 가 그대로 돌아야 한다).

    PLAN 12절이 예시로 든 평평한 표기(`scoring.fit_weights`)도 받는다. 문서대로 적은
    설정이 조용히 무시되면 사용자는 튜닝이 먹은 줄 알고 잘못된 모델을 갖게 된다.
    """
    scoring = (cfg or {}).get("scoring") or {}
    fit = scoring.get("fit") or {}
    raw_weights = dict(scoring.get("fit_weights") or {})
    raw_weights.update(fit.get("weights") or {})
    unknown = sorted(set(raw_weights) - set(AXES))
    if unknown:
        _warn_once(f"config scoring.fit.weights 에 모르는 축이 있어 무시합니다: "
                   f"{', '.join(unknown)} (가능한 축: {', '.join(AXES)})")
    weights = dict(DEFAULT_WEIGHTS)
    weights.update({k: v for k, v in raw_weights.items() if k in AXES})
    zeroed = sorted(k for k, v in weights.items() if not v)
    if zeroed:
        _warn_once(f"config 에서 가중치가 0 인 축은 계산에서 빠지고 나머지로 "
                   f"재정규화됩니다(0점 반영이 아닙니다): {', '.join(zeroed)}")
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(fit.get("thresholds") or {})
    order = [thresholds[k] for k in ("strong_apply", "apply", "review", "archive")]
    if order != sorted(order, reverse=True):
        _warn_once(f"config scoring.fit.thresholds 가 내림차순이 아닙니다: "
                   f"{thresholds}. 구간이 겹쳐 추천이 뜻대로 안 나옵니다.")
    return {
        "weights": weights,
        "thresholds": thresholds,
        "min_required_coverage": fit.get("min_required_coverage", DEFAULT_MIN_COVERAGE),
        "min_required_rows": fit.get("min_required_rows", DEFAULT_MIN_REQUIRED_ROWS),
        "career_review_gap": fit.get("career_review_gap", DEFAULT_CAREER_REVIEW_GAP),
        "career_skip_gap": fit.get("career_skip_gap", DEFAULT_CAREER_SKIP_GAP),
    }


def match_axis(requirements, importance):
    """중요도별 매칭 평균 → (값|None, 판정선건수, 전체건수).

    UNKNOWN 은 분모에서 빠진다. 전부 UNKNOWN 이면 값이 None(= 축 자체를 제외).
    """
    rows = [r for r in (requirements or []) if r.get("importance") == importance]
    known = [r for r in rows if r.get("match") in MATCH_VALUE]
    if not known:
        return None, 0, len(rows)
    total = sum(MATCH_VALUE[r["match"]] for r in known)
    return total / len(known), len(known), len(rows)


def tech_axis(item, cfg):
    """기술 축. 공고 제목 x 프로필 스택으로만 잰다.

    요구사항 행을 여기서 다시 읽지 않는다. 기술 요구사항은 이미 required/preferred
    축에서 근거와 함께 세어졌고, 같은 행을 두 번 읽으면 "필수 55%"가 실제로는
    65% 처럼 동작한다. 제목 기반 신호가 약한 것은 감수한다(비중 10%).
    """
    prof = (cfg or {}).get("profile")
    if not prof:
        return None
    return score_tech_title(item, prof)


def preference_axis(item, cfg):
    """선호 축. 근무지·고용형태처럼 공고 데이터만으로 정해지는 것."""
    f = (cfg or {}).get("filter")
    if not f:
        return None
    return (score_region(item, f["regions_preferred"]) + score_employment(item)) / 2


def career_axis(item, cfg):
    """경력 축. 규칙 점수와 같은 함수를 쓴다(이미 회귀 테스트가 있다)."""
    prof = (cfg or {}).get("profile")
    sc = (cfg or {}).get("scoring")
    if not prof or not sc:
        return None
    return score_career(item, prof["career_years"], sc["career_tolerance_over"])


def career_gap(item, cfg):
    """공고가 내 경력보다 몇 년을 더 요구하나. 알 수 없으면 None.

    음수(내가 더 시니어)는 0 으로 본다. 여기서 만든 값은 점수가 아니라
    추천 강등 판정에만 쓴다(조사문서 7절).
    """
    prof = (cfg or {}).get("profile")
    if not prof:
        return None
    cmin = item.get("careerMin")
    if cmin is None:
        return None
    try:
        return max(0, int(cmin) - int(prof["career_years"]))
    except (TypeError, ValueError):
        return None


def is_senior_role(item):
    """제목에 시니어 표시가 있나. 경력차 SKIP 판정에만 쓴다."""
    title = (item.get("title") or "").lower()
    return any(t in title for t in SENIOR_TITLE_TOKENS)


def looks_like_career_gap(text):
    """결격 후보 문구가 '요구 연수에 못 미침'으로 읽히나.

    숫자+연수와 경력 단어가 같이 있어야 한다. 신입 전용처럼 방향이 반대인 배제는
    연수 차이가 아니므로 여기서 False 다(= 결격으로 채택될 수 있다).
    """
    s = (text or "").lower()
    if _entry_only(s):
        return False
    return bool(_YEARS_RE.search(s)) and any(w in s for w in _CAREER_WORDS)


def adopt_blockers(blockers):
    """결격 후보 → (채택, 반려[사유 포함]). LLM 은 후보만 내고 판정은 여기서(PLAN 13절).

    두 가지를 반려한다.
    - 경력 연수 차이: PLAN 13절이 자동 결격으로 두지 말라고 못박았다. 경력은 점수 축과
      추천 강등으로 다룬다.
    - 공고 근거가 없는 것: 근거 없는 결격 하나가 지원 기회를 통째로 없앤다.
      근거 없는 FULL 을 안 믿는 것과 같은 기준이다.
    """
    adopted, rejected = [], []
    for b in blockers or []:
        kind = (b.get("kind") or "").lower()
        detail = b.get("detail")
        # kind 만 보고 거르지 않는다. LLM 이 "신입 전용"에도 kind=career 를 붙인다.
        career_gap_only = looks_like_career_gap(detail) or (
            kind == "career" and not _entry_only(detail))
        if career_gap_only:
            rejected.append(dict(b, reject="경력 연수 차이는 자동 결격이 아님"))
        elif not b.get("evidence"):
            rejected.append(dict(b, reject="공고 원문 근거 없음"))
        else:
            adopted.append(b)
    return adopted, rejected


def fit_score(requirements, item, cfg):
    """적합도 0~100 과 축별 내역. 요구사항이 없으면 None(규칙 점수로 폴백).

    데이터가 없는 축은 가중합에서 빼고 남은 가중치로 재정규화한다.
    """
    if not requirements:
        return None
    conf = _cfg_block(cfg)
    req, req_known, req_total = match_axis(requirements, "REQUIRED")
    pref, _pk, _pt = match_axis(requirements, "PREFERRED")
    parts = {
        "required": req,
        "preferred": pref,
        "career": career_axis(item, cfg),
        "tech": tech_axis(item, cfg),
        "preference": preference_axis(item, cfg),
    }
    w = conf["weights"]
    used = {k: v for k, v in parts.items() if v is not None and w.get(k)}
    wsum = sum(w[k] for k in used)
    if not used or wsum <= 0:
        return None
    score = round(sum(parts[k] * w[k] for k in used) / wsum * 100, 1)
    return {
        "score": score,
        "parts": {k: (round(v, 4) if v is not None else None) for k, v in parts.items()},
        "weights_used": sorted(used),
        "required_known": req_known,
        "required_total": req_total,
        "required_coverage": (round(req_known / req_total, 4) if req_total else None),
    }


def _base_recommendation(score, thresholds):
    if score >= thresholds["strong_apply"]:
        return "STRONG_APPLY"
    if score >= thresholds["apply"]:
        return "APPLY"
    if score >= thresholds["review"]:
        return "REVIEW"
    if score >= thresholds["archive"]:
        return "ARCHIVE"
    return "SKIP"


def _cap(current, ceiling):
    """추천을 ceiling 아래로 눌러 내린다. 이미 더 낮으면 그대로."""
    return current if RECOMMENDATIONS.index(current) >= RECOMMENDATIONS.index(ceiling) \
        else ceiling


def recommend(fit, adopted_blockers, gap, senior, cfg):
    """점수 + 정책 → (추천, 사유 목록). 결격은 점수보다 우선한다(PLAN 14절).

    결격이 있어도 나머지 사유를 버리지 않는다. 6개월 뒤 "왜 제외였나"를 되짚을 때
    결격 문구 한 줄만 남아 있으면 근거가 모자란다(PLAN 15절).

    "근거가 얇다"는 판단은 **요구사항 건수**로 한다. `jd_source=="thin"` 은 쓰지 않는다:
    그 값은 본문 길이를 config `enrich.min_chars`(기본 1000)와 비교한 상대적 표시라
    소스에 따라 거의 모든 공고가 thin 이 된다(실측 2026-09-06: 이틀치 노트의 공고가
    100% thin). 그걸로 누르면 레이더가 통째로 침묵한다. 반면 요구사항 건수는
    본문 유무를 직접 반영한다.
    """
    conf = _cfg_block(cfg)
    why = []
    if adopted_blockers:
        why.append("결격: " + " / ".join(b["detail"] for b in adopted_blockers))
    rec = None
    if fit:
        rec = _base_recommendation(fit["score"], conf["thresholds"])
        cov = fit["required_coverage"]
        if not fit["required_total"]:
            rec = _cap(rec, "REVIEW")
            why.append("필수 요구사항을 뽑지 못해 근거가 얇음")
        elif fit["required_total"] < conf["min_required_rows"]:
            rec = _cap(rec, "REVIEW")
            why.append(f"필수 요구사항이 {fit['required_total']}건뿐이라 근거가 얇음"
                       f"(공고 본문이 비면 목록 정보만으로 판정된다)")
        elif fit["parts"].get("required") is None:
            # 필수 행은 있는데 하나도 판정이 안 섰다(전부 UNKNOWN 이거나 근거 없어 강등).
            rec = _cap(rec, "REVIEW")
            why.append(f"필수 요구사항 {fit['required_total']}건이 모두 미판정")
        elif cov is not None and cov <= conf["min_required_coverage"]:
            rec = _cap(rec, "REVIEW")
            why.append(f"필수 요구사항 {fit['required_total']}건 중 "
                       f"{fit['required_known']}건만 판정됨(나머지는 이력에 언급 없음)")
        if gap is not None and gap >= conf["career_skip_gap"] and senior:
            rec = _cap(rec, "SKIP")
            why.append(f"요구 경력이 {gap}년 많고 시니어 명시 공고")
        elif gap is not None and gap >= conf["career_review_gap"]:
            rec = _cap(rec, "REVIEW")
            why.append(f"요구 경력이 {gap}년 많음")
    if adopted_blockers:
        return "SKIP", why  # 점수·경력 사유는 남기되 판정은 결격이 이긴다
    return rec, why


def evaluate(item, structured, cfg):
    """구조화 결과 + 공고 → 점수·추천 묶음. build_match 가 이걸 그대로 싣는다."""
    requirements = structured.get("requirements") or []
    adopted, rejected = adopt_blockers(structured.get("hard_blockers"))
    fit = fit_score(requirements, item, cfg)
    gap = career_gap(item, cfg)
    rec, why = recommend(fit, adopted, gap, is_senior_role(item), cfg)
    return {
        "fit": fit,
        "fit_score": fit["score"] if fit else None,
        "recommendation": rec,
        "recommendation_why": why,
        "hard_blockers": adopted or None,
        "blockers_rejected": rejected or None,
        "career_gap": gap,
    }
