# -*- coding: utf-8 -*-
"""공식 채용소스 스키마(careers_url·platform_family·collection_status)와 YAML 로딩.

조사 문서(TARGET_COMPANIES_RESEARCH 11절)가 "코드가 무시하면 안 된다"고 못박은
필드들이다. 무시하면 45곳에 45개 크롤러를 만드는 방향으로 흘러간다.
"""

import io
import json
import os
import tempfile
import unittest

from tests.helpers import make_config

from radar.targeting import (
    COLLECTIBLE_STATUSES, COLLECTION_STATUSES, TargetConfigError,
    load_registry, parse_registry, read_registry_file,
)


def company(cid, name, **kw):
    c = {"id": cid, "name": name}
    c.update(kw)
    return c


def doc(*companies, **kw):
    d = {"version": 1, "companies": list(companies)}
    d.update(kw)
    return d


class TestSourceFields(unittest.TestCase):
    def test_fields_are_parsed(self):
        reg = parse_registry(doc(company(
            "a", "회사A",
            careers_url="https://careers.a.invalid/jobs",
            platform_family="example_ats",
            verification="verified",
            collection_status="adapter_candidate",
            priority=100,
            source_filter={"company": "회사A"})))
        c = reg.by_id("a")
        self.assertEqual(c.careers_url, "https://careers.a.invalid/jobs")
        self.assertEqual(c.platform_family, "example_ats")
        self.assertEqual(c.verification, "verified")
        self.assertEqual(c.collection_status, "adapter_candidate")
        self.assertEqual(c.priority, 100)
        self.assertEqual(c.source_filter, {"company": "회사A"})

    def test_defaults_when_absent(self):
        c = parse_registry(doc(company("a", "회사A"))).by_id("a")
        self.assertIsNone(c.careers_url)
        self.assertIsNone(c.platform_family)
        self.assertIsNone(c.collection_status)
        self.assertEqual(c.source_filter, {})

    def test_careers_url_must_be_http(self):
        with self.assertRaises(TargetConfigError) as cm:
            parse_registry(doc(company("a", "회사A", careers_url="careers.a.invalid")))
        self.assertIn("careers_url", str(cm.exception))

    def test_bad_collection_status(self):
        with self.assertRaises(TargetConfigError) as cm:
            parse_registry(doc(company("a", "회사A",
                                       collection_status="adaptor_candidate")))
        msg = str(cm.exception)
        self.assertIn("collection_status", msg)
        self.assertIn("adaptor_candidate", msg)

    def test_all_documented_statuses_accepted(self):
        for st in COLLECTION_STATUSES:
            reg = parse_registry(doc(company("a", "회사A", collection_status=st)))
            self.assertEqual(reg.by_id("a").collection_status, st)

    def test_bad_priority_type(self):
        with self.assertRaises(TargetConfigError):
            parse_registry(doc(company("a", "회사A", priority="high")))

    def test_bad_source_filter_type(self):
        with self.assertRaises(TargetConfigError):
            parse_registry(doc(company("a", "회사A", source_filter=["x"])))

    def test_official_urls_merges_careers_url_and_sources(self):
        c = parse_registry(doc(company(
            "a", "회사A",
            careers_url="https://a.invalid/careers",
            sources=[{"type": "official", "url": "https://a.invalid/jobs"},
                     {"type": "platform"}]))).by_id("a")
        self.assertEqual(c.official_urls(),
                         ["https://a.invalid/careers", "https://a.invalid/jobs"])

    def test_official_urls_dedupes(self):
        c = parse_registry(doc(company(
            "a", "회사A",
            careers_url="https://a.invalid/careers",
            sources=[{"type": "official", "url": "https://a.invalid/careers"}]))).by_id("a")
        self.assertEqual(c.official_urls(), ["https://a.invalid/careers"])


