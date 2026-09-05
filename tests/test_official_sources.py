# -*- coding: utf-8 -*-
"""공식 채용페이지 어댑터와 수집 오케스트레이션.

회사명은 전부 가상이다. 네트워크를 타지 않고 합성 HTML 로 검증한다.
"""

import json
import unittest

from radar.sources.official import OFFICIAL_ADAPTERS, collect_official
from radar.sources.official.base import OfficialSource
from radar.sources.official.greetinghr import (
    GreetingHRSource, _region, extract_openings,
)
from radar.targeting import parse_registry


def reg(*companies, defaults=None):
    d = {"version": 1, "companies": list(companies)}
    if defaults:
        d["defaults"] = defaults
    return parse_registry(d)


def company(cid, name, **kw):
    c = {"id": cid, "name": name}
    c.update(kw)
    return c


def opening(oid, title, **kw):
    pos = {
        "workspaceField": {"field": kw.get("field", "기술")},
        "workspaceOccupation": {"occupation": kw.get("occupation", "서버")},
        "workspacePlace": {"location": kw.get("location", "판교"),
                           "place": kw.get("place", "경기도 성남시 분당구 1"),
                           "workFromHome": kw.get("wfh", False)},
        "jobPositionCareer": {"careerFrom": kw.get("cmin", 3),
                              "careerTo": kw.get("cmax")},
        "jobPositionEmployment": {"employmentType": kw.get("emp", "FULL_TIME_WORKER")},
    }
    return {
        "openingId": oid, "title": title,
        "openDate": kw.get("open", "2026-09-01T00:00:00Z"),
        "dueDate": kw.get("due"),
        "openingJobPosition": {"openingJobPositions": kw.get("positions", [pos])},
    }


def page(openings):
    payload = {"props": {"pageProps": {"dehydratedState": {"queries": [
        {"queryKey": ["getCareerBaseInfo"], "state": {"data": {}}},
        {"queryKey": ["openings"], "state": {"data": openings}},
    ]}}}}
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(payload, ensure_ascii=False)
            + "</script></body></html>")


