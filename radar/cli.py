# -*- coding: utf-8 -*-
"""CLI 엔트리. 기존 플래그는 그대로 유지한다(run.bat·작업 스케줄러 호환)."""

import argparse
import datetime as dt

from radar import pipeline
from radar.config import load_config, load_profile
from radar.models import match_card
from radar.notify import send_telegram, telegram_test
from radar.output import write_dashboard, write_note
from radar.settings import load_dotenv
from radar.state import load_seen, save_seen
from radar.targeting import TargetConfigError, load_registry
from radar.util import log


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="LLM 단계 생략(규칙 점수만)")
    ap.add_argument("--seed", action="store_true",
                    help="알림 없이 현재 스캔 공고를 notified로 기록(첫 세팅·소음 억제용)")
    ap.add_argument("--dry-run", action="store_true", help="노트/상태 저장 안 함")
    ap.add_argument("--config", default="config.json",
                    help="프로필 config 파일(다중 지원자용). 기본 config.json")
    ap.add_argument("--days", type=int, default=None, metavar="N",
                    help="신규로 볼 등록 경과일(createdAt 윈도우). "
                         "지정 시 config의 new_within_days를 덮어씀. "
                         "예: 매일 돌리면 --days 1, 3일만에 돌리면 --days 3")
    ap.add_argument("--test-telegram", action="store_true",
                    help="텔레그램 설정 확인용 테스트 메시지 1건 전송 후 종료")
    ap.add_argument("--mode", choices=pipeline.MODES, default=pipeline.MODE_ALL,
                    help="레이더 모드. discovery=조건 기반 전체 탐색만, "
                         "target=감시 대상 기업만, all=둘 다(기본). "
                         "감시 대상 기업이 0곳이면 all과 discovery는 결과가 같다")
    ap.add_argument("--list-targets", action="store_true",
                    help="감시 대상 기업 목록을 출력하고 종료(설정 점검용)")
    return ap


def print_targets(registry, cfg):
    """--list-targets: 레지스트리가 실제로 어떻게 읽혔는지 눈으로 확인."""
    tcfg = cfg.get("targets") or {}
    src = tcfg.get("file") or "(설정 없음)"
    if not len(registry):
        log(f"감시 대상 기업 0곳. targets.file={src}")
        log("  · 파일이 없거나 targets.enabled=false 입니다. "
            "target-companies.example.json 을 복사해 만드세요.")
        return
    on = registry.enabled_companies()
    log(f"감시 대상 기업 {len(registry)}곳 (활성 {len(on)}곳) · targets.file={src}")
    by_tier = {}
    for c in registry.companies:
        by_tier.setdefault(c.tier, []).append(c)
    for tier in ("S", "A", "B", "C"):
        for c in sorted(by_tier.get(tier, []), key=lambda x: x.id):
            state = "  " if c.enabled else "off"
            roles = ""
            if c.roles_include or c.roles_exclude:
                roles = f" | 직무 +{len(c.roles_include)}/-{len(c.roles_exclude)}"
            alias = f" | alias {len(c.aliases)}개" if c.aliases else " | alias 없음"
            log(f"  [{tier}]{state} {c.id:24s} {c.name}{alias}{roles}")
    no_alias = [c.id for c in on if not c.aliases]
    if no_alias:
        log(f"  ! alias 없는 활성 기업 {len(no_alias)}곳: {', '.join(no_alias[:8])}")
        log("    공고에 뜨는 표기가 name과 정확히 다르면 못 잡습니다. "
            "한글명·영문명을 aliases에 넣으세요.")


def main(argv=None):
    args = build_parser().parse_args(argv)

    load_dotenv()  # .env(있으면)의 TELEGRAM_BOT_TOKEN 등 주입
    cfg = load_config(args.config)
    if args.test_telegram:
        telegram_test(cfg)
        return
    registry, registry_error = None, None
    try:
        registry = load_registry(cfg)
    except TargetConfigError as e:
        registry_error = str(e)
        if args.mode == pipeline.MODE_TARGET or args.list_targets:
            # target 만 돌리라고 했는데 목록을 못 읽으면 할 일이 없다. 바로 멈춘다.
            log(f"감시 대상 기업 설정 오류: {registry_error}")
            raise SystemExit(2)
        # discovery 는 살린다. 설정 오타 하나로 그날 다이제스트·알림이
        # 통째로 안 나가는 게 더 나쁘다. 대신 노트에도 경고를 남긴다.
        log(f"  ! 감시 대상 기업 설정 오류: {registry_error}")
        log("    → 이번 실행은 discovery 만 돕니다. "
            "target-companies.json 을 고친 뒤 다시 실행하세요.")
    if args.list_targets:
        print_targets(registry, cfg)
        return
    person = cfg.get("name", "")
    if person:
        log(f"===== 프로필: {person} ({args.config}) =====")
    if registry is not None and len(registry):
        log(f"감시 대상 기업 {len(registry.enabled_companies())}곳 활성 "
            f"(등록 {len(registry)}곳) · mode={args.mode}")
    profile_text = load_profile(cfg)
    seen = load_seen(cfg)  # {id: {first_seen, created_at, age?, notified, title, rule}}
    today = dt.date.today().isoformat()
    window = args.days if args.days is not None else cfg["search"]["new_within_days"]
    if args.days is not None:
        log(f"신규 윈도우: createdAt ≤ {window}일 (--days 오버라이드)")

    result = pipeline.run(cfg, seen, profile_text, window,
                          no_llm=args.no_llm, seed=args.seed,
                          mode=args.mode, registry=registry,
                          registry_error=registry_error)
    seen, stats, matches = result.seen, result.stats, result.matches

    if result.seeded:
        if not args.dry_run:
            save_seen(cfg, seen)
        log(f"--seed 완료: 신규 {stats['fresh']}건을 notified 처리. "
            f"(총 {len(seen)} id 기록) 다음 실행부터 새 공고만 알림.")
        return

    # 콘솔 요약
    notify = cfg["scoring"]["notify_threshold"]
    log(f"=== 신규 매칭 (알림문턱 {notify}) ===")
    for m in matches[:12]:
        flag = "🔥" if m["score"] >= notify else "  "
        tg = m.get("target")
        mark = f" 🎯{tg['tier']}" if tg else ""
        log(f"  {flag} {m['score']:>5}{mark} | {m['company']} | {m['title'][:34]}")

    if args.dry_run:
        log("--dry-run: 노트/상태 저장 생략")
        return

    # 노트에 수록된 공고만 notified 처리 + 대시보드용 카드 축적.
    # target 공고는 점수 문턱을 건너뛴다: 지정한 회사 공고를 문턱 때문에
    # 놓치면 Target Radar를 만든 이유가 없어진다.
    minsc = cfg["output"]["min_score_in_note"]
    for m in matches:
        if m["score"] >= minsc or m.get("target"):
            rec = seen[m["id"]]
            rec["notified"] = True
            rec["rule"] = m["rule"]
            rec["card"] = match_card(m, today)

    path, n = write_note(cfg, matches, stats)
    save_seen(cfg, seen)
    dash = write_dashboard(cfg, seen)
    send_telegram(cfg, matches, stats, path)
    hot = sum(1 for m in matches if m["score"] >= notify)
    tgt = len(result.targets())
    tgt_s = f", 🎯{tgt}건" if tgt else ""
    log(f"노트 작성: {path} (수록 {n}건, 🔥{hot}건{tgt_s})")
    if dash:
        log(f"대시보드: {dash}")
