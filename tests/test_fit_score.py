# -*- coding: utf-8 -*-
"""결정론적 적합도·추천 회귀(Phase 6).

완료조건 네 가지를 여기서 고정한다. 같은 입력이면 같은 점수, 결격이 점수보다 우선,
가중치를 config 로 바꿀 수 있음, 그리고 `UNKNOWN` 처리 정책이 테스트로 명시될 것
(PLAN 12절이 명시적으로 요구한다).
"""

import unittest

from tests.helpers import make_config, make_item

from radar import fit


def req(match="FULL", importance="REQUIRED", category="language", **kw):
    """합성 요구사항. 실제 이력·스택과 겹치지 않는 가상값만 쓴다(공개 리포)."""
    row = {"category": category, "importance": importance,
           "requirement": "Erlang/OTP 분산시스템", "candidate_evidence": "근거 문장",
           "match": match, "confidence": "HIGH", "demoted": False}
    row.update(kw)
    return row


def blocker(**kw):
    b = {"kind": "license", "detail": "정보처리기사 미보유", "evidence": "자격요건에 명시"}
    b.update(kw)
    return b


class MatchAxisTest(unittest.TestCase):

    def test_match_values(self):
        v, known, total = fit.match_axis(
            [req("FULL"), req("PARTIAL"), req("NONE")], "REQUIRED")
        self.assertEqual(round(v, 4), round((1.0 + 0.5 + 0.0) / 3, 4))
        self.assertEqual((known, total), (3, 3))

    def test_unknown_is_excluded_from_the_denominator(self):
        # PLAN 12절: UNKNOWN 처리 정책을 테스트로 명시한다.
        # 정보가 없는 것을 0점(미달)으로도 0.5점(절반 충족)으로도 치지 않는다.
        v, known, total = fit.match_axis([req("FULL"), req("UNKNOWN")], "REQUIRED")
        self.assertEqual(v, 1.0)
        self.assertEqual((known, total), (1, 2))

    def test_all_unknown_means_no_data_not_zero(self):
        v, known, total = fit.match_axis([req("UNKNOWN"), req("UNKNOWN")], "REQUIRED")
        self.assertIsNone(v)
        self.assertEqual((known, total), (0, 2))

    def test_other_importances_ignored(self):
        rows = [req("FULL"), req("NONE", importance="PREFERRED"),
                req("NONE", importance="CONTEXT")]
        self.assertEqual(fit.match_axis(rows, "REQUIRED")[0], 1.0)
        self.assertEqual(fit.match_axis(rows, "PREFERRED")[0], 0.0)


