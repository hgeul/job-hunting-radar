# -*- coding: utf-8 -*-
"""본 공고 상태(seen) 저장소.

키는 소스 접두어 없는 raw 공고 id다. 기존 실사용 state 수백 건이 이 형태라
접두어를 붙이면 전부 신규로 재판정되어 알림이 폭증한다. 바꾸지 않는다.
"""

import datetime as dt
import json
import os

from radar.settings import HERE


def state_path(cfg):
    return os.path.join(HERE, cfg["output"]["state_file"])


def load_seen(cfg):
    """{id: {first_seen, created_at?, notified?, rule?, title?, card?}}.

    파일이 없거나 깨졌으면 빈 dict. 구버전(필드 일부 없음) state도 그대로 읽는다.
    """
    try:
        with open(state_path(cfg), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen(cfg, seen):
    path = state_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 오래된 기록 정리
    keep = cfg["output"].get("seen_retention_days", 90)
    cutoff = (dt.date.today() - dt.timedelta(days=keep)).isoformat()
    seen = {k: v for k, v in seen.items() if v.get("first_seen", "9999") >= cutoff}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)
