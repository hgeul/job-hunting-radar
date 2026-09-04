# -*- coding: utf-8 -*-
"""Target Company Registry: 정규화·alias 매칭·검증.

회사명은 전부 가상(예시알파 등). 실제 감시 대상은 코드·테스트에 두지 않는다.
"""

import json
import os
import tempfile
import unittest

from tests.helpers import make_config, make_item

from radar.targeting import (
    CompanyRegistry, TargetConfigError, load_registry, normalize_company,
    parse_registry,
)


def doc(*companies, **kw):
    d = {"version": 1, "companies": list(companies)}
    d.update(kw)
    return d


def company(cid, name, **kw):
    c = {"id": cid, "name": name}
    c.update(kw)
    return c


class TestNormalize(unittest.TestCase):
    def test_case_and_space(self):
        self.assertEqual(normalize_company("Example Alpha"), "examplealpha")
        self.assertEqual(normalize_company("  EXAMPLE  ALPHA  "), "examplealpha")

    def test_legal_suffix_removed(self):
        for raw in ("Example Alpha Inc.", "Example Alpha Corp.",
                    "Example Alpha Co., Ltd.", "Example Alpha Corporation",
                    "Example Alpha LLC"):
            self.assertEqual(normalize_company(raw), "examplealpha", raw)

    def test_korean_legal_marks(self):
        for raw in ("예시알파(주)", "㈜예시알파", "㈲예시알파", "주식회사 예시알파",
                    "(주) 예시알파", "예시알파 유한회사"):
            self.assertEqual(normalize_company(raw), "예시알파", raw)

    def test_korean_legal_token_without_space(self):
        # 국내 표기는 띄어쓰기 없이 붙여 쓰는 쪽이 오히려 흔하다.
        for raw in ("주식회사예시알파", "예시알파주식회사", "유한회사예시알파",
                    "예시알파유한책임회사"):
            self.assertEqual(normalize_company(raw), "예시알파", raw)

    def test_fullwidth_is_folded(self):
        self.assertEqual(normalize_company("ＥＸＡＭＰＬＥ"),
                         normalize_company("EXAMPLE"))

    def test_name_that_is_only_a_legal_token_keeps_key(self):
        # 다 떼면 빈 키가 되어 서로 다른 회사가 한 칸에 몰린다. 그건 막는다.
        self.assertNotEqual(normalize_company("Company"), "")
        self.assertNotEqual(normalize_company("Co Ltd"), "")

    def test_legal_token_only_as_whole_word(self):
        # "cobalt"의 "co"를 법인표기로 오인해 떼면 아예 다른 말이 된다.
        self.assertEqual(normalize_company("Cobalt"), "cobalt")
        self.assertEqual(normalize_company("Incheon"), "incheon")
        self.assertEqual(normalize_company("Ltdream"), "ltdream")

    def test_affiliates_stay_distinct(self):
        self.assertNotEqual(normalize_company("예시알파"),
                            normalize_company("예시알파 클라우드"))

    def test_empty(self):
        self.assertEqual(normalize_company(None), "")
        self.assertEqual(normalize_company("   "), "")
        self.assertEqual(normalize_company("(주)"), "")


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.reg = parse_registry(doc(
            company("example-alpha", "예시알파",
                    aliases=["Example Alpha", "Example Alpha Inc."], tier="S"),
            company("example-alpha-cloud", "예시알파 클라우드",
                    aliases=["Example Alpha Cloud"], tier="A"),
            company("example-beta", "예시베타", tier="B", enabled=False),
        ))

    def test_by_name_and_aliases(self):
        for raw in ("예시알파", "Example Alpha", "EXAMPLE ALPHA",
                    "Example Alpha Inc.", "예시알파(주)", "㈜예시알파"):
            got = self.reg.resolve(raw)
            self.assertIsNotNone(got, raw)
            self.assertEqual(got.id, "example-alpha", raw)

    def test_affiliate_not_merged(self):
        self.assertEqual(self.reg.resolve("예시알파 클라우드").id, "example-alpha-cloud")
        self.assertEqual(self.reg.resolve("Example Alpha Cloud").id,
                         "example-alpha-cloud")

    def test_non_target_is_none(self):
        for raw in ("무관회사", "Example", "예시", "Alpha", ""):
            self.assertIsNone(self.reg.resolve(raw), raw)

    def test_no_substring_matching(self):
        # 부분일치를 허용하면 서로 다른 회사가 합쳐진다. 절대 매칭되면 안 된다.
        self.assertIsNone(self.reg.resolve("예시알파테크놀로지"))
        self.assertIsNone(self.reg.resolve("Example Alpha Games"))

    def test_disabled_excluded_from_resolve(self):
        self.assertIsNone(self.reg.resolve("예시베타"))
        self.assertFalse(self.reg.is_target("예시베타"))

    def test_disabled_still_identifiable(self):
        # "꺼놓은 target"과 "target이 아님"은 다르다.
        got = self.reg.resolve_any("예시베타")
        self.assertIsNotNone(got)
        self.assertFalse(got.enabled)
        self.assertIsNone(self.reg.resolve_any("무관회사"))

    def test_enabled_companies(self):
        self.assertEqual(sorted(c.id for c in self.reg.enabled_companies()),
                         ["example-alpha", "example-alpha-cloud"])
        self.assertEqual(len(self.reg), 3)

    def test_resolve_item(self):
        self.assertEqual(
            self.reg.resolve_item(make_item(company="Example Alpha")).id,
            "example-alpha")
        self.assertIsNone(self.reg.resolve_item(make_item(company="무관회사")))


