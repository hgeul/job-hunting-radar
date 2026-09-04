# -*- coding: utf-8 -*-
"""알림 채널. 봇 토큰 같은 시크릿은 코드/config가 아니라 env에서만 읽는다."""

from radar.notify.telegram import send_telegram, telegram_test  # noqa: F401
