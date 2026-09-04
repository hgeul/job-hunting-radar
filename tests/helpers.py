# -*- coding: utf-8 -*-
"""테스트 공용 헬퍼. 합성 데이터만 쓴다(크롤 대상·개인정보 미포함)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_item(**kw):
    """1차 소스 목록 항목 형태의 합성 공고 dict."""
    item = {
        "id": kw.pop("id", "id-1"),
        "title": kw.pop("title", "백엔드 개발자"),
        "company": {"name": kw.pop("company", "테스트회사")},
        "depthOnes": kw.pop("depthOnes", ["IT_개발"]),
        "depthTwos": kw.pop("depthTwos", ["서버_백엔드"]),
        "regions": kw.pop("regions", ["서울"]),
        "employeeTypes": kw.pop("employeeTypes", ["정규직"]),
        "careerMin": kw.pop("careerMin", 3),
        "careerMax": kw.pop("careerMax", 7),
        "_source": kw.pop("source", "platform"),
    }
    item.update(kw)
    return item


def make_config(**over):
    """job_watcher가 요구하는 최소 config 형태."""
    cfg = {
        "name": "테스터",
        "search": {"filters": {}, "keywords": [], "max_pages": 1, "page_size": 10,
                   "new_within_days": 3, "max_detail_fetches": 10},
        "filter": {
            "target_depth_ones": ["IT_개발"],
            "primary_depth_twos": ["서버_백엔드"],
            "secondary_depth_twos": ["시스템_네트워크"],
            "regions_preferred": ["서울", "경기"],
            "exclude_title_keywords": ["프론트엔드", "디자이너"],
        },
        "profile": {"career_years": 4,
                    "tech_primary": ["java", "spring"],
                    "tech_secondary": ["kafka"]},
        "scoring": {
            "weights": {"role": 35, "career": 25, "tech_title": 20,
                        "region": 10, "employment": 10},
            "career_tolerance_over": 2, "rule_threshold": 45, "notify_threshold": 60,
        },
        "llm": {"enabled": False, "provider": "claude_cli", "model": "m",
                "cli_model": "sonnet", "max_calls_per_run": 5, "max_output_tokens": 800},
        "output": {"matches_dir": "matches/test", "state_file": "state/test.json",
                   "profile_file": "profile.example.md",
                   "min_score_in_note": 50, "seen_retention_days": 90},
        "enrich": {"enabled": False, "min_chars": 100,
                   "max_crawls_per_run": 1, "page_timeout_sec": 5},
        "notify": {"telegram": {"enabled": False, "threshold": 50,
                                "chat_id": "", "bot_token_env": "TELEGRAM_BOT_TOKEN"}},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg
