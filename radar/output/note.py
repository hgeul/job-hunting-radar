# -*- coding: utf-8 -*-
"""마크다운 다이제스트 노트."""

import datetime as dt
import os

from radar.models import deadline_info
from radar.settings import HERE, PLATFORM_NAME, source_badge


def write_note(cfg, matches, stats):
    today = dt.date.today().isoformat()
    out_dir = os.path.join(HERE, cfg["output"]["matches_dir"])
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{today}.md")
    minsc = cfg["output"]["min_score_in_note"]
    notify = cfg["scoring"]["notify_threshold"]

    # target 공고는 점수 문턱을 건너뛴다(cli의 notified 기준과 반드시 같아야 한다.
    # 어긋나면 notified 처리됐는데 노트에 안 실려 영영 못 보는 공고가 생긴다).
    shown = [m for m in matches if m["score"] >= minsc or m.get("target")]
    # target 우선, 그 다음 점수순.
    shown.sort(key=lambda m: (0 if m.get("target") else 1, -m["score"]))

    lines = []
    person = cfg.get("name", "")
    lines.append("---")
    lines.append(f"date: {today}")
    lines.append("type: job-match-digest")
    if person:
        lines.append(f"person: {person}")
    lines.append(f"window_days: {stats.get('window', '?')}")
    lines.append(f"scanned: {stats['scanned']}")
    lines.append(f"fresh: {stats['fresh']}")
    lines.append(f"matched: {len(shown)}")
    n_target = sum(1 for m in shown if m.get("target"))
    if n_target:
        lines.append(f"targets: {n_target}")
    # 🎯 열은 target 매칭이 있을 때만 낸다. 감시 대상 기업을 안 쓰는 사람의
    # 노트가 빈 열 때문에 달라지지 않도록.
    show_target_col = n_target > 0
    lines.append("tags: [job-hunt]")
    lines.append("---")
    lines.append("")
    title_who = f"{person} · " if person else ""
    lines.append(f"# 🎯 {title_who}{PLATFORM_NAME} 매칭 공고 — {today}")
    lines.append("")
    enr = stats.get("enriched", 0)
    enr_s = f"원본크롤 {enr}건 · " if enr else ""
    wd = stats.get("window", "?")
    lines.append(
        f"> **최근 {wd}일 등록 공고** 대상 · 스캔 {stats['scanned']}건 · 규칙통과 {stats['rule_pass']}건 · "
        f"신규 {stats['fresh']}건 · LLM평가 {stats['llm_scored']}건 · "
        f"{enr_s}노트수록 {len(shown)}건 (점수≥{minsc})"
    )
    if stats.get("llm_note"):
        lines.append(f">")
        lines.append(f"> ⚠️ LLM 단계 생략: {stats['llm_note']} (규칙 점수만 표시)")
    if stats.get("targets_error"):
        lines.append(f">")
        lines.append(f"> ⚠️ **감시 대상 기업 목록을 못 읽었습니다**: "
                     f"{stats['targets_error']}")
        lines.append(f">")
        lines.append(f"> 이 다이제스트에는 target 공고가 빠져 있습니다. "
                     f"`target-companies.json` 을 고친 뒤 다시 실행하세요.")
    lines.append("")

    if not shown:
        lines.append("_오늘은 문턱을 넘는 신규 매칭 공고가 없어요._")
    else:
        if show_target_col:
            lines.append("| 점수 | 🎯 | 판정 | 마감 | 회사 | 공고 | 경력 | 지역 |")
            lines.append("|---:|:--:|:--:|:--:|---|---|:--:|:--:|")
        else:
            lines.append("| 점수 | 판정 | 마감 | 회사 | 공고 | 경력 | 지역 |")
            lines.append("|---:|:--:|:--:|---|---|:--:|:--:|")
        for m in shown:
            flag = "🔥" if m["score"] >= notify else ""
            verdict = m.get("verdict") or ("⚙️ LLM 미검증" if m.get("llm") is None else "-")
            dlabel, ddays, dstate = deadline_info(m.get("deadline"))
            dl_cell = f"⏰{dlabel}" if (dstate == "date" and ddays is not None and ddays <= 3) else dlabel
            tg = m.get("target")
            tcell = (f"**{tg['tier']}**" if tg else "") + " | " if show_target_col else ""
            lines.append(
                f"| **{m['score']}**{flag} | {tcell}{verdict} | {dl_cell} | {m['company']} "
                f"| [{m['title']}]({m['url']}) | {m['career']} | {m['region']} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")
        for m in shown:
            flag = " 🔥" if m["score"] >= notify else ""
            sb = source_badge(m.get("sources"))
            sb = f"  {sb}" if sb else ""
            tg = m.get("target")
            head = f"🎯 " if tg else ""
            lines.append(f"## {head}{m['score']}점{flag} · {m['company']} — {m['title']}{sb}")
            lines.append("")
            if tg:
                lines.append(f"- 🎯 **감시 대상 기업** · {tg['tier']} tier ({tg['name']})")
            age = m.get("age")
            age_s = f" | 등록 {age}일 전" if age is not None else ""
            src = m.get("jd_source")
            src_badge = {
                "원본크롤": " · 🔎 원본 JD 크롤 반영",
                "thin": " · ⚠️ 상세 없음(원본 링크 직접 확인 권장)",
            }.get(src, "")
            lines.append(f"- 🔗 [지원/공고 링크]({m['url']}){src_badge}")
            lines.append(f"- 직무: {m['depth']} | 경력: {m['career']} | 지역: {m['region']}{age_s}")
            dlabel, ddays, dstate = deadline_info(m.get("deadline"))
            urgent = " ⏰**마감임박**" if (dstate == "date" and ddays is not None and 0 <= ddays <= 3) else ""
            lines.append(f"- 🗓️ 서류 마감: {dlabel}{urgent}")
            lines.append(
                f"- 규칙점수 {m['rule']}"
                + (f" → LLM {m['llm']}" if m.get("llm") is not None else "")
            )
            if m.get("one_liner"):
                lines.append(f"- 총평: {m['one_liner']}")
            if m.get("reasons"):
                lines.append(f"- ✅ 부합: " + " / ".join(m["reasons"]))
            if m.get("gaps"):
                lines.append(f"- ⚠️ 리스크: " + " / ".join(m["gaps"]))
            # 알림문턱(기본 60) 넘는 유망 공고엔 지원·합격 전략 코멘트
            if m["score"] >= notify and m.get("strategy"):
                lines.append(f"- 🎯 **지원·합격 전략**")
                for s in m["strategy"]:
                    lines.append(f"    - {s}")
            lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path, len(shown)
