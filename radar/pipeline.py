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
from radar.scoring import company_excluded, discovery_qualified
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

    __slots__ = ("matches", "seen", "stats", "source_results",
                 "official_results", "seeded")

    def __init__(self, matches, seen, stats, source_results, seeded=False,
                 official_results=None):
        self.matches = matches
        self.seen = seen
        self.stats = stats
        self.source_results = source_results
        self.official_results = official_results or []
        self.seeded = seeded

    def targets(self):
        """Target Radar가 잡은 매칭만."""
        return [m for m in self.matches if m.get("target")]


def run(cfg, seen, profile_text, window, no_llm=False, seed=False,
        mode=MODE_ALL, registry=None, registry_error=None,
        official=False, deps=None):
    """공고 수집부터 매칭 산출까지. 부수효과는 seen dict 갱신뿐이다."""
    # 기본값을 인자 기본값으로 굳히지 않는다(모듈 전역을 갈아끼운 테스트가 먹히도록).
    deps = deps or DEFAULT_DEPS
    registry = registry if registry is not None else EMPTY_REGISTRY
    if mode not in MODES:
        raise ValueError(f"알 수 없는 mode: {mode} (허용: {', '.join(MODES)})")
    want_discovery = mode in (MODE_DISCOVERY, MODE_ALL)
    want_target = mode in (MODE_TARGET, MODE_ALL) and len(registry) > 0
    # 공식 채용페이지는 target 을 볼 때만, 그리고 명시적으로 켰을 때만 수집한다.
    want_official = bool(official) and want_target
    if official and not want_official:
        why = ("mode=discovery 라 target 을 안 봅니다"
               if mode == MODE_DISCOVERY else "감시 대상 기업이 0곳입니다")
        log(f"  ! --official 을 켰지만 공식 채용페이지를 수집하지 않습니다: {why}")
    if mode == MODE_TARGET and len(registry) == 0:
        log("  ! --mode target 인데 감시 대상 기업이 0곳입니다 "
            "(config.targets.file 확인). 이번 실행은 아무것도 찾지 못합니다.")
    today = dt.date.today().isoformat()
    max_detail = cfg["search"]["max_detail_fetches"]

    log("공고 수집 시작…")
    listings, source_results, official_results = deps.collect(
        cfg, registry=registry, with_official=want_official)
    log(f"총 {len(listings)}건 수집(dedupe)")

    stats = {
        "scanned": len(listings), "rule_pass": 0, "detail_fetched": 0,
        "fresh": 0, "llm_scored": 0, "llm_structured": 0, "llm_demoted": 0,
        "llm_blockers": 0, "enriched": 0, "llm_note": None,
        "window": window, "mode": mode,
        "discovery_candidates": 0, "target_candidates": 0, "target_fresh": 0,
        "target_near_misses": {}, "targets_error": registry_error,
        "target_role_dropped": 0, "target_role_dropped_samples": [],
        "company_excluded": 0, "company_excluded_samples": [],
        "deferred": 0, "llm_failed": 0,
    }

    # 두 레이더가 각자 후보를 고르고, 같은 공고를 둘 다 잡으면 하나로 합친다
    # (중복 알림 방지). Target 은 규칙 문턱을 건너뛰지만 직무 필터는 받는다.
    prelim = []
    for it in listings:
        # 제외 회사는 state 에도 넣지 않는다(안 볼 공고로 상태를 불리지 않는다).
        if company_excluded(it, cfg["filter"]):
            stats["company_excluded"] += 1
            if len(stats["company_excluded_samples"]) < 5:
                stats["company_excluded_samples"].append(
                    f"{(it.get('company') or {}).get('name', '?')} / "
                    f"{it.get('title', '?')}")
            continue
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
    if stats["company_excluded"]:
        log(f"  · 제외 회사 공고 {stats['company_excluded']}건 건너뜀 "
            f"(filter.exclude_companies)")
        for smp in stats["company_excluded_samples"][:3]:
            log(f"      {smp[:70]}")
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
        return RunResult([], seen, stats, source_results, seeded=True,
                         official_results=official_results)

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
        failed = False
        try:
            llm_out = deps.llm_score(
                engine, cfg["llm"]["max_output_tokens"], profile_text, it, jd)
            stats["llm_scored"] += 1
        except Exception as e:  # noqa: BLE001
            failed = True
            stats["llm_failed"] += 1
            log(f"  ! LLM 실패({rid[:8]}): {e}")
        m = build_match(it, rsc, detail, age, llm_out, jd_source, company, cfg)
        if failed:
            # 호출 실패도 예산 초과와 같이 다룬다: 검증할 수 있었는데 못 한 공고를
            # 규칙 점수만으로 밀어내지 않고, notified 도 안 찍어 다음 실행에서 재시도.
            # 실측(2026-09-11): 메모리 부족으로 호출 40건이 죽자 전부 규칙 점수(80)로
            # 폴백돼 알림으로 나갔다. 실패는 평가가 아니다.
            m["deferred"] = True
        # 구조화 매칭을 실제로 받아온 건수. LLM 이 응답은 했는데 requirements 가
        # 비면 여기서 차이가 드러난다(프롬프트·토큰 예산 회귀 감지용).
        if m.get("structured"):
            stats["llm_structured"] += 1
        # 근거 없는 FULL 주장을 몇 번 내렸는지. 이 방어가 실제로 발동하는지
        # 세지 않으면 완료조건이 죽은 규칙이 된다.
        stats["llm_demoted"] += m.get("demoted") or 0
        if m.get("hard_blockers"):
            stats["llm_blockers"] += 1
        matches.append(m)

    if stats["llm_scored"]:
        # 화면에 남겨야 프롬프트가 조용히 망가진 걸 알아챈다. 0/n 이면 응답이
        # 잘렸거나(토큰) 스키마 지시가 무시된 것이다.
        log(f"LLM 응답 {stats['llm_scored']}건 중 요구사항 매칭 확보 "
            f"{stats['llm_structured']}건"
            + (f" · 근거없어 강등된 요구사항 {stats['llm_demoted']}건"
               if stats["llm_demoted"] else "")
            + (f" · 결격 후보 있는 공고 {stats['llm_blockers']}건"
               if stats["llm_blockers"] else ""))

    # 예산 초과분. LLM 이 켜져 있는데 예산이 모자라 못 본 공고는 **미평가**로 미룬다:
    # 알리지 않고 notified 도 찍지 않아 다음 실행에서 평가받게 한다.
    # 실측(2026-09-11): 5일치를 한 번에 돌리자 초과분 35건이 규칙 점수(80)만으로
    # 전부 알림으로 나갔고, 정작 LLM 을 거친 38건은 추천 0건이라 하나도 안 나갔다.
    # 검증 안 된 쪽이 밀리고 검증된 쪽이 묻히는 거꾸로 된 결과다. 게다가 노트에
    # 실리면서 notified 가 찍혀 영영 평가를 못 받게 된다.
    # LLM 이 아예 꺼진 실행(--no-llm 등)은 규칙 점수가 최선이므로 그대로 둔다.
    deferred = engine is not None
    for it, rsc, detail, age, company in rest:
        m = build_match(it, rsc, detail, age, None,
                        "platform" if detail else None, company, cfg)
        m["deferred"] = deferred
        matches.append(m)
    if deferred and rest:
        stats["deferred"] = len(rest)
        log(f"  · LLM 예산({llm_budget}건) 초과로 {len(rest)}건 미평가. "
            f"알리지 않고 다음 실행에서 평가합니다.")
    if stats["llm_failed"]:
        stats["deferred"] += stats["llm_failed"]
        log(f"  ! LLM 호출 실패 {stats['llm_failed']}건도 미평가로 미룹니다"
            f"(규칙 점수만으로 알리지 않음). 다음 실행에서 재시도합니다.")

    matches.sort(key=lambda m: m["score"], reverse=True)
    return RunResult(matches, seen, stats, source_results,
                     official_results=official_results)
