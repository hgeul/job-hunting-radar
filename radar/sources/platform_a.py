# -*- coding: utf-8 -*-
"""1차 소스(플랫폼 A) 어댑터. 대상명·엔드포인트는 .env(PLATFORM_*)에서만 온다."""

import urllib.parse

from radar import settings
from radar.sources.base import JobSource, SourceResult
from radar.util import http_get_json, log

# 사이트 필터 = API 파라미터. 리스트형(다중값) + 스칼라형.
_LIST_FILTER_KEYS = ("depthOnes", "depthTwos", "regions", "employeeTypes",
                     "educations", "companyTypes", "deadlineTypes")
_SCALAR_FILTER_KEYS = ("careerMin", "careerMax")


def build_filter_qs(filters):
    """config search.filters → 플랫폼 필터 쿼리스트링. 빈 값은 생략(= 전체)."""
    parts = []
    for key in _LIST_FILTER_KEYS:
        for v in filters.get(key) or []:
            parts.append(f"{key}={urllib.parse.quote(str(v))}")
    for key in _SCALAR_FILTER_KEYS:
        if filters.get(key) is not None:
            parts.append(f"{key}={filters[key]}")
    return "&".join(parts)


class PlatformASource(JobSource):
    name = "platform"

    def fetch(self, search, cfg=None):
        """서버단 필터 + 선택 keyword로 페이지 수집, id dedupe. _source 태깅."""
        if not settings._API_BASE:
            raise SystemExit(
                "PLATFORM_API_BASE 미설정: .env에 크롤 대상 API 베이스를 넣으세요 "
                "(.env.example 참고).")
        s = search or {}
        fqs = build_filter_qs(s.get("filters") or {})
        max_pages = s.get("max_pages", s.get("pages_per_keyword", 8))
        keywords = s.get("keywords") or [None]
        by_id = {}
        failed = None
        for kw in keywords:
            label = kw or "(필터만)"
            total = "?"
            for pg in range(max_pages):
                bits = [fqs, f"size={s['page_size']}", f"page={pg}"]
                if s.get("sort"):
                    bits.append(f"sort={s['sort']}")
                if kw:
                    bits.append(f"keyword={urllib.parse.quote(kw)}")
                url = f"{settings.API_LIST}?" + "&".join(b for b in bits if b)
                try:
                    data = http_get_json(url).get("data") or {}
                except RuntimeError as e:
                    log(f"  ! [{settings.PLATFORM_NAME}] '{label}' p{pg} 수집 실패: {e}")
                    failed = str(e)
                    break
                total = data.get("totalElements", total)
                content = data.get("content") or []
                for it in content:
                    it["_source"] = self.name
                    by_id[it["id"]] = it
                if data.get("last") or not content:
                    break
            log(f"  · [{settings.PLATFORM_NAME}] '{label}' 대상 {total}건 "
                f"→ 누적 {len(by_id)}건")
        items = list(by_id.values())
        # 일부라도 받았으면 성공으로 본다. 전부 실패했을 때만 수집 실패로 기록.
        ok = failed is None or bool(items)
        return SourceResult(self.name, items, ok=ok, error=None if ok else failed)

    def fetch_detail(self, item):
        return http_get_json(settings.API_DETAIL.format(id=item["id"])).get("data") or {}

    def apply_url(self, item, detail=None):
        if detail and detail.get("redirectUrl"):
            return detail["redirectUrl"]
        return settings.DETAIL_PAGE.format(id=item["id"])
