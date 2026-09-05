# -*- coding: utf-8 -*-
"""Discovery / Target / All 모드 분리.

가장 중요한 회귀: **감시 대상 기업이 0곳이면 세 모드가 전부 기존 동작과 같아야 한다.**
기존 사용자는 target-companies.json 이 없으므로 이게 하위호환의 전부다.
"""

import datetime as dt
import unittest

from tests.helpers import make_config, make_item
from tests.test_pipeline import FakeDeps, days_ago, rich

from radar import pipeline
from radar.targeting import parse_registry

TODAY = dt.date.today()


def registry(*companies):
    return parse_registry({"version": 1, "companies": list(companies)})


TARGET_ALPHA = {"id": "alpha", "name": "예시알파",
                "aliases": ["Example Alpha"], "tier": "S"}


class TestBackwardCompat(unittest.TestCase):
    """레지스트리가 비면 mode 와 무관하게 기존 discovery 동작."""

    def _items(self):
        return [make_item(id="hit", title="Java 백엔드", company="아무회사"),
                make_item(id="miss", title="프론트엔드 개발자", company="아무회사")]

    def _details(self):
        return {"hit": rich(days_ago(1)), "miss": rich(days_ago(1))}

    def test_all_equals_discovery_without_registry(self):
        outs = {}
        for mode in ("all", "discovery"):
            deps = FakeDeps(self._items(), self._details())
            res = pipeline.run(make_config(), {}, "P", window=3,
                               mode=mode, deps=deps)
            outs[mode] = [(m["id"], m["score"], m["target"]) for m in res.matches]
        self.assertEqual(outs["all"], outs["discovery"])
        self.assertEqual([m[0] for m in outs["all"]], ["hit"])

    def test_target_mode_without_registry_finds_nothing(self):
        deps = FakeDeps(self._items(), self._details())
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="target", deps=deps)
        self.assertEqual(res.matches, [])

    def test_target_field_is_none_without_registry(self):
        deps = FakeDeps(self._items(), self._details())
        res = pipeline.run(make_config(), {}, "P", window=3, deps=deps)
        self.assertIsNone(res.matches[0]["target"])
        self.assertEqual(res.matches[0]["origin"], "discovery")
        self.assertEqual(res.targets(), [])

    def test_unknown_mode_rejected(self):
        deps = FakeDeps(self._items(), self._details())
        with self.assertRaises(ValueError):
            pipeline.run(make_config(), {}, "P", window=3, mode="nope", deps=deps)