class FitScoreTest(unittest.TestCase):

    def setUp(self):
        self.cfg = make_config()
        self.item = make_item()

    def test_same_input_same_score(self):
        # 완료조건: 동일 입력은 동일 score.
        rows = [req("FULL"), req("PARTIAL", importance="PREFERRED")]
        a = fit.fit_score(rows, self.item, self.cfg)
        b = fit.fit_score([dict(r) for r in rows], dict(self.item), self.cfg)
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(a["parts"], b["parts"])

    def test_no_requirements_means_no_score(self):
        self.assertIsNone(fit.fit_score([], self.item, self.cfg))
        self.assertIsNone(fit.fit_score(None, self.item, self.cfg))

    def test_all_full_beats_all_none(self):
        hi = fit.fit_score([req("FULL")], self.item, self.cfg)["score"]
        lo = fit.fit_score([req("NONE")], self.item, self.cfg)["score"]
        self.assertGreater(hi, lo)

    def test_missing_axis_is_renormalized_not_zeroed(self):
        # cfg 가 없으면 경력·선호 축을 못 만든다. 그 축을 0점으로 치면
        # "정보 없음"이 "미달"로 둔갑한다. 빼고 재정규화해야 한다.
        rows = [req("FULL")]
        no_cfg = fit.fit_score(rows, self.item, None)
        self.assertEqual(no_cfg["score"], 100.0)
        self.assertNotIn("career", no_cfg["weights_used"])
        self.assertNotIn("preference", no_cfg["weights_used"])

    def test_coverage_reported(self):
        f = fit.fit_score([req("FULL"), req("UNKNOWN"), req("UNKNOWN")],
                          self.item, self.cfg)
        self.assertEqual((f["required_known"], f["required_total"]), (1, 3))
        self.assertAlmostEqual(f["required_coverage"], 1 / 3, places=3)

    def test_weights_are_config_driven(self):
        # 완료조건: config weight 변경 가능.
        rows = [req("NONE"), req("FULL", importance="PREFERRED")]
        base = fit.fit_score(rows, self.item, self.cfg)["score"]
        cfg = make_config()
        cfg["scoring"]["fit"] = {"weights": {"required": 0.10, "preferred": 0.60}}
        tilted = fit.fit_score(rows, self.item, cfg)["score"]
        # 필수가 NONE, 우대가 FULL 이므로 우대 가중치를 키우면 점수가 올라간다.
        self.assertGreater(tilted, base)

    def test_tech_axis_is_deterministic_only(self):
        # 요구사항 행을 여기서 다시 읽으면 근거 한 줄이 required 와 tech 양쪽에서
        # 만점을 받아 "필수 55%"가 실효 65% 가 된다.
        self.assertEqual(fit.tech_axis(make_item(title="백엔드 개발자"), self.cfg), 0.0)
        self.assertGreater(
            fit.tech_axis(make_item(title="java spring 백엔드"), self.cfg), 0)

    def test_too_few_required_rows_capped_at_review(self):
        # 실측(2026-09-06): 본문이 빈 공고에서 메타데이터만으로 뽑은 요구사항 2건이
        # 전부 FULL 로 판정돼 88.2점 추천이 나왔다. 점수는 두되 추천을 누른다.
        f = fit.fit_score([req("FULL"), req("FULL")], self.item, self.cfg)
        self.assertEqual(f["required_coverage"], 1.0)  # 비율로는 안 걸린다
        rec, why = fit.recommend(f, [], 0, False, self.cfg)
        self.assertEqual(rec, "REVIEW")
        self.assertTrue(any("2건뿐" in w for w in why))

    def test_enough_rows_is_not_capped(self):
        rows = [req("FULL") for _ in range(fit.DEFAULT_MIN_REQUIRED_ROWS)]
        f = fit.fit_score(rows, self.item, self.cfg)
        self.assertIn(fit.recommend(f, [], 0, False, self.cfg)[0],
                      ("STRONG_APPLY", "APPLY"))

    def test_min_required_rows_is_config_driven(self):
        cfg = make_config()
        cfg["scoring"]["fit"] = {"min_required_rows": 1}
        f = fit.fit_score([req("FULL"), req("FULL")], self.item, cfg)
        self.assertNotEqual(fit.recommend(f, [], 0, False, cfg)[0], "REVIEW")

    def test_one_required_row_does_not_max_out_every_axis(self):
        f = fit.fit_score([req("FULL", category="language")], make_item(), self.cfg)
        self.assertEqual(f["parts"]["required"], 1.0)
        self.assertEqual(f["parts"]["tech"], 0.0)   # 제목에 스택 없음
        self.assertLess(f["score"], 100.0)