class TestRoleFilter(unittest.TestCase):
    def setUp(self):
        self.reg = parse_registry(doc(
            company("a", "회사A", roles={"include": ["backend", "백엔드", "서버"],
                                       "exclude": ["frontend", "프론트엔드"]}),
            company("b", "회사B"),  # roles 없음 = 제한 없음
        ))

    def test_include_hit(self):
        c = self.reg.by_id("a")
        self.assertTrue(self.reg.role_allowed(c, make_item(title="백엔드 개발자")))
        self.assertTrue(self.reg.role_allowed(c, make_item(title="Backend Engineer")))

    def test_include_miss(self):
        c = self.reg.by_id("a")
        self.assertFalse(self.reg.role_allowed(
            c, make_item(title="데이터 분석가", depthTwos=["데이터분석"])))

    def test_exclude_wins_over_include(self):
        c = self.reg.by_id("a")
        self.assertFalse(self.reg.role_allowed(
            c, make_item(title="프론트엔드 백엔드 풀스택")))

    def test_matches_depth_fields_too(self):
        c = self.reg.by_id("a")
        self.assertTrue(self.reg.role_allowed(
            c, make_item(title="개발자 모집", depthTwos=["서버_백엔드"])))

    def test_no_roles_allows_everything(self):
        c = self.reg.by_id("b")
        self.assertTrue(self.reg.role_allowed(c, make_item(title="아무 직무")))


