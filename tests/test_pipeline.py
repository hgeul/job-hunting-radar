# -*- coding: utf-8 -*-
"""파이프라인 오케스트레이션 회귀.

함수 단위 테스트는 "함수는 그대로인데 호출 순서만 바뀐" 사고를 못 잡는다.
여기서는 외부 의존을 전부 가짜로 주입해 흐름 자체를 고정한다.
"""

import datetime as dt
import unittest

from tests.helpers import make_config, make_item

from radar import pipeline

TODAY = dt.date.today()


def days_ago(n):
    return (TODAY - dt.timedelta(days=n)).isoformat() + "T09:00:00"


class FakeDeps(pipeline.Deps):
    """네트워크·LLM 없이 파이프라인만 돌리는 의존 묶음."""

    def __init__(self, items, details, engine=("fake", None), llm=None, crawl=None):
        self.calls = {"detail": [], "llm": [], "crawl": 0, "official": None}
        self._items = items
        self._details = details
        self._engine = engine
        self._llm = llm
        self._crawl = crawl or {}
        super().__init__(
            collect=self._collect, fetch_detail=self._detail, crawl=self._do_crawl,
            resolve_engine=self._resolve, llm_score=self._score)

    def _collect(self, cfg, registry=None, with_official=False):
        self.calls["official"] = with_official
        return [dict(i) for i in self._items], [], []

    def _detail(self, item):
        self.calls["detail"].append(item["id"])
        d = self._details.get(item["id"])
        if d is None:
            raise RuntimeError("상세 없음")
        return d

    def _do_crawl(self, url_by_id, timeout=45):
        self.calls["crawl"] += 1
        return {k: "크롤된 원본 " * 50 for k in url_by_id if k in self._crawl}

    def _resolve(self, cfg, no_llm):
        if no_llm:
            return None, "--no-llm 플래그"
        name, why = self._engine
        if name is None:
            return None, why
        return {"provider": name, "model": "m"}, None

    def _score(self, engine, max_tokens, profile, item, jd):
        self.calls["llm"].append((item["id"], len(jd)))
        if self._llm and item["id"] in self._llm:
            out = self._llm[item["id"]]
            if isinstance(out, Exception):
                raise out
            return out
        return structured_llm()


def structured_llm(full=1, none=0):
    """구조화 LLM 응답 하나. FULL/NONE 개수로 적합도를 조절한다."""
    def row(n, match):
        return {"category": "language", "importance": "REQUIRED",
                "requirement": f"요구 {n}", "candidate_evidence": "근거 문장",
                "match": match, "confidence": "HIGH"}
    reqs = [row(n, "FULL") for n in range(full)]
    reqs += [row(full + n, "NONE") for n in range(none)]
    return {"requirements": reqs, "hard_blockers": [], "strengths": ["r"],
            "risks": [], "summary": "ok", "deadline": "상시", "strategy": ["s"]}


def rich(created, **kw):
    d = {"createdAt": created,
         "content": {"type": "doc",
                     "content": [{"type": "text", "text": "충실한 JD 본문 " * 40}]}}
    d.update(kw)
    return d


