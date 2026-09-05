# -*- coding: utf-8 -*-
"""GreetingHR 공개 채용페이지 어댑터.

수집 방식은 embedded state다. 공개 목록 페이지를 1회 GET 하면 그 회사 공고
전체가 `__NEXT_DATA__` 안에 들어 있다. 목록 API·페이지네이션·DOM 파싱이 필요 없다.
조사 근거와 robots 준수사항은 docs/reference/GREETINGHR_ADAPTER_NOTES.md 참고.

경로는 회사마다 다르다(`/`, `/ko/recruiting`, `/ko/openposition`, 커스텀 도메인).
고정하지 말고 레지스트리의 `careers_url` 을 그대로 쓴다.
"""

import gzip
import json
import re
import urllib.error
import urllib.request

from radar.settings import UA
from radar.sources.official.base import OfficialSource

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# 채용 목록이 들어 있는 React Query 키.
_OPENINGS_KEY = ["openings"]

# 고용형태 코드 → 사람이 읽는 말. 규칙 점수의 '정규직' 판정이 이 문자열을 본다.
_EMPLOYMENT = {
    "FULL_TIME_WORKER": "정규직",
    "CONTRACT_WORKER": "계약직",
    "INTERN": "인턴",
    "PART_TIME_WORKER": "파트타임",
}


# robots 의 Disallow 를 그대로 옮긴 것. 문서에만 적어두면 레지스트리에 잘못된
# URL 이 들어왔을 때 코드가 그냥 가져온다.
#   Disallow: /o/*/apply  /ko/o/*/apply  /en/o/*/apply  /m/*  /a/*  /ko/a/*  /en/a/*
# 주의: 막는 것은 **공고별 지원 폼**(/o/{id}/apply)이지 채용 목록 페이지가 아니다.
# 어떤 회사는 목록 자체가 /ko/apply 라서, 단순히 "apply 로 끝나면 차단"하면
# 멀쩡한 목록을 막아버린다.
_DISALLOW_RE = re.compile(
    r"^(?:/(?:ko|en))?(?:"
    r"/o/[^/]+/apply"      # 공고별 지원 폼
    r"|/m/"                # 모바일 전용 경로
    r"|/a/"                # 관리 경로
    r")")


def is_disallowed(url):
    """robots Disallow 경로인지. 지원 폼은 절대 건드리지 않는다."""
    rest = url.split("://", 1)[-1]
    path = rest[rest.find("/"):] if "/" in rest else "/"
    path = path.split("?", 1)[0].split("#", 1)[0]
    return bool(_DISALLOW_RE.match(path))


