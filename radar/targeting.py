# -*- coding: utf-8 -*-
"""Target Company Registry: 내가 지정한 기업 목록과 회사명 정규화·alias 매칭.

회사 이름은 **코드에 하드코딩하지 않는다**(PLAN 23절). 전부 설정 파일에서 온다.
설정 파일은 개인정보라 `target-companies.{yaml,json}`은 gitignore, 공개는 example 템플릿만.

정규화 원칙(PLAN 6절):
    raw name → 소문자 → 법인표기 제거 → 공백·기호 제거 → alias 완전일치 lookup

**부분일치·유사도 매칭을 하지 않는다.** "예시알파"와 "예시알파 클라우드"는 별개 target으로
유지되어야 하는데, 접두어 매칭을 허용하면 둘이 합쳐진다. 잘못 합치는 것이
못 찾는 것보다 위험하다(PLAN 25절).

설정 파일은 YAML·JSON 둘 다 읽는다. YAML 은 PyYAML 이 있을 때만 읽히고,
없으면 무엇을 설치해야 하는지 알려준다(조용히 target 을 통째로 빠뜨리지 않는다).

`collection_status`·`platform_family` 는 **수집 전략을 지시하는 필드**라 코드가 무시하면
안 된다(TARGET_COMPANIES_RESEARCH 11절). 특히 `restricted_research_needed` 는
"자동 접근이 제한될 수 있으니 우회 크롤러부터 만들지 말라"는 뜻이다.
"""

import json
import os
import re
import unicodedata

from radar.scoring import title_excluded
from radar.settings import HERE
from radar.util import log

# 법인 표기. 정규화 때 떼어낸다. 회사명이 아니라 **법인 형태**만 넣는다.
#
# 한글과 라틴을 나눠 다루는 이유:
#   한국어는 띄어쓰기 없이 붙여 쓰는 표기가 흔하다("주식회사예시알파", "예시알파주식회사").
#   여기에 단어경계 가드를 걸면 한국어에서는 가드가 사실상 항상 걸려 아무것도 못 뗀다.
#   반면 한글 법인표기는 3~6자 고유 문자열이라 가드 없이 떼도 다른 회사가 되지 않는다.
#   라틴은 정반대다: "co"·"inc"·"ltd"를 가드 없이 떼면
#   "Cobalt"→"balt", "Incheon"→"heon"처럼 아예 다른 말이 된다.
_LEGAL_TOKENS_KO = (
    "유한책임회사", "주식회사", "유한회사", "합자회사", "합명회사",
)
_LEGAL_TOKENS_EN = (
    "corporation", "incorporated", "limited", "company",
    "corp", "inc", "ltd", "llc", "plc", "gmbh", "pte", "co",
)
# 괄호형 법인 표기. NFKC 정규화가 ㈜→(주), ㈲→(有) 로 풀어주므로 그 결과형을 잡는다.
_LEGAL_MARKS = re.compile(r"\(\s*(?:주|유|株|有)\s*\)")

VALID_TIERS = ("S", "A", "B", "C")

# 수집 전략 상태. 조사 문서(TARGET_COMPANIES_RESEARCH 4절)가 정의한 값.
# 코드가 이 값을 보고 "지금 자동 수집을 시도해도 되는가"를 판단한다.
COLLECTION_STATUSES = (
    # 공통 ATS 를 쓰므로 범용 어댑터 하나로 여러 회사를 함께 커버한다.
    "generic_adapter_priority",
    # 공식 목록이 공개돼 있어 바로 collector 후보로 삼을 수 있다.
    "adapter_candidate",
    # 공식 URL 은 확인했으나 JS 렌더링 비중이 높다. endpoint 조사 먼저.
    "dynamic_research_needed",
    # 자동 접근 제한(403 등) 가능성. **우회를 기본 전략으로 쓰지 않는다.**
    "restricted_research_needed",
    # 그룹 통합 포털. 한 번 수집하고 source_filter 로 회사를 분리한다.
    "group_portal_research_needed",
    # 채용 메뉴는 있으나 실제 공고 목록 endpoint 를 더 확인해야 한다.
    "job_endpoint_research_needed",
)

