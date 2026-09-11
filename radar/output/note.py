# -*- coding: utf-8 -*-
"""마크다운 다이제스트 노트."""

import datetime as dt
import os

from radar.models import deadline_info, notify_eligible, rec_rank
from radar.settings import HERE, PLATFORM_NAME, source_badge

_AXIS_LABEL = {"required": "필수", "preferred": "우대", "career": "경력",
               "tech": "기술", "preference": "선호"}


def _axis_summary(fit):
    """적합도 축별 내역 한 줄. 계산에 실제로 쓴 축만 낸다."""
    if not fit:
        return "-"
    parts = fit.get("parts") or {}
    used = fit.get("weights_used") or []
    return " · ".join(f"{_AXIS_LABEL.get(k, k)} {round(parts[k] * 100)}"
                      for k in _AXIS_LABEL if k in used and parts.get(k) is not None)


def write_note(cfg, matches, stats):
    today = dt.date.today().isoformat()
    out_dir = os.path.join(HERE, cfg["output"]["matches_dir"])
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{today}.md")
    minsc = cfg["output"]["min_score_in_note"]
    notify = cfg["scoring"]["notify_threshold"]

    # target 공고는 점수 문턱을 건너뛴다(cli의 notified 기준과 반드시 같아야 한다.
    # 어긋나면 notified 처리됐는데 노트에 안 실려 영영 못 보는 공고가 생긴다).
    # 미평가(LLM 예산 초과)는 싣지 않는다. 노트 수록은 cli 의 notified 처리와 짝이라,
    # 여기 실으면 평가도 못 받은 채 "봤다"로 찍힌다. 건수만 헤더에 알린다.
    deferred_n = sum(1 for m in matches if m.get("deferred"))
    shown = [m for m in matches
             if not m.get("deferred") and (m["score"] >= minsc or m.get("target"))]
    # target 우선 → 추천 순위 → 점수순. 추천을 정렬에 넣지 않으면 결격으로 제외된
    # 공고가 점수만 높아 맨 위에 앉는다(점수는 결격을 모른다).
    shown.sort(key=lambda m: (0 if m.get("target") else 1, rec_rank(m), -m["score"]))

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
    # 구조화 매칭 확보 건수는 노트에 남긴다. 콘솔 로그는 흘러가지만 노트는 남아서
    # "언제부터 요구사항이 안 잡히기 시작했나"를 나중에 되짚을 수 있다.
    # LLM 을 실제로 부른 실행에서만 낸다(--no-llm 이면 "0건 (요구사항 매칭 0건)"이 된다).
    st_n = stats.get("llm_structured")
    st_s = (f"(요구사항 매칭 {st_n}건) "
            if st_n is not None and stats.get("llm_scored") else "")
    lines.append(
        f"> **최근 {wd}일 등록 공고** 대상 · 스캔 {stats['scanned']}건 · 규칙통과 {stats['rule_pass']}건 · "
        f"신규 {stats['fresh']}건 · LLM평가 {stats['llm_scored']}건 {st_s}· "
        f"{enr_s}노트수록 {len(shown)}건 (점수≥{minsc})"
    )
    if stats.get("llm_note"):
        lines.append(f">")
        lines.append(f"> ⚠️ LLM 단계 생략: {stats['llm_note']} (규칙 점수만 표시)")
    if deferred_n:
        lines.append(f">")
        lines.append(f"> ⏳ LLM 예산 초과로 {deferred_n}건은 아직 평가하지 않았습니다. "
                     f"다음 실행에서 평가합니다(신규 윈도우 안에 있는 동안).")
    if stats.get("targets_error"):
        lines.append(f">")
        lines.append(f"> ⚠️ **감시 대상 기업 목록을 못 읽었습니다**: "
                     f"{stats['targets_error']}")
        lines.append(f">")
        lines.append(f"> 이 다이제스트에는 target 공고가 빠져 있습니다. "
                     f"감시 대상 기업 목록을 고친 뒤 다시 실행하세요.")
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
            flag = "🔥" if notify_eligible(m, notify) else ""
            verdict = m.get("verdict") or ("⚙️ 근거 미확보" if m.get("fit_score") is None else "-")
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
            flag = " 🔥" if notify_eligible(m, notify) else ""
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
            fit = m.get("fit")
            if m.get("fit_score") is not None:
                # 점수가 어디서 왔는지 보이게 축을 같이 낸다. 산식이 코드에 있으니
                # 이 줄만 보고도 "왜 이 점수인가"를 되짚을 수 있어야 한다.
                lines.append(f"- 규칙점수 {m['rule']} → **적합도 {m['fit_score']}** "
                             f"({_axis_summary(fit)})")
                cov = (fit or {}).get("required_coverage")
                if cov is not None and (fit or {}).get("required_total"):
                    lines.append(f"- 필수 요구사항 {fit['required_total']}건 중 "
                                 f"{fit['required_known']}건 판정"
                                 + (f" · 근거 없어 강등 {m['demoted']}건"
                                    if m.get("demoted") else ""))
            else:
                lines.append(f"- 규칙점수 {m['rule']} (요구사항 근거 미확보로 적합도 미산출)")
            if m.get("recommendation_why"):
                lines.append("- 판정 사유: " + " / ".join(m["recommendation_why"]))
            if m.get("one_liner"):
                lines.append(f"- 총평: {m['one_liner']}")
            if m.get("reasons"):
                lines.append(f"- ✅ 부합: " + " / ".join(m["reasons"]))
            if m.get("gaps"):
                lines.append(f"- ⚠️ 리스크: " + " / ".join(m["gaps"]))
            # 결격 후보는 점수보다 먼저 눈에 띄어야 한다. 다만 아직 자동 판정에
            # 반영하지 않으므로 그 사실을 같이 적는다.
            # 여기 싣는 건 blocker 의 detail 뿐이다. 요구사항별 근거(이력 원문 인용)는
            # 텔레그램으로 첨부되어 나가므로 노트에 싣지 않는다.
            if m.get("hard_blockers"):
                lines.append("- 🚫 **결격**: "
                             + " / ".join(b["detail"] for b in m["hard_blockers"]))
                lines.append("    - 결격이 있으면 점수와 무관하게 제외로 판정합니다. "
                             "공고 원문을 직접 확인하세요.")
            if m.get("blockers_rejected"):
                # 정책이 몇 건을 걸렀는지만 알린다. LLM 이 쓴 detail 원문은 싣지 않는다.
                # 반려 사유 대부분이 경력 관련이라 그 문장에 이력 정보가 섞이기 쉽고,
                # 이 노트는 텔레그램으로 첨부되어 나간다.
                why = sorted({b["reject"] for b in m["blockers_rejected"]})
                lines.append(f"- 참고: 결격 후보 {len(m['blockers_rejected'])}건을 "
                             f"결격 아님으로 판단 ({' / '.join(why)})")
            # 알림문턱(기본 60) 넘는 유망 공고엔 지원·합격 전략 코멘트.
            # 결격 공고에는 달지 않는다(제외 판정 옆에 지원 전략이 붙으면 모순이다).
            if notify_eligible(m, notify) and m.get("strategy"):
                lines.append(f"- 🎯 **지원·합격 전략**")
                for s in m["strategy"]:
                    lines.append(f"    - {s}")
            lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path, len(shown)
