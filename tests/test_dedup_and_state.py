# -*- coding: utf-8 -*-
"""소스 간 dedup·state 왕복 회귀."""

import datetime as dt
import json
import os
import tempfile
import unittest

from tests.helpers import make_config, make_item

import job_watcher as jw


class TestNormKey(unittest.TestCase):
    def test_ignores_parentheses_and_case(self):
        a = make_item(company="테스트회사", title="백엔드 개발자 (신입/경력)")
        b = make_item(company="테스트회사", title="백엔드개발자")
        self.assertEqual(jw._norm_key(a), jw._norm_key(b))

    def test_different_company_differs(self):
        a = make_item(company="회사A", title="백엔드 개발자")
        b = make_item(company="회사B", title="백엔드 개발자")
        self.assertNotEqual(jw._norm_key(a), jw._norm_key(b))


class TestDedup(unittest.TestCase):
    def test_same_source_kept_apart(self):
        # 같은 소스 안에서는 병합하지 않는다(id로 이미 구분됨).
        items = [make_item(id="1"), make_item(id="2")]
        self.assertEqual(len(jw._dedup(items)), 2)

    def test_cross_source_merged(self):
        a = make_item(id="1", source="platform")
        b = make_item(id="9", source="platform_b")
        out = jw._dedup([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(sorted(out[0]["_sources"]), ["platform", "platform_b"])

    def test_deadline_bearing_source_wins_body(self):
        a = make_item(id="1", source="platform")
        b = make_item(id="9", source="platform_b", _deadline="2026-09-30")
        out = jw._dedup([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["_deadline"], "2026-09-30")
        self.assertEqual(sorted(out[0]["_sources"]), ["platform", "platform_b"])

    def test_existing_deadline_not_overwritten(self):
        a = make_item(id="1", source="platform", _deadline="2026-09-01")
        b = make_item(id="9", source="platform_b", _deadline="2026-09-30")
        out = jw._dedup([a, b])
        self.assertEqual(out[0]["_deadline"], "2026-09-01")

    def test_different_titles_not_merged(self):
        # false merge 방지가 중복 표시보다 중요하다.
        a = make_item(id="1", source="platform", title="백엔드 개발자")
        b = make_item(id="9", source="platform_b", title="프론트엔드 개발자")
        self.assertEqual(len(jw._dedup([a, b])), 2)


class TestStateRoundtrip(unittest.TestCase):
    def _cfg(self, tmpdir):
        cfg = make_config()
        cfg["output"]["state_file"] = os.path.join(tmpdir, "s.json")
        return cfg

    def test_save_load(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            today = dt.date.today().isoformat()
            seen = {"a": {"first_seen": today, "notified": True, "rule": 80.0}}
            jw.save_seen(cfg, seen)
            self.assertEqual(jw.load_seen(cfg), seen)

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(jw.load_seen(self._cfg(d)), {})

    def test_corrupt_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            with open(cfg["output"]["state_file"], "w", encoding="utf-8") as f:
                f.write("{ not json")
            self.assertEqual(jw.load_seen(cfg), {})

    def test_retention_prunes_old(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            cfg["output"]["seen_retention_days"] = 10
            old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
            new = dt.date.today().isoformat()
            jw.save_seen(cfg, {"old": {"first_seen": old}, "new": {"first_seen": new}})
            self.assertEqual(list(jw.load_seen(cfg)), ["new"])

    def test_v1_state_without_new_fields_loads(self):
        # 기존 실사용 state는 schema_version·card가 없다. 그대로 읽혀야 한다.
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            v1 = {"84d1626a": {"first_seen": dt.date.today().isoformat(),
                               "title": "인프라 엔지니어",
                               "created_at": "2026-05-26T21:11:20.161321"}}
            with open(cfg["output"]["state_file"], "w", encoding="utf-8") as f:
                json.dump(v1, f, ensure_ascii=False)
            got = jw.load_seen(cfg)
            self.assertEqual(got["84d1626a"]["title"], "인프라 엔지니어")
            self.assertIsNone(got["84d1626a"].get("notified"))


if __name__ == "__main__":
    unittest.main()
