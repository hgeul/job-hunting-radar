# -*- coding: utf-8 -*-
"""enrich: 플랫폼 본문이 빈약하면 원본(기업 채용페이지)을 crawl4ai로 크롤.

crawl4ai는 .venv에만 두는 선택 의존성이다. 미설치·크롤 실패는 조용히 폴백한다
(전역 numpy를 건드리지 않기 위해 import를 함수 안에 가둔다).
"""

from radar.util import log


def crawl_originals(url_by_id, page_timeout_sec=45):
    """{id: redirectUrl} → {id: 정제 JD 마크다운}. crawl4ai(로컬 헤드리스)로 SPA 렌더링.

    crawl4ai 미설치·크롤 실패는 조용히 건너뜀(해당 id는 결과에 없음 → 폴백).
    """
    if not url_by_id:
        return {}
    try:
        import asyncio  # noqa: WPS433
        from crawl4ai import (  # noqa: WPS433
            AsyncWebCrawler, BrowserConfig, CrawlerRunConfig,
        )
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError:
        log("  · enrich 생략: crawl4ai 미설치 (pip install crawl4ai)")
        return {}

    async def _run():
        out = {}
        bcfg = BrowserConfig(headless=True, verbose=False)
        mdgen = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48))
        run = CrawlerRunConfig(page_timeout=page_timeout_sec * 1000,
                               wait_until="networkidle",
                               markdown_generator=mdgen, verbose=False)
        async with AsyncWebCrawler(config=bcfg) as crawler:
            for rid, url in url_by_id.items():
                try:
                    r = await crawler.arun(url=url, config=run)
                    md = getattr(r.markdown, "fit_markdown", "") or \
                        getattr(r.markdown, "raw_markdown", "") or ""
                    if r.success and len(md) > 200:
                        out[rid] = md[:6000]
                except Exception as e:  # noqa: BLE001
                    log(f"  ! 원본 크롤 실패({rid[:8]}): {str(e)[:80]}")
        return out

    try:
        return asyncio.run(_run())
    except Exception as e:  # noqa: BLE001
        log(f"  · enrich 생략: 크롤러 구동 실패 ({str(e)[:80]})")
        return {}
