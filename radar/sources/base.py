# -*- coding: utf-8 -*-
"""소스 어댑터 공통 계약.

downstream(점수·노트·대시보드)은 소스별 raw 응답 스키마를 몰라야 한다.
어댑터는 raw 응답을 **공통 item dict**로 정규화해서 넘긴다.

공통 item dict (현행 표현. v2에서 dataclass로 승격 예정):
    id            str            소스 내 고유 id (state 키로 그대로 쓰임)
    _source       str            어댑터 이름 ("platform" | "platform_b" | ...)
    title         str
    company       {"name": str}
    depthOnes     list[str]      대분류 직무
    depthTwos     list[str]      소분류 직무
    regions       list[str]
    employeeTypes list[str]
    careerMin     int | None
    careerMax     int | None
    _deadline     str | None     소스가 마감일을 네이티브로 주면 채움("YYYY-MM-DD" | "상시")

소스가 상황에 따라 채우는 키:
    _canonical_company str       감시 대상 기업 id. 소스마다 회사 표기가 달라서
                                 ("예시알파" / "Example Alpha") dedup 이 이 값으로 비교한다.
                                 radar/dedup.py::canonicalize 가 붙이고,
                                 공식 소스는 처음부터 직접 넣는다.
    _depth_synthetic bool        depth 필드를 소스가 준 게 아니라 **우리가 지어냈다**는 표시.
                                 지금은 platform_b 가 넣는다(서버단 카테고리로 이미
                                 필터됐다는 이유로 config 값을 채워 넣기 때문).
                                 직무 필터(targeting)가 이 값을 믿지 않게 한다.

공식 채용소스(radar/sources/official/)만 채우는 키:
    _created_at   str | None     등록일. 목록에서 이미 받아 상세 조회가 불필요할 때.
    _detail_url   str | None     공고 상세 URL. fetch_detail 이 redirectUrl 로 넘긴다.

공통 detail dict:
    createdAt     str | None     등록 시각(ISO). 신규 판정 기준.
    content       JSON | None    TipTap 문서(1차 소스)
    _jd_text      str | None     소스가 평문 JD를 직접 주면 이걸 우선 사용
    redirectUrl   str | None     원본 공고 페이지(enrich 크롤 대상)
    _deadline     str | None
"""


class SourceResult:
    """어댑터 1회 수집 결과. '공고 0건'과 '수집 실패'를 구분하기 위한 그릇.

    PLAN 17절: 30~50개 회사를 감시하면 둘을 섞으면 안 된다.
    Phase 1에서는 값만 채우고, source health 저장은 이후 Phase에서 붙인다.
    """

    __slots__ = ("name", "items", "ok", "error")

    def __init__(self, name, items=None, ok=True, error=None):
        self.name = name
        self.items = items or []
        self.ok = ok
        self.error = error

    @property
    def count(self):
        return len(self.items)

    def __repr__(self):  # pragma: no cover - 디버그용
        state = "ok" if self.ok else f"error({self.error})"
        return f"<SourceResult {self.name} {self.count}건 {state}>"


class JobSource:
    """소스 어댑터 인터페이스.

    새 소스를 붙일 때 이 4개만 구현하면 core pipeline은 건드리지 않아도 된다.
    """

    name = "?"

    def fetch(self, source_cfg, cfg):
        """목록 수집 → 공통 item dict 리스트. 실패는 예외 대신 빈 리스트 + 로그."""
        raise NotImplementedError

    def fetch_detail(self, item):
        """공고 1건 상세 → 공통 detail dict. 실패 시 RuntimeError."""
        raise NotImplementedError

    def apply_url(self, item, detail=None):
        """지원/공고 링크."""
        raise NotImplementedError