def _fetch_html(url, timeout=25):
    """반환: (status, 최종 URL, 본문). 최종 URL 이 다르면 리다이렉트된 것이다."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return r.status, r.geturl(), raw.decode("utf-8", "replace")


def extract_openings(html):
    """페이지 HTML → 공고 배열. 구조가 바뀌면 ValueError."""
    m = _NEXT_DATA.search(html)
    if not m:
        raise ValueError("__NEXT_DATA__ 를 못 찾음(페이지 구조 변경 의심)")
    try:
        doc = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"__NEXT_DATA__ JSON 파싱 실패: {e}") from e
    props = (doc.get("props") or {}).get("pageProps") or {}
    queries = (props.get("dehydratedState") or {}).get("queries") or []
    for q in queries:
        if q.get("queryKey") == _OPENINGS_KEY:
            data = (q.get("state") or {}).get("data")
            if isinstance(data, list):
                return data
            raise ValueError("openings 항목이 배열이 아님")
    raise ValueError("openings 쿼리가 없음(목록 페이지가 아닐 수 있음)")


def _positions(raw):
    return ((raw.get("openingJobPosition") or {})
            .get("openingJobPositions") or [])


_ADMIN = re.compile(
    r"(서울특별시|서울|부산광역시|부산|대구광역시|대구|인천광역시|인천|광주광역시|광주|"
    r"대전광역시|대전|울산광역시|울산|세종특별자치시|세종|경기도|경기|강원특별자치도|강원|"
    r"충청북도|충북|충청남도|충남|전북특별자치도|전라북도|전북|전라남도|전남|"
    r"경상북도|경북|경상남도|경남|제주특별자치도|제주)")


def _region(place):
    """근무지 dict → 지역 문자열.

    주소에서 시/도만 뽑는다. 사무실 별칭(예: 회사 이름)이 location 에 들어오는
    경우가 있어 그대로 쓰면 지역 점수와 노트 표시가 둘 다 엉킨다.
    주소가 없으면 location 을 폴백으로 쓴다.
    """
    addr = (place.get("place") or "").strip()
    m = _ADMIN.search(addr)
    if m:
        rest = addr[m.end():].strip().split()
        city = rest[0] if rest else ""
        return f"{m.group(1)} {city}".strip()
    loc = (place.get("location") or "").strip()
    return loc or addr or ""


def _deadline(raw):
    """dueDate 없으면 상시채용."""
    due = raw.get("dueDate")
    if not due:
        return "상시"
    return str(due)[:10]


class GreetingHRSource(OfficialSource):
    family = "greetinghr"
    parser_version = 1

    def fetch_company(self, company):
        url = company.careers_url
        if not url:
            return self.fail(company, "careers_url 이 없음")
        if is_disallowed(url):
            # robots 가 막은 경로다. 설정이 잘못된 것이지 수집할 일이 아니다.
            return self.fail(company, f"robots Disallow 경로라 수집하지 않음: {url}")
        try:
            status, final_url, html = _fetch_html(url)
        except urllib.error.HTTPError as e:
            return self.fail(company, f"HTTP {e.code}", http_status=e.code)
        except Exception as e:  # noqa: BLE001 - 한 회사 실패가 전체를 죽이지 않게
            return self.fail(company, e)
        try:
            raw_openings = extract_openings(html)
        except ValueError as e:
            return self.fail(company, e, http_status=status)
        items = [self.to_item(company, raw) for raw in raw_openings]
        return self.ok(company, [i for i in items if i], http_status=status,
                       final_url=final_url)

    def to_item(self, company, raw):
        """GreetingHR 공고 → 공통 item dict."""
        oid = raw.get("openingId")
        if oid is None:
            return None
        item = self.base_item(company, oid, raw.get("title"))

        regions, occupations, fields = [], [], []
        cmin, cmax, employments = None, None, []
        for pos in _positions(raw):
            place = pos.get("workspacePlace") or {}
            r = _region(place)
            if r:
                regions.append(r)
            if place.get("workFromHome"):
                regions.append("재택")
            occ = (pos.get("workspaceOccupation") or {}).get("occupation")
            if occ:
                occupations.append(occ)
            fld = (pos.get("workspaceField") or {}).get("field")
            if fld:
                fields.append(fld)
            career = pos.get("jobPositionCareer") or {}
            # 한 공고에 직무가 여럿이면 가장 넓은 경력 범위를 취한다.
            if career.get("careerFrom") is not None:
                cmin = (career["careerFrom"] if cmin is None
                        else min(cmin, career["careerFrom"]))
            if career.get("careerTo") is not None:
                cmax = (career["careerTo"] if cmax is None
                        else max(cmax, career["careerTo"]))
            emp = (pos.get("jobPositionEmployment") or {}).get("employmentType")
            if emp:
                employments.append(_EMPLOYMENT.get(emp, emp))

        item["regions"] = list(dict.fromkeys(regions))
        item["depthTwos"] = list(dict.fromkeys(occupations))
        item["depthOnes"] = list(dict.fromkeys(fields))
        item["employeeTypes"] = list(dict.fromkeys(employments))
        item["careerMin"] = cmin
        item["careerMax"] = cmax
        item["_deadline"] = _deadline(raw)
        # 등록일과 상세 URL. 상세를 따로 조회할 필요가 없다.
        item["_created_at"] = raw.get("openDate")
        item["_detail_url"] = self.detail_url(company, oid)
        return item

    @staticmethod
    def detail_url(company, opening_id):
        """공고 상세 페이지. robots 의 Disallow(/apply)를 건드리지 않는 경로."""
        base = (company.careers_url or "").split("/ko/")[0].split("/en/")[0]
        return f"{base.rstrip('/')}/ko/o/{opening_id}"

    def fetch_detail(self, item):
        """목록에서 이미 전부 받았으므로 추가 요청이 없다."""
        return {
            "createdAt": item.get("_created_at"),
            "_jd_text": "",
            "redirectUrl": item.get("_detail_url"),
            "_deadline": item.get("_deadline"),
        }

    def apply_url(self, item, detail=None):
        if detail and detail.get("redirectUrl"):
            return detail["redirectUrl"]
        return item.get("_detail_url") or "#"
