# -*- coding: utf-8 -*-
"""LLM 구조화 매칭 결과의 정규화(Phase 5).

LLM 은 요구사항 추출·근거 매핑·리스크 설명까지만 한다. 최종 수치와 판정은
코드가 만든다(PLAN 10절). 이 모듈은 그 경계에 서서 **LLM 이 뱉은 자유 형식을
계산 가능한 값으로 좁히는** 일만 한다. 점수 계산은 여기서 하지 않는다(Phase 6).

원칙 두 가지가 여기 박혀 있다.

1. `UNKNOWN` 을 `NONE` 으로 바꾸지 않는다. "정보가 없다"와 "확인했는데 미달이다"는
   다른 사실이고, 섞으면 지원자가 억울하게 떨어진다(PLAN 11절).
2. 근거 없는 `FULL`/`PARTIAL` 을 믿지 않는다. 근거 문장이 비어 있으면 `UNKNOWN` 으로
   내린다(Phase 5 완료조건). LLM 이 이력에 없는 걸 있다고 말하는 쪽이,
   모른다고 말하는 쪽보다 위험하다.

정규화 함수는 어떤 입력에도 예외를 내지 않는다. LLM 출력은 신뢰할 수 없는 입력이고,
파싱 실패로 실행 전체가 멈추면 안 된다(Phase 5 완료조건).
"""

IMPORTANCES = ("REQUIRED", "PREFERRED", "CONTEXT")
MATCHES = ("FULL", "PARTIAL", "NONE", "UNKNOWN")
CONFIDENCES = ("HIGH", "MEDIUM", "LOW")

# 한 공고에서 받아들이는 최대 요구사항 수. 넘으면 앞에서부터 자른다(프롬프트가
# 필수를 먼저 쓰라고 지시하므로 잘리는 쪽은 대체로 CONTEXT다).
# 이 값들은 `radar/llm.py::build_prompt` 가 그대로 읽어 지시문에 박는다. 프롬프트가
# "최대 10개"라 하는데 코드가 12에서 자르면 `truncated` 가 무슨 뜻인지 알 수 없어진다.
MAX_REQUIREMENTS = 12
MAX_BLOCKERS = 6
MAX_BULLETS = 3

# 중요도·근거 유무 판정에 쓰는 동의어. LLM 이 한국어로 답할 때가 많아 같이 받는다.
_IMPORTANCE_ALIASES = {
    "REQUIRED": "REQUIRED", "REQUIRE": "REQUIRED", "MUST": "REQUIRED",
    "필수": "REQUIRED", "자격요건": "REQUIRED", "지원자격": "REQUIRED",
    "PREFERRED": "PREFERRED", "PREFER": "PREFERRED", "NICE_TO_HAVE": "PREFERRED",
    "NICE-TO-HAVE": "PREFERRED", "OPTIONAL": "PREFERRED",
    "우대": "PREFERRED", "우대사항": "PREFERRED",
    "CONTEXT": "CONTEXT", "참고": "CONTEXT", "기타": "CONTEXT",
}

# "근거를 못 찾았다"는 뜻으로 LLM 이 흔히 넣는 값들. 빈 문자열과 같이 취급한다.
# "none"·"unknown" 같은 단어가 진짜 근거 문장일 가능성은 사실상 없다(근거는 이력의
# 문장을 인용하게 되어 있다). 오탐보다 미탐이 위험한 쪽이라 넓게 잡는다.
_EMPTY_EVIDENCE = {
    "", "-", "--", "n/a", "na", "null", "none", "없음", "해당없음", "해당 없음",
    "미상", "불명", "확인불가", "확인 불가", "정보없음", "정보 없음", "unknown",
}


def _text(value):
    """무엇이 오든 다듬은 문자열로. dict/list 는 근거로 쓸 수 없으니 버린다."""
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return ""
    return str(value).strip()


def _enum(value, allowed, default, aliases=None):
    key = _text(value).upper().replace(" ", "_")
    if aliases and key in aliases:
        return aliases[key]
    return key if key in allowed else default


def has_evidence(value):
    """근거 문장이 실제로 채워졌는가. 자리채움 문구는 비었다고 본다."""
    s = _text(value)
    return bool(s) and s.lower() not in _EMPTY_EVIDENCE


def normalize_requirement(raw):
    """요구사항 1건 → 계산 가능한 dict. 쓸 수 없으면 None.

    반환 dict 의 `demoted` 는 "LLM 이 충족이라 했지만 근거가 없어 내린 것"을 뜻한다.
    Phase 6 의 점수 계산과 Phase 7 의 표시가 이 플래그로 신뢰도를 낮춘다.
    """
    if not isinstance(raw, dict):
        return None
    requirement = _text(raw.get("requirement") or raw.get("text"))
    if not requirement:
        return None  # 요구사항 문장 자체가 없으면 매칭 단위가 성립하지 않는다
    evidence = _text(raw.get("candidate_evidence") or raw.get("evidence"))
    if not has_evidence(evidence):
        evidence = ""
    match = _enum(raw.get("match"), MATCHES, "UNKNOWN")
    demoted = False
    if match in ("FULL", "PARTIAL") and not evidence:
        # 근거 없는 충족 주장은 받지 않는다. NONE 이 아니라 UNKNOWN 으로 내린다
        # (미달이라고 확인한 게 아니라 확인이 안 된 것이므로).
        match = "UNKNOWN"
        demoted = True
    confidence = _enum(raw.get("confidence"), CONFIDENCES, "LOW")
    if demoted:
        # 근거가 없어 내린 행은 확신도도 같이 내린다. "UNKNOWN 인데 HIGH" 를 남기면
        # Phase 6 산식이 확신도를 가중치로 쓸 때 근거 없는 주장이 되살아난다.
        confidence = "LOW"
    return {
        "category": _text(raw.get("category")) or "기타",
        "importance": _enum(raw.get("importance"), IMPORTANCES, "CONTEXT",
                            _IMPORTANCE_ALIASES),
        "requirement": requirement,
        "candidate_evidence": evidence,
        "match": match,
        "confidence": confidence,
        "demoted": demoted,
    }


