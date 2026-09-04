# -*- coding: utf-8 -*-
"""로깅·HTTP 유틸. 표준 라이브러리만 사용."""

import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from radar.settings import UA

# ---- Windows 콘솔에서 한글 깨짐 방지 ----
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def http_get_json(url, retries=3, pause=1.5):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(pause * (i + 1))
    raise RuntimeError(f"GET 실패: {url} :: {last}")


def http_post_json(url, data, timeout=15):
    """폼 인코딩 POST → JSON 응답. 텔레그램 sendMessage 등에 사용."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post_multipart(url, fields, files, timeout=60):
    """multipart/form-data POST(파일 업로드) → JSON. 텔레그램 sendDocument용.

    fields: {name: str}, files: {name: (filename, bytes, mime)}.
    """
    boundary = "----zw" + os.urandom(8).hex()
    crlf = "\r\n"
    body = b""
    for name, val in fields.items():
        body += (f"--{boundary}{crlf}"
                 f'Content-Disposition: form-data; name="{name}"{crlf}{crlf}'
                 f"{val}{crlf}").encode("utf-8")
    for name, (fname, content, mime) in files.items():
        body += (f"--{boundary}{crlf}"
                 f'Content-Disposition: form-data; name="{name}"; filename="{fname}"{crlf}'
                 f"Content-Type: {mime}{crlf}{crlf}").encode("utf-8")
        body += content + crlf.encode("utf-8")
    body += f"--{boundary}--{crlf}".encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": UA,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))
