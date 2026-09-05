# -*- coding: utf-8 -*-
"""직무 필터 표기 정규화와 defaults.roles 상속.

이 필터가 좁으면 Target Radar 가 조용히 무력해진다. 실측에서 감시 대상 기업
공고 136건 중 96건(71%)이 여기서 탈락하던 상태가 있었다. 원인은 두 가지였다.
  1) include 가 영문 전용이라 한글 제목 공고가 전부 떨어짐
  2) "Back-end" 와 "backend", "서버_백엔드" 와 "백엔드" 를 다른 말로 봄
"""

import unittest

from tests.helpers import make_item

from radar.targeting import _role_norm, parse_registry


def doc(*companies, **kw):
    d = {"version": 1, "companies": list(companies)}
    d.update(kw)
    return d


def company(cid, name, **kw):
    c = {"id": cid, "name": name}
    c.update(kw)
    return c


class TestRoleNorm(unittest.TestCase):
    def test_separators_removed(self):
        self.assertEqual(_role_norm("Back-end Engineer"), "backendengineer")
        self.assertEqual(_role_norm("서버_백엔드"), "서버백엔드")
        self.assertEqual(_role_norm("Software Engineer, Backend"),
                         "softwareengineerbackend")

    def test_case_folded(self):
        self.assertEqual(_role_norm("BACKEND"), _role_norm("backend"))

    def test_fullwidth_folded(self):
        self.assertEqual(_role_norm("ＢＡＣＫＥＮＤ"), "backend")


class TestSpellingVariants(unittest.TestCase):
    """같은 직무의 다른 표기를 같이 잡아야 한다."""

    def setUp(self):
        self.c = parse_registry(doc(company(
            "a", "회사A",
            roles={"include": ["backend", "백엔드", "서버"], "exclude": []}))).by_id("a")

    def test_hyphenated_english(self):
        self.assertTrue(self.c.role_allowed(
            make_item(title="Staff Back-end Engineer (Lending)")))

    def test_korean_title(self):
        self.assertTrue(self.c.role_allowed(make_item(title="백엔드 개발자")))
        self.assertTrue(self.c.role_allowed(make_item(title="서버 개발자 - 결제")))

    def test_platform_taxonomy_in_depth(self):
        # 플랫폼이 주는 depthTwos 표기(서버_백엔드)도 잡혀야 한다.
        self.assertTrue(self.c.role_allowed(
            make_item(title="퇴직연금 도메인 개발자", depthTwos=["서버_백엔드"])))

    def test_unrelated_still_dropped(self):
        self.assertFalse(self.c.role_allowed(
            make_item(title="그래픽 디자이너", depthTwos=["디자인"],
                      depthOnes=["디자인"])))


class TestExcludeWins(unittest.TestCase):
    def setUp(self):
        self.c = parse_registry(doc(company(
            "a", "회사A",
            roles={"include": ["engineer", "개발"],
                   "exclude": ["안드로이드", "android"]}))).by_id("a")

    def test_exclude_beats_include(self):
        self.assertFalse(self.c.role_allowed(
            make_item(title="Staff Mobile Engineer (TW Android)")))

    def test_include_alone_passes(self):
        self.assertTrue(self.c.role_allowed(
            make_item(title="Staff Backend Engineer")))


class TestShortFragmentHazard(unittest.TestCase):
    """짧은 exclude 조각은 엉뚱한 단어에 걸린다. 실제로 겪은 사고 2건."""

    def test_md_would_swallow_unrelated_titles(self):
        bad = parse_registry(doc(company(
            "a", "회사A", roles={"include": ["engineer", "developer"],
                                "exclude": ["md"]}))).by_id("a")
        # 'MDM' 과 'ALM Developer' 가 'md' 에 걸려 탈락한다.
        self.assertFalse(bad.role_allowed(
            make_item(title="Internal Systems Engineer (MDM)")))
        self.assertFalse(bad.role_allowed(
            make_item(title="Finance (ALM) Developer")))

    def test_specific_term_is_safe(self):
        good = parse_registry(doc(company(
            "a", "회사A", roles={"include": ["engineer", "developer"],
                                "exclude": ["온라인md", "머천다이저"]}))).by_id("a")
        self.assertTrue(good.role_allowed(
            make_item(title="Internal Systems Engineer (MDM)")))
        self.assertTrue(good.role_allowed(
            make_item(title="Finance (ALM) Developer")))


class TestDefaultsInheritance(unittest.TestCase):
    """roles 를 45곳에 복제하지 않는다. 한 줄 고칠 때 45곳을 고치게 된다."""

    def _reg(self):
        return parse_registry(doc(
            company("inherits", "상속회사"),
            company("overrides", "재정의회사",
                    roles={"include": ["디자이너"], "exclude": []}),
            defaults={"roles": {"include": ["backend", "백엔드"],
                                "exclude": ["디자이너"]}},
        ))

    def test_company_without_roles_inherits(self):
        c = self._reg().by_id("inherits")
        self.assertEqual(c.roles_source, "defaults")
        self.assertTrue(c.role_allowed(make_item(title="백엔드 개발자")))
        self.assertFalse(c.role_allowed(make_item(title="그래픽 디자이너")))

    def test_company_with_roles_overrides(self):
        c = self._reg().by_id("overrides")
        self.assertEqual(c.roles_source, "own")
        # 자기 목록이 이기므로 defaults 의 exclude(디자이너)에 안 걸린다.
        self.assertTrue(c.role_allowed(make_item(title="그래픽 디자이너")))
        self.assertFalse(c.role_allowed(make_item(title="백엔드 개발자")))

    def test_no_defaults_and_no_roles_allows_everything(self):
        c = parse_registry(doc(company("a", "회사A"))).by_id("a")
        self.assertEqual(c.roles_source, "none")
        self.assertTrue(c.role_allowed(make_item(title="아무 직무")))

    def test_bad_defaults_roles_type(self):
        from radar.targeting import TargetConfigError
        with self.assertRaises(TargetConfigError):
            parse_registry(doc(company("a", "회사A"), defaults={"roles": ["x"]}))


class TestSyntheticDepthStillIgnored(unittest.TestCase):
    def test_synthetic_depth_not_trusted(self):
        c = parse_registry(doc(company(
            "a", "회사A", roles={"include": ["백엔드"], "exclude": []}))).by_id("a")
        self.assertFalse(c.role_allowed(
            make_item(title="디자이너 모집", depthTwos=["서버_백엔드"],
                      _depth_synthetic=True)))
        self.assertTrue(c.role_allowed(
            make_item(title="디자이너 모집", depthTwos=["서버_백엔드"])))


if __name__ == "__main__":
    unittest.main()
