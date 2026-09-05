# -*- coding: utf-8 -*-
"""target 표시가 실제 출력물에 닿는지: 대시보드 배지와 --list-targets."""

import datetime as dt
import io
import json
import os
import tempfile
import unittest

from tests.helpers import make_config

from radar.output.dashboard import _badge, write_dashboard
from radar.targeting import parse_registry

TODAY = dt.date.today().isoformat()


def card(**kw):
    c = {"score": 70.0, "company": "예시알파", "title": "백엔드 개발자",
         "url": "https://x.invalid/1", "verdict": "추천", "deadline": "상시",
         "one_liner": "총평", "career": "3~7년", "region": "서울",
         "sources": ["platform"], "llm": True, "date": TODAY, "target": None}
    c.update(kw)
    return c


class TestBadge(unittest.TestCase):
    def test_target_tier_comes_first(self):
        b = _badge(card(target={"id": "a", "name": "예시알파", "tier": "S"}))
        self.assertTrue(b.startswith("🎯S"), b)

    def test_no_target_is_source_badge_only(self):
        self.assertNotIn("🎯", _badge(card()))

    def test_missing_target_key_is_safe(self):
        # 구버전 state 의 카드에는 target 키가 아예 없다.
        old = card()
        del old["target"]
        self.assertNotIn("🎯", _badge(old))

    def test_no_sources_and_no_target(self):
        self.assertEqual(_badge(card(sources=None)), "")


class TestDashboardWithTargets(unittest.TestCase):
    def _write(self, seen):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_config()
            cfg["output"]["matches_dir"] = d
            path = write_dashboard(cfg, seen)
            if path is None:
                return None
            with io.open(path, encoding="utf-8") as f:
                return f.read()

    def test_tier_badge_reaches_html(self):
        seen = {"a": {"first_seen": TODAY,
                      "card": card(target={"id": "a", "name": "예시알파",
                                           "tier": "S"})}}
        doc = self._write(seen)
        self.assertIn('"src": "🎯S', doc)

    def test_old_card_without_target_still_renders(self):
        old = card()
        del old["target"]
        seen = {"a": {"first_seen": TODAY, "card": old}}
        doc = self._write(seen)
        self.assertIn("백엔드 개발자", doc)
        # 페이지 제목에도 🎯가 있으므로 배지 필드만 본다.
        self.assertNotIn('"src": "🎯', doc)

    def test_dashboard_stays_self_contained(self):
        seen = {"a": {"first_seen": TODAY,
                      "card": card(target={"id": "a", "name": "예시알파",
                                           "tier": "A"})}}
        doc = self._write(seen)
        for forbidden in ("<script src", "<link ", "@import", "fetch(",
                          "cdn.", "googleapis"):
            self.assertNotIn(forbidden, doc, forbidden)


class TestListTargets(unittest.TestCase):
    def _capture(self, registry, cfg):
        from radar import cli
        said = []
        orig, cli.log = cli.log, lambda m: said.append(m)
        try:
            cli.print_targets(registry, cfg)
        finally:
            cli.log = orig
        return "\n".join(said)

    def test_empty_registry_explains_how_to_start(self):
        from radar.targeting import EMPTY_REGISTRY
        out = self._capture(EMPTY_REGISTRY, make_config())
        self.assertIn("0곳", out)
        self.assertIn("target-companies.example.yaml", out)

    def test_lists_by_tier_and_marks_disabled(self):
        reg = parse_registry({"version": 1, "companies": [
            {"id": "aa", "name": "회사A", "tier": "S", "aliases": ["A Corp"]},
            {"id": "bb", "name": "회사B", "tier": "B", "enabled": False},
        ]})
        out = self._capture(reg, make_config(targets={"file": "t.json"}))
        self.assertIn("[S]", out)
        self.assertIn("[B]", out)
        self.assertIn("aa", out)
        self.assertIn("off", out)          # 꺼진 회사 표시
        self.assertIn("활성 1곳", out)
        # S가 B보다 먼저 나온다
        self.assertLess(out.index("[S]"), out.index("[B]"))

    def test_warns_about_missing_aliases(self):
        reg = parse_registry({"version": 1, "companies": [
            {"id": "noalias", "name": "회사A", "tier": "S"},
        ]})
        out = self._capture(reg, make_config(targets={"file": "t.json"}))
        self.assertIn("alias 없는 활성 기업", out)
        self.assertIn("noalias", out)

    def test_no_alias_warning_when_all_have_aliases(self):
        reg = parse_registry({"version": 1, "companies": [
            {"id": "ok", "name": "회사A", "tier": "S", "aliases": ["A Corp"]},
        ]})
        out = self._capture(reg, make_config(targets={"file": "t.json"}))
        self.assertNotIn("alias 없는 활성 기업", out)


class TestExampleConfigDefaults(unittest.TestCase):
    """공개 템플릿이 신규 사용자에게 매 실행 경고를 띄우지 않아야 한다."""

    def test_targets_disabled_by_default(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "config.example.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn("targets", cfg)
        self.assertFalse(cfg["targets"]["enabled"])
        self.assertEqual(cfg["targets"]["file"], "target-companies.yaml")


if __name__ == "__main__":
    unittest.main()
