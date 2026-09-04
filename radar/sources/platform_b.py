# -*- coding: utf-8 -*-
"""2차 소스(플랫폼 B) 어댑터. 대상명·엔드포인트는 .env(PLATFORM_B_*)에서만 온다.

목록에 closedAt(마감)·경력·지역·스택이 있어 마감일을 네이티브로 확보한다(LLM 추출 불필요).
등록일(publishedAt)은 목록에 없어 상세에서 확인한다(신규판정 시).
"""

import urllib.parse

from radar import settings
from radar.sources.base import JobSource, SourceResult
from radar.util import http_get_json, log


def native_deadline(raw):
    """closedAt/alwaysOpen → 공통 deadline(YYYY-MM-DD | '상시' | None)."""
    if raw.get("alwaysOpen"):
        return "상시"
    c = raw.get("closedAt")
    return str(c)[:10] if c else None


def _page_url(item):
    """공고 페이지 URL. 사이트 베이스 미설정이면 None(= enrich 크롤 대상에서 제외)."""
    if not settings.PLATFORM_B_SITE_BASE:
        return None
    return f"{settings.PLATFORM_B_SITE_BASE}/position/{item['id']}"


class PlatformBSource(JobSource):
    name = "platform_b"

    def fetch(self, search, cfg=None):
        """서버단 필터로 positions 수집 → 공통 item으로 정규화."""
        if not settings.PLATFORM_B_API_BASE:
            log(f"  · [{settings.PLATFORM_B_NAME}] PLATFORM_B_API_BASE 미설정 → 건너뜀")
            return SourceResult(self.name, [], ok=True)
        s = search or {}
        role_primary = ((cfg or {}).get("filter") or {}).get("primary_depth_twos", [])
        cats = s.get("job_categories") or []
        max_pages = s.get("max_pages", 5)
        size = s.get("page_size", 50)
        sort = s.get("sort", "relation")
        by_id = {}
        failed = None
        cat_params = [f"jobCategory={urllib.parse.quote(str(c))}" for c in cats] or [None]
        for cp in cat_params:
            for pg in range(max_pages):
                bits = [f"page={pg}", f"size={size}", f"sort={sort}"]
                if cp:
                    bits.append(cp)
                url = (f"{settings.PLATFORM_B_API_BASE}/api/positions?"
                       + "&".join(b for b in bits if b))
                try:
                    res = http_get_json(url).get("result") or {}
                except RuntimeError as e:
                    log(f"  ! [{settings.PLATFORM_B_NAME}] p{pg} 수집 실패: {e}")
                    failed = str(e)
                    break
                positions = res.get("positions") or []
                for p in positions:
                    rid = str(p.get("id"))
                    by_id[rid] = {
                        "id": rid,
                        "_source": self.name,
                        "title": p.get("title") or "?",
                        "company": {"name": p.get("companyName") or "?"},
                        "careerMin": p.get("minCareer"),
                        "careerMax": p.get("maxCareer"),
                        "regions": p.get("locations") or [],
                        "employeeTypes": [],
                        # 서버단 직무 카테고리로 이미 필터됨 → 역할 점수는 주력으로 취급
                        "depthTwos": list(role_primary),
                        "depthOnes": [],
                        "_deadline": native_deadline(p),
                    }
                if res.get("emptyPosition") or not positions:
                    break
            log(f"  · [{settings.PLATFORM_B_NAME}] cat={cp or '(전체)'} "
                f"→ 누적 {len(by_id)}건")
        items = list(by_id.values())
        ok = failed is None or bool(items)
        return SourceResult(self.name, items, ok=ok, error=None if ok else failed)

    def fetch_detail(self, item):
        """상세 → 공통 detail dict(createdAt=publishedAt, _jd_text=구조화 JD)."""
        raw_id = item["id"]
        d = http_get_json(
            f"{settings.PLATFORM_B_API_BASE}/api/position/{raw_id}").get("result") or {}
        parts = []
        for k in ("responsibility", "qualifications", "preferredRequirements", "welfares"):
            if d.get(k):
                parts.append(f"[{k}]\n{d[k]}")
        # techStacks는 목록에선 문자열, 상세에선 dict({name:...}) 배열일 수 있어 방어.
        stacks = [s.get("name") if isinstance(s, dict) else s
                  for s in (d.get("techStacks") or [])]
        stacks = [s for s in stacks if s]
        if stacks:
            parts.append("[기술스택] " + ", ".join(stacks))
        return {
            "createdAt": d.get("publishedAt"),
            "_jd_text": "\n\n".join(parts).strip(),
            "redirectUrl": _page_url(item),
            "_deadline": native_deadline(d),
        }

    def apply_url(self, item, detail=None):
        if detail and detail.get("redirectUrl"):
            return detail["redirectUrl"]
        # 사이트 베이스가 없으면 링크를 만들 수 없다. 노트/대시보드에는 '#'로 둔다.
        return _page_url(item) or "#"
