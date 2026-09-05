# -*- coding: utf-8 -*-
"""CLI 엔트리. 기존 플래그는 그대로 유지한다(run.bat·작업 스케줄러 호환)."""

import argparse
import datetime as dt

from radar import pipeline
from radar import health as health_mod
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
    ap.add_argument("--official", action="store_true",
                    help="감시 대상 기업의 공식 채용페이지도 수집(수집 가능으로 "
                         "분류된 곳만). 채용 플랫폼에 안 올라오는 공고를 잡는다")
    ap.add_argument("--source-health", action="store_true",
                    help="공식 채용소스 상태를 출력하고 종료")
    return ap


def print_targets(registry, cfg):
    """--list-targets: 레지스트리가 실제로 어떻게 읽혔는지 눈으로 확인.

    tier 목록만 뱉지 않고 **공식 수집 전략**까지 같이 보여준다. 어느 어댑터를
    먼저 만들면 몇 곳이 함께 켜지는지가 Phase 4 의 실제 의사결정이기 때문이다.
    """
    tcfg = cfg.get("targets") or {}
    src = tcfg.get("file") or "(설정 없음)"
    if not len(registry):
        log(f"감시 대상 기업 0곳. targets.file={src}")
        log("  · 파일이 없거나 targets.enabled=false 입니다. "
            "target-companies.example.yaml 을 복사해 만드세요.")
        return
    on = registry.enabled_companies()
    log(f"감시 대상 기업 {len(registry)}곳 (활성 {len(on)}곳) · targets.file={src}")

    by_tier = {}
    for c in registry.companies:
        by_tier.setdefault(c.tier, []).append(c)
    for tier in ("S", "A", "B", "C"):
        rows = by_tier.get(tier)
        if not rows:
            continue
        log(f"  ── {tier} tier ({len(rows)}곳) ──")
        for c in sorted(rows, key=lambda x: (-(x.priority or 0), x.id)):
            state = "  " if c.enabled else "off"
            roles = ""
            if c.roles_include or c.roles_exclude:
                roles = f" | 직무 +{len(c.roles_include)}/-{len(c.roles_exclude)}"
            alias = f" | alias {len(c.aliases)}" if c.aliases else " | alias 없음"
            fam = f" | {c.platform_family}" if c.platform_family else ""
            # 직접 확인이 아닌 URL 은 눈에 띄게 둔다(간접 확인 + 접근제한이 최악 조합).
            ver = ""
            if c.verification and c.verification != "verified":
                ver = f" | {c.verification}"
            log(f"  [{tier}]{state} {c.id:22s} {c.name}{alias}{roles}{fam}{ver}")

    log("")
    log("공식 채용소스 수집 계획 (어댑터 하나로 여러 곳을 커버하는 순서):")
    for fam, cs in registry.collectible_families().items():
        mark = "★" if len(cs) > 1 else " "
        log(f"  {mark} {fam:28s} {len(cs)}곳: {', '.join(c.id for c in cs)}")

    research = registry.research_needed()
    if research:
        log("")
        log("사람이 수집 전략을 먼저 조사해야 하는 곳:")
        for status in sorted(research):
            cs = research[status]
            log(f"  · {status:30s} {len(cs)}곳: {', '.join(c.id for c in cs)}")
        if "restricted_research_needed" in research:
            log("    ! restricted 는 자동 접근이 제한될 수 있다는 뜻입니다. "
                "차단 우회 크롤러를 만들지 마세요.")

    typos = registry.key_typos()
    if typos:
        log("")
        log(f"  ! 모르는 필드명이 있는 회사 {len(typos)}곳 (오타 가능성):")
        for cid, keys in list(typos.items())[:8]:
            log(f"      {cid}: {', '.join(keys)}")

    unclassified = registry.unclassified()
    if unclassified:
        log("")
        log(f"  ! collection_status 가 없는 활성 기업 {len(unclassified)}곳: "
            f"{', '.join(c.id for c in unclassified[:8])}")
        log("    수집 계획 어느 목록에도 안 나옵니다. 상태를 지정하세요.")

    conflicts = registry.portal_conflicts()
    if conflicts:
        log("")
        log("  ! 같은 채용 포털을 공유하는데 source_filter 가 없는 회사:")
        for url, cs in list(conflicts.items())[:6]:
            log(f"      {', '.join(c.id for c in cs)}  <- {url}")
        log("    포털을 긁으면 남의 회사 공고를 자기 것으로 가져갈 수 있습니다. "
            "각 회사에 source_filter 를 넣으세요.")

    no_alias = [c.id for c in on if not c.aliases]
    if no_alias:
        log("")
        log(f"  ! alias 없는 활성 기업 {len(no_alias)}곳: {', '.join(no_alias[:8])}")
        log("    공고에 뜨는 표기가 name과 정확히 다르면 못 잡습니다. "
            "한글명·영문명을 aliases에 넣으세요.")
    no_url = [c.id for c in on if not c.careers_url]
    if no_url:
        log(f"  ! careers_url 없는 활성 기업 {len(no_url)}곳: {', '.join(no_url[:8])}")