class TestFreshness(unittest.TestCase):
    def test_only_within_window_is_fresh(self):
        items = [make_item(id="new", title="Java 백엔드"),
                 make_item(id="old", title="Spring 백엔드")]
        deps = FakeDeps(items, {"new": rich(days_ago(1)), "old": rich(days_ago(30))})
        res = pipeline.run(make_config(), {}, "PROFILE", window=3, deps=deps)
        self.assertEqual(res.stats["fresh"], 1)
        self.assertEqual([m["id"] for m in res.matches], ["new"])

    def test_notified_is_skipped(self):
        items = [make_item(id="a", title="Java 백엔드")]
        deps = FakeDeps(items, {"a": rich(days_ago(1))})
        seen = {"a": {"first_seen": days_ago(5)[:10], "notified": True}}
        res = pipeline.run(make_config(), seen, "P", window=3, deps=deps)
        self.assertEqual(res.matches, [])
        self.assertEqual(deps.calls["detail"], [])  # 상세도 안 부른다

    def test_created_at_cache_avoids_freshness_refetch(self):
        # 캐시가 있으면 신규판정 단계는 상세를 안 부른다(예산 detail_fetched 소모 0).
        # LLM 단계는 JD 본문이 필요하므로 그때 한 번 부른다.
        items = [make_item(id="a", title="Java 백엔드")]
        deps = FakeDeps(items, {"a": rich(days_ago(1))})
        seen = {"a": {"first_seen": days_ago(5)[:10], "created_at": days_ago(1)}}
        res = pipeline.run(make_config(), seen, "P", window=3, deps=deps)
        self.assertEqual(res.stats["detail_fetched"], 0)
        self.assertEqual(deps.calls["detail"], ["a"])

    def test_created_at_cache_costs_nothing_when_stale(self):
        # 캐시가 윈도우 밖이면 상세를 아예 안 부른다.
        items = [make_item(id="a", title="Java 백엔드")]
        deps = FakeDeps(items, {"a": rich(days_ago(30))})
        seen = {"a": {"first_seen": days_ago(40)[:10], "created_at": days_ago(30)}}
        res = pipeline.run(make_config(), seen, "P", window=3, deps=deps)
        self.assertEqual(deps.calls["detail"], [])
        self.assertEqual(res.matches, [])

    def test_missing_created_at_falls_back_to_first_seen(self):
        # 등록일 없는 소스는 first_seen으로 폴백한다(영구 미신규 + 예산 잠식 방지).
        items = [make_item(id="a", title="Java 백엔드")]
        deps = FakeDeps(items, {"a": rich(None)})
        seen = {}
        res = pipeline.run(make_config(), seen, "P", window=3, deps=deps)
        self.assertEqual(seen["a"]["created_at"], TODAY.isoformat())
        self.assertEqual(res.stats["fresh"], 1)

    def test_detail_budget_stops_fetching(self):
        items = [make_item(id=f"i{n}", title="Java 백엔드") for n in range(10)]
        details = {f"i{n}": rich(days_ago(1)) for n in range(10)}
        cfg = make_config()
        cfg["search"]["max_detail_fetches"] = 3
        deps = FakeDeps(items, details)
        res = pipeline.run(cfg, {}, "P", window=3, deps=deps)
        self.assertEqual(len(deps.calls["detail"]), 3)
        self.assertEqual(res.stats["fresh"], 3)

    def test_detail_failure_skips_only_that_job(self):
        items = [make_item(id="bad", title="Java 백엔드"),
                 make_item(id="good", title="Java 백엔드")]
        deps = FakeDeps(items, {"good": rich(days_ago(1))})  # bad는 없음 → RuntimeError
        res = pipeline.run(make_config(), {}, "P", window=3, deps=deps)
        self.assertEqual([m["id"] for m in res.matches], ["good"])