class CareerPolicyTest(unittest.TestCase):
    """조사문서 7절. 연수 차이만으로 탈락시키지 않는다."""

    def setUp(self):
        self.cfg = make_config()  # career_years = 4

    def test_gap_measured_against_profile_years(self):
        self.assertEqual(fit.career_gap(make_item(careerMin=5), self.cfg), 1)
        self.assertEqual(fit.career_gap(make_item(careerMin=7), self.cfg), 3)

    def test_more_senior_than_required_is_not_a_gap(self):
        self.assertEqual(fit.career_gap(make_item(careerMin=1), self.cfg), 0)

    def test_unknown_career_min(self):
        self.assertIsNone(fit.career_gap(make_item(careerMin=None), self.cfg))
        self.assertIsNone(fit.career_gap(make_item(careerMin="신입"), self.cfg))

    def test_senior_role_detection(self):
        for t in ("Senior Backend Engineer", "백엔드 개발 리드", "수석 개발자",
                  "Staff Engineer"):
            self.assertTrue(fit.is_senior_role(make_item(title=t)), t)
        self.assertFalse(fit.is_senior_role(make_item(title="백엔드 개발자")))

    def _rec(self, item):
        # 경력 정책만 보려는 테스트다. 근거 얇음 가드에 걸리지 않게 충분히 준다.
        rows = [req("FULL") for _ in range(fit.DEFAULT_MIN_REQUIRED_ROWS)]
        f = fit.fit_score(rows, item, self.cfg)
        return fit.recommend(f, [], fit.career_gap(item, self.cfg),
                             fit.is_senior_role(item), self.cfg)[0]

    def test_one_year_gap_is_not_demoted(self):
        # 요구 5년 vs 경력 4년. blocker 도 아니고 REVIEW 강등도 아니다.
        self.assertIn(self._rec(make_item(careerMin=5, careerMax=9)),
                      ("STRONG_APPLY", "APPLY"))

    def test_two_year_gap_capped_at_review(self):
        self.assertEqual(self._rec(make_item(careerMin=6, careerMax=9)), "REVIEW")

    def test_three_year_gap_with_senior_title_is_skip(self):
        self.assertEqual(
            self._rec(make_item(careerMin=7, careerMax=12,
                                title="Senior 백엔드 개발자")), "SKIP")

    def test_three_year_gap_without_senior_title_stays_review(self):
        self.assertEqual(
            self._rec(make_item(careerMin=7, careerMax=12, title="백엔드 개발자")),
            "REVIEW")


class BlockerAdoptionTest(unittest.TestCase):
    """LLM 은 후보만 낸다. 채택은 코드가 한다(PLAN 13절)."""

    def test_adopts_grounded_blocker(self):
        adopted, rejected = fit.adopt_blockers([blocker()])
        self.assertEqual(len(adopted), 1)
        self.assertEqual(rejected, [])

    def test_rejects_career_kind(self):
        adopted, rejected = fit.adopt_blockers(
            [blocker(kind="career", detail="요구 경력 미달")])
        self.assertEqual(adopted, [])
        self.assertIn("경력", rejected[0]["reject"])

    def test_rejects_career_wording_even_with_other_kind(self):
        # LLM 이 kind 를 느슨하게 고른다. 문구로도 한 번 더 거른다.
        adopted, _ = fit.adopt_blockers(
            [blocker(kind="기타", detail="요구 경력 5년 이상인데 4년차")])
        self.assertEqual(adopted, [])

    def test_rejects_blocker_without_evidence(self):
        # 근거 없는 결격 하나가 지원 기회를 통째로 없앤다.
        adopted, rejected = fit.adopt_blockers([blocker(evidence="")])
        self.assertEqual(adopted, [])
        self.assertIn("근거", rejected[0]["reject"])

    def test_rejection_keeps_the_original_fields(self):
        _a, rejected = fit.adopt_blockers([blocker(evidence="")])
        self.assertEqual(rejected[0]["detail"], "정보처리기사 미보유")


