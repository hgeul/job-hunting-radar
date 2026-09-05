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
from radar.scoring import discovery_qualified
from radar.targeting import EMPTY_REGISTRY
from radar.util import log

# Radar 모드. 기본은 ALL이지만 target 레지스트리가 비어 있으면 DISCOVERY와 같다
# (기존 사용자는 target-companies.json 이 없으므로 동작이 그대로다).
MODE_DISCOVERY = "discovery"
MODE_TARGET = "target"
MODE_ALL = "all"
MODES = (MODE_DISCOVERY, MODE_TARGET, MODE_ALL)


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

    def targets(self):
        """Target Radar가 잡은 매칭만."""
        return [m for m in self.matches if m.get("target")]


def run(cfg, seen, profile_text, window, no_llm=False, seed=False,
        mode=MODE_ALL, registry=None, registry_error=None, deps=None):
    """공고 수집부터 매칭 산출까지. 부수효과는 seen dict 갱신뿐이다."""
    # 기본값을 인자 기본값으로 굳히지 않는다(모듈 전역을 갈아끼운 테스트가 먹히도록).
    deps = deps or DEFAULT_DEPS
    registry = registry if registry is not None else EMPTY_REGISTRY
    if mode not in MODES:
        raise ValueError(f"알 수 없는 mode: {mode} (허용: {', '.join(MODES)})")
    want_discovery = mode in (MODE_DISCOVERY, MODE_ALL)
    want_target = mode in (MODE_TARGET, MODE_ALL) and len(registry) > 0
    if mode == MODE_TARGET and len(registry) == 0:
        log("  ! --mode target 인데 감시 대상 기업이 0곳입니다 "
            "(config.targets.file 확인). 이번 실행은 아무것도 찾지 못합니다.")
    today = dt.date.today().isoformat()
    max_detail = cfg["search"]["max_detail_fetches"]

    log("공고 수집 시작…")
    listings, source_results = deps.collect(cfg)
    log(f"총 {len(listings)}건 수집(dedupe)")

    stats = {
        "scanned": len(listings), "rule_pass": 0, "detail_fetched": 0,
        "fresh": 0, "llm_scored": 0, "enriched": 0, "llm_note": None,
        "window": window, "mode": mode,
        "discovery_candidates": 0, "target_candidates": 0, "target_fresh": 0,
        "target_near_misses": {}, "targets_error": registry_error,
        "target_role_dropped": 0, "target_role_dropped_samples": [],
    }

    # 두 레이더가 각자 후보를 고르고, 같은 공고를 둘 다 잡으면 하나로 합친다
    # (중복 알림 방지). Target 은 규칙 문턱을 건너뛰지만 직무 필터는 받는다.
    prelim = []
    for it in listings:
        rid = it["id"]
        seen.setdefault(rid, {"first_seen": today, "title": it.get("title")})
        disc_ok, rsc, _parts = discovery_qualified(it, cfg)
        company, why = (registry.qualify_verbose(it, cfg) if want_target
                        else (None, None))
        if why == "role":
            stats["target_role_dropped"] += 1
            if len(stats["target_role_dropped_samples"]) < 8:
                stats["target_role_dropped_samples"].append(
                    f"{(it.get('company') or {}).get('name', '?')} / {it.get('title', '?')}")
        if want_target and company is None:
            # target 은 아닌데 이름이 비슷하다 = alias 를 빠뜨렸을 가능성.
            # 매칭에는 절대 쓰지 않고 경고로만 보여준다.
            raw = (it.get("company") or {}).get("name") or ""
            near = registry.near_misses(raw)
            if near:
                stats["target_near_misses"].setdefault(raw, near)
        if disc_ok:
            stats["discovery_candidates"] += 1
        if company is not None:
            stats["target_candidates"] += 1
        picked_by_discovery = want_discovery and disc_ok
        if not picked_by_discovery and company is None:
            continue
        stats["rule_pass"] += 1  # 기존 노트 문구가 쓰는 값이라 이름을 유지한다
        prelim.append((it, rsc, company))
    prelim.sort(key=lambda x: x[1], reverse=True)
    if want_target:
        log(f"후보 {stats['rule_pass']}건 "
            f"(discovery {stats['discovery_candidates']} · "
            f"target {stats['target_candidates']}) → createdAt 확인")
    else:
        log(f"규칙 통과 {stats['rule_pass']}건 → createdAt 확인")

    # createdAt으로 '진짜 신규' 판정. 상세는 id당 1회만(캐시). detail 재사용.
    fresh = []  # (it, rsc, detail, age, company)
    for it, rsc, company in prelim:
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
            if company is not None:
                stats["target_fresh"] += 1
            fresh.append((it, rsc, detail, age, company))
    log(f"신규(createdAt≤{window}일) {stats['fresh']}건"
        + (f" (그중 target {stats['target_fresh']}건)"
           if stats["target_fresh"] else ""))
    if stats["target_role_dropped"]:
        log(f"  · 감시 대상 기업 공고 {stats['target_role_dropped']}건이 "
            f"직무 필터에서 제외됨:")
        for s in stats["target_role_dropped_samples"][:5]:
            log(f"      {s}")
        log("    원하던 공고가 섞여 있으면 roles.include 를 넓히거나 "
            "exclude 의 짧은 조각을 구체적으로 바꾸세요.")
    nm = stats["target_near_misses"]
    if nm:
        log(f"  ! 감시 대상과 이름이 비슷한데 매칭 안 된 회사 {len(nm)}곳 "
            f"(alias 누락일 수 있음):")
        for raw, ids in list(nm.items())[:8]:
            log(f"      \"{raw}\" ~ {', '.join(ids)}")
        log("    맞다면 감시 대상 기업 목록의 해당 회사 aliases 에 "
            "위 표기를 그대로 추가하세요.")

    if seed:
        for it, _rsc, _d, _a, _c in fresh:
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
    prepared = []  # (it, rsc, detail, age, base_text, is_thin, company)
    to_crawl = {}
    for it, rsc, detail, age, company in to_score:
        if detail is None:
            detail = deps.fetch_detail(it)
        base = detail_to_text(detail)
        is_thin = len(base) < min_chars
        if (enr_cfg.get("enabled") and is_thin and detail.get("redirectUrl")
                and len(to_crawl) < enr_cfg.get("max_crawls_per_run", 15)):
            to_crawl[it["id"]] = detail["redirectUrl"]
        prepared.append((it, rsc, detail, age, base, is_thin, company))

    enriched = {}
    if to_crawl:
        log(f"원본 크롤 시작(crawl4ai): 빈약 공고 {len(to_crawl)}건…")
        enriched = deps.crawl(to_crawl, enr_cfg.get("page_timeout_sec", 45))
        stats["enriched"] = len(enriched)
        log(f"원본 확보 {len(enriched)}/{len(to_crawl)}건")

    matches = []
    for it, rsc, detail, age, base, is_thin, company in prepared:
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
        matches.append(build_match(it, rsc, detail, age, llm_out, jd_source,
                                   company))

    # 예산 초과분: 규칙 점수만
    for it, rsc, detail, age, company in rest:
        matches.append(build_match(it, rsc, detail, age, None,
                                   "platform" if detail else None, company))

    matches.sort(key=lambda m: m["score"], reverse=True)
    return RunResult(matches, seen, stats, source_results)