class TestCollectionStrategy(unittest.TestCase):
    """수집 가능 여부는 collection_status 가 정한다."""

    def test_collectible_vs_research(self):
        for st in COLLECTIBLE_STATUSES:
            c = parse_registry(doc(company("a", "회사A",
                                           collection_status=st))).by_id("a")
            self.assertTrue(c.is_collectible(), st)
            self.assertFalse(c.needs_research(), st)
        for st in set(COLLECTION_STATUSES) - set(COLLECTIBLE_STATUSES):
            c = parse_registry(doc(company("a", "회사A",
                                           collection_status=st))).by_id("a")
            self.assertFalse(c.is_collectible(), st)
            self.assertTrue(c.needs_research(), st)

    def test_restricted_is_never_collectible(self):
        # 차단 우회를 만들지 말라는 신호다. 자동 수집 대상이 되면 안 된다.
        c = parse_registry(doc(company(
            "a", "회사A",
            collection_status="restricted_research_needed"))).by_id("a")
        self.assertFalse(c.is_collectible())

    def test_status_absent_is_not_collectible_and_not_research(self):
        c = parse_registry(doc(company("a", "회사A"))).by_id("a")
        self.assertFalse(c.is_collectible())
        self.assertFalse(c.needs_research())


class TestPlatformFamilyGrouping(unittest.TestCase):
    def _reg(self):
        return parse_registry(doc(
            company("a1", "A1", platform_family="shared_ats",
                    collection_status="generic_adapter_priority"),
            company("a2", "A2", platform_family="shared_ats",
                    collection_status="generic_adapter_priority"),
            company("b1", "B1", platform_family="own_site",
                    collection_status="adapter_candidate"),
            company("c1", "C1", collection_status="dynamic_research_needed"),
            company("d1", "D1", platform_family="off_ats", enabled=False,
                    collection_status="adapter_candidate"),
        ))

    def test_groups_by_family(self):
        g = self._reg().by_platform_family()
        self.assertEqual(sorted(c.id for c in g["shared_ats"]), ["a1", "a2"])
        self.assertEqual([c.id for c in g["own_site"]], ["b1"])

    def test_company_without_family_is_its_own_group(self):
        self.assertIn("c1", self._reg().by_platform_family())

    def test_disabled_excluded_by_default(self):
        self.assertNotIn("off_ats", self._reg().by_platform_family())
        self.assertIn("off_ats",
                      self._reg().by_platform_family(enabled_only=False))

    def test_collectible_families_sorted_by_coverage(self):
        # 하나 만들면 여러 곳이 켜지는 것부터 나와야 한다.
        fams = list(self._reg().collectible_families())
        self.assertEqual(fams[0], "shared_ats")
        self.assertNotIn("c1", fams)  # 조사 필요는 빠진다

    def test_research_needed_grouped_by_status(self):
        r = self._reg().research_needed()
        self.assertEqual([c.id for c in r["dynamic_research_needed"]], ["c1"])


class TestNearMissPrecision(unittest.TestCase):
    """접두어/접미어 + 길이차 2 이상. 중간 부분일치는 헛경고를 만든다.

    계열사 표기는 모회사 이름을 앞뒤로 늘이거나(예시알파 -> 예시알파랩스)
    떼고 줄이는(예시알파모빌리티 -> 모빌리티) 두 방향으로 나타난다. 둘 다 잡아야 한다.
    """

    def setUp(self):
        self.reg = parse_registry(doc(
            company("parent", "예시알파", aliases=["Example Alpha"]),
            company("abc", "에이비씨디"),
            company("mobility", "예시알파모빌리티"),
            company("short", "베타"),
        ))

    def test_affiliate_prefix_is_reported(self):
        self.assertEqual(self.reg.near_misses("예시알파랩스"), ["parent"])
        self.assertEqual(self.reg.near_misses("Example Alpha Games"), ["parent"])

    def test_affiliate_suffix_is_reported(self):
        # 국내 플랫폼은 모회사 접두어를 떼고 등록하기도 한다.
        self.assertEqual(self.reg.near_misses("모빌리티"), ["mobility"])

    def test_short_brand_can_anchor(self):
        # 2글자 브랜드가 앵커가 못 되면 그 브랜드의 계열사를 통째로 놓친다.
        self.assertEqual(self.reg.near_misses("베타페이먼츠"), ["short"])

    def test_one_char_difference_is_not_reported(self):
        # "이비씨디"는 "에이비씨디"의 접미어지만 길이차가 1이라 계열사로 보기 어렵다.
        # (실제로 겪은 사고: 서로 무관한 두 회사가 한 글자 차이로 걸렸다)
        self.assertEqual(self.reg.near_misses("이비씨디"), [])

    def test_mid_string_substring_is_not_reported(self):
        # 앞뒤 어느 쪽 끝에도 걸리지 않으면 후보가 아니다.
        self.assertEqual(self.reg.near_misses("이비씨"), [])
        self.assertEqual(self.reg.near_misses("비씨"), [])

    def test_exact_match_is_never_a_near_miss(self):
        self.assertEqual(self.reg.near_misses("예시알파"), [])
        self.assertEqual(self.reg.near_misses("베타"), [])

    def test_unrelated_is_not_reported(self):
        self.assertEqual(self.reg.near_misses("전혀다른회사"), [])