class RecommendationTest(unittest.TestCase):

    def setUp(self):
        self.cfg = make_config()

    def _rec(self, score, **kw):
        f = {"score": score, "required_known": 6, "required_total": 6,
             "required_coverage": 1.0, "parts": {"required": 1.0}}
        f.update(kw)
        return fit.recommend(f, [], 0, False, self.cfg)

    def test_thresholds(self):
        self.assertEqual(self._rec(95)[0], "STRONG_APPLY")
        self.assertEqual(self._rec(90)[0], "STRONG_APPLY")
        self.assertEqual(self._rec(85)[0], "APPLY")
        self.assertEqual(self._rec(75)[0], "REVIEW")
        self.assertEqual(self._rec(65)[0], "ARCHIVE")
        self.assertEqual(self._rec(59)[0], "SKIP")

    def test_blocker_beats_score(self):
        # 완료조건: hard blocker 우선.
        rec, why = fit.recommend({"score": 99, "required_known": 4,
                                  "required_total": 4, "required_coverage": 1.0,
                                  "parts": {"required": 1.0}},
                                 [blocker()], 0, False, self.cfg)
        self.assertEqual(rec, "SKIP")
        self.assertIn("정보처리기사", why[0])

    def test_blocker_keeps_the_other_reasons(self):
        # 결격이 이겨도 나머지 사유를 버리지 않는다(PLAN 15절: 근거를 같이 보여준다).
        rec, why = fit.recommend(
            {"score": 99, "required_known": 1, "required_total": 5,
             "required_coverage": 0.2, "parts": {"required": 1.0}},
            [blocker()], 3, True, self.cfg)
        self.assertEqual(rec, "SKIP")
        self.assertEqual(len(why), 3)  # 결격 + 커버리지 + 경력차
        self.assertTrue(why[0].startswith("결격"))

    def test_all_required_unknown_capped_at_review(self):
        rec, why = self._rec(95, required_known=0, required_total=6,
                             required_coverage=0.0, parts={"required": None})
        self.assertEqual(rec, "REVIEW")
        self.assertIn("모두 미판정", why[0])

    def test_blocker_skips_even_without_a_score(self):
        self.assertEqual(fit.recommend(None, [blocker()], None, False, self.cfg)[0],
                         "SKIP")

    def test_no_fit_means_no_recommendation(self):
        self.assertEqual(fit.recommend(None, [], 0, False, self.cfg), (None, []))

    def test_low_coverage_capped_at_review(self):
        rec, why = self._rec(95, required_known=1, required_total=5,
                             required_coverage=0.2)
        self.assertEqual(rec, "REVIEW")
        self.assertIn("판정", why[0])

    def test_no_required_rows_capped_at_review(self):
        rec, why = self._rec(95, required_known=0, required_total=0,
                             required_coverage=None)
        self.assertEqual(rec, "REVIEW")
        self.assertIn("얇음", why[0])

    def test_cap_never_promotes(self):
        # 이미 SKIP 인 것을 REVIEW 로 올려주면 안 된다.
        rec, _ = self._rec(30, required_known=1, required_total=5,
                           required_coverage=0.2)
        self.assertEqual(rec, "SKIP")

    def test_thresholds_are_config_driven(self):
        cfg = make_config()
        # 구간이 겹치지 않게 통째로 내린다(역전 설정은 경고 대상이다).
        cfg["scoring"]["fit"] = {"thresholds": {"strong_apply": 60, "apply": 50,
                                                "review": 40, "archive": 30}}
        f = {"score": 65, "required_known": 6, "required_total": 6,
             "required_coverage": 1.0, "parts": {"required": 1.0}}
        self.assertEqual(fit.recommend(f, [], 0, False, cfg)[0], "STRONG_APPLY")

    def test_coverage_threshold_is_config_driven(self):
        cfg = make_config()
        cfg["scoring"]["fit"] = {"min_required_coverage": 0.0}
        f = {"score": 95, "required_known": 1, "required_total": 5,
             "required_coverage": 0.2, "parts": {"required": 1.0}}
        self.assertEqual(fit.recommend(f, [], 0, False, cfg)[0], "STRONG_APPLY")


