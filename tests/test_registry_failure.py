# -*- coding: utf-8 -*-
"""감시 대상 기업 설정이 깨졌을 때의 실패 격리.

설정 오타 하나로 그날 다이제스트·알림이 통째로 안 나가면 안 된다.
그렇다고 조용히 넘어가서 "target을 보고 있다고 믿는데 실은 아닌" 상태도 안 된다.
"""

import json
import os
import tempfile
import unittest

from tests.helpers import make_config

from radar import cli
from radar.targeting import TargetConfigError, load_registry


class TestBrokenRegistry(unittest.TestCase):
    def _cfg_with(self, content):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return make_config(targets={"file": p}), p

    def test_broken_json_raises(self):
        cfg, _ = self._cfg_with("{ not json")
        with self.assertRaises(TargetConfigError):
            load_registry(cfg)

    def test_alias_collision_raises(self):
        cfg, _ = self._cfg_with(json.dumps({
            "version": 1, "companies": [
                {"id": "one", "name": "회사", "aliases": ["같은이름"]},
                {"id": "two", "name": "다른회사", "aliases": ["같은 이름"]},
            ]}, ensure_ascii=False))
        with self.assertRaises(TargetConfigError) as cm:
            load_registry(cfg)
        self.assertIn("alias 충돌", str(cm.exception))


class TestNoteSurfacesTheError(unittest.TestCase):
    def test_note_warns_when_registry_failed(self):
        from radar.output.note import write_note
        stats = {"scanned": 10, "rule_pass": 2, "fresh": 1, "llm_scored": 0,
                 "enriched": 0, "llm_note": None, "window": 3,
                 "targets_error": "alias 충돌: 'x'를 'a'와 'b'가 함께 주장합니다"}
        with tempfile.TemporaryDirectory() as d:
            cfg = make_config()
            cfg["output"]["matches_dir"] = d
            path, _ = write_note(cfg, [], stats)
            with open(path, encoding="utf-8") as f:
                body = f.read()
        self.assertIn("감시 대상 기업 목록을 못 읽었습니다", body)
        self.assertIn("alias 충돌", body)
        self.assertIn("감시 대상 기업 목록", body)

    def test_note_silent_when_registry_fine(self):
        from radar.output.note import write_note
        stats = {"scanned": 10, "rule_pass": 2, "fresh": 1, "llm_scored": 0,
                 "enriched": 0, "llm_note": None, "window": 3,
                 "targets_error": None}
        with tempfile.TemporaryDirectory() as d:
            cfg = make_config()
            cfg["output"]["matches_dir"] = d
            path, _ = write_note(cfg, [], stats)
            with open(path, encoding="utf-8") as f:
                body = f.read()
        self.assertNotIn("감시 대상 기업 목록을 못 읽었습니다", body)


class TestCliFailureMode(unittest.TestCase):
    """--mode target 은 즉시 중단, 나머지는 discovery 를 살린다."""

    def _run(self, argv, cfg_path):
        return cli.main(["--config", cfg_path] + argv)

    def _broken_config(self, tmpdir):
        reg = os.path.join(tmpdir, "targets.json")
        with open(reg, "w", encoding="utf-8") as f:
            f.write("{ broken")
        cfg = make_config(targets={"file": reg})
        cfg["output"]["state_file"] = os.path.join(tmpdir, "s.json")
        cfg["output"]["matches_dir"] = tmpdir
        cfg_path = os.path.join(tmpdir, "cfg.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        return cfg_path

    def test_target_mode_exits(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._broken_config(d)
            with self.assertRaises(SystemExit) as cm:
                self._run(["--mode", "target", "--no-llm", "--dry-run"], cfg_path)
            self.assertEqual(cm.exception.code, 2)

    def test_list_targets_exits(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._broken_config(d)
            with self.assertRaises(SystemExit):
                self._run(["--list-targets"], cfg_path)

    def test_discovery_survives_broken_registry(self):
        from radar import pipeline
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._broken_config(d)
            calls = {}

            def fake_collect(cfg, registry=None, with_official=False):
                calls["ran"] = True
                return [], [], []

            orig = pipeline.DEFAULT_DEPS
            pipeline.DEFAULT_DEPS = pipeline.Deps(collect=fake_collect)
            try:
                # SystemExit 없이 끝나야 한다. discovery 는 계속 돈다.
                self._run(["--mode", "all", "--no-llm", "--dry-run"], cfg_path)
            finally:
                pipeline.DEFAULT_DEPS = orig
            self.assertTrue(calls.get("ran"))


if __name__ == "__main__":
    unittest.main()
