# -*- coding: utf-8 -*-
"""상세(JD) 본문 처리와 등록일 계산."""

import datetime as dt


def flatten_content(node, out):
    """상세의 TipTap/ProseMirror content(JSON doc)를 평문으로."""
    if isinstance(node, dict):
        if node.get("type") == "text" and node.get("text"):
            out.append(node["text"])
        for v in node.get("content", []) or []:
            flatten_content(v, out)
    elif isinstance(node, list):
        for v in node:
            flatten_content(v, out)


def created_age_days(created_at):
    """createdAt(ISO) → 오늘까지 경과일. 파싱 실패 시 None."""
    if not created_at:
        return None
    try:
        d0 = dt.date.fromisoformat(str(created_at)[:10])
    except ValueError:
        return None
    return (dt.date.today() - d0).days


def detail_to_text(detail):
    if detail.get("_jd_text"):  # 소스가 구조화 JD를 직접 제공(2차 소스 등)
        return detail["_jd_text"]
    parts = []
    flatten_content(detail.get("content"), parts)
    body = " ".join(parts)
    kw = detail.get("keywords") or []
    if kw:
        body += "\n[태그] " + ", ".join(kw)
    return body.strip()