class TestExtractOpenings(unittest.TestCase):
    def test_extracts_list(self):
        got = extract_openings(page([opening(1, "서버 개발자")]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["title"], "서버 개발자")

    def test_missing_next_data(self):
        with self.assertRaises(ValueError) as cm:
            extract_openings("<html><body>없음</body></html>")
        self.assertIn("__NEXT_DATA__", str(cm.exception))

    def test_broken_json(self):
        html = ('<script id="__NEXT_DATA__" type="application/json">'
                "{not json}</script>")
        with self.assertRaises(ValueError):
            extract_openings(html)

    def test_no_openings_query(self):
        payload = {"props": {"pageProps": {"dehydratedState": {"queries": [
            {"queryKey": ["other"], "state": {"data": []}}]}}}}
        html = ('<script id="__NEXT_DATA__">' + json.dumps(payload) + "</script>")
        with self.assertRaises(ValueError) as cm:
            extract_openings(html)
        self.assertIn("openings", str(cm.exception))

    def test_empty_list_is_valid(self):
        # 공고 0건은 정상이다. 실패가 아니다.
        self.assertEqual(extract_openings(page([])), [])


class TestRegion(unittest.TestCase):
    def test_extracts_admin_area(self):
        self.assertEqual(_region({"place": "경기도 성남시 분당구 판교역로 166"}),
                         "경기도 성남시")
        self.assertEqual(_region({"place": "서울특별시 강남구 테헤란로 133"}),
                         "서울특별시 강남구")

    def test_falls_back_to_location(self):
        # 주소가 없으면 location 을 쓴다.
        self.assertEqual(_region({"location": "판교"}), "판교")

    def test_office_nickname_not_used_when_address_exists(self):
        # location 에 사무실 별칭이 들어와도 주소가 있으면 주소를 쓴다.
        got = _region({"location": "본사", "place": "서울특별시 마포구 1"})
        self.assertEqual(got, "서울특별시 마포구")

    def test_empty(self):
        self.assertEqual(_region({}), "")


class TestToItem(unittest.TestCase):
    def setUp(self):
        self.src = GreetingHRSource()
        self.c = reg(company("alpha", "예시알파",
                             careers_url="https://careers.alpha.invalid/ko/recruiting",
                             platform_family="greetinghr",
                             collection_status="generic_adapter_priority")).by_id("alpha")

    def test_field_mapping(self):
        it = self.src.to_item(self.c, opening(
            77, "서버 개발자", cmin=3, cmax=7, due="2026-10-01T00:00:00Z"))
        self.assertEqual(it["id"], "greetinghr:alpha:77")
        self.assertEqual(it["_source"], "greetinghr")
        self.assertEqual(it["_canonical_company"], "alpha")
        self.assertEqual(it["company"]["name"], "예시알파")
        self.assertEqual(it["title"], "서버 개발자")
        self.assertEqual(it["careerMin"], 3)
        self.assertEqual(it["careerMax"], 7)
        self.assertEqual(it["employeeTypes"], ["정규직"])
        self.assertEqual(it["regions"], ["경기도 성남시"])
        self.assertEqual(it["_deadline"], "2026-10-01")
        self.assertEqual(it["_created_at"], "2026-09-01T00:00:00Z")

    def test_no_due_date_is_rolling(self):
        it = self.src.to_item(self.c, opening(1, "서버", due=None))
        self.assertEqual(it["_deadline"], "상시")

    def test_multiple_positions_widen_career_range(self):
        p1 = opening(1, "x", cmin=3, cmax=5)["openingJobPosition"]["openingJobPositions"][0]
        p2 = opening(2, "y", cmin=7, cmax=10)["openingJobPosition"]["openingJobPositions"][0]
        it = self.src.to_item(self.c, opening(9, "서버", positions=[p1, p2]))
        self.assertEqual((it["careerMin"], it["careerMax"]), (3, 10))

    def test_work_from_home_adds_region(self):
        it = self.src.to_item(self.c, opening(1, "서버", wfh=True))
        self.assertIn("재택", it["regions"])

    def test_missing_opening_id_is_dropped(self):
        self.assertIsNone(self.src.to_item(self.c, {"title": "id 없음"}))

    def test_detail_url_avoids_apply_path(self):
        url = self.src.detail_url(self.c, 77)
        self.assertEqual(url, "https://careers.alpha.invalid/ko/o/77")
        self.assertNotIn("/apply", url)

    def test_fetch_detail_needs_no_extra_request(self):
        it = self.src.to_item(self.c, opening(1, "서버"))
        d = self.src.fetch_detail(it)
        self.assertEqual(d["createdAt"], it["_created_at"])
        self.assertEqual(d["redirectUrl"], it["_detail_url"])


class TestFetchCompany(unittest.TestCase):
    """네트워크는 모듈 함수를 갈아끼워 대신한다."""

    def setUp(self):
        self.src = GreetingHRSource()
        self.reg = reg(company(
            "alpha", "예시알파", careers_url="https://careers.alpha.invalid/",
            platform_family="greetinghr",
            collection_status="generic_adapter_priority"))
        self.c = self.reg.by_id("alpha")

    def _patch(self, fn):
        from radar.sources.official import greetinghr as g
        self._orig = g._fetch_html
        g._fetch_html = fn

    def tearDown(self):
        from radar.sources.official import greetinghr as g
        if hasattr(self, "_orig"):
            g._fetch_html = self._orig

    def test_success(self):
        self._patch(lambda url, timeout=25: (200, url, page([opening(1, "서버 개발자")])))
        r = self.src.fetch_company(self.c)
        self.assertTrue(r.ok)
        self.assertEqual(r.count, 1)
        self.assertEqual(r.http_status, 200)
        self.assertIsNone(r.error)

    def test_zero_jobs_is_success_not_failure(self):
        # 공고 0건과 수집 실패는 절대 같은 상태가 아니다.
        self._patch(lambda url, timeout=25: (200, url, page([])))
        r = self.src.fetch_company(self.c)
        self.assertTrue(r.ok)
        self.assertEqual(r.count, 0)

    def test_http_error_is_failure(self):
        import urllib.error

        def boom(url, timeout=25):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        self._patch(boom)
        r = self.src.fetch_company(self.c)
        self.assertFalse(r.ok)
        self.assertEqual(r.http_status, 404)
        self.assertIn("404", r.error)

    def test_network_error_is_failure(self):
        def boom(url, timeout=25):
            raise OSError("연결 실패")

        self._patch(boom)
        r = self.src.fetch_company(self.c)
        self.assertFalse(r.ok)
        self.assertIn("연결", r.error)

    def test_structure_change_is_failure(self):
        self._patch(lambda url, timeout=25: (200, url, "<html>구조 변경</html>"))
        r = self.src.fetch_company(self.c)
        self.assertFalse(r.ok)
        self.assertIn("__NEXT_DATA__", r.error)

    def test_final_url_recorded_on_redirect(self):
        # careers_url 이 죽어 다른 곳으로 넘어가면 사람이 즉시 알아야 한다.
        self._patch(lambda url, timeout=25:
                    (200, "https://moved.invalid/new", page([opening(1, "서버")])))
        r = self.src.fetch_company(self.c)
        self.assertEqual(r.final_url, "https://moved.invalid/new")

    def test_missing_careers_url_is_failure(self):
        c = reg(company("b", "예시베타", platform_family="greetinghr",
                        collection_status="adapter_candidate")).by_id("b")
        r = self.src.fetch_company(c)
        self.assertFalse(r.ok)
        self.assertIn("careers_url", r.error)


class FakeAdapter(OfficialSource):
    family = "fake_ats"

    def __init__(self, per_company=None, fail_ids=()):
        # 이름을 fail_ids 로 둔다. self.fail 로 두면 부모의 fail() 헬퍼를 가린다.
        self.per_company = per_company or {}
        self.fail_ids = set(fail_ids)
        self.seen = []

    def fetch_company(self, company):
        self.seen.append(company.id)
        if company.id in self.fail_ids:
            return self.fail(company, "합성 실패")
        n = self.per_company.get(company.id, 0)
        items = [self.base_item(company, i, f"공고{i}") for i in range(n)]
        return self.ok(company, items)



class TestCollectOfficial(unittest.TestCase):
    def setUp(self):
        self._orig = dict(OFFICIAL_ADAPTERS)

    def tearDown(self):
        OFFICIAL_ADAPTERS.clear()
        OFFICIAL_ADAPTERS.update(self._orig)

    def _install(self, adapter):
        OFFICIAL_ADAPTERS.clear()
        OFFICIAL_ADAPTERS[adapter.family] = adapter

    def test_collects_all_companies_in_family(self):
        a = FakeAdapter({"c1": 2, "c2": 3})
        self._install(a)
        r = reg(company("c1", "회사1", platform_family="fake_ats",
                        collection_status="generic_adapter_priority"),
                company("c2", "회사2", platform_family="fake_ats",
                        collection_status="generic_adapter_priority"))
        items, results = collect_official(r, delay=0)
        self.assertEqual(len(items), 5)
        self.assertEqual(sorted(a.seen), ["c1", "c2"])
        self.assertTrue(all(x.ok for x in results))

    def test_skips_companies_needing_research(self):
        a = FakeAdapter({"c1": 1, "c2": 1})
        self._install(a)
        r = reg(company("c1", "회사1", platform_family="fake_ats",
                        collection_status="adapter_candidate"),
                company("c2", "회사2", platform_family="fake_ats",
                        collection_status="restricted_research_needed"))
        items, results = collect_official(r, delay=0)
        self.assertEqual(a.seen, ["c1"])
        self.assertEqual(len(results), 1)

    def test_skips_family_without_adapter(self):
        self._install(FakeAdapter({"c1": 1}))
        r = reg(company("c1", "회사1", platform_family="unknown_ats",
                        collection_status="adapter_candidate"))
        items, results = collect_official(r, delay=0)
        self.assertEqual(items, [])
        self.assertEqual(results, [])

    def test_one_failure_does_not_stop_others(self):
        a = FakeAdapter({"c1": 2, "c2": 4}, fail_ids={"c1"})
        self._install(a)
        r = reg(company("c1", "회사1", platform_family="fake_ats",
                        collection_status="adapter_candidate"),
                company("c2", "회사2", platform_family="fake_ats",
                        collection_status="adapter_candidate"))
        items, results = collect_official(r, delay=0)
        self.assertEqual(len(items), 4)
        self.assertEqual(sorted(x.company_id for x in results if not x.ok), ["c1"])

    def test_empty_registry(self):
        self.assertEqual(collect_official(None, delay=0), ([], []))
        self.assertEqual(collect_official(reg(), delay=0), ([], []))

    def test_disabled_company_skipped(self):
        a = FakeAdapter({"c1": 1})
        self._install(a)
        r = reg(company("c1", "회사1", platform_family="fake_ats", enabled=False,
                        collection_status="adapter_candidate"))
        items, results = collect_official(r, delay=0)
        self.assertEqual(a.seen, [])


class TestRobotsGuard(unittest.TestCase):
    """robots Disallow 경로는 코드가 막는다. 문서에만 적어두면 안 지켜진다."""

    def test_apply_paths_blocked(self):
        from radar.sources.official.greetinghr import is_disallowed
        for u in ("https://a.invalid/ko/o/1/apply",
                  "https://a.invalid/o/1/apply",
                  "https://a.invalid/en/o/1/apply"):
            self.assertTrue(is_disallowed(u), u)

    def test_m_and_a_paths_blocked(self):
        from radar.sources.official.greetinghr import is_disallowed
        for u in ("https://a.invalid/m/x", "https://a.invalid/a/x",
                  "https://a.invalid/ko/a/x"):
            self.assertTrue(is_disallowed(u), u)

    def test_listing_and_detail_allowed(self):
        from radar.sources.official.greetinghr import is_disallowed
        for u in ("https://a.invalid/", "https://a.invalid/ko/recruiting",
                  "https://a.invalid/ko/openposition",
                  "https://a.invalid/ko/o/77",
                  "https://a.invalid/ko/apply"):
            self.assertFalse(is_disallowed(u), u)

    def test_fetch_refuses_disallowed_url(self):
        c = reg(company("x", "예시엑스", careers_url="https://a.invalid/m/list",
                        platform_family="greetinghr",
                        collection_status="adapter_candidate")).by_id("x")
        r = GreetingHRSource().fetch_company(c)
        self.assertFalse(r.ok)
        self.assertIn("robots", r.error)


class TestAdapterContract(unittest.TestCase):
    """fetch_detail·apply_url 을 빠뜨린 어댑터는 등록 시점에 드러나야 한다."""

    def test_base_declares_all_three(self):
        class Incomplete(OfficialSource):
            family = "incomplete"

        inc = Incomplete()
        for fn, args in (("fetch_company", (None,)), ("fetch_detail", (None,)),
                         ("apply_url", (None,))):
            with self.assertRaises(NotImplementedError, msg=fn):
                getattr(inc, fn)(*args)


class TestRealAdapterRegistered(unittest.TestCase):
    def test_greetinghr_is_registered(self):
        self.assertIn("greetinghr", OFFICIAL_ADAPTERS)
        self.assertIsInstance(OFFICIAL_ADAPTERS["greetinghr"], GreetingHRSource)


if __name__ == "__main__":
    unittest.main()
