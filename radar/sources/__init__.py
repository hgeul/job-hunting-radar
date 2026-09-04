# -*- coding: utf-8 -*-
"""소스 레지스트리 + 수집 오케스트레이션.

새 소스를 붙이는 방법:
  1) radar/sources/<name>.py 에 JobSource 구현
  2) 아래 REGISTRY에 한 줄 등록
  3) config.sources[]에 {"type": "<name>", "enabled": true} 추가
core pipeline은 건드리지 않는다.
"""

from radar.dedup import dedup_across_sources
from radar.sources.base import JobSource, SourceResult  # noqa: F401 (재수출)
from radar.sources.platform_a import PlatformASource, build_filter_qs  # noqa: F401
from radar.sources.platform_b import PlatformBSource
from radar.util import log

REGISTRY = {
    PlatformASource.name: PlatformASource(),
    PlatformBSource.name: PlatformBSource(),
}

DEFAULT_SOURCE = PlatformASource.name


def get_source(name):
    return REGISTRY.get(name)


def source_for(item):
    """item의 _source에 맞는 어댑터. 모르는 소스면 1차 소스로 폴백."""
    return REGISTRY.get(item.get("_source"), REGISTRY[DEFAULT_SOURCE])


def iter_source_configs(cfg):
    """config.sources[](enabled만) 또는 레거시 단일 search를 1차 소스로 매핑(하위호환)."""
    if cfg.get("sources"):
        return [s for s in cfg["sources"] if s.get("enabled", True)]
    return [{"type": DEFAULT_SOURCE, "search": cfg.get("search", {})}]


def fetch_detail_for(item):
    """소스별 상세 조회 → 공통 detail dict로 dispatch."""
    return source_for(item).fetch_detail(item)


def apply_url(item, detail=None):
    """소스별 지원/공고 링크로 dispatch."""
    return source_for(item).apply_url(item, detail)


def collect(cfg):
    """활성 소스 전체를 수집·정규화 후 소스 간 중복 병합.

    반환: (items, results) — results는 소스별 SourceResult(공고 0건 vs 수집 실패 구분용).
    한 소스가 실패해도 나머지 소스는 계속 수집한다.
    """
    items = []
    results = []
    for sc in iter_source_configs(cfg):
        t = sc.get("type", DEFAULT_SOURCE)
        adapter = get_source(t)
        if adapter is None:
            log(f"  ! 알 수 없는 소스 type: {t} (건너뜀)")
            results.append(SourceResult(t, [], ok=False, error="unknown source type"))
            continue
        search = sc.get("search")
        if search is None and t == DEFAULT_SOURCE:
            search = cfg.get("search", {})  # 레거시: 1차 소스는 전역 search를 그대로 씀
        try:
            res = adapter.fetch(search or {}, cfg)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 - 한 소스 실패가 전체 실행을 죽이지 않게
            log(f"  ! [{t}] 수집 중 예외: {str(e)[:150]}")
            res = SourceResult(t, [], ok=False, error=str(e)[:200])
        results.append(res)
        items += res.items
    before = len(items)
    merged = dedup_across_sources(items)
    if before != len(merged):
        log(f"  · 소스 간 중복 병합: {before} → {len(merged)}건")
    return merged, results


def fetch_listings(cfg):
    """하위호환 표면: 병합된 item 리스트만 반환."""
    items, _ = collect(cfg)
    return items