class TestTargetRadar(unittest.TestCase):
    def test_target_bypasses_rule_threshold(self):
        # 규칙 점수가 문턱(45) 미만인 공고. discovery 는 버리고 target 은 잡는다.
        low = make_item(id="low", title="사업개발 매니저", company="예시알파",
                        depthTwos=["기타"], depthOnes=["기타"],
                        regions=["부산"], employeeTypes=["계약직"],
                        careerMin=10, careerMax=20)
        deps = FakeDeps([low], {"low": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="discovery", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual(res.matches, [])

        deps = FakeDeps([low], {"low": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="all", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual([m["id"] for m in res.matches], ["low"])
        self.assertEqual(res.matches[0]["target"]["tier"], "S")

    def test_target_matched_by_alias(self):
        it = make_item(id="a", title="Java 백엔드", company="Example Alpha Inc.")
        deps = FakeDeps([it], {"a": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="target", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual(res.matches[0]["target"]["id"], "alpha")

    def test_non_target_company_ignored_in_target_mode(self):
        it = make_item(id="a", title="Java 백엔드", company="무관회사")
        deps = FakeDeps([it], {"a": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="target", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual(res.matches, [])

    def test_disabled_target_ignored(self):
        off = dict(TARGET_ALPHA, enabled=False)
        it = make_item(id="a", title="사업개발 매니저", company="예시알파",
                       depthTwos=["기타"], depthOnes=["기타"])
        deps = FakeDeps([it], {"a": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="all", registry=registry(off), deps=deps)
        self.assertEqual(res.matches, [])

    def test_target_still_respects_global_exclude_without_roles(self):
        # roles 설정이 없으면 전역 제외 키워드가 적용된다.
        # target 이라고 디자이너 공고까지 받아보고 싶진 않다.
        it = make_item(id="a", title="프론트엔드 개발자", company="예시알파")
        deps = FakeDeps([it], {"a": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="all", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual(res.matches, [])

    def test_company_roles_override_global_exclude(self):
        # 회사가 자기 roles 를 정의하면 그게 기준이다.
        c = dict(TARGET_ALPHA, roles={"include": ["프론트엔드"], "exclude": []})
        it = make_item(id="a", title="프론트엔드 개발자", company="예시알파")
        deps = FakeDeps([it], {"a": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="all", registry=registry(c), deps=deps)
        self.assertEqual([m["id"] for m in res.matches], ["a"])

    def test_company_roles_exclude_drops_job(self):
        c = dict(TARGET_ALPHA, roles={"include": ["백엔드"], "exclude": ["인턴"]})
        items = [make_item(id="ok", title="백엔드 개발자", company="예시알파"),
                 make_item(id="no", title="백엔드 인턴", company="예시알파")]
        deps = FakeDeps(items, {"ok": rich(days_ago(1)), "no": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="target", registry=registry(c), deps=deps)
        self.assertEqual([m["id"] for m in res.matches], ["ok"])

    def test_target_still_respects_freshness(self):
        # target 이라도 오래된 공고를 새로 알리지는 않는다.
        it = make_item(id="a", title="Java 백엔드", company="예시알파")
        deps = FakeDeps([it], {"a": rich(days_ago(30))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="target", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual(res.matches, [])

    def test_target_still_respects_notified(self):
        it = make_item(id="a", title="Java 백엔드", company="예시알파")
        deps = FakeDeps([it], {"a": rich(days_ago(1))})
        seen = {"a": {"first_seen": days_ago(5)[:10], "notified": True}}
        res = pipeline.run(make_config(), seen, "P", window=3,
                           mode="target", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual(res.matches, [])


class TestNoDoubleCount(unittest.TestCase):
    """두 레이더가 같은 공고를 잡아도 매칭은 하나여야 한다(중복 알림 방지)."""

    def test_single_match_when_both_radars_hit(self):
        it = make_item(id="a", title="Java Spring 백엔드", company="예시알파")
        deps = FakeDeps([it], {"a": rich(days_ago(1))})
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="all", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual(len(res.matches), 1)
        self.assertEqual(len(deps.calls["llm"]), 1)
        # 둘 다 잡았으면 target 정보가 붙는다(알림에서 구분되도록).
        self.assertIsNotNone(res.matches[0]["target"])

    def test_stats_count_both_radars(self):
        items = [make_item(id="both", title="Java Spring 백엔드", company="예시알파"),
                 make_item(id="disc", title="Java Spring 백엔드", company="무관회사"),
                 make_item(id="tgt", title="사업개발", company="예시알파",
                           depthTwos=["기타"], depthOnes=["기타"])]
        details = {k: rich(days_ago(1)) for k in ("both", "disc", "tgt")}
        deps = FakeDeps(items, details)
        res = pipeline.run(make_config(), {}, "P", window=3,
                           mode="all", registry=registry(TARGET_ALPHA), deps=deps)
        self.assertEqual(res.stats["discovery_candidates"], 2)  # both, disc
        self.assertEqual(res.stats["target_candidates"], 2)     # both, tgt
        self.assertEqual(res.stats["rule_pass"], 3)             # 합집합
        self.assertEqual(len(res.matches), 3)
        self.assertEqual(res.stats["target_fresh"], 2)
        self.assertEqual(sorted(m["id"] for m in res.targets()), ["both", "tgt"])


class TestModeIsolation(unittest.TestCase):
    def _fixture(self):
        items = [make_item(id="disc", title="Java Spring 백엔드", company="무관회사"),
                 make_item(id="tgt", title="사업개발", company="예시알파",
                           depthTwos=["기타"], depthOnes=["기타"])]
        return items, {k: rich(days_ago(1)) for k in ("disc", "tgt")}

    def test_discovery_only(self):
        items, details = self._fixture()
        res = pipeline.run(make_config(), {}, "P", window=3, mode="discovery",
                           registry=registry(TARGET_ALPHA),
                           deps=FakeDeps(items, details))
        self.assertEqual([m["id"] for m in res.matches], ["disc"])

    def test_target_only(self):
        items, details = self._fixture()
        res = pipeline.run(make_config(), {}, "P", window=3, mode="target",
                           registry=registry(TARGET_ALPHA),
                           deps=FakeDeps(items, details))
        self.assertEqual([m["id"] for m in res.matches], ["tgt"])

    def test_all(self):
        items, details = self._fixture()
        res = pipeline.run(make_config(), {}, "P", window=3, mode="all",
                           registry=registry(TARGET_ALPHA),
                           deps=FakeDeps(items, details))
        self.assertEqual(sorted(m["id"] for m in res.matches), ["disc", "tgt"])


class TestOutputConsistency(unittest.TestCase):
    """cli 의 notified 기준과 note 의 수록 기준이 어긋나면 공고가 영영 사라진다."""

    def test_low_score_target_survives_min_score_in_note(self):
        import os
        import tempfile

        from radar.models import match_card
        from radar.output.note import write_note

        it = make_item(id="a", title="사업개발", company="예시알파",
                       depthTwos=["기타"], depthOnes=["기타"],
                       regions=["부산"], employeeTypes=["계약직"])
        deps = FakeDeps([it], {"a": rich(days_ago(1))}, engine=(None, "--no-llm"))
        cfg = make_config()
        res = pipeline.run(cfg, {}, "P", window=3, mode="all",
                           registry=registry(TARGET_ALPHA), deps=deps)
        m = res.matches[0]
        self.assertLess(m["score"], cfg["output"]["min_score_in_note"])

        with tempfile.TemporaryDirectory() as d:
            cfg["output"]["matches_dir"] = d
            path, n = write_note(cfg, res.matches, res.stats)
            self.assertEqual(n, 1)  # 문턱 미달인데도 수록돼야 한다
            with open(path, encoding="utf-8") as f:
                body = f.read()
            self.assertIn("감시 대상 기업", body)
            self.assertIn("S tier", body)
            self.assertIn("targets: 1", body)
        # 카드에도 target 정보가 실린다(대시보드용)
        card = match_card(m, "2026-09-05")
        self.assertEqual(card["target"]["tier"], "S")

    def test_telegram_includes_low_score_target(self):
        from radar.notify import telegram as tg_mod

        sent = {}

        def fake_post(url, data, timeout=15):
            sent["caption"] = data.get("text", "")
            return {"ok": True}

        orig = tg_mod.http_post_json
        tg_mod.http_post_json = fake_post
        try:
            cfg = make_config()
            cfg["notify"]["telegram"] = {"enabled": True, "threshold": 50,
                                         "chat_id": "1",
                                         "bot_token_env": "FAKE_TG_TOKEN"}
            os_env = __import__("os").environ
            os_env["FAKE_TG_TOKEN"] = "x"
            matches = [{"id": "a", "score": 20.0, "company": "예시알파",
                        "title": "사업개발", "rule": 20.0, "llm": None,
                        "target": {"id": "alpha", "name": "예시알파", "tier": "S"}}]
            tg_mod.send_telegram(cfg, matches, {"window": 3}, None)
            self.assertIn("예시알파", sent["caption"])
            self.assertIn("🎯S", sent["caption"])
        finally:
            tg_mod.http_post_json = orig
            __import__("os").environ.pop("FAKE_TG_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