def print_source_health(cfg):
    """--source-health: 공식 채용소스가 언제 마지막으로 성공했는지."""
    doc = health_mod.load_health(cfg)
    sources = doc.get("sources") or {}
    if not sources:
        log("공식 채용소스 수집 기록이 없습니다. "
            "`python job_watcher.py --official` 로 한 번 수집하세요.")
        return
    log(f"공식 채용소스 {len(sources)}곳")
    for key in sorted(sources):
        rec = sources[key]
        fails = rec.get("consecutive_failures", 0)
        days = health_mod.stale_since(rec)
        age = (f"마지막 성공 {days}일 전" if days is not None
               else "성공한 적 없음")
        mark = "!" if fails else " "
        pv = rec.get("parser_version")
        log(f"  {mark} {key:34s} 공고 {str(rec.get('jobs_seen', '-')):>4}건 | {age}"
            + (f" | 연속실패 {fails}회" if fails else "")
            + (f" | parser v{pv}" if pv else ""))
        if rec.get("last_error"):
            log(f"      마지막 오류: {rec['last_error'][:100]}")
        if rec.get("last_nonzero_jobs"):
            log(f"      ! 전에는 {rec['last_nonzero_jobs']}건이었는데 지금 0건입니다. "
                f"수집은 되지만 결과가 비었습니다.")
        if rec.get("final_url"):
            log(f"      최종 URL: {rec['final_url']}")
    warn = health_mod.warnings(doc)
    if warn:
        log("")
        failed = [w for w in warn if w["kind"] == "failed"]
        emptied = [w for w in warn if w["kind"] == "emptied"]
        if failed:
            log(f"  ! 연속 실패 중인 소스 {len(failed)}곳. "
                f"공고가 없는 게 아니라 수집이 안 되는 상태입니다.")
        if emptied:
            log(f"  ! 있던 공고가 사라진 소스 {len(emptied)}곳. "
                f"수집은 되는데 결과가 0건입니다(필터·차단 변경 의심).")


def report_official(cfg, results, save=True):
    """수집 결과를 건강 기록에 반영하고 요약을 남긴다."""
    if not results:
        return
    summary = health_mod.summarize(results)
    log(f"공식 채용소스: {summary['checked']}곳 확인 · 공고 {summary['jobs']}건 "
        f"· 실패 {summary['failed']}곳 · 공고없음 {summary['empty']}곳")
    if summary["failed_ids"]:
        log(f"  ! 수집 실패: {', '.join(summary['failed_ids'])}")
        log("    '공고 없음'이 아니라 '못 가져옴'입니다. URL 이나 페이지 구조를 확인하세요.")
    doc = health_mod.load_health(cfg)
    health_mod.record(doc, results)
    if save:
        health_mod.save_health(cfg, doc)
    for w in health_mod.warnings(doc):
        if w["kind"] == "failed":
            log(f"  ! {w['source_id']} 연속 {w['failures']}회 실패 "
                f"(마지막 성공 {w['last_success_at'] or '없음'})")
        else:
            log(f"  ! {w['source_id']} 공고가 {w['was']}건에서 0건이 됐습니다 "
                f"(수집은 성공). 페이지 필터나 접근 정책 변경을 의심하세요.")


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
            "감시 대상 기업 목록을 고친 뒤 다시 실행하세요.")
    if args.list_targets:
        print_targets(registry, cfg)
        return
    if args.source_health:
        print_source_health(cfg)
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
                          registry_error=registry_error, official=args.official)
    seen, stats, matches = result.seen, result.stats, result.matches

    if result.seeded:
        # seed 여도 공식 페이지 수집은 실제로 일어났다. 건강 기록을 버리면
        # 그 실행에서만 "0건 vs 실패" 구분이 조용히 무효가 된다.
        report_official(cfg, result.official_results, save=not args.dry_run)
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
        # 수집은 이미 실제로 일어났다. 상태는 안 남기되 결과는 보여준다.
        report_official(cfg, result.official_results, save=False)
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

    report_official(cfg, result.official_results)
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
