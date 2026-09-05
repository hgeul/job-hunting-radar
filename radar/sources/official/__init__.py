# -*- coding: utf-8 -*-
"""공식 채용페이지 수집 오케스트레이션.

수집 단위가 `platform_family` 다. 어댑터 하나가 그 family 의 회사 전부를 담당한다.
감시 대상이 수십 곳이어도 그 수만큼 크롤러를 만들지 않는다.

`collection_status` 가 수집 가능이 아닌 회사는 여기까지 오지 않는다
(`registry.collectible_families()` 가 걸러낸다). 특히
`restricted_research_needed` 는 "차단 우회를 만들지 말라"는 신호라 자동 수집 대상이 아니다.
"""

import time

from radar.sources.official.base import CompanyFetchResult, OfficialSource  # noqa: F401
from radar.sources.official.greetinghr import GreetingHRSource
from radar.util import log

# platform_family → 어댑터. 새 ATS 는 여기 한 줄 등록.
OFFICIAL_ADAPTERS = {
    GreetingHRSource.family: GreetingHRSource(),
}

# 회사 사이 간격(초). 예의는 호스트 단위 개념이지만 지금은 어댑터가 하나뿐이라
# 전역 직렬 지연으로 둔다. 어댑터가 늘거나 회사가 수십 곳이 되면
# 호스트별 간격으로 바꿀 자리다(config targets.official_delay_sec 로 조절 가능).
POLITE_DELAY_SEC = 1.0


def collect_official(registry, delay=None, sleep=time.sleep, cfg=None):
    """수집 가능한 감시 대상 기업의 공식 채용페이지를 훑는다.

    반환: (items, results). results 는 회사별 CompanyFetchResult.
    한 회사가 실패해도 나머지는 계속한다. 어댑터가 없는 family 는 조용히 건너뛴다
    (아직 안 만든 것이지 실패가 아니다).
    """
    items, results = [], []
    if delay is None:
        delay = ((cfg or {}).get("targets") or {}).get(
            "official_delay_sec", POLITE_DELAY_SEC)
    if registry is None or not len(registry):
        return items, results

    families = registry.collectible_families()
    todo = [(fam, cs) for fam, cs in families.items() if fam in OFFICIAL_ADAPTERS]
    if not todo:
        return items, results

    total = sum(len(cs) for _f, cs in todo)
    log(f"공식 채용페이지 수집: {len(todo)}개 소스 / {total}곳")
    first = True
    for fam, companies in todo:
        adapter = OFFICIAL_ADAPTERS[fam]
        for company in companies:
            if not first and delay:
                sleep(delay)
            first = False
            res = adapter.fetch_company(company)
            results.append(res)
            if res.ok:
                items += res.items
                log(f"  · [{fam}] {company.id}: {res.count}건")
            else:
                log(f"  ! [{fam}] {company.id}: 수집 실패 - {res.error}")
    return items, results
