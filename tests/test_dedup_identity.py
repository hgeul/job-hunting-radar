# -*- coding: utf-8 -*-
"""소스 간 병합의 identity 안정성.

가장 되돌리기 어려운 변경이라 여기서 고정한다.

배경: 예전 구현은 "마감일 있는 쪽을 본체로" 교체했다. 그러면 살아남는 id가
바뀌고, 이미 notified 로 기록된 공고가 새 id를 받아 **다시 알림이 간다**.
공식 소스를 켰다 껐다 하는 것만으로도 id가 왔다갔다했다.
실측에서 6건 전부 플랫폼 id가 greetinghr id로 교체되고 있었다.
"""

import unittest

from radar.dedup import canonicalize, dedup_across_sources, norm_key
from radar.targeting import parse_registry


def reg(*pairs):
    return parse_registry({"version": 1, "companies": [
        {"id": cid, "name": name, "aliases": list(al)}
        for cid, name, al in pairs]})


def item(iid, source, company, title, **kw):
    d = {"id": iid, "_source": source, "company": {"name": company},
         "title": title}
    d.update(kw)
    return d


class TestIdStability(unittest.TestCase):
    def setUp(self):
        self.reg = reg(("alpha", "예시알파", ["Example Alpha"]))

    def _merge(self, a, b):
        items = canonicalize([a, b], self.reg)
        return dedup_across_sources(items)

    def test_first_seen_id_survives(self):
        # 플랫폼이 먼저 수집되므로 상태 이력이 있는 플랫폼 id가 남아야 한다.
        plat = item("plat-uuid", "platform", "예시알파", "서버 엔지니어")
        off = item("greetinghr:alpha:1", "greetinghr", "Example Alpha", "서버 엔지니어",
                   _deadline="상시")
        out = self._merge(plat, off)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "plat-uuid")

    def test_deadline_is_still_gained(self):
        # id는 지키되 부족한 정보는 채워온다.
        plat = item("plat-uuid", "platform", "예시알파", "서버 엔지니어")
        off = item("greetinghr:alpha:1", "greetinghr", "Example Alpha", "서버 엔지니어",
                   _deadline="2026-10-01", _detail_url="https://x.invalid/o/1")
        out = self._merge(plat, off)
        self.assertEqual(out[0]["_deadline"], "2026-10-01")
        self.assertEqual(out[0]["_detail_url"], "https://x.invalid/o/1")

    def test_existing_value_not_overwritten(self):
        plat = item("plat-uuid", "platform", "예시알파", "서버 엔지니어",
                    _deadline="2026-09-10")
        off = item("greetinghr:alpha:1", "greetinghr", "Example Alpha", "서버 엔지니어",
                   _deadline="상시")
        out = self._merge(plat, off)
        self.assertEqual(out[0]["_deadline"], "2026-09-10")

    def test_both_source_badges_recorded(self):
        plat = item("plat-uuid", "platform", "예시알파", "서버 엔지니어")
        off = item("greetinghr:alpha:1", "greetinghr", "Example Alpha", "서버 엔지니어",
                   _deadline="상시")
        out = self._merge(plat, off)
        self.assertEqual(sorted(out[0]["_sources"]), ["greetinghr", "platform"])

    def test_toggling_official_does_not_change_surviving_id(self):
        # --official 을 켜고 끄는 것만으로 재알림이 나면 안 된다.
        plat = item("plat-uuid", "platform", "예시알파", "서버 엔지니어")
        off = item("greetinghr:alpha:1", "greetinghr", "Example Alpha", "서버 엔지니어",
                   _deadline="상시")
        without = dedup_across_sources(canonicalize([dict(plat)], self.reg))
        with_off = dedup_across_sources(
            canonicalize([dict(plat), dict(off)], self.reg))
        self.assertEqual(without[0]["id"], with_off[0]["id"])