class TestValidation(unittest.TestCase):
    def _err(self, d):
        with self.assertRaises(TargetConfigError) as cm:
            parse_registry(d)
        return str(cm.exception)

    def test_missing_companies(self):
        self.assertIn("companies", self._err({"version": 1}))

    def test_companies_not_a_list(self):
        self.assertIn("배열", self._err({"companies": {}}))

    def test_missing_id(self):
        self.assertIn("id", self._err(doc({"name": "이름만"})))

    def test_missing_name(self):
        self.assertIn("name", self._err(doc({"id": "x"})))

    def test_duplicate_id(self):
        msg = self._err(doc(company("dup", "회사A"), company("dup", "회사B")))
        self.assertIn("중복된 회사 id", msg)
        self.assertIn("dup", msg)

    def test_alias_collision_between_companies(self):
        # id를 구분되는 값으로 둔다. "a"/"b" 는 에러 문구 자체에 들어 있어
        # assertIn 이 항상 참이 되어 아무것도 검증하지 못한다.
        msg = self._err(doc(
            company("first-co", "회사A", aliases=["같은이름"]),
            company("second-co", "회사B", aliases=["같은 이름"]),  # 정규화하면 동일
        ))
        self.assertIn("alias 충돌", msg)
        self.assertIn("first-co", msg)
        self.assertIn("second-co", msg)

    def test_bad_tier(self):
        msg = self._err(doc(company("a", "회사A", tier="Z")))
        self.assertIn("tier", msg)
        self.assertIn("Z", msg)

    def test_bad_enabled_type(self):
        self.assertIn("true/false", self._err(doc(company("a", "회사A", enabled="yes"))))

    def test_aliases_not_a_list(self):
        self.assertIn("aliases", self._err(doc(company("a", "회사A", aliases="x"))))

    def test_source_without_type(self):
        self.assertIn("type", self._err(doc(company("a", "회사A", sources=[{"url": "u"}]))))

    def test_name_normalizes_to_empty(self):
        self.assertIn("정규화", self._err(doc(company("a", "(주)"))))

    def test_bad_id_format(self):
        msg = self._err(doc(company("Bad_Id", "회사A")))
        self.assertIn("id", msg)
        self.assertIn("Bad_Id", msg)

    def test_unknown_source_type(self):
        msg = self._err(doc(company("a", "회사A",
                                    sources=[{"type": "offical"}])))
        self.assertIn("source type", msg)
        self.assertIn("offical", msg)

    def test_unsupported_version(self):
        msg = self._err({"version": 99, "companies": []})
        self.assertIn("version", msg)
        self.assertIn("99", msg)

    def test_tier_is_case_insensitive(self):
        reg = parse_registry(doc(company("a", "회사A", tier="s")))
        self.assertEqual(reg.by_id("a").tier, "S")


class TestNearMiss(unittest.TestCase):
    """완전일치가 놓친 경우를 사용자가 알아챌 수 있어야 한다."""

    def setUp(self):
        self.reg = parse_registry(doc(
            company("example-alpha", "예시알파", aliases=["Example Alpha"])))

    def test_similar_name_is_reported(self):
        # alias 에 없는 표기. 매칭은 안 되지만 경고 후보로는 잡혀야 한다.
        self.assertEqual(self.reg.near_misses("예시알파테크"), ["example-alpha"])
        self.assertEqual(self.reg.near_misses("Example Alpha Games"),
                         ["example-alpha"])

    def test_exact_match_is_not_a_near_miss(self):
        self.assertEqual(self.reg.near_misses("예시알파"), [])
        self.assertEqual(self.reg.near_misses("Example Alpha"), [])

    def test_unrelated_name_is_not_reported(self):
        self.assertEqual(self.reg.near_misses("전혀다른회사"), [])
        self.assertEqual(self.reg.near_misses(""), [])

    def test_near_miss_never_affects_matching(self):
        # 경고용이지 매칭용이 아니다.
        self.assertIsNone(self.reg.resolve("예시알파테크"))


class TestSyntheticDepth(unittest.TestCase):
    """일부 소스는 depth 필드를 지어낸다. 직무 필터가 그걸 믿으면 안 된다."""

    def setUp(self):
        self.reg = parse_registry(doc(company(
            "a", "회사A", roles={"include": ["백엔드"], "exclude": []})))
        self.c = self.reg.by_id("a")

    def test_real_depth_is_trusted(self):
        it = make_item(title="개발자 모집", depthTwos=["서버_백엔드"])
        self.assertTrue(self.c.role_allowed(it))

    def test_synthetic_depth_is_ignored(self):
        it = make_item(title="디자이너 모집", depthTwos=["서버_백엔드"],
                       _depth_synthetic=True)
        self.assertFalse(self.c.role_allowed(it))

    def test_synthetic_depth_still_matches_on_title(self):
        it = make_item(title="백엔드 개발자", depthTwos=["서버_백엔드"],
                       _depth_synthetic=True)
        self.assertTrue(self.c.role_allowed(it))