class ConfigBlockTest(unittest.TestCase):

    def setUp(self):
        fit._warned.clear()

    def test_plan_flat_weight_key_is_accepted(self):
        # PLAN 12절 예시 표기. 조용히 무시되면 사용자는 튜닝이 먹은 줄 안다.
        cfg = make_config()
        cfg["scoring"]["fit_weights"] = {"required": 0.9, "preferred": 0.1}
        self.assertEqual(fit._cfg_block(cfg)["weights"]["required"], 0.9)

    def test_nested_key_wins_over_flat(self):
        cfg = make_config()
        cfg["scoring"]["fit_weights"] = {"required": 0.9}
        cfg["scoring"]["fit"] = {"weights": {"required": 0.3}}
        self.assertEqual(fit._cfg_block(cfg)["weights"]["required"], 0.3)

    def test_unknown_axis_warns(self):
        cfg = make_config()
        cfg["scoring"]["fit"] = {"weights": {"requiredd": 0.9}}
        fit._cfg_block(cfg)
        self.assertTrue(any("모르는 축" in w for w in fit._warned))

    def test_threshold_inversion_warns(self):
        cfg = make_config()
        cfg["scoring"]["fit"] = {"thresholds": {"strong_apply": 50}}
        fit._cfg_block(cfg)
        self.assertTrue(any("내림차순" in w for w in fit._warned))

    def test_missing_block_uses_defaults(self):
        b = fit._cfg_block(make_config())
        self.assertEqual(b["weights"], fit.DEFAULT_WEIGHTS)
        self.assertEqual(b["thresholds"], fit.DEFAULT_THRESHOLDS)


class CoverageBoundaryTest(unittest.TestCase):
    """경계값이 어느 쪽으로 붙는지 못박는다."""

    def test_exactly_half_known_is_capped(self):
        cfg = make_config()
        rows = [req("FULL")] * 3 + [req("UNKNOWN")] * 3
        f = fit.fit_score(rows, make_item(), cfg)
        self.assertEqual(f["required_coverage"], 0.5)
        self.assertEqual(fit.recommend(f, [], 0, False, cfg)[0], "REVIEW")

    def test_above_half_is_not_capped(self):
        cfg = make_config()
        rows = [req("FULL")] * 6 + [req("UNKNOWN")] * 2
        f = fit.fit_score(rows, make_item(), cfg)
        self.assertGreater(f["required_coverage"], 0.5)
        self.assertNotEqual(fit.recommend(f, [], 0, False, cfg)[0], "REVIEW")


class BlockerWordingTest(unittest.TestCase):
    """PLAN 13절이 결격 예시로 든 것들이 문구 매칭에 휩쓸리지 않아야 한다."""

    def test_entry_only_is_a_real_blocker(self):
        adopted, _ = fit.adopt_blockers(
            [blocker(kind="career", detail="신입 전용 공고로 경력자 지원 불가",
                     evidence="공고 제목")])
        self.assertEqual(len(adopted), 1)

    def test_education_filter_is_a_real_blocker(self):
        adopted, _ = fit.adopt_blockers(
            [blocker(kind="education", detail="4년제 대졸 이상 필수",
                     evidence="지원자격")])
        self.assertEqual(len(adopted), 1)

    def test_year_shortfall_is_rejected(self):
        for d in ("요구 경력 5년 이상인데 4년차", "경력 7년 이상 필수",
                  "requires 8 years of experience"):
            adopted, rejected = fit.adopt_blockers([blocker(detail=d)])
            self.assertEqual(adopted, [], d)
            self.assertIn("경력 연수", rejected[0]["reject"], d)


class EvaluateTest(unittest.TestCase):
    """build_match 가 그대로 싣는 묶음."""

    def test_shape(self):
        from radar.matching import normalize_structured
        st = normalize_structured({
            "requirements": [req(), req("NONE", importance="PREFERRED")],
            "hard_blockers": [blocker(kind="career", detail="경력 1년 부족")]})
        ev = fit.evaluate(make_item(), st, make_config())
        self.assertIsNotNone(ev["fit_score"])
        self.assertIn(ev["recommendation"], fit.RECOMMENDATIONS)
        self.assertIsNone(ev["hard_blockers"])          # 경력 결격은 반려
        self.assertEqual(len(ev["blockers_rejected"]), 1)

    def test_empty_structured_is_all_none(self):
        from radar.matching import empty
        ev = fit.evaluate(make_item(), empty(), make_config())
        self.assertIsNone(ev["fit_score"])
        self.assertIsNone(ev["recommendation"])
        self.assertIsNone(ev["hard_blockers"])

    def test_labels_cover_every_recommendation(self):
        for r in fit.RECOMMENDATIONS:
            self.assertIn(r, fit.RECOMMENDATION_LABELS)


if __name__ == "__main__":
    unittest.main()