def normalize_blocker(raw):
    """hard blocker **후보** 1건 → dict. 채택 여부는 코드가 정한다(PLAN 13절).

    여기서는 형태만 맞춘다. 경력 1년 차이 같은 걸 걸러내는 정책 판단은 Phase 6.
    """
    if isinstance(raw, str):
        raw = {"detail": raw}
    if not isinstance(raw, dict):
        return None
    detail = _text(raw.get("detail") or raw.get("reason") or raw.get("blocker"))
    if not detail:
        return None
    evidence = _text(raw.get("evidence"))
    return {
        "kind": _text(raw.get("kind") or raw.get("category")) or "기타",
        "detail": detail,
        # 근거 판정은 requirement 와 같은 기준을 쓴다(자리채움 문구 = 근거 없음).
        # blocker 를 버리지는 않는다. 채택 여부는 코드 정책의 몫이다(PLAN 13절).
        "evidence": evidence if has_evidence(evidence) else "",
    }


def _bullets(raw, limit=MAX_BULLETS):
    """문자열 리스트로 정규화. dict 가 섞여 오면 대표 필드를 꺼내 쓴다."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for v in raw:
        if isinstance(v, dict):
            v = v.get("text") or v.get("detail") or v.get("summary")
        s = _text(v)
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def empty():
    """빈 구조화 블록. 매번 새 리스트를 만든다(호출부가 append 해도 오염 없게)."""
    return {
        "requirements": [], "hard_blockers": [], "strengths": [], "risks": [],
        "summary": "", "structured": False, "truncated": False, "demoted": 0,
        "deadline": None, "strategy": [],
    }


def normalize_structured(llm_out):
    """LLM 응답 dict → 정규화된 매칭 블록. 어떤 입력에도 예외를 내지 않는다.

    schema_hint 의 **모든** 필드가 이 함수 한 곳을 지난다. 일부만 정규화하고
    나머지를 날것으로 꺼내 쓰면 "LLM 출력은 신뢰할 수 없는 입력"이라는 전제가
    반쪽이 된다(키 하나 빠진 응답이 실행을 죽이면 안 된다).

    `structured` 는 "요구사항 매칭을 실제로 받아왔는가"다. 구식 응답(reasons/gaps만
    있는 응답)이나 파싱 실패는 False 로 남고, 호출부가 기존 폴백 경로를 탄다.
    """
    out = empty()
    if not isinstance(llm_out, dict):
        return out

    raw_reqs = llm_out.get("requirements")
    if isinstance(raw_reqs, (list, tuple)):
        for r in raw_reqs:
            norm = normalize_requirement(r)
            if norm is not None:
                out["requirements"].append(norm)
    out["truncated"] = len(out["requirements"]) > MAX_REQUIREMENTS
    out["requirements"] = out["requirements"][:MAX_REQUIREMENTS]
    out["demoted"] = sum(1 for r in out["requirements"] if r["demoted"])

    raw_blockers = llm_out.get("hard_blockers")
    if isinstance(raw_blockers, (list, tuple, str)):
        seen = set()
        for b in ([raw_blockers] if isinstance(raw_blockers, str) else raw_blockers):
            norm = normalize_blocker(b)
            if norm is None:
                continue
            # 중복 판정은 `detail` 로만 한다. 같은 결격이라도 evidence 문구는
            # 실행마다 흔들리므로 dict 전체를 비교하면 사실상 중복이 안 걸린다.
            key = norm["detail"].lower()
            if key in seen:
                continue
            seen.add(key)
            out["hard_blockers"].append(norm)
    out["hard_blockers"] = out["hard_blockers"][:MAX_BLOCKERS]

    # 구식 필드(reasons/gaps/one_liner)도 받는다. 프롬프트를 바꿔도 캐시된
    # 응답이나 예전 state 가 섞여 들어올 수 있다.
    out["strengths"] = _bullets(llm_out.get("strengths") or llm_out.get("reasons"))
    out["risks"] = _bullets(llm_out.get("risks") or llm_out.get("gaps"))
    out["summary"] = _text(llm_out.get("summary") or llm_out.get("one_liner"))
    out["structured"] = bool(out["requirements"])

    # score·verdict 는 Phase 6 에서 프롬프트에서 뺐다. 점수와 판정은 `radar/fit.py`
    # 가 만든다. 옛 응답에 남아 있어도 읽지 않는다.
    out["deadline"] = _text(llm_out.get("deadline")) or None
    out["strategy"] = _bullets(llm_out.get("strategy"), limit=6)
    return out
