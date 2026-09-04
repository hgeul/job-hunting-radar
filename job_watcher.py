#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""job_watcher — 채용 플랫폼의 공고를 내 이력에 대입해 매칭 공고를 알려주는 워처.

실제 구현은 `radar/` 패키지에 있다. 이 파일은 **엔트리포인트 + 하위호환 표면**이다.
(run.bat·작업 스케줄러가 `python job_watcher.py ...`로 부르므로 파일명·플래그를 유지한다.)

파이프라인:
  1) 채용 플랫폼 공개 API(베이스는 .env의 PLATFORM_*)에서 공고 목록 수집
  2) 직무(depthTwos)·경력·지역으로 규칙 기반 프리필터 + 점수화
  3) createdAt으로 "새로 뜬" 공고만 추림(notified로 재알림 방지)
  4) 플랫폼 본문이 빈약하면 원본 JD를 crawl4ai로 크롤(enrich)
  5) LLM(Claude)이 이력서와 정밀 대조해 재점수 → 노트·대시보드·텔레그램

크롤 대상(플랫폼명·API 베이스)은 코드에 두지 않고 .env로만 주입한다(.env.example 참고).
의존성: 표준 라이브러리만으로 규칙 기반 동작. LLM·enrich는 선택 의존.

사용법:  python job_watcher.py                 (전체 실행)
         python job_watcher.py --no-llm        (규칙 점수만)
         python job_watcher.py --seed          (알림 없이 현재 공고를 seen에 기록만)
         python job_watcher.py --dry-run       (노트/상태 저장 안 함)
         python job_watcher.py --days 3        (신규 판정 윈도우 오버라이드)
         python job_watcher.py --config X.json (프로필 지정)
         python job_watcher.py --test-telegram (텔레그램 설정 확인)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 하위호환 표면: 예전에 `import job_watcher` 로 쓰던 심볼을 그대로 재수출한다.
# 새 코드는 radar.* 를 직접 import 할 것. 여기 목록은 늘리지 않는다.
# ---------------------------------------------------------------------------
from radar.cli import main  # noqa: E402,F401
from radar.config import load_config, load_profile  # noqa: E402,F401
from radar.dedup import dedup_across_sources as _dedup  # noqa: E402,F401
from radar.dedup import norm_key as _norm_key  # noqa: E402,F401
from radar.enrich import crawl_originals  # noqa: E402,F401
from radar.jd import created_age_days, detail_to_text, flatten_content  # noqa: E402,F401
from radar.llm import (  # noqa: E402,F401
    LLM_SYS, build_prompt, llm_score, resolve_engine,
)
from radar.models import build_match as _build_match  # noqa: E402,F401
from radar.models import deadline_info  # noqa: E402,F401
from radar.notify import send_telegram, telegram_test  # noqa: E402,F401
from radar.output import write_dashboard, write_note  # noqa: E402,F401
from radar.scoring import (  # noqa: E402,F401
    rule_score, score_career, score_employment, score_region, score_role,
    score_tech_title, title_excluded,
)
from radar.settings import PLATFORM_NAME, load_dotenv, source_badge  # noqa: E402,F401
from radar.sources import apply_url, fetch_detail_for, fetch_listings  # noqa: E402,F401
from radar.state import load_seen, save_seen  # noqa: E402,F401
from radar.util import http_get_json, http_post_json, http_post_multipart, log  # noqa: E402,F401

if __name__ == "__main__":
    main()
