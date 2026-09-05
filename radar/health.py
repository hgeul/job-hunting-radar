# -*- coding: utf-8 -*-
"""소스 건강 상태 저장소.

**`공고 0건`과 `수집 실패`를 절대 같은 상태로 두지 않는다**
(TARGET_COMPANIES_RESEARCH 8절). 감시 대상이 수십 곳이면 조용한 실패를
사람이 눈치채지 못한다.

기존 `state/{name}.json` 을 건드리지 않고 **사이드카 파일**에 둔다.
그 파일은 `{공고id: 레코드}` 형태라 최상위에 다른 키를 넣으면
`save_seen`·`write_dashboard` 가 문자열에 `.get` 을 불러 깨진다.
"""

import datetime as dt
import json
import os

from radar.settings import HERE

SCHEMA_VERSION = 1

# 이 횟수 이상 연달아 실패하면 경고를 올린다. 일시적 네트워크 오류로
# 매번 시끄러워지지 않게 한 번은 봐준다.
FAIL_WARN_THRESHOLD = 2


def health_path(cfg):
    """state/{name}.json → state/{name}.sources.json"""
    base = cfg["output"]["state_file"]
    root, _ext = os.path.splitext(base)
    path = root + ".sources.json"
    if not os.path.isabs(path):
        path = os.path.join(HERE, path)
    return path


def load_health(cfg):
    try:
        with open(health_path(cfg), encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "sources": {}}
    if not isinstance(doc, dict):
        return {"schema_version": SCHEMA_VERSION, "sources": {}}
    doc.setdefault("schema_version", SCHEMA_VERSION)
    doc.setdefault("sources", {})
    return doc


def save_health(cfg, doc):
    path = health_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)


def record(doc, results):
    """CompanyFetchResult 목록을 건강 기록에 반영한다.

    성공하면 연속 실패 카운터를 0으로 되돌리고, 실패하면 마지막 성공 시각은
    그대로 둔다. "언제부터 안 되는가"를 알아야 하기 때문이다.
    """
    sources = doc.setdefault("sources", {})
    for r in results:
        key = f"{r.family}:{r.company_id}"
        rec = sources.setdefault(key, {})
        rec["source_id"] = key
        rec["family"] = r.family
        rec["company_id"] = r.company_id
        rec["last_checked_at"] = r.checked_at
        rec["http_status"] = r.http_status
        rec["parser_version"] = r.parser_version
        final = getattr(r, "final_url", None)
        if final:
            rec["final_url"] = final
        if r.ok:
            # 직전에 공고가 있던 소스가 0건이 되는 것도 사건이다. 수집은 성공했는데
            # 결과가 비는 경우(필터 기본값 변경·로그인 게이팅·지역 차단)를
            # 실패와 똑같이 놓치면 "0건 vs 실패" 구분의 반쪽만 지키는 셈이다.
            prev = rec.get("jobs_seen")
            if r.count == 0 and prev:
                rec["last_nonzero_jobs"] = prev
                rec["last_nonzero_at"] = rec.get("last_success_at")
            elif r.count > 0:
                rec.pop("last_nonzero_jobs", None)
                rec.pop("last_nonzero_at", None)
            rec["last_success_at"] = r.checked_at
            rec["last_error"] = None
            rec["jobs_seen"] = r.count
            rec["consecutive_failures"] = 0
        else:
            rec["last_error"] = r.error
            rec["consecutive_failures"] = rec.get("consecutive_failures", 0) + 1
    return doc


def warnings(doc, threshold=FAIL_WARN_THRESHOLD):
    """경고할 소스 목록.

    두 종류를 낸다.
      kind="failed": 연속 실패가 threshold 이상
      kind="emptied": 수집은 되는데 있던 공고가 0건이 됨
    """
    out = []
    for key, rec in sorted((doc.get("sources") or {}).items()):
        n = rec.get("consecutive_failures", 0)
        if n >= threshold:
            out.append({
                "kind": "failed",
                "source_id": key,
                "failures": n,
                "last_error": rec.get("last_error"),
                "last_success_at": rec.get("last_success_at"),
            })
        elif rec.get("jobs_seen") == 0 and rec.get("last_nonzero_jobs"):
            out.append({
                "kind": "emptied",
                "source_id": key,
                "failures": 0,
                "was": rec["last_nonzero_jobs"],
                "last_error": None,
                "last_success_at": rec.get("last_nonzero_at"),
            })
    return out


def summarize(results):
    """이번 실행 요약. 0건과 실패를 나눠 센다."""
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    return {
        "checked": len(results),
        "ok": len(ok),
        "failed": len(failed),
        "empty": len([r for r in ok if r.count == 0]),
        "jobs": sum(r.count for r in ok),
        "failed_ids": [f"{r.family}:{r.company_id}" for r in failed],
        "empty_ids": [f"{r.family}:{r.company_id}" for r in ok if r.count == 0],
    }


def stale_since(rec, now=None):
    """마지막 성공 이후 며칠 지났나. 성공 기록이 없으면 None."""
    last = rec.get("last_success_at")
    if not last:
        return None
    try:
        d = dt.date.fromisoformat(str(last)[:10])
    except ValueError:
        return None
    today = (now or dt.date.today())
    return (today - d).days
