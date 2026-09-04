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
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    load_dotenv()  # .env(있으면)의 TELEGRAM_BOT_TOKEN 등 주입
    cfg = load_config(args.config)
    if args.test_telegram:
        telegram_test(cfg)
        return
    person = cfg.get("name", "")
    if person:
        log(f"===== 프로필: {person} ({args.config}) =====")
    profile_text = load_profile(cfg)
    seen = load_seen(cfg)  # {id: {first_seen, created_at, age?, notified, title, rule}}
    today = dt.date.today().isoformat()
    window = args.days if args.days is not None else cfg["search"]["new_within_days"]
    if args.days is not None:
        log(f"신규 윈도우: createdAt ≤ {window}일 (--days 오버라이드)")

    result = pipeline.run(cfg, seen, profile_text, window,
                          no_llm=args.no_llm, seed=args.seed)
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
        log(f"  {flag} {m['score']:>5} | {m['company']} | {m['title'][:34]}")

    if args.dry_run:
        log("--dry-run: 노트/상태 저장 생략")
        return

    # 노트에 수록된 공고만 notified 처리 + 대시보드용 카드 축적
    minsc = cfg["output"]["min_score_in_note"]
    for m in matches:
        if m["score"] >= minsc:
            rec = seen[m["id"]]
            rec["notified"] = True
            rec["rule"] = m["rule"]
            rec["card"] = match_card(m, today)

    path, n = write_note(cfg, matches, stats)
    save_seen(cfg, seen)
    dash = write_dashboard(cfg, seen)
    send_telegram(cfg, matches, stats, path)
    hot = sum(1 for m in matches if m["score"] >= notify)
    log(f"노트 작성: {path} (수록 {n}건, 🔥{hot}건)")
    if dash:
        log(f"대시보드: {dash}")