class TestLoadRegistry(unittest.TestCase):
    def test_no_targets_config_gives_empty(self):
        self.assertEqual(len(load_registry(make_config())), 0)

    def test_missing_file_gives_empty_but_warns(self):
        # 경로 오타가 이 경로로 떨어진다. 조용하면 target을 놓친 걸 못 알아챈다.
        from radar import targeting as t
        said = []
        orig, t.log = t.log, lambda m: said.append(m)
        try:
            cfg = make_config(targets={"file": "nope-does-not-exist.json"})
            self.assertEqual(len(load_registry(cfg)), 0)
        finally:
            t.log = orig
        self.assertTrue(any("nope-does-not-exist.json" in m for m in said), said)

    def test_no_targets_key_is_silent(self):
        # 아예 설정을 안 한 사람에게는 경고하지 않는다.
        from radar import targeting as t
        said = []
        orig, t.log = t.log, lambda m: said.append(m)
        try:
            self.assertEqual(len(load_registry(make_config())), 0)
        finally:
            t.log = orig
        self.assertEqual(said, [])

    def test_disabled_gives_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc(company("a", "회사A")), f, ensure_ascii=False)
            cfg = make_config(targets={"enabled": False, "file": p})
            self.assertEqual(len(load_registry(cfg)), 0)

    def test_loads_absolute_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc(company("a", "회사A", aliases=["Company A"])),
                          f, ensure_ascii=False)
            reg = load_registry(make_config(targets={"file": p}))
            self.assertEqual(reg.resolve("Company A").id, "a")

    def test_broken_json_raises(self):
        # 조용히 무시하면 target을 놓친 걸 못 알아챈다.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{ not json")
            with self.assertRaises(TargetConfigError):
                load_registry(make_config(targets={"file": p}))


class TestExampleTemplate(unittest.TestCase):
    """공개 템플릿이 실제로 파싱되는지. 문서와 코드가 갈라지는 걸 막는다."""

    def test_example_file_parses(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "target-companies.example.json")
        with open(path, encoding="utf-8") as f:
            reg = parse_registry(json.load(f), where="target-companies.example.json")
        self.assertGreaterEqual(len(reg), 3)
        # 계열사 분리 예시가 실제로 분리되는지
        a = reg.resolve("예시알파")
        cloud = reg.resolve("예시알파 클라우드")
        self.assertIsNotNone(a)
        self.assertIsNotNone(cloud)
        self.assertNotEqual(a.id, cloud.id)
        # enabled=false 예시가 실제로 꺼져 있는지
        self.assertIsNone(reg.resolve("예시베타"))
        self.assertIsNotNone(reg.resolve_any("예시베타"))
        # tier 값이 전부 유효한지
        self.assertTrue(all(c.tier in ("S", "A", "B", "C") for c in reg.companies))

    def test_registry_handles_recommended_scale(self):
        # 권장 규모(30~50곳)를 실제로 파싱하고 조회되는지 확인한다.
        many = [company(f"c{n}", f"회사{n}", aliases=[f"Company{n}"])
                for n in range(50)]
        reg = parse_registry(doc(*many))
        self.assertEqual(len(reg), 50)
        self.assertEqual(reg.resolve("Company49").id, "c49")


class TestRegistryConstruction(unittest.TestCase):
    def test_empty_registry(self):
        reg = CompanyRegistry([])
        self.assertEqual(len(reg), 0)
        self.assertIsNone(reg.resolve("아무회사"))
        self.assertEqual(reg.enabled_companies(), [])

    def test_official_urls(self):
        reg = parse_registry(doc(company(
            "a", "회사A", sources=[{"type": "platform"},
                                 {"type": "official", "url": "https://x.invalid/"}])))
        c = reg.by_id("a")
        self.assertEqual(c.official_urls(), ["https://x.invalid/"])
        self.assertEqual(sorted(c.source_types()), ["official", "platform"])


if __name__ == "__main__":
    unittest.main()