class TestCanonicalMatching(unittest.TestCase):
    def test_different_spellings_merge(self):
        r = reg(("alpha", "예시알파", ["Example Alpha"]))
        a = item("1", "platform", "예시알파", "백엔드 개발자")
        b = item("2", "greetinghr", "Example Alpha", "백엔드 개발자")
        canonicalize([a, b], r)
        self.assertEqual(norm_key(a), norm_key(b))

    def test_non_target_company_keeps_name_key(self):
        # 감시 대상이 아니면 canonical id가 없으므로 표기로 비교한다.
        r = reg(("alpha", "예시알파", ["Example Alpha"]))
        a = item("1", "platform", "무관회사", "백엔드 개발자")
        canonicalize([a], r)
        self.assertNotIn("_canonical_company", a)

    def test_different_companies_never_merge(self):
        r = reg(("alpha", "예시알파", ["Example Alpha"]), ("beta", "예시베타", []))
        a = item("1", "platform", "예시알파", "백엔드 개발자")
        b = item("2", "greetinghr", "예시베타", "백엔드 개발자")
        out = dedup_across_sources(canonicalize([a, b], r))
        self.assertEqual(len(out), 2)

    def test_same_source_reposts_are_merged(self):
        # 예전 계약은 "같은 소스 동명 공고는 서로 다른 자리"였다. 실측(2026-09-07)에서
        # 뒤집혔다: 614건 중 87건이 회사·제목·경력·근무지가 전부 같은 재게시였고,
        # 하나는 33건짜리였다. 접지 않으면 중복마다 LLM 을 부르고 중복 알림이 간다.
        r = reg(("alpha", "예시알파", ["Example Alpha"]))
        a = item("1", "platform", "예시알파", "백엔드 개발자",
                 careerMin=3, careerMax=7, regions=["서울"])
        b = item("2", "platform", "예시알파", "백엔드 개발자",
                 careerMin=3, careerMax=7, regions=["서울"])
        out = dedup_across_sources(canonicalize([a, b], r))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "1")  # 먼저 온 id 유지

    def test_empty_registry_is_noop(self):
        a = item("1", "platform", "예시알파", "백엔드")
        canonicalize([a], parse_registry({"version": 1, "companies": []}))
        self.assertNotIn("_canonical_company", a)
        canonicalize([a], None)
        self.assertNotIn("_canonical_company", a)


class TestSameTitleDifferentJobs(unittest.TestCase):
    """같은 회사가 같은 제목으로 다른 공고를 올리면 한쪽이 사라진다.

    현재 정책상 소스가 다르면 병합된다. 이건 알려진 절충이고,
    같은 소스 안에서는 병합하지 않으므로 실제 손실 범위가 제한된다.
    """

    def test_cross_source_same_title_merges(self):
        r = reg(("alpha", "예시알파", ["Example Alpha"]))
        a = item("1", "platform", "예시알파", "백엔드 개발자")
        b = item("2", "greetinghr", "Example Alpha", "백엔드 개발자")
        out = dedup_across_sources(canonicalize([a, b], r))
        # 병합되지만 살아남은 쪽 id는 먼저 온 것이다.
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "1")

    def test_same_title_different_career_survives(self):
        # 제목이 같아도 요구 경력이 다르면 다른 공고다. 실측: 제목만으로 접으면
        # 42개 그룹에서 서로 다른 메타가 합쳐졌다(경력 3~7 과 5~15 처럼).
        r = reg(("alpha", "예시알파", ["Example Alpha"]))
        out = dedup_across_sources(canonicalize([
            item("1", "greetinghr", "Example Alpha", "백엔드 개발자",
                 careerMin=3, careerMax=7),
            item("2", "greetinghr", "Example Alpha", "백엔드 개발자",
                 careerMin=5, careerMax=15)], r))
        self.assertEqual(len(out), 2)

    def test_same_title_different_region_survives(self):
        r = reg(("alpha", "예시알파", ["Example Alpha"]))
        out = dedup_across_sources(canonicalize([
            item("1", "platform", "예시알파", "백엔드 개발자", regions=["서울"]),
            item("2", "platform", "예시알파", "백엔드 개발자", regions=["부산"])], r))
        self.assertEqual(len(out), 2)

    def test_parentheses_distinguish_postings(self):
        # 느슨한 키(norm_key)는 괄호를 지운다. 재게시 판정에 그걸 쓰면 서로 다른
        # 공고가 뭉개진다. 실측: 한 증권사가 (Market Data)·(Trading Platform)·(검색)
        # 등 11건을 올렸는데 느슨한 키로는 전부 하나가 된다.
        rows = [item("1", "platform", "예시알파", "Backend Engineer (Market Data)"),
                item("2", "platform", "예시알파", "Backend Engineer (Trading)"),
                item("3", "platform", "예시알파", "Backend Engineer (검색)")]
        out = dedup_across_sources([dict(r) for r in rows])
        self.assertEqual(len(out), 3)
        self.assertEqual(norm_key(rows[0]), norm_key(rows[1]))  # 느슨한 키는 같다

    def test_ambiguous_loose_key_blocks_cross_source_merge(self):
        # 느슨한 키 하나에 서로 다른 공고가 여럿 걸려 있으면 어느 쪽에 붙일지
        # 정할 근거가 없다. 잘못 합치느니 중복으로 둔다(PLAN 25절).
        r = reg(("alpha", "예시알파", ["Example Alpha"]))
        out = dedup_across_sources(canonicalize([
            item("1", "platform", "예시알파", "Backend Engineer (Market Data)"),
            item("2", "platform", "예시알파", "Backend Engineer (Trading)"),
            item("3", "greetinghr", "Example Alpha", "Backend Engineer")], r))
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
