# -*- coding: utf-8 -*-
"""텔레그램 푸시. 요약 캡션 + 노트 md 첨부(sendDocument)."""

import os

from radar.models import notify_eligible
from radar.settings import PLATFORM_NAME
from radar.util import http_post_json, http_post_multipart, log


def send_telegram(cfg, matches, stats=None, note_path=None):
    """score ≥ notify.telegram.threshold 인 매칭이 있으면 **노트 md 파일을 첨부**해서 푸시.

    - 짧은 캡션(누구·건수·문턱·기간 + 상위 몇 건) + 노트 .md 파일 첨부(sendDocument).
      그룹에서 파일을 탭하면 노트 전체(총평·리스크·전략·공고 링크)를 앱에서 열람.
    - 봇 토큰은 **환경변수(bot_token_env, 기본 TELEGRAM_BOT_TOKEN)에서만** 읽는다.
    - chat_id는 config.notify.telegram.chat_id 또는 env TELEGRAM_CHAT_ID.
    - 설정/토큰/chat_id/대상 없으면 조용히 skip. 신규(재알림 안 된) 공고만이라 중복 없음.
    """
    tg = (cfg.get("notify") or {}).get("telegram") or {}
    if not tg.get("enabled"):
        return
    token = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"))
    chat_id = tg.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("  · 텔레그램 skip: 봇 토큰(env) 또는 chat_id 없음")
        return
    threshold = tg.get("threshold", 50)
    # target 공고는 점수 문턱을 건너뛴다. 지정한 회사에서 공고가 났다는 사실
    # 자체가 알릴 가치이고, 직무 필터는 이미 통과한 것들이다.
    # 노출 판단은 노트·CLI 와 같은 함수를 쓴다. 여기만 따로 두면 노트에는 🔥 가
    # 없는데 텔레그램으로는 나가는 어긋남이 생긴다.
    # 감시 대상 기업은 점수가 낮아도 보낸다(그게 감시하는 이유다). 다만 결격은 뺀다.
    hits = [m for m in matches
            if not m.get("deferred")
            and (notify_eligible(m, threshold)
                 or (m.get("target") and m.get("recommendation") != "SKIP"))]
    if not hits:
        log(f"  · 텔레그램 skip: ≥{threshold}점 신규 없음")
        return
    # target 먼저, 그 다음 점수순.
    hits.sort(key=lambda m: (0 if m.get("target") else 1, -m["score"]))
    notify = cfg["scoring"]["notify_threshold"]
    person = cfg.get("name", "")
    who = f"{person} · " if person else ""
    window = (stats or {}).get("window")
    span = f" · 최근 {window}일" if window is not None else ""
    n_target = sum(1 for m in hits if m.get("target"))
    tgt_s = f" · 🎯{n_target}" if n_target else ""
    lines = [f"🎯 {who}{PLATFORM_NAME} 매칭 {len(hits)}건 (≥{threshold}점{span}{tgt_s})"]
    for m in hits[:8]:
        tg = m.get("target")
        flag = f"🎯{tg['tier']}" if tg else ("🔥" if notify_eligible(m, notify) else "•")
        lines.append(f"{flag} {m['score']}점 · {m['company']} — {m['title']}")
    if len(hits) > 8:
        lines.append(f"…외 {len(hits) - 8}건")
    lines.append("📄 전체 상세·전략·공고링크는 첨부 노트 ↓")
    caption = "\n".join(lines)[:1024]

    # 노트 md 파일을 문서로 첨부. 파일 못 읽으면 캡션만 텍스트로 폴백.
    doc = None
    if note_path and os.path.isfile(note_path):
        try:
            with open(note_path, "rb") as f:
                doc = (os.path.basename(note_path), f.read(), "text/markdown")
        except Exception:  # noqa: BLE001
            doc = None
    try:
        if doc:
            res = http_post_multipart(
                f"https://api.telegram.org/bot{token}/sendDocument",
                {"chat_id": str(chat_id), "caption": caption},
                {"document": doc},
            )
        else:
            res = http_post_json(
                f"https://api.telegram.org/bot{token}/sendMessage",
                {"chat_id": chat_id, "text": caption, "disable_web_page_preview": "true"},
            )
        if res.get("ok"):
            log(f"  ✓ 텔레그램 알림 전송 ({len(hits)}건 ≥{threshold}점"
                + (", 노트 첨부)" if doc else ")"))
        else:
            log(f"  ! 텔레그램 실패: {str(res)[:150]}")
    except Exception as e:  # noqa: BLE001
        log(f"  ! 텔레그램 전송 예외: {str(e)[:150]}")


def telegram_test(cfg):
    """--test-telegram: 봇 토큰(env)+chat_id 설정이 맞는지 테스트 메시지 1건 전송."""
    tg = (cfg.get("notify") or {}).get("telegram") or {}
    token = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"))
    chat_id = tg.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        log("텔레그램 테스트 실패: 봇 토큰 환경변수 없음. "
            "setx 후 '새 터미널'에서 다시 실행하세요.")
        return
    if not chat_id:
        log("텔레그램 테스트 실패: config.notify.telegram.chat_id 가 비어있음.")
        return
    try:
        res = http_post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat_id,
             "text": "✅ job-hunting-radar 텔레그램 연결 OK. 이제 ≥50점 신규 매칭이 여기로 옵니다."},
        )
        if res.get("ok"):
            log(f"텔레그램 테스트 전송 성공 → chat_id {chat_id}. 앱에서 메시지 확인하세요.")
        else:
            log(f"텔레그램 테스트 실패: {str(res)[:200]}")
    except Exception as e:  # noqa: BLE001
        log(f"텔레그램 테스트 예외: {str(e)[:200]}")
