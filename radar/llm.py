# -*- coding: utf-8 -*-
"""LLM 정밀 점수(선택). 실패하면 규칙 점수로 폴백한다."""

import json
import os
import re
import tempfile

from radar.matching import MAX_BLOCKERS, MAX_BULLETS, MAX_REQUIREMENTS

LLM_SYS = (
    "너는 시니어 백엔드 채용 매칭 어시스턴트다. 지원자 이력과 채용 공고를 대조해 "
    "공고의 요구사항을 하나씩 뽑고, 각 요구사항에 대해 지원자 이력에서 근거를 찾아 "
    "충족 여부를 판정한다. 과장 없이, 공고와 이력에 실제로 적힌 것만 근거로 삼는다. "
    "이력에 없는 내용을 지어내지 않는다. 반드시 JSON만 출력한다."
)

# claude CLI를 볼트 밖 중립 폴더에서 실행 → 볼트 CLAUDE.md/스킬 미로드로 오버헤드 감소
CLI_CWD = os.path.join(tempfile.gettempdir(), "job_watcher_cli")

# 구조화 응답(requirements 배열)은 예전 한 줄 총평보다 길다. 옛 config 값(800 등)을
# 그대로 쓰면 응답이 상한에 걸려 버려지므로 api 경로에서 바닥값을 강제한다.
# 실측: 요구사항 9~11건짜리 한국어 응답이 2000 토큰을 넘겼다.
# 상한은 초과분을 막는 장치일 뿐 미리 과금되지 않으므로 넉넉히 잡는다.
STRUCTURED_MIN_TOKENS = 6000

# CLI 는 출력 토큰 상한을 인자가 아니라 이 환경변수로 받는다. 그런데 **여기에 상한을
# 걸면 안 된다.** 실측(2026-09-05): 상한에 걸리면 오류를 내는 게 아니라 다음 턴으로
# 이어 쓰고(num_turns=2) `result` 에는 이어쓴 뒷조각만 남는다. rc=0 이라 성공처럼
# 보이는데 JSON 앞부분이 없어 파싱이 실패한다. 상한 없이는 한 턴에 다 나온다.
#   상한 1500 → num_turns=2, result 616자(JSON 중간부터 시작), 파싱 실패
#   상한 없음 → num_turns=1, result 3749자, 정상
# 그래서 값을 넣는 대신 **부모 환경에 설정돼 있으면 지운다**(쉘 설정이 새어들면
# 같은 증상이 재현되므로).
CLI_MAX_TOKENS_ENV = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"


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
        '{"requirements": [{'
        '"category": "<career|language|framework|database|infrastructure|domain|education|location|기타>", '
        '"importance": "<REQUIRED|PREFERRED|CONTEXT>", '
        '"requirement": "<공고가 요구하는 것 한 줄>", '
        '"candidate_evidence": "<지원자 이력에서 찾은 근거. 못 찾으면 빈 문자열 \\"\\">", '
        '"match": "<FULL|PARTIAL|NONE|UNKNOWN>", '
        '"confidence": "<HIGH|MEDIUM|LOW>"}], '
        '"hard_blockers": [{"kind": "<career|license|language|location|security|기타>", '
        '"detail": "<지원 자체가 불가능한 결격 사유>", "evidence": "<공고 원문 근거>"}], '
        f'"strengths": ["이 공고 기준 강점 최대{MAX_BULLETS}"], '
        f'"risks": ["부족/리스크 최대{MAX_BULLETS}"], '
        '"summary": "<한 줄 총평>", '
        '"deadline": "<지원 마감일: 구체 날짜면 YYYY-MM-DD, 상시채용/채용시 마감이면 \'상시\', 본문에 없으면 \'미상\'>", '
        '"strategy": ["지원·합격 전략 2~4개(구체적으로): 서류에서 강조할 이력·키워드, '
        '이력서/자기소개서 각색 포인트, 면접 대비 포인트, risks 보완·프레이밍 방법"]}'
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
        f"**requirements 가 이 응답의 본체다**: 공고에 실제로 적힌 요구사항만 "
        f"최대 {MAX_REQUIREMENTS}개까지 한 줄씩 뽑아라(REQUIRED 를 먼저, 그다음 PREFERRED). "
        f"공고에 없는 요구사항을 만들어내지 마라. "
        f"각 요구사항마다 지원자 이력에서 근거 문장을 찾아 candidate_evidence 에 넣어라.\n"
        f"**match 판정 규칙(엄격히)**: "
        f"FULL = 이력에 명확한 근거가 있고 요구를 충족한다. "
        f"PARTIAL = 근거는 있으나 범위·연차·깊이가 요구에 못 미친다. "
        f"NONE = 이력을 확인했고 해당 경험이 없다고 판단된다. "
        f"UNKNOWN = 이력에 언급이 없어 판단할 수 없다. "
        f"**근거를 못 찾았으면 FULL 이나 PARTIAL 을 쓰지 마라.** "
        f"candidate_evidence 가 비면 match 는 NONE 또는 UNKNOWN 이어야 한다. "
        f"'정보가 없다(UNKNOWN)'와 '확인했는데 없다(NONE)'를 섞지 마라.\n"
        f"**hard_blockers 는 지원 자체가 불가능한 절대 결격만 최대 {MAX_BLOCKERS}개 넣어라**: "
        f"필수 자격증·면허 없음, 요구 언어 완전 불일치, 근무지 절대 불가, 신입 전용 공고, "
        f"특정 보안 신원 요건 같은 것. "
        f"경력 연수가 1~2년 모자란 것은 blocker 가 아니다. 그런 건 category=\"career\" 인 "
        f"requirement 로 표현하고 hard_blockers 는 빈 배열로 둬라. "
        f"애매하면 넣지 마라(최종 판정은 프로그램이 한다).\n"
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
    """응답 텍스트에서 결과 JSON 객체 하나를 꺼낸다.

    `{.*}` 한 방으로 자르면 응답에 객체가 두 덩이 들어올 때 통째로 실패한다
    ("Extra data"). 실제로 그렇게 오는 경우가 있어서, `{` 마다 파싱을 시도하고
    성공한 것 중 **키가 가장 많은 객체**를 고른다(설명용 작은 객체가 앞에 오는
    경우가 많아 그쪽을 집으면 안 된다).
    """
    dec = json.JSONDecoder()
    best = None
    for i, ch in enumerate(txt):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(txt, i)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        # requirements 가 있으면 그게 본체다. 없으면 키 수로 고른다.
        rank = (1 if "requirements" in obj else 0, len(obj))
        if best is None or rank > best[0]:
            best = (rank, obj)
    if best is None:
        raise ValueError(f"LLM 응답에서 JSON 못 찾음: {txt[:200]}")
    return best[1]


