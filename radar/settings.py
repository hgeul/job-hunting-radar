# -*- coding: utf-8 -*-
"""환경 바인딩. 크롤 대상(플랫폼명·API 베이스)은 코드에 두지 않고 .env로만 주입한다.

import 시점에 .env를 os.environ에 주입한 뒤 상수를 계산한다(기존 job_watcher.py와 동일 순서).
"""

import os

# 패키지가 radar/ 아래 있으므로 리포 루트는 한 단계 위. config·state·matches 경로 기준점.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(path=None):
    """리포 루트의 .env를 읽어 os.environ에 주입(이미 있는 값은 안 덮음).

    python-dotenv 없이 표준 라이브러리만으로 동작. 파일 없으면 조용히 통과.
    형식: KEY=VALUE (한 줄에 하나, # 주석·빈 줄 무시, 따옴표 자동 제거).
    """
    path = path or os.path.join(HERE, ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:  # noqa: BLE001
        pass


load_dotenv()  # import 시점 주입 → 아래 엔드포인트/키가 채워짐

PLATFORM_NAME = os.getenv("PLATFORM_NAME", "채용 플랫폼")
_API_BASE = (os.getenv("PLATFORM_API_BASE") or "").rstrip("/")
_SITE_BASE = (os.getenv("PLATFORM_SITE_BASE") or "").rstrip("/")
API_LIST = f"{_API_BASE}/api/recruitments"
API_DETAIL = f"{_API_BASE}/api/recruitments/{{id}}"
DETAIL_PAGE = f"{_SITE_BASE}/recruitment/{{id}}"
UA = os.getenv("PLATFORM_UA", "Mozilla/5.0 (job-watcher; personal job-match tool)")

# 2번째 소스(2차 소스)도 대상명을 코드에 두지 않고 .env로만 주입.
PLATFORM_B_API_BASE = (os.getenv("PLATFORM_B_API_BASE") or "").rstrip("/")
PLATFORM_B_SITE_BASE = (os.getenv("PLATFORM_B_SITE_BASE") or "").rstrip("/")
PLATFORM_B_NAME = os.getenv("PLATFORM_B_NAME", "소스B")

_SOURCE_LABEL = {"platform": PLATFORM_NAME, "platform_b": PLATFORM_B_NAME}


def source_badge(sources):
    """공고 출처 배지. 표시명은 .env에서 옴(코드에 대상명 없음)."""
    return " ".join("🔹" + _SOURCE_LABEL.get(s, s) for s in (sources or []))