# 지금 자동 수집을 시도해도 되는 상태. 나머지는 사람이 조사를 먼저 해야 한다.
COLLECTIBLE_STATUSES = ("generic_adapter_priority", "adapter_candidate")
# 등록 가능한 source type. "official"(공식 채용페이지)은 Phase 4에서 실제 수집이 붙는다.
# 지금 허용해 두는 이유: 목록을 미리 채워둘 수 있게 하되 오타는 즉시 잡기 위해서.
VALID_SOURCE_TYPES = ("platform", "platform_b", "official")
# 회사 항목에서 우리가 아는 키. 오타를 잡기 위한 목록이지 금지 목록이 아니다.
# 모르는 키는 막지 않고 경고만 한다(앞으로 늘어날 필드를 미리 막지 않기 위해).
KNOWN_COMPANY_KEYS = frozenset((
    "id", "name", "aliases", "tier", "enabled", "priority", "notes",
    "roles", "sources", "careers_url", "platform_family", "verification",
    "collection_status", "source_filter",
))
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SCHEMA_VERSION = 1


class TargetConfigError(ValueError):
    """target-companies 설정이 잘못됐을 때. 메시지에 어디가 왜 틀렸는지 담는다."""


def _squeeze(s):
    return re.sub(r"[\s\W_]+", "", s)


def _role_norm(s):
    """직무 비교용 정규화: 소문자 + 공백·하이픈·언더바·슬래시 제거.

    "Back-end Engineer"·"백엔드 개발자"·"서버_백엔드"처럼 같은 말이 표기만
    다른 경우가 흔하다. 이걸 접지 않으면 "backend"가 "Back-end"를 놓친다.
    """
    return re.sub(r"[\s\-_/.,()·|]+", "", unicodedata.normalize("NFKC", str(s)).lower())