def _score_api(engine, max_tokens, prompt):
    resp = engine["client"].messages.create(
        model=engine["model"], max_tokens=max_tokens,
        system=LLM_SYS,
        messages=[{"role": "user", "content": prompt}],
    )
    txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return extract_json(txt)


def _score_cli(engine, prompt):
    import subprocess  # noqa: WPS433
    os.makedirs(CLI_CWD, exist_ok=True)
    full = LLM_SYS + "\n\n" + prompt
    # 출력 토큰 상한을 걸지 않는다(위 CLI_MAX_TOKENS_ENV 주석 참고).
    # 부모 환경에 값이 있으면 지운다.
    env = dict(os.environ)
    env.pop(CLI_MAX_TOKENS_ENV, None)
    proc = subprocess.run(
        [engine["exe"], "-p", "--output-format", "json", "--model", engine["model"]],
        input=full, capture_output=True, text=True, encoding="utf-8",
        cwd=CLI_CWD, timeout=180, env=env,
    )
    if proc.returncode != 0:
        # 토큰 상한 초과 같은 오류는 stderr 가 비고 stdout 에만 남는다. 둘 다 실어야
        # 다음 실패를 로그만 보고 진단할 수 있다.
        why = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        raise RuntimeError(f"claude CLI 실패(rc={proc.returncode}): {why[:300]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error") or envelope.get("subtype") != "success":
        raise RuntimeError(f"CLI 오류 응답: {str(envelope)[:200]}")
    try:
        return extract_json(envelope.get("result") or "")
    except ValueError as e:
        turns = envelope.get("num_turns")
        if turns and turns > 1:
            # 응답이 여러 턴으로 쪼개졌다 = 출력 상한에 걸렸다는 뜻. 원인을 못 박아둔다.
            raise RuntimeError(
                f"응답이 {turns}턴으로 쪼개져 뒷부분만 돌아왔습니다"
                f"(출력 토큰 상한 의심). {e}") from e
        raise


def llm_score(engine, max_tokens, profile_text, item, detail_text):
    prompt = build_prompt(profile_text, item, detail_text)
    if engine["provider"] == "api":
        # config 값이 구조화 응답을 담기엔 작으면 올려 쓴다(상한에 걸리면 통째로 버려진다).
        return _score_api(engine, max(int(max_tokens or 0), STRUCTURED_MIN_TOKENS), prompt)
    return _score_cli(engine, prompt)
