# -*- coding: utf-8 -*-
"""LLM 정밀 점수(선택). 실패하면 규칙 점수로 폴백한다."""

import json
import os
import re
import tempfile

LLM_SYS = (
    "너는 시니어 백엔드 채용 매칭 어시스턴트다. 지원자 이력과 채용 공고를 대조해 "
    "적합도를 0~100으로 냉정하게 평가한다. 과장 없이, 공고에 명시된 요구사항 기준으로만 판단한다. "
    "반드시 JSON만 출력한다."
)

# claude CLI를 볼트 밖 중립 폴더에서 실행 → 볼트 CLAUDE.md/스킬 미로드로 오버헤드 감소
CLI_CWD = os.path.join(tempfile.gettempdir(), "job_watcher_cli")


def resolve_engine(cfg, no_llm):
    """어떤 LLM 백엔드를 쓸지 결정. 반환: (engine dict | None, 사유 str | None).

    engine = {"provider": "api"|"claude_cli", ...}
    - claude_cli: 지금 쓰는 Claude Code 구독으로 채점(별도 키 불필요).
    - api: anthropic SDK + ANTHROPIC_API_KEY.
    """
    if no_llm:
        return None, "--no-llm 플래그"
    llm = cfg["llm"]
    if not llm.get("enabled"):
        return None, "config에서 llm.enabled=false"
    provider = llm.get("provider", "claude_cli")
    if provider == "claude_cli":
        import shutil  # noqa: WPS433
        exe = shutil.which("claude") or shutil.which("claude.cmd")
        if not exe:
            return None, "claude CLI를 PATH에서 못 찾음"
        return {"provider": "claude_cli", "exe": exe,
                "model": llm.get("cli_model", "sonnet")}, None
    if provider == "api":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None, "ANTHROPIC_API_KEY 환경변수 없음"
        try:
            import anthropic  # noqa: WPS433
        except ImportError:
            return None, "anthropic 패키지 미설치 (pip install anthropic)"
        return {"provider": "api", "client": anthropic.Anthropic(),
                "model": llm.get("model", "claude-sonnet-5")}, None
    return None, f"알 수 없는 provider: {provider}"


def build_prompt(profile_text, item, detail_text):
    schema_hint = (
        '{"score": <0-100 정수>, "verdict": "<강력추천|추천|보통|낮음>", '
        '"reasons": ["부합 근거 최대3"], "gaps": ["부족/리스크 최대3"], '
        '"one_liner": "<한 줄 총평>", '
        '"deadline": "<지원 마감일: 구체 날짜면 YYYY-MM-DD, 상시채용/채용시 마감이면 \'상시\', 본문에 없으면 \'미상\'>", '
        '"strategy": ["지원·합격 전략 2~4개(구체적으로): 서류에서 강조할 이력·키워드, '
        '이력서/자기소개서 각색 포인트, 면접 대비 포인트, gaps 보완·프레이밍 방법"]}'
    )
    return (
        f"## 지원자 이력\n{profile_text}\n\n"
        f"## 채용 공고\n"
        f"회사: {item.get('company',{}).get('name')}\n"
        f"제목: {item.get('title')}\n"
        f"직무: {', '.join(item.get('depthTwos') or [])}\n"
        f"경력: {item.get('careerMin')}~{item.get('careerMax')}년 / 지역: {', '.join(item.get('regions') or [])}\n\n"
        f"### 공고 상세\n"
        f"(원문 크롤 결과라 사이트 네비게이션·로그인·회원가입·푸터·다른 공고 목록 같은 "
        f"잡음이 섞였을 수 있다. 그런 부분은 무시하고 이 공고의 실제 채용 요구사항"
        f"(담당업무·자격요건·우대사항·기술스택)만 근거로 삼아 판단하라.)\n"
        f"{detail_text[:6000]}\n\n"
        f"위 지원자가 이 공고에 지원했을 때의 서류 통과 가능성과 직무 적합도를 평가하라.\n"
        f"**중요 — 필수와 우대를 구분해 가중치를 다르게 매겨라**: "
        f"공고의 요구사항을 '필수(자격요건·필수·지원자격·Requirements)'와 "
        f"'우대(우대사항·있으면 좋음·Preferred·Nice to have)'로 나눠라. "
        f"필수 미충족은 서류 통과에 실질적 감점이다. 그러나 **우대 미충족은 경미하게** 평가하라 — "
        f"대용량 트래픽 처리·특정 프레임워크·특정 인프라 같은 우대 역량은 "
        f"보통 '입사해서 하게 되는 일'이지 입사 전 필수 조건이 아닌 경우가 많다. "
        f"필수를 충족하면 우대가 여럿 비어도 지원 가치는 충분할 수 있다. "
        f"우대 역량 부족을 필수 결격처럼 과하게 깎지 마라.\n"
        f"strategy에는 이 지원자가 이 공고에 실제로 지원한다면 어떻게 어필하고 준비해야 합격 확률이 높아질지 "
        f"실행 가능한 조언을 담아라.\n"
        f"deadline은 공고 본문에서 지원(서류) 마감일을 찾아 넣어라. "
        f"'2026-07-31'처럼 구체 날짜가 있으면 그 날짜(YYYY-MM-DD)를, "
        f"'상시채용'·'채용 시 마감'·'수시'면 '상시'를, 아무 언급이 없으면 '미상'을 넣어라. 추측하지 마라.\n"
        f"다음 JSON 형식만 출력:\n{schema_hint}"
    )


def extract_json(txt):
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError(f"LLM 응답에서 JSON 못 찾음: {txt[:200]}")
    return json.loads(m.group(0))


def _score_api(engine, max_tokens, prompt):
    resp = engine["client"].messages.create(
        model=engine["model"], max_tokens=max_tokens,
        system=LLM_SYS,
        messages=[{"role": "user", "content": prompt}],
    )
    txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return extract_json(txt)


def _score_cli(engine, max_tokens, prompt):
    import subprocess  # noqa: WPS433
    os.makedirs(CLI_CWD, exist_ok=True)
    full = LLM_SYS + "\n\n" + prompt
    proc = subprocess.run(
        [engine["exe"], "-p", "--output-format", "json", "--model", engine["model"]],
        input=full, capture_output=True, text=True, encoding="utf-8",
        cwd=CLI_CWD, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 실패(rc={proc.returncode}): {(proc.stderr or '')[:200]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error") or envelope.get("subtype") != "success":
        raise RuntimeError(f"claude CLI 오류 응답: {str(envelope)[:200]}")
    return extract_json(envelope["result"])


def llm_score(engine, max_tokens, profile_text, item, detail_text):
    prompt = build_prompt(profile_text, item, detail_text)
    if engine["provider"] == "api":
        return _score_api(engine, max_tokens, prompt)
    return _score_cli(engine, max_tokens, prompt)