class TestPrefilter(unittest.TestCase):
    def test_excluded_title_never_reaches_detail(self):
        items = [make_item(id="fe", title="프론트엔드 개발자")]
        deps = FakeDeps(items, {"fe": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3, deps=deps)
        self.assertEqual(res.stats["rule_pass"], 0)
        self.assertEqual(deps.calls["detail"], [])

    def test_zero_role_score_is_dropped(self):
        items = [make_item(id="x", title="영업 담당", depthTwos=["영업"],
                           depthOnes=["영업"])]
        deps = FakeDeps(items, {"x": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3, deps=deps)
        self.assertEqual(res.stats["rule_pass"], 0)

    def test_every_listing_is_recorded_in_seen(self):
        items = [make_item(id="fe", title="프론트엔드 개발자"),
                 make_item(id="be", title="Java 백엔드")]
        seen = {}
        pipeline.run(make_config(), seen, "P", window=3,
                     deps=FakeDeps(items, {"be": rich(days_ago(1))}))
        self.assertEqual(sorted(seen), ["be", "fe"])


class TestLlmStage(unittest.TestCase):
    def test_no_llm_falls_back_to_rule_score(self):
        items = [make_item(id="a", title="Java Spring 백엔드")]
        deps = FakeDeps(items, {"a": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3, no_llm=True, deps=deps)
        m = res.matches[0]
        self.assertIsNone(m["fit_score"])
        self.assertEqual(m["score"], m["rule"])
        self.assertEqual(res.stats["llm_note"], "--no-llm 플래그")
        self.assertEqual(deps.calls["llm"], [])

    def test_llm_failure_falls_back_to_rule_score(self):
        items = [make_item(id="a", title="Java Spring 백엔드")]
        deps = FakeDeps(items, {"a": rich(days_ago(1))},
                        llm={"a": RuntimeError("boom")})
        res = pipeline.run(make_config(), {}, "P", window=3, deps=deps)
        m = res.matches[0]
        self.assertIsNone(m["fit_score"])
        self.assertEqual(m["score"], m["rule"])
        self.assertEqual(res.stats["llm_scored"], 0)

    def test_budget_overflow_gets_rule_score_only(self):
        items = [make_item(id=f"i{n}", title="Java Spring 백엔드") for n in range(5)]
        details = {f"i{n}": rich(days_ago(1)) for n in range(5)}
        cfg = make_config()
        cfg["llm"]["max_calls_per_run"] = 2
        deps = FakeDeps(items, details)
        res = pipeline.run(cfg, {}, "P", window=3, deps=deps)
        self.assertEqual(len(deps.calls["llm"]), 2)
        self.assertEqual(sum(1 for m in res.matches if m["fit_score"] is not None), 2)
        self.assertEqual(len(res.matches), 5)


class TestEnrich(unittest.TestCase):
    def _cfg(self):
        cfg = make_config()
        cfg["enrich"] = {"enabled": True, "min_chars": 100,
                         "max_crawls_per_run": 5, "page_timeout_sec": 5}
        return cfg

    def test_thin_body_with_link_is_crawled(self):
        items = [make_item(id="a", title="Java 백엔드")]
        thin = {"createdAt": days_ago(1),
                "content": {"type": "doc",
                            "content": [{"type": "text", "text": "짧음"}]},
                "redirectUrl": "https://example.invalid/a"}
        deps = FakeDeps(items, {"a": thin}, crawl={"a"})
        res = pipeline.run(self._cfg(), {}, "P", window=3, deps=deps)
        self.assertEqual(res.stats["enriched"], 1)
        self.assertEqual(res.matches[0]["jd_source"], "원본크롤")

    def test_thin_body_without_link_is_marked_thin(self):
        items = [make_item(id="a", title="Java 백엔드")]
        thin = {"createdAt": days_ago(1),
                "content": {"type": "doc",
                            "content": [{"type": "text", "text": "짧음"}]}}
        deps = FakeDeps(items, {"a": thin})
        res = pipeline.run(self._cfg(), {}, "P", window=3, deps=deps)
        self.assertEqual(deps.calls["crawl"], 0)
        self.assertEqual(res.matches[0]["jd_source"], "thin")

    def test_crawl_failure_falls_back_to_platform_body(self):
        items = [make_item(id="a", title="Java 백엔드")]
        thin = {"createdAt": days_ago(1),
                "content": {"type": "doc",
                            "content": [{"type": "text", "text": "짧음"}]},
                "redirectUrl": "https://example.invalid/a"}
        deps = FakeDeps(items, {"a": thin}, crawl=set())  # 크롤 결과 없음
        res = pipeline.run(self._cfg(), {}, "P", window=3, deps=deps)
        self.assertEqual(res.stats["enriched"], 0)
        self.assertEqual(res.matches[0]["jd_source"], "thin")


class TestSeedMode(unittest.TestCase):
    def test_seed_marks_notified_without_matches(self):
        items = [make_item(id="a", title="Java 백엔드")]
        deps = FakeDeps(items, {"a": rich(days_ago(1))})
        seen = {}
        res = pipeline.run(make_config(), seen, "P", window=3, seed=True, deps=deps)
        self.assertTrue(res.seeded)
        self.assertEqual(res.matches, [])
        self.assertTrue(seen["a"]["notified"])
        self.assertEqual(deps.calls["llm"], [])


class TestOrdering(unittest.TestCase):
    def test_matches_sorted_by_score_desc(self):
        items = [make_item(id=f"i{n}", title="Java Spring 백엔드") for n in range(4)]
        details = {f"i{n}": rich(days_ago(1)) for n in range(4)}
        # FULL 개수를 다르게 해 적합도가 서로 다르게 나오도록.
        llm = {f"i{n}": structured_llm(full=n, none=3 - n) for n in range(4)}
        res = pipeline.run(make_config(), {}, "P", window=3,
                           deps=FakeDeps(items, details, llm=llm))
        scores = [m["score"] for m in res.matches]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