def normalize_company(raw):
    """회사명 → 비교용 정규화 키. 빈 결과면 빈 문자열.

    NFKC로 전각·호환문자를 먼저 접는다(ＥＸＡＭＰＬＥ→EXAMPLE, ㈜→(주)).
    소스가 항상 반각 NFC를 준다는 보장이 없어서다.
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    s = _LEGAL_MARKS.sub(" ", s)
    stripped = s
    for tok in _LEGAL_TOKENS_KO:
        stripped = stripped.replace(tok, " ")
    for tok in _LEGAL_TOKENS_EN:
        stripped = re.sub(r"(?<![0-9a-z가-힣])" + re.escape(tok) + r"(?![0-9a-z가-힣])",
                          " ", stripped)
    out = _squeeze(stripped)
    # 이름 전체가 법인표기와 겹치면(예: "Company", "주식회사") 다 떼서 빈 키가 된다.
    # 그때는 떼기 전 형태를 쓴다. 빈 키는 서로 다른 회사를 전부 한 칸에 몰아넣는다.
    return out or _squeeze(s)


class TargetCompany:
    """감시 대상 기업 1곳."""

    __slots__ = ("id", "name", "aliases", "tier", "enabled", "sources",
                 "roles_include", "roles_exclude", "roles_source",
                 "priority", "notes", "keys",
                 "careers_url", "platform_family", "verification",
                 "collection_status", "source_filter", "raw", "unknown_keys")

    def __init__(self, raw, where="?", default_roles=None):
        if not isinstance(raw, dict):
            raise TargetConfigError(f"{where}: 회사 항목은 객체여야 합니다 (받은 값: {type(raw).__name__})")
        self.id = str(raw.get("id") or "").strip()
        if not self.id:
            raise TargetConfigError(f"{where}: 'id'가 비어 있습니다")
        if not _ID_RE.match(self.id):
            raise TargetConfigError(
                f"{where}: id '{self.id}' 형식이 잘못됐습니다 "
                f"(소문자·숫자·하이픈만, 예: example-alpha)")
        self.name = str(raw.get("name") or "").strip()
        if not self.name:
            raise TargetConfigError(f"{where}(id={self.id}): 'name'이 비어 있습니다")

        aliases = raw.get("aliases") or []
        if not isinstance(aliases, list):
            raise TargetConfigError(f"{where}(id={self.id}): 'aliases'는 배열이어야 합니다")
        self.aliases = [str(a).strip() for a in aliases if str(a).strip()]

        self.tier = str(raw.get("tier") or "B").strip().upper()
        if self.tier not in VALID_TIERS:
            raise TargetConfigError(
                f"{where}(id={self.id}): tier '{self.tier}'는 허용되지 않습니다 "
                f"(허용: {', '.join(VALID_TIERS)})")

        self.enabled = raw.get("enabled", True)
        if not isinstance(self.enabled, bool):
            raise TargetConfigError(f"{where}(id={self.id}): 'enabled'는 true/false여야 합니다")

        self.sources = raw.get("sources") or []
        if not isinstance(self.sources, list):
            raise TargetConfigError(f"{where}(id={self.id}): 'sources'는 배열이어야 합니다")
        for s in self.sources:
            if not isinstance(s, dict) or not s.get("type"):
                raise TargetConfigError(
                    f"{where}(id={self.id}): sources 항목에는 'type'이 필요합니다")
            if s["type"] not in VALID_SOURCE_TYPES:
                raise TargetConfigError(
                    f"{where}(id={self.id}): 알 수 없는 source type "
                    f"'{s['type']}' (허용: {', '.join(VALID_SOURCE_TYPES)}). "
                    f"오타면 조용히 수집이 안 됩니다")

        roles = raw.get("roles")
        if roles is not None and not isinstance(roles, dict):
            raise TargetConfigError(f"{where}(id={self.id}): 'roles'는 객체여야 합니다")
        # 회사가 자기 roles 를 안 쓰면 최상위 defaults.roles 를 물려받는다.
        # 45곳에 같은 목록을 45번 복제해두면 한 줄 고칠 때 45곳을 다 고쳐야 한다.
        self.roles_source = "own"
        if not roles:
            roles = default_roles or {}
            self.roles_source = "defaults" if roles else "none"
        self.roles_include = [_role_norm(r) for r in (roles.get("include") or [])
                              if str(r).strip()]
        self.roles_exclude = [_role_norm(r) for r in (roles.get("exclude") or [])
                              if str(r).strip()]

        self.priority = raw.get("priority")
        if self.priority is not None and not isinstance(self.priority, int):
            raise TargetConfigError(
                f"{where}(id={self.id}): 'priority'는 정수여야 합니다 "
                f"(받은 값: {self.priority!r})")
        self.notes = raw.get("notes")

        # -- 공식 채용 소스 관련(Phase 4 어댑터가 소비) --------------------
        self.careers_url = (raw.get("careers_url") or "").strip() or None
        if self.careers_url and not self.careers_url.startswith(("http://", "https://")):
            raise TargetConfigError(
                f"{where}(id={self.id}): careers_url 이 http(s) 로 시작하지 않습니다 "
                f"({self.careers_url!r})")
        self.platform_family = (raw.get("platform_family") or "").strip() or None
        # verification 은 조사 신뢰도 메모다. 값이 자유로워 검증하지 않는다.
        self.verification = raw.get("verification")

        self.collection_status = raw.get("collection_status")
        if self.collection_status is not None:
            if self.collection_status not in COLLECTION_STATUSES:
                raise TargetConfigError(
                    f"{where}(id={self.id}): 알 수 없는 collection_status "
                    f"'{self.collection_status}' "
                    f"(허용: {', '.join(COLLECTION_STATUSES)})")
        # 그룹 통합 포털에서 이 회사만 골라내는 필터(예: {"company": "예시알파 클라우드"}).
        self.source_filter = raw.get("source_filter") or {}
        if not isinstance(self.source_filter, dict):
            raise TargetConfigError(
                f"{where}(id={self.id}): 'source_filter'는 객체여야 합니다")
        # 아직 코드가 안 쓰는 필드도 잃지 않게 원본을 들고 있는다.
        self.raw = raw
        # 키 오타는 값 오타보다 위험하다. collection_status 를 colletion_status 로
        # 쓰면 값 검증을 통과해버리고 그 회사는 조용히 "분류 없음"이 된다.
        self.unknown_keys = sorted(set(raw) - KNOWN_COMPANY_KEYS)

        # 이 회사를 가리키는 모든 정규화 키(name + aliases)
        keys = [normalize_company(self.name)]
        keys += [normalize_company(a) for a in self.aliases]
        self.keys = sorted({k for k in keys if k})
        if not self.keys:
            raise TargetConfigError(
                f"{where}(id={self.id}): name/aliases가 전부 정규화 후 비었습니다")

    def role_allowed(self, item):
        """이 회사의 roles.include/exclude로 공고 제목·직무를 거른다.

        exclude가 우선. include가 비어 있으면 "직무 제한 없음"으로 본다.

        회사명은 완전일치인데 직무는 **부분일치**다. 비대칭인 이유:
        회사를 잘못 합치면 엉뚱한 회사 공고를 내 target으로 착각하지만,
        직무는 틀려도 "볼 필요 없는 공고가 하나 섞이는" 정도로 끝난다.
        대신 include/exclude 값은 짧은 조각("qa", "ios")보다
        구체적인 말("QA 엔지니어", "안드로이드")을 쓰는 게 안전하다.
        """
        parts = [item.get("title") or ""]
        # 일부 소스는 depth 필드를 자기가 지어낸다(config 값을 그대로 채움).
        # 그런 값으로 직무를 거르면 항상 통과해버리므로 제목만 본다.
        if not item.get("_depth_synthetic"):
            parts.append(" ".join(item.get("depthTwos") or []))
            parts.append(" ".join(item.get("depthOnes") or []))
        hay = _role_norm(" ".join(parts))
        if any(x in hay for x in self.roles_exclude):
            return False
        if not self.roles_include:
            return True
        return any(x in hay for x in self.roles_include)

    def official_urls(self):
        """공식 채용페이지 URL. careers_url 과 sources[].url 을 합쳐 돌려준다."""
        urls = [self.careers_url] if self.careers_url else []
        urls += [s.get("url") for s in self.sources
                 if s.get("type") == "official" and s.get("url")]
        return list(dict.fromkeys(u for u in urls if u))

    def is_collectible(self):
        """지금 자동 수집을 시도해도 되는 상태인가.

        False 면 사람이 수집 전략을 먼저 조사해야 한다는 뜻이다. 특히
        restricted_research_needed 는 차단 우회를 만들라는 신호가 아니다.
        """
        return self.collection_status in COLLECTIBLE_STATUSES

    def needs_research(self):
        return (self.collection_status is not None
                and self.collection_status not in COLLECTIBLE_STATUSES)

    def source_types(self):
        return [s.get("type") for s in self.sources if s.get("type")]

    def __repr__(self):  # pragma: no cover - 디버그용
        return f"<TargetCompany {self.id} tier={self.tier} enabled={self.enabled}>"


class CompanyRegistry:
    """회사명 → TargetCompany 해석기.

    `enabled: false`인 회사도 **인덱스에는 남긴다**. 두 가지를 얻는다.
    (1) 꺼진 회사의 alias도 충돌 검사에 참여한다 → 나중에 다시 켤 때
        이름이 겹쳐 있으면 그때가 아니라 지금 걸린다.
    (2) `--list-targets`와 near-miss 경고가 "꺼놓은 target"과
        "아예 target이 아님"을 구분해 말할 수 있다.
    `resolve()`는 켜진 회사만, `resolve_any()`는 꺼진 것까지 돌려준다.
    """

    def __init__(self, companies=None, version=1, meta=None):
        self.version = version
        # 코드가 아직 안 쓰는 최상위 블록(profile_assumption, defaults,
        # generated_at, purpose)을 보존한다. 뒤 Phase 에서 소비한다.
        self.meta = dict(meta or {})
        self.companies = list(companies or [])
        self._by_id = {}
        self._by_key = {}
        for c in self.companies:
            if c.id in self._by_id:
                raise TargetConfigError(f"중복된 회사 id: '{c.id}'")
            self._by_id[c.id] = c
            for k in c.keys:
                owner = self._by_key.get(k)
                if owner is not None and owner.id != c.id:
                    raise TargetConfigError(
                        f"alias 충돌: '{k}'를 '{owner.id}'와 '{c.id}'가 함께 주장합니다. "
                        f"서로 다른 계열사를 같은 이름으로 두지 마세요")
                self._by_key[k] = c

    # -- 조회 ---------------------------------------------------------
    def __len__(self):
        return len(self.companies)

    def enabled_companies(self):
        return [c for c in self.companies if c.enabled]

    def by_id(self, cid):
        return self._by_id.get(cid)

    def resolve(self, raw_company_name):
        """켜져 있는 target만 해석. 아니면 None."""
        c = self.resolve_any(raw_company_name)
        return c if (c is not None and c.enabled) else None

    def resolve_any(self, raw_company_name):
        """꺼진 target도 포함해 해석. 아니면 None."""
        return self._by_key.get(normalize_company(raw_company_name))

    def is_target(self, raw_company_name):
        return self.resolve(raw_company_name) is not None

    def resolve_item(self, item):
        """공고 item → TargetCompany | None."""
        return self.resolve((item.get("company") or {}).get("name"))

    # -- 직무 필터 -----------------------------------------------------
    def qualify(self, item, cfg):
        """Target Radar 판정: 이 공고가 감시 대상 기업의 볼 만한 공고인가.

        반환: TargetCompany(후보) | None.

        직무 기준의 우선순위는 다음과 같다.
          1) 회사가 자기 `roles`를 쓰면 그것
          2) 없으면 레지스트리의 `defaults.roles`
          3) 둘 다 없을 때만 전역 `filter.exclude_title_keywords`
        target 기업이라는 이유만으로 디자이너·영업 공고까지 받아보고 싶지는 않기 때문이다.

        규칙 점수 문턱(`rule_threshold`)은 **적용하지 않는다.** 그게 Target Radar의
        존재 이유다: 내가 지정한 회사면 discovery 문턱에 못 미쳐도 놓치지 않는다.
        """
        company = self.resolve_item(item)
        if company is None:
            return None
        if company.roles_include or company.roles_exclude:
            return company if company.role_allowed(item) else None
        return None if title_excluded(item, cfg["filter"]) else company

    def qualify_verbose(self, item, cfg):
        """qualify() + 왜 떨어졌는지. 반환: (TargetCompany|None, 사유|None).

        사유는 "not_target"(감시 대상 아님) | "role"(직무 필터) | None(통과).
        직무 필터 탈락을 세어두지 않으면, 감시 대상 회사 공고가 조용히 사라진 것과
        애초에 공고가 없던 것을 구분할 수 없다. alias 누락은 near-miss 경고로
        보이게 해놓고 직무 누락만 무음이면 같은 실수를 반복하는 것이다.
        """
        company = self.resolve_item(item)
        if company is None:
            return None, "not_target"
        if company.roles_include or company.roles_exclude:
            if company.role_allowed(item):
                return company, None
            return None, "role"
        if title_excluded(item, cfg["filter"]):
            return None, "role"
        return company, None

    def role_allowed(self, company, item):
        """TargetCompany.role_allowed 위임(호출부 편의)."""
        return company.role_allowed(item)

    def by_platform_family(self, enabled_only=True):
        """{platform_family: [TargetCompany]}. Phase 4 어댑터 구현 단위.

        감시 대상이 수십 곳이어도 그 수만큼 크롤러를 만들지 않는다. 공통 ATS를
        쓰는 회사들과 그룹 통합 포털을 공유하는 계열사들을 한 어댑터로 묶는 것이 요점이다.
        family 가 없는 회사는 자기 id 를 키로 단독 그룹이 된다.
        """
        out = {}
        pool = self.enabled_companies() if enabled_only else self.companies
        for c in pool:
            out.setdefault(c.platform_family or c.id, []).append(c)
        return out

    def collectible_families(self):
        """자동 수집을 시도해도 되는 회사만 모은 family 그룹.

        커버 회사 수가 많은 family 부터 돌려준다. 어댑터 구현 순서가 곧
        이 순서다(하나 만들면 여러 회사가 함께 켜지는 것부터).
        """
        groups = {}
        for fam, cs in self.by_platform_family().items():
            hits = [c for c in cs if c.is_collectible()]
            if hits:
                groups[fam] = hits
        return dict(sorted(groups.items(),
                           key=lambda kv: (-len(kv[1]), kv[0])))

    def portal_conflicts(self):
        """같은 careers_url 을 공유하는데 회사 구분 수단이 없는 그룹.

        그룹 통합 포털을 긁으면 공고가 여러 회사 것으로 섞여 나온다. 이때
        `source_filter` 가 없는 회사는 "이 포털 공고 전부가 내 것"으로 읽힐 위험이
        있고, 그건 조사문서 9절이 duplicate 보다 위험하다고 못박은 false merge 다.

        반환: {careers_url: [source_filter 없는 회사]} (2곳 이상 공유하는 경우만)
        """
        by_url = {}
        for c in self.enabled_companies():
            if c.careers_url:
                by_url.setdefault(c.careers_url, []).append(c)
        out = {}
        for url, cs in by_url.items():
            if len(cs) < 2:
                continue
            missing = [c for c in cs if not c.source_filter]
            if missing:
                out[url] = missing
        return out

    def unclassified(self):
        """collection_status 가 없는 회사. 수집 계획 어느 목록에도 안 나온다."""
        return [c for c in self.enabled_companies() if c.collection_status is None]

    def key_typos(self):
        """모르는 키를 가진 회사. 대부분 오타다."""
        return {c.id: c.unknown_keys for c in self.companies if c.unknown_keys}

    def research_needed(self):
        """사람이 수집 전략을 먼저 조사해야 하는 회사들. 상태별로 묶어서."""
        out = {}
        for c in self.enabled_companies():
            if c.needs_research():
                out.setdefault(c.collection_status, []).append(c)
        return out

    def near_misses(self, raw_company_name):
        """target은 아니지만 이름이 비슷한 회사들의 id.

        **매칭에는 절대 쓰지 않는다.** alias를 빠뜨렸을 때 사용자가 알아채도록
        경고에만 쓴다. 완전일치 정책의 비용(미탐)을 보이게 만드는 장치다.

        한쪽이 다른 쪽의 **접두어 또는 접미어**일 때만 후보로 본다. 계열사 표기는
        모회사 이름을 앞뒤로 늘이거나(예시알파 → 예시알파랩스) 떼고 줄이는
        (예시알파모빌리티 → 모빌리티) 형태이기 때문이다.

        길이 차가 2자 이상일 때만 본다. 한 글자만 다른 짝(예: "abc" 와 "xabc")은
        서로 무관한 회사인 경우가 많아 헛경고가 된다.
        중간 부분일치는 허용하지 않는다.
        """
        key = normalize_company(raw_company_name)
        if not key or key in self._by_key or len(key) < 2:
            return []
        out = []
        for k, c in self._by_key.items():
            if len(k) < 2 or abs(len(k) - len(key)) < 2:
                continue
            if (key.startswith(k) or k.startswith(key)
                    or key.endswith(k) or k.endswith(key)):
                out.append(c.id)
        return sorted(set(out))


EMPTY_REGISTRY = CompanyRegistry([])


# =========================================================================
# 로딩
# =========================================================================
def parse_registry(doc, where="target-companies"):
    """설정 dict → CompanyRegistry. 형식 오류는 어디가 왜 틀렸는지 담아 던진다."""
    if not isinstance(doc, dict):
        raise TargetConfigError(f"{where}: 최상위는 객체여야 합니다")
    version = doc.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise TargetConfigError(
            f"{where}: 지원하지 않는 version {version} (이 코드는 {SCHEMA_VERSION}만 읽습니다)")
    raw_companies = doc.get("companies")
    if raw_companies is None:
        raise TargetConfigError(f"{where}: 'companies' 배열이 없습니다")
    if not isinstance(raw_companies, list):
        raise TargetConfigError(f"{where}: 'companies'는 배열이어야 합니다")
    default_roles = ((doc.get("defaults") or {}).get("roles")) or {}
    if default_roles and not isinstance(default_roles, dict):
        raise TargetConfigError(f"{where}: 'defaults.roles'는 객체여야 합니다")
    companies = [TargetCompany(c, where=f"{where}.companies[{n}]",
                               default_roles=default_roles)
                 for n, c in enumerate(raw_companies)]
    meta = {k: v for k, v in doc.items() if k not in ("companies", "version")}
    return CompanyRegistry(companies, version=version, meta=meta)


def load_registry(cfg):
    """config.targets.file 을 읽어 레지스트리 생성.

    설정이 없거나 파일이 없으면 **빈 레지스트리**(discovery 전용 동작 유지).
    파일이 있는데 깨졌으면 예외. 조용히 무시하면 target을 놓친 걸 못 알아챈다.
    """
    tcfg = (cfg.get("targets") or {})
    if not tcfg.get("enabled", True):
        return EMPTY_REGISTRY
    path = tcfg.get("file")
    if not path:
        # targets 설정 자체가 없다 = 사용자가 이 기능을 안 켰다. 조용히 넘어간다.
        return EMPTY_REGISTRY
    if not os.path.isabs(path):
        path = os.path.join(HERE, path)
    if not os.path.isfile(path):
        # 켰다고 적어놨는데 파일이 없다. 경로 오타가 여기로 떨어지므로 조용히 넘기지 않는다.
        log(f"  ! 감시 대상 기업 파일 없음: {tcfg['file']} "
            f"(target-companies.example.yaml 또는 .example.json 을 복사해 만드세요). "
            f"이번 실행은 discovery 만 돕니다.")
        return EMPTY_REGISTRY
    doc = read_registry_file(path)
    return parse_registry(doc, where=os.path.basename(path))


def read_registry_file(path):
    """YAML/JSON 설정 파일 → dict. 확장자로 형식을 고른다.

    YAML 은 PyYAML 이 있어야 읽힌다. 없으면 무엇을 하면 되는지 알려준다.
    조용히 빈 레지스트리로 넘어가지 않는 이유는 "target 을 보고 있다고 믿는데
    실은 아닌" 상태를 만들지 않기 위해서다.

    단 여기서 던지는 TargetConfigError 는 cli 의 실패 격리 경로를 탄다.
    `--mode target`/`--list-targets` 는 중단되지만 `--mode all|discovery` 는
    경고만 남기고 discovery 를 계속 돈다(노트에도 경고가 실린다).
    """
    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        try:
            import yaml  # noqa: WPS433
        except ImportError:
            raise TargetConfigError(
                f"{name}: YAML 을 읽으려면 PyYAML 이 필요합니다. "
                f"`pip install pyyaml` 하거나, 설정을 JSON 으로 바꾸고 "
                f"targets.file 을 .json 파일로 가리키세요") from None
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise TargetConfigError(f"{name}: YAML 파싱 실패: {e}") from e
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise TargetConfigError(f"{name}: JSON 파싱 실패: {e}") from e