class TestYamlLoading(unittest.TestCase):
    YAML = """
version: 1
purpose: test
defaults:
  notify_recommendations: [STRONG_APPLY]
companies:
  - id: alpha
    name: 예시알파
    aliases: [Example Alpha]
    tier: S
    enabled: true
    careers_url: https://careers.alpha.invalid/
    platform_family: shared_ats
    collection_status: generic_adapter_priority
    roles:
      include: [backend]
"""

    def _write(self, d, name, text):
        p = os.path.join(d, name)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_reads_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "t.yaml", self.YAML)
            reg = load_registry(make_config(targets={"file": p}))
        self.assertEqual(reg.resolve("Example Alpha").id, "alpha")
        self.assertEqual(reg.by_id("alpha").platform_family, "shared_ats")

    def test_yml_extension_too(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "t.yml", self.YAML)
            self.assertEqual(len(load_registry(make_config(targets={"file": p}))), 1)

    def test_json_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "t.json", json.dumps(
                doc(company("alpha", "예시알파")), ensure_ascii=False))
            self.assertEqual(len(load_registry(make_config(targets={"file": p}))), 1)

    def test_broken_yaml_raises_with_filename(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "t.yaml", "companies:\n  - id: a\n   bad indent\n")
            with self.assertRaises(TargetConfigError) as cm:
                read_registry_file(p)
            self.assertIn("t.yaml", str(cm.exception))

    def test_top_level_meta_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "t.yaml", self.YAML)
            reg = load_registry(make_config(targets={"file": p}))
        self.assertEqual(reg.meta["purpose"], "test")
        self.assertEqual(reg.meta["defaults"]["notify_recommendations"],
                         ["STRONG_APPLY"])
        self.assertNotIn("companies", reg.meta)


class TestExampleYaml(unittest.TestCase):
    """공개 템플릿이 실제로 파싱되고 가상 데이터만 담는지."""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = os.path.join(root, "target-companies.example.yaml")
        self.reg = parse_registry(read_registry_file(self.path),
                                  where="target-companies.example.yaml")

    def test_parses(self):
        self.assertGreaterEqual(len(self.reg), 3)

    def test_only_fictional_companies(self):
        for c in self.reg.companies:
            self.assertTrue("예시" in c.name or "Example" in c.name, c.name)
            for u in c.official_urls():
                self.assertIn(".invalid", u, u)

    def test_demonstrates_affiliate_separation(self):
        a = self.reg.resolve("예시알파")
        cloud = self.reg.resolve("예시알파 클라우드")
        self.assertIsNotNone(a)
        self.assertIsNotNone(cloud)
        self.assertNotEqual(a.id, cloud.id)

    def test_demonstrates_shared_portal(self):
        # 그룹 포털 공유 + source_filter 사용례가 템플릿에 있어야 한다.
        fam = self.reg.by_platform_family()
        self.assertTrue(any(len(v) > 1 for v in fam.values()))
        self.assertTrue(any(c.source_filter for c in self.reg.companies))

    def test_example_json_and_yaml_agree_on_core_ids(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        j = parse_registry(read_registry_file(
            os.path.join(root, "target-companies.example.json")))
        self.assertEqual(sorted(c.id for c in j.companies),
                         sorted(c.id for c in self.reg.companies))


if __name__ == "__main__":
    unittest.main()
