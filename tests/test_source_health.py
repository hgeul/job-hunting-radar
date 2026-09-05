# -*- coding: utf-8 -*-
"""소스 건강 기록.

핵심 불변식: **`공고 0건`과 `수집 실패`를 같은 상태로 두지 않는다.**
감시 대상이 수십 곳이면 조용한 실패를 사람이 눈치채지 못한다.
"""

import datetime as dt
import json
import os
import tempfile
import unittest

from tests.helpers import make_config

from radar import health
from radar.sources.official.base import CompanyFetchResult


def result(cid, family="fake_ats", n=0, ok=True, error=None, status=200):
    r = CompanyFetchResult(cid, family, [{"i": i} for i in range(n)],
                           ok=ok, error=error, http_status=status)
    return r


class TestSidecarPath(unittest.TestCase):
    def test_derived_from_state_file(self):
        cfg = make_config()
        cfg["output"]["state_file"] = "state/hong.json"
        self.assertTrue(health.health_path(cfg).endswith("hong.sources.json"))

    def test_does_not_touch_state_file(self):
        # 기존 state 는 {공고id: 레코드} 라 최상위에 다른 키를 넣으면 깨진다.
        cfg = make_config()
        self.assertNotEqual(health.health_path(cfg), cfg["output"]["state_file"])


class TestRecord(unittest.TestCase):
    def test_success_sets_last_success_and_clears_failures(self):
        doc = health.load_health(make_config())
        health.record(doc, [result("c1", n=5)])
        rec = doc["sources"]["fake_ats:c1"]
        self.assertEqual(rec["jobs_seen"], 5)
        self.assertIsNone(rec["last_error"])
        self.assertEqual(rec["consecutive_failures"], 0)
        self.assertTrue(rec["last_success_at"])

    def test_zero_jobs_is_success(self):
        doc = health.load_health(make_config())
        health.record(doc, [result("c1", n=0)])
        rec = doc["sources"]["fake_ats:c1"]
        self.assertEqual(rec["jobs_seen"], 0)
        self.assertIsNone(rec["last_error"])
        self.assertEqual(rec["consecutive_failures"], 0)
        self.assertTrue(rec["last_success_at"])

    def test_failure_keeps_previous_success_time(self):
        # "언제부터 안 되는가"를 알려면 마지막 성공 시각이 남아야 한다.
        doc = health.load_health(make_config())
        health.record(doc, [result("c1", n=3)])
        first_success = doc["sources"]["fake_ats:c1"]["last_success_at"]
        health.record(doc, [result("c1", ok=False, error="HTTP 404", status=404)])
        rec = doc["sources"]["fake_ats:c1"]
        self.assertEqual(rec["last_success_at"], first_success)
        self.assertEqual(rec["last_error"], "HTTP 404")
        self.assertEqual(rec["consecutive_failures"], 1)
        self.assertEqual(rec["jobs_seen"], 3)  # 마지막으로 본 건수는 유지

    def test_consecutive_failures_accumulate_then_reset(self):
        doc = health.load_health(make_config())
        for _ in range(3):
            health.record(doc, [result("c1", ok=False, error="boom")])
        self.assertEqual(doc["sources"]["fake_ats:c1"]["consecutive_failures"], 3)
        health.record(doc, [result("c1", n=1)])
        self.assertEqual(doc["sources"]["fake_ats:c1"]["consecutive_failures"], 0)

    def test_parser_version_recorded(self):
        doc = health.load_health(make_config())
        r = result("c1", n=1)
        r.parser_version = 7
        health.record(doc, [r])
        self.assertEqual(doc["sources"]["fake_ats:c1"]["parser_version"], 7)


class TestWarnings(unittest.TestCase):
    def test_warns_only_past_threshold(self):
        doc = health.load_health(make_config())
        health.record(doc, [result("c1", ok=False, error="boom")])
        self.assertEqual(health.warnings(doc), [])  # 1회는 봐준다
        health.record(doc, [result("c1", ok=False, error="boom")])
        w = health.warnings(doc)
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["source_id"], "fake_ats:c1")
        self.assertEqual(w[0]["failures"], 2)

    def test_zero_jobs_never_warns(self):
        doc = health.load_health(make_config())
        for _ in range(5):
            health.record(doc, [result("c1", n=0)])
        self.assertEqual(health.warnings(doc), [])

    def test_recovery_clears_warning(self):
        doc = health.load_health(make_config())
        for _ in range(3):
            health.record(doc, [result("c1", ok=False, error="boom")])
        self.assertTrue(health.warnings(doc))
        health.record(doc, [result("c1", n=2)])
        self.assertEqual(health.warnings(doc), [])


