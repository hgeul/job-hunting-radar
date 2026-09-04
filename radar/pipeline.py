# -*- coding: utf-8 -*-
"""수집 → 프리필터 → 신규판정 → enrich → LLM 채점까지의 오케스트레이션.

바깥세상(HTTP·크롤러·LLM)과 닿는 지점을 `Deps`로 모아 주입 가능하게 했다.
덕분에 파이프라인 전체를 네트워크 없이 고정 입력으로 돌려 회귀 스냅샷을 뜰 수 있다
(함수는 그대로인데 호출 **순서**만 바뀌는 리팩터 사고를 잡기 위한 장치다).

파일 쓰기·알림은 여기서 하지 않는다. cli가 한다.
"""

import datetime as dt

from radar import enrich as enrich_mod
from radar import llm as llm_mod
from radar import sources as sources_mod
from radar.jd import created_age_days, detail_to_text
from radar.models import build_match
from radar.scoring import passes_prefilter
from radar.util import log


class Deps:
    """파이프라인의 외부 의존. 테스트/스냅샷에서 갈아끼운다."""

    def __init__(self, collect=None, fetch_detail=None, crawl=None,
                 resolve_engine=None, llm_score=None):
        self.collect = collect or sources_mod.collect
        self.fetch_detail = fetch_detail or sources_mod.fetch_detail_for
        self.crawl = crawl or enrich_mod.crawl_originals
        self.resolve_engine = resolve_engine or llm_mod.resolve_engine
        self.llm_score = llm_score or llm_mod.llm_score


DEFAULT_DEPS = Deps()


class RunResult:
    """1회 실행 결과. cli가 이걸 받아 노트·상태·알림을 만든다."""

    __slots__ = ("matches", "seen", "stats", "source_results", "seeded")

    def __init__(self, matches, seen, stats, source_results, seeded=False):
        self.matches = matches
        self.seen = seen
        self.stats = stats
        self.source_results = source_results
        self.seeded = seeded


def run(cfg, seen, profile_text, window, no_llm=False, seed=False, deps=None):
    """공고 수집부터 매칭 산출까지. 부수효과는 seen dict 갱신뿐이다."""
    # 기본값을 인자 기본값으로 굳히지 않는다(모듈 전역을 갈아끼운 테스트가 먹히도록).
    deps = deps or DEFAULT_DEPS
    today = dt.date.today().isoformat()
    max_detail = cfg["search"]["max_detail_fetches"]

    log("공고 수집 시작…")
    listings, source_results = deps.collect(cfg)
    log(f"총 {len(listings)}건 수집(dedupe)")

    stats = {
        "scanned": len(listings), "rule_pass": 0, "detail_fetched": 0,
        "fresh": 0, "llm_scored": 0, "enriched": 0, "llm_note": None,
        "window": window,
    }

    # 규칙 프리필터 → 상세조회 후보(점수순)
    prelim = []
    for it in listings:
        rid = it["id"]
        seen.setdefault(rid, {"first_seen": today, "title": it.get("title")})
        ok, rsc, _parts = passes_prefilter(it, cfg)
        if not ok:
            continue
        stats["rule_pass"] += 1
        prelim.append((it, rsc))
    prelim.sort(key=lambda x: x[1], reverse=True)
    log(f"규칙 통과 {stats['rule_pass']}건 → createdAt 확인")

    # createdAt으로 '진짜 신규' 판정. 상세는 id당 1회만(캐시). detail 재사용.
    fresh = []  # (it, rsc, detail, age)
    for it, rsc in prelim:
        rid = it["id"]
        rec = seen[rid]
        if rec.get("notified"):
            continue  # 이미 알린 공고 재알림 안 함
        detail = None
        age = created_age_days(rec.get("created_at"))
        if age is None:  # createdAt 아직 모름 → 상세 1회 조회
            if stats["detail_fetched"] >= max_detail:
                continue  # 이번 실행 상세 예산 소진(다음 실행에서 확인)
            try:
                detail = deps.fetch_detail(it)
                stats["detail_fetched"] += 1
            except RuntimeError as e:
                log(f"  ! 상세 실패({rid[:8]}): {e}")
                continue
            # 등록일 없으면(2차 소스 publishedAt 누락 등) first_seen로 폴백.
            # None이면 age=None→영구 미신규 + 매 실행 재조회로 상세 예산 잠식하므로 방지.
            rec["created_at"] = detail.get("createdAt") or rec.get("first_seen")
            age = created_age_days(rec.get("created_at"))
        if age is not None and age <= window:
            stats["fresh"] += 1
            fresh.append((it, rsc, detail, age))
    log(f"신규(createdAt≤{window}일) {stats['fresh']}건")

    if seed:
        for it, _rsc, _d, _a in fresh:
            seen[it["id"]]["notified"] = True
        return RunResult([], seen, stats, source_results, seeded=True)

    # LLM 정밀 점수
    engine, why = deps.resolve_engine(cfg, no_llm)
    if engine is None:
        stats["llm_note"] = why
        log(f"LLM 생략: {why}")
    else:
        log(f"LLM 엔진: {engine['provider']} ({engine['model']})")

    fresh.sort(key=lambda x: x[1], reverse=True)
    llm_budget = cfg["llm"]["max_calls_per_run"]
    to_score = fresh[:llm_budget] if engine is not None else []
    rest = fresh[llm_budget:] if engine is not None else fresh

    # LLM 대상의 상세 확보 + base JD. 빈약하면 enrich 후보로.
    enr_cfg = cfg.get("enrich") or {}
    min_chars = enr_cfg.get("min_chars", 100)
    prepared = []  # (it, rsc, detail, age, base_text, is_thin)
    to_crawl = {}
    for it, rsc, detail, age in to_score:
        if detail is None:
            detail = deps.fetch_detail(it)
        base = detail_to_text(detail)
        is_thin = len(base) < min_chars
        if (enr_cfg.get("enabled") and is_thin and detail.get("redirectUrl")
                and len(to_crawl) < enr_cfg.get("max_crawls_per_run", 15)):
            to_crawl[it["id"]] = detail["redirectUrl"]
        prepared.append((it, rsc, detail, age, base, is_thin))

    enriched = {}
    if to_crawl:
        log(f"원본 크롤 시작(crawl4ai): 빈약 공고 {len(to_crawl)}건…")
        enriched = deps.crawl(to_crawl, enr_cfg.get("page_timeout_sec", 45))
        stats["enriched"] = len(enriched)
        log(f"원본 확보 {len(enriched)}/{len(to_crawl)}건")

    matches = []
    for it, rsc, detail, age, base, is_thin in prepared:
        rid = it["id"]
        jd = enriched.get(rid) or base
        jd_source = ("원본크롤" if rid in enriched
                     else ("thin" if is_thin else "platform"))
        llm_out = None
        try:
            llm_out = deps.llm_score(
                engine, cfg["llm"]["max_output_tokens"], profile_text, it, jd)
            stats["llm_scored"] += 1
        except Exception as e:  # noqa: BLE001
            log(f"  ! LLM 실패({rid[:8]}): {e}")
        matches.append(build_match(it, rsc, detail, age, llm_out, jd_source))

    # 예산 초과분: 규칙 점수만
    for it, rsc, detail, age in rest:
        matches.append(build_match(it, rsc, detail, age, None,
                                   "platform" if detail else None))

    matches.sort(key=lambda m: m["score"], reverse=True)
    return RunResult(matches, seen, stats, source_results)
