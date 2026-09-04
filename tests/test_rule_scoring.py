# -*- coding: utf-8 -*-
"""규칙 점수 회귀. 리팩터 전/후 동일 결과를 강제한다."""

import unittest

from tests.helpers import make_config, make_item

import job_watcher as jw


class TestRoleScore(unittest.TestCase):
    def setUp(self):
        self.f = make_config()["filter"]

    def test_primary(self):
        self.assertEqual(jw.score_role(make_item(depthTwos=["서버_백엔드"]), self.f), 1.0)

    def test_secondary(self):
        self.assertEqual(
            jw.score_role(make_item(depthTwos=["시스템_네트워크"]), self.f), 0.6)

    def test_depth_one_only(self):
        self.assertEqual(
            jw.score_role(make_item(depthTwos=["그외"], depthOnes=["IT_개발"]), self.f),
            0.35)

    def test_no_match(self):
        self.assertEqual(
            jw.score_role(make_item(depthTwos=["그외"], depthOnes=["영업"]), self.f), 0.0)


class TestCareerScore(unittest.TestCase):
    def test_in_range(self):
        self.assertEqual(jw.score_career(make_item(careerMin=3, careerMax=7), 4, 2), 1.0)

    def test_unspecified_is_neutral(self):
        self.assertEqual(
            jw.score_career(make_item(careerMin=None, careerMax=None), 4, 2), 0.7)

    def test_under_within_tolerance(self):
        # 공고가 6년 요구, 나는 4년 → gap 2 (tol 2 이내)
        self.assertAlmostEqual(
            jw.score_career(make_item(careerMin=6, careerMax=10), 4, 2), 0.3)

    def test_under_beyond_tolerance(self):
        self.assertEqual(
            jw.score_career(make_item(careerMin=9, careerMax=15), 4, 2), 0.1)

    def test_over_junior_posting(self):
        self.assertEqual(
            jw.score_career(make_item(careerMin=0, careerMax=2), 4, 2), 0.5)

    def test_over_newbie_only(self):
        self.assertEqual(
            jw.score_career(make_item(careerMin=0, careerMax=1), 4, 2), 0.3)


class TestOtherScores(unittest.TestCase):
    def test_region(self):
        pref = ["서울", "경기"]
        self.assertEqual(jw.score_region(make_item(regions=["서울"]), pref), 1.0)
        self.assertEqual(jw.score_region(make_item(regions=["부산"]), pref), 0.3)
        self.assertEqual(jw.score_region(make_item(regions=[]), pref), 0.8)

    def test_employment(self):
        self.assertEqual(jw.score_employment(make_item(employeeTypes=["정규직"])), 1.0)
        self.assertEqual(jw.score_employment(make_item(employeeTypes=[])), 0.7)
        self.assertEqual(jw.score_employment(make_item(employeeTypes=["계약직"])), 0.4)

    def test_tech_title(self):
        prof = make_config()["profile"]
        self.assertEqual(
            jw.score_tech_title(make_item(title="Java Backend"), prof), 0.5)
        self.assertEqual(
            jw.score_tech_title(make_item(title="Java Spring Kafka"), prof), 1.0)
        self.assertEqual(jw.score_tech_title(make_item(title="Go 개발자"), prof), 0.0)

    def test_title_excluded(self):
        f = make_config()["filter"]
        self.assertTrue(jw.title_excluded(make_item(title="프론트엔드 개발자"), f))
        self.assertFalse(jw.title_excluded(make_item(title="백엔드 개발자"), f))


class TestRuleScore(unittest.TestCase):
    def test_full_match_is_deterministic(self):
        cfg = make_config()
        item = make_item(title="Java Spring 백엔드 개발자")
        first, parts = jw.rule_score(item, cfg)
        again, _ = jw.rule_score(item, cfg)
        self.assertEqual(first, again)
        self.assertEqual(first, 100.0)
        self.assertEqual(sorted(parts), ["career", "employment", "region",
                                         "role", "tech_title"])

    def test_weights_are_normalized_to_100(self):
        cfg = make_config()
        cfg["scoring"]["weights"] = {"role": 1, "career": 1, "tech_title": 1,
                                     "region": 1, "employment": 1}
        score, _ = jw.rule_score(make_item(title="Java Spring 백엔드"), cfg)
        self.assertEqual(score, 100.0)


class TestDeadlineInfo(unittest.TestCase):
    def test_unknown(self):
        for v in (None, "", "미상", "unknown", "이상한값"):
            self.assertEqual(jw.deadline_info(v)[2], "unknown")

    def test_rolling(self):
        for v in ("상시", "채용시 마감", "수시채용"):
            self.assertEqual(jw.deadline_info(v)[2], "rolling")

    def test_date(self):
        import datetime as dt
        today = dt.date.today()
        label, days, state = jw.deadline_info(today.isoformat())
        self.assertEqual((days, state), (0, "date"))
        self.assertIn("오늘마감", label)
        label, days, state = jw.deadline_info(
            (today + dt.timedelta(days=5)).isoformat())
        self.assertEqual((days, state), (5, "date"))
        self.assertTrue(label.startswith("D-5"))
        _, days, _ = jw.deadline_info((today - dt.timedelta(days=1)).isoformat())
        self.assertEqual(days, -1)


if __name__ == "__main__":
    unittest.main()