class TestEmptiedDetection(unittest.TestCase):
    """수집은 되는데 있던 공고가 0건이 되는 것도 사건이다.

    "0건 vs 실패" 구분의 나머지 반쪽. 70건이던 회사가 0건이 됐는데 아무 말이
    없으면 실패를 감시하는 의미가 절반으로 준다.
    """

    def test_drop_to_zero_warns(self):
        doc = health.load_health(make_config())
        health.record(doc, [result("c1", n=70)])
        self.assertEqual(health.warnings(doc), [])
        health.record(doc, [result("c1", n=0)])
        w = health.warnings(doc)
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["kind"], "emptied")
        self.assertEqual(w[0]["was"], 70)

    def test_always_zero_never_warns(self):
        # 처음부터 공고가 없던 회사는 정상이다.
        doc = health.load_health(make_config())
        for _ in range(3):
            health.record(doc, [result("c1", n=0)])
        self.assertEqual(health.warnings(doc), [])

    def test_recovery_clears_emptied(self):
        doc = health.load_health(make_config())
        health.record(doc, [result("c1", n=5)])
        health.record(doc, [result("c1", n=0)])
        self.assertTrue(health.warnings(doc))
        health.record(doc, [result("c1", n=5)])
        self.assertEqual(health.warnings(doc), [])

    def test_failure_takes_precedence_over_emptied(self):
        doc = health.load_health(make_config())
        health.record(doc, [result("c1", n=9)])
        health.record(doc, [result("c1", n=0)])
        for _ in range(2):
            health.record(doc, [result("c1", ok=False, error="boom")])
        kinds = [w["kind"] for w in health.warnings(doc)]
        self.assertEqual(kinds, ["failed"])


class TestSummarize(unittest.TestCase):
    def test_separates_empty_from_failed(self):
        s = health.summarize([
            result("a", n=3), result("b", n=0),
            result("c", ok=False, error="boom"),
        ])
        self.assertEqual(s["checked"], 3)
        self.assertEqual(s["ok"], 2)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["empty"], 1)
        self.assertEqual(s["jobs"], 3)
        self.assertEqual(s["failed_ids"], ["fake_ats:c"])
        self.assertEqual(s["empty_ids"], ["fake_ats:b"])

    def test_empty_input(self):
        s = health.summarize([])
        self.assertEqual((s["checked"], s["ok"], s["failed"]), (0, 0, 0))


class TestPersistence(unittest.TestCase):
    def _cfg(self, d):
        cfg = make_config()
        cfg["output"]["state_file"] = os.path.join(d, "p.json")
        return cfg

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            doc = health.load_health(cfg)
            health.record(doc, [result("c1", n=4)])
            health.save_health(cfg, doc)
            again = health.load_health(cfg)
        self.assertEqual(again["sources"]["fake_ats:c1"]["jobs_seen"], 4)
        self.assertEqual(again["schema_version"], health.SCHEMA_VERSION)

    def test_missing_file_gives_empty_doc(self):
        with tempfile.TemporaryDirectory() as d:
            doc = health.load_health(self._cfg(d))
        self.assertEqual(doc["sources"], {})

    def test_corrupt_file_gives_empty_doc(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            with open(health.health_path(cfg), "w", encoding="utf-8") as f:
                f.write("{ not json")
            doc = health.load_health(cfg)
        self.assertEqual(doc["sources"], {})

    def test_state_file_untouched(self):
        # 사이드카가 본 state 파일을 건드리면 안 된다.
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            with open(cfg["output"]["state_file"], "w", encoding="utf-8") as f:
                json.dump({"job1": {"first_seen": "2026-09-05"}}, f)
            doc = health.load_health(cfg)
            health.record(doc, [result("c1", n=1)])
            health.save_health(cfg, doc)
            with open(cfg["output"]["state_file"], encoding="utf-8") as f:
                state = json.load(f)
        self.assertEqual(state, {"job1": {"first_seen": "2026-09-05"}})


class TestStaleSince(unittest.TestCase):
    def test_days_since_last_success(self):
        today = dt.date(2026, 9, 5)
        rec = {"last_success_at": "2026-09-01T10:00:00"}
        self.assertEqual(health.stale_since(rec, now=today), 4)

    def test_no_success_yet(self):
        self.assertIsNone(health.stale_since({}))
        self.assertIsNone(health.stale_since({"last_success_at": None}))

    def test_bad_value(self):
        self.assertIsNone(health.stale_since({"last_success_at": "언젠가"}))


if __name__ == "__main__":
    unittest.main()
