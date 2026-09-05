# -*- coding: utf-8 -*-
"""공식 채용페이지 어댑터 공통 계약.

플랫폼 소스(`radar/sources/platform_*.py`)와 다른 점:
  - 수집 단위가 **감시 대상 기업**이다. config.sources[] 가 아니라 레지스트리가 몬다.
  - 어댑터 하나가 여러 회사를 담당한다(`platform_family` 단위).
    공통 ATS를 쓰는 회사들은 코드 한 벌로 전부 커버된다.
  - 회사별로 성공/실패를 따로 기록한다. "공고 0건"과 "수집 실패"는 다른 사건이다
    (TARGET_COMPANIES_RESEARCH 8절).
"""

import datetime as dt


class CompanyFetchResult:
    """회사 1곳 수집 결과. 성공이든 실패든 항상 만들어진다."""

    __slots__ = ("company_id", "family", "items", "ok", "error",
                 "http_status", "parser_version", "checked_at", "final_url")

    def __init__(self, company_id, family, items=None, ok=True, error=None,
                 http_status=None, parser_version=1):
        self.company_id = company_id
        self.family = family
        self.items = items or []
        self.ok = ok
        self.error = error
        self.http_status = http_status
        self.parser_version = parser_version
        # 요청 URL과 최종 URL이 다르면 리다이렉트다. careers_url 이 죽었는지
        # 사람이 즉시 판별할 수 있게 남긴다.
        self.final_url = None
        self.checked_at = dt.datetime.now().isoformat(timespec="seconds")

    @property
    def count(self):
        return len(self.items)

    def __repr__(self):  # pragma: no cover - 디버그용
        state = "ok" if self.ok else f"error({self.error})"
        return f"<CompanyFetchResult {self.company_id} {self.count}건 {state}>"


class OfficialSource:
    """공식 채용페이지 어댑터.

    새 ATS를 붙이는 방법:
      1) 이 클래스를 상속해 `family`와 `fetch_company()` 를 구현
      2) `radar/sources/official/__init__.py` 의 OFFICIAL_ADAPTERS 에 한 줄 등록
      3) 레지스트리에서 해당 회사의 `platform_family` 를 그 이름으로 맞춤
    core pipeline 은 건드리지 않는다.
    """

    family = "?"
    # 파서를 고칠 때마다 올린다. source health 에 남아서
    # "언제부터 수집이 깨졌나"를 파서 변경과 대조할 수 있다.
    parser_version = 1

    def fetch_company(self, company):
        """TargetCompany 1곳 → CompanyFetchResult. 예외를 밖으로 던지지 않는다."""
        raise NotImplementedError

    def fetch_detail(self, item):
        """공통 detail dict. 목록에서 다 받았으면 추가 요청 없이 만들어 돌려준다.

        파이프라인이 `radar/sources/__init__.py` 의 dispatch 를 통해 부른다.
        구현하지 않으면 실행 도중에 터지므로 계약으로 명시한다.
        """
        raise NotImplementedError

    def apply_url(self, item, detail=None):
        """지원/공고 링크. 위와 같은 이유로 계약에 포함한다."""
        raise NotImplementedError

    # -- 하위 구현이 쓰는 헬퍼 ---------------------------------------
    def ok(self, company, items, http_status=200, final_url=None):
        r = CompanyFetchResult(company.id, self.family, items, ok=True,
                               http_status=http_status,
                               parser_version=self.parser_version)
        r.final_url = final_url
        return r

    def fail(self, company, error, http_status=None):
        return CompanyFetchResult(company.id, self.family, [], ok=False,
                                  error=str(error)[:200],
                                  http_status=http_status,
                                  parser_version=self.parser_version)

    def base_item(self, company, source_id, title):
        """공통 item dict 뼈대. 계약은 radar/sources/base.py docstring 참고.

        id 는 `{family}:{회사}:{소스id}` 다. family 만 붙이면 같은 ATS 의 다른
        테넌트가 같은 번호를 쓸 때 state 에서 한쪽이 다른 쪽을 조용히 덮어쓴다.
        실측에서 충돌은 없었지만 전역 유일이라는 보장이 없어 회사를 끼워 둔다.
        공식 소스는 기존 상태 이력이 없어 지금 바꾸는 비용이 0이다.
        """
        return {
            "id": f"{self.family}:{company.id}:{source_id}",
            "_source": self.family,
            "_canonical_company": company.id,
            "title": title or "?",
            "company": {"name": company.name},
            "depthOnes": [],
            "depthTwos": [],
            "regions": [],
            "employeeTypes": [],
            "careerMin": None,
            "careerMax": None,
            "_deadline": None,
        }
