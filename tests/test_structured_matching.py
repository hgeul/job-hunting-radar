# -*- coding: utf-8 -*-
"""구조화 LLM 매칭 정규화 회귀(Phase 5).

여기서 지키는 것은 두 가지다. UNKNOWN 이 NONE 으로 새지 않을 것, 근거 없는 충족
주장이 FULL 로 남지 않을 것. 나머지는 "LLM 이 무슨 쓰레기를 뱉어도 예외 없이
계산 가능한 값으로 좁혀지는가"를 본다.
"""

import json
import os
import unittest

from tests.helpers import make_item

from radar import matching
from radar import llm as llm_mod
from radar.llm import build_prompt, extract_json
from radar.models import build_match


def req(**kw):
    """합성 요구사항 1건. 실제 이력·스택과 겹치지 않는 가상값만 쓴다(공개 리포)."""
    base = {"category": "language", "importance": "REQUIRED",
            "requirement": "Erlang/OTP 분산시스템 9년 이상",
            "candidate_evidence": "Erlang 텔레콤 스위치 11년", "match": "FULL",
            "confidence": "HIGH"}
    base.update(kw)
    return base


class NormalizeRequirementTest(unittest.TestCase):

    def test_keeps_valid_row(self):
        r = matching.normalize_requirement(req())
        self.assertEqual(r["match"], "FULL")
        self.assertEqual(r["importance"], "REQUIRED")
        self.assertFalse(r["demoted"])

    def test_full_without_evidence_becomes_unknown_not_none(self):
        # 완료조건: evidence 없는 requirement 를 임의로 FULL 처리하지 않는다.
        r = matching.normalize_requirement(req(candidate_evidence=""))
        self.assertEqual(r["match"], "UNKNOWN")
        self.assertTrue(r["demoted"])

    def test_partial_without_evidence_also_demoted(self):
        r = matching.normalize_requirement(req(match="PARTIAL", candidate_evidence="  "))
        self.assertEqual(r["match"], "UNKNOWN")

    def test_placeholder_evidence_counts_as_empty(self):
        for filler in ("없음", "해당 없음", "N/A", "-", "미상", "null"):
            r = matching.normalize_requirement(req(candidate_evidence=filler))
            self.assertEqual(r["match"], "UNKNOWN", filler)
            self.assertEqual(r["candidate_evidence"], "", filler)

    def test_none_without_evidence_stays_none(self):
        # "확인했는데 없다"는 후보 근거가 없는 게 정상이다. 내리지 않는다.
        r = matching.normalize_requirement(req(match="NONE", candidate_evidence=""))
        self.assertEqual(r["match"], "NONE")
        self.assertFalse(r["demoted"])

    def test_unknown_is_never_converted_to_none(self):
        for ev in ("", "근거 문장"):
            r = matching.normalize_requirement(req(match="UNKNOWN", candidate_evidence=ev))
            self.assertEqual(r["match"], "UNKNOWN")

    def test_unrecognized_match_falls_back_to_unknown(self):
        for bad in ("MAYBE", "", None, 3, ["FULL"]):
            r = matching.normalize_requirement(req(match=bad))
            self.assertEqual(r["match"], "UNKNOWN", repr(bad))

    def test_korean_importance_aliases(self):
        self.assertEqual(matching.normalize_requirement(
            req(importance="필수"))["importance"], "REQUIRED")
        self.assertEqual(matching.normalize_requirement(
            req(importance="우대사항"))["importance"], "PREFERRED")
        self.assertEqual(matching.normalize_requirement(
            req(importance="주절주절"))["importance"], "CONTEXT")

    def test_unrecognized_confidence_falls_back_to_low(self):
        self.assertEqual(matching.normalize_requirement(
            req(confidence="아주높음"))["confidence"], "LOW")

    def test_demotion_also_drops_confidence(self):
        # "UNKNOWN 인데 confidence HIGH" 를 남기면 Phase 6 산식이 확신도를
        # 가중치로 쓸 때 근거 없는 주장이 되살아난다.
        r = matching.normalize_requirement(req(candidate_evidence="", confidence="HIGH"))
        self.assertEqual(r["match"], "UNKNOWN")
        self.assertEqual(r["confidence"], "LOW")

    def test_rows_without_requirement_text_dropped(self):
        self.assertIsNone(matching.normalize_requirement(req(requirement="")))
        self.assertIsNone(matching.normalize_requirement({"match": "FULL"}))
        self.assertIsNone(matching.normalize_requirement("문자열"))
        self.assertIsNone(matching.normalize_requirement(None))


class NormalizeBlockerTest(unittest.TestCase):

    def test_dict_and_string_forms(self):
        a = matching.normalize_blocker({"kind": "license", "detail": "정보처리기사 필수",
                                        "evidence": "자격요건에 명시"})
        self.assertEqual(a["kind"], "license")
        b = matching.normalize_blocker("신입 전용 공고")
        self.assertEqual(b["detail"], "신입 전용 공고")
        self.assertEqual(b["kind"], "기타")

    def test_empty_detail_dropped(self):
        self.assertIsNone(matching.normalize_blocker({"kind": "career"}))
        self.assertIsNone(matching.normalize_blocker(""))
        self.assertIsNone(matching.normalize_blocker(42))

    def test_placeholder_evidence_blanked_but_blocker_kept(self):
        # 근거 판정은 requirement 와 같은 기준. 다만 blocker 자체는 버리지 않는다
        # (채택 여부는 코드 정책이 정한다).
        b = matching.normalize_blocker({"detail": "신입 전용 공고", "evidence": "없음"})
        self.assertEqual(b["evidence"], "")
        self.assertEqual(b["detail"], "신입 전용 공고")


class NormalizeStructuredTest(unittest.TestCase):

    def test_never_raises_on_garbage(self):
        for bad in (None, "문자열", 3, [], {"requirements": "배열아님"},
                    {"requirements": [None, 1, "x"]},
                    {"hard_blockers": {"detail": "dict가 배열자리에"}}):
            st = matching.normalize_structured(bad)
            self.assertIsInstance(st["requirements"], list)
            self.assertIsInstance(st["hard_blockers"], list)

    def test_structured_flag_needs_at_least_one_requirement(self):
        self.assertFalse(matching.normalize_structured({"requirements": []})["structured"])
        self.assertTrue(matching.normalize_structured(
            {"requirements": [req()]})["structured"])

    def test_legacy_fields_still_read(self):
        # 구식 응답(reasons/gaps/one_liner)도 표시 필드로 흘러야 한다.
        st = matching.normalize_structured(
            {"reasons": ["r1"], "gaps": ["g1"], "one_liner": "총평"})
        self.assertEqual(st["strengths"], ["r1"])
        self.assertEqual(st["risks"], ["g1"])
        self.assertEqual(st["summary"], "총평")
        self.assertFalse(st["structured"])

    def test_structured_fields_win_over_legacy(self):
        st = matching.normalize_structured(
            {"strengths": ["s1"], "reasons": ["r1"], "summary": "새", "one_liner": "옛"})
        self.assertEqual(st["strengths"], ["s1"])
        self.assertEqual(st["summary"], "새")

    def test_bullets_dedupe_and_cap(self):
        st = matching.normalize_structured({"risks": ["a", "a", "b", "c", "d", "e", "f"]})
        self.assertEqual(st["risks"][:3], ["a", "b", "c"])
        self.assertLessEqual(len(st["risks"]), matching.MAX_BULLETS)

    def test_requirement_cap_marks_truncated(self):
        many = [req(requirement=f"요구 {n}") for n in range(matching.MAX_REQUIREMENTS + 3)]
        st = matching.normalize_structured({"requirements": many})
        self.assertEqual(len(st["requirements"]), matching.MAX_REQUIREMENTS)
        self.assertTrue(st["truncated"])

    def test_duplicate_blockers_collapsed(self):
        st = matching.normalize_structured(
            {"hard_blockers": ["신입 전용", "신입 전용", "근무지 해외"]})
        self.assertEqual(len(st["hard_blockers"]), 2)

    def test_duplicate_blockers_collapse_despite_evidence_wording(self):
        # LLM 은 같은 결격을 근거 문구만 바꿔 두 번 낸다. detail 기준으로 합친다.
        st = matching.normalize_structured({"hard_blockers": [
            {"detail": "신입 전용 공고", "evidence": "공고 제목에 신입"},
            {"detail": "신입 전용 공고", "evidence": "자격요건 첫 줄"},
        ]})
        self.assertEqual(len(st["hard_blockers"]), 1)

    def test_demoted_count_exposed(self):
        st = matching.normalize_structured({"requirements": [
            req(), req(requirement="근거없음1", candidate_evidence=""),
            req(requirement="근거없음2", candidate_evidence="N/A"),
        ]})
        self.assertEqual(st["demoted"], 2)

    def test_empty_block_is_not_shared_state(self):
        # 얕은 복사로 모듈 전역 리스트를 돌려주면 호출부의 append 가 전역을 오염시킨다.
        a = matching.empty()
        a["requirements"].append("오염")
        self.assertEqual(matching.empty()["requirements"], [])
        self.assertEqual(matching.normalize_structured(None)["requirements"], [])

    def test_score_and_verdict_are_not_read(self):
        # Phase 6 에서 프롬프트에서 뺐다. 옛 응답에 남아 있어도 무시한다
        # (점수·판정은 radar/fit.py 가 만든다).
        st = matching.normalize_structured({"score": 88, "verdict": "강력추천"})
        self.assertNotIn("score", st)
        self.assertNotIn("verdict", st)


class BuildMatchStructuredTest(unittest.TestCase):
    """match dict 계약. 노트·대시보드·텔레그램이 이걸 공유한다."""

    def _match(self, llm_out):
        return build_match(make_item(), 55.0, None, 1, llm_out, "platform")

    def test_structured_output_fills_display_fields(self):
        m = self._match({
            "score": 80, "verdict": "추천", "requirements": [req()],
            "hard_blockers": [], "strengths": ["Java 실무"], "risks": ["AWS 불명"],
            "summary": "필수 충족", "strategy": ["s1"],
        })
        self.assertTrue(m["structured"])
        self.assertEqual(m["reasons"], ["Java 실무"])
        self.assertEqual(m["gaps"], ["AWS 불명"])
        self.assertEqual(m["one_liner"], "필수 충족")
        self.assertEqual(len(m["requirements"]), 1)
        self.assertIsNone(m["hard_blockers"])

    def test_legacy_llm_output_falls_back_to_rule_score(self):
        # 구식 응답에는 requirements 가 없다 → 적합도를 낼 수 없으니 규칙 점수.
        m = self._match({"score": 70, "verdict": "보통", "reasons": ["r"],
                         "gaps": ["g"], "one_liner": "총평"})
        self.assertFalse(m["structured"])
        self.assertEqual(m["reasons"], ["r"])  # 표시 필드는 계속 흐른다
        self.assertIsNone(m["requirements"])
        self.assertIsNone(m["fit_score"])
        self.assertIsNone(m["recommendation"])
        self.assertEqual(m["score"], 55.0)  # LLM 이 준 70 을 쓰지 않는다

    def test_no_llm_keeps_rule_score(self):
        m = self._match(None)
        self.assertIsNone(m["fit_score"])
        self.assertEqual(m["score"], 55.0)
        self.assertFalse(m["structured"])
        self.assertIsNone(m["requirements"])
        self.assertIsNone(m["recommendation"])

    def test_broken_llm_shape_does_not_break_match(self):
        m = self._match({"score": 60, "requirements": "배열이 아님",
                         "hard_blockers": None, "summary": None})
        self.assertFalse(m["structured"])
        self.assertEqual(m["score"], 55.0)
        self.assertIsNone(m["fit_score"])

    def test_requirements_drive_the_score_not_the_llm(self):
        # 같은 requirements 에 LLM 이 어떤 score 를 붙여도 결과가 같아야 한다.
        a = self._match({"requirements": [req()], "summary": "ok"})
        b = self._match({"requirements": [req()], "summary": "ok", "score": 3})
        self.assertEqual(a["score"], b["score"])
        self.assertTrue(a["structured"])
        self.assertIsNotNone(a["fit_score"])
        self.assertIsNotNone(a["recommendation"])

    def test_blockers_and_demoted_counts_ride_along(self):
        m = self._match({"requirements": [
            req(), req(requirement="근거없음", candidate_evidence="")],
            "hard_blockers": [{"kind": "license", "detail": "필수 자격증 미보유",
                               "evidence": "자격요건에 명시"}]})
        self.assertEqual(m["demoted"], 1)
        self.assertEqual(len(m["hard_blockers"]), 1)
        self.assertEqual(m["recommendation"], "SKIP")  # 결격은 점수보다 우선


class ExtractJsonTest(unittest.TestCase):
    """응답 텍스트에서 본체 객체 꺼내기. 실제로 겪은 실패 모드를 고정한다."""

    def test_plain_object(self):
        self.assertEqual(extract_json('{"score": 80}'), {"score": 80})

    def test_prose_around_object(self):
        self.assertEqual(extract_json('설명 {"score": 80} 끝'), {"score": 80})

    def test_code_fence(self):
        self.assertEqual(
            extract_json('```json\n{"score": 80}\n```'), {"score": 80})

    def test_two_objects_picks_the_body(self):
        # 탐욕 정규식은 여기서 "Extra data"로 통째 실패했다(실측 실패 모드).
        out = extract_json('{"note": "먼저"}\n{"requirements": [], "score": 80}')
        self.assertEqual(out["score"], 80)

    def test_requirements_wins_over_key_count(self):
        out = extract_json('{"a":1,"b":2,"c":3,"d":4} {"requirements":[],"score":9}')
        self.assertIn("requirements", out)

    def test_no_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            extract_json("JSON 이 없는 응답")

    def test_nested_braces_survive(self):
        out = extract_json('{"requirements": [{"requirement": "x"}], "score": 70}')
        self.assertEqual(len(out["requirements"]), 1)


class CliTokenCapTest(unittest.TestCase):
    """CLI 경로에 출력 토큰 상한을 걸지 않는다는 계약.

    실측(2026-09-05): 상한에 걸리면 CLI 가 오류 대신 다음 턴으로 이어 쓰고
    result 에 뒷조각만 남긴다(rc=0, num_turns=2). 성공처럼 보이는데 JSON 앞부분이
    없어 파싱이 실패한다. 그래서 값을 넣지 않고, 부모 환경에 있으면 지운다.
    """

    def _run(self, parent_env, envelope, returncode=0):
        seen = {}

        class Proc:
            def __init__(self):
                self.returncode = returncode
                self.stdout = json.dumps(envelope)
                self.stderr = ""

        def fake_run(cmd, **kw):
            seen["env"] = kw.get("env")
            return Proc()

        import subprocess
        real_run, real_environ = subprocess.run, os.environ
        try:
            subprocess.run = fake_run
            os.environ = dict(parent_env)
            out = llm_mod._score_cli({"exe": "claude", "model": "sonnet"}, "프롬프트")
        finally:
            subprocess.run, os.environ = real_run, real_environ
        return out, seen["env"]

    def test_cap_env_is_stripped_from_child(self):
        env_in = {"PATH": "x", llm_mod.CLI_MAX_TOKENS_ENV: "1500"}
        out, env_out = self._run(
            env_in, {"subtype": "success", "result": '{"score": 80}', "num_turns": 1})
        self.assertNotIn(llm_mod.CLI_MAX_TOKENS_ENV, env_out)
        self.assertEqual(env_out["PATH"], "x")  # 나머지 환경은 물려준다
        self.assertEqual(out["score"], 80)

    def test_split_response_names_the_cause(self):
        # 뒷조각만 온 응답. 원인을 모른 채 "JSON 못 찾음"으로 끝나면 안 된다.
        with self.assertRaises(RuntimeError) as ctx:
            self._run({}, {"subtype": "success", "num_turns": 2,
                           "result": '족"], "summary": "뒷조각"'})
        self.assertIn("2턴", str(ctx.exception))
        self.assertIn("상한", str(ctx.exception))

    def test_single_turn_parse_failure_keeps_value_error(self):
        with self.assertRaises(ValueError):
            self._run({}, {"subtype": "success", "num_turns": 1, "result": "JSON 없음"})


class PromptContractTest(unittest.TestCase):
    """프롬프트가 깨지면 아래 전부가 조용히 무너진다. 지시문 존재만 고정한다."""

    def setUp(self):
        self.p = build_prompt("이력", make_item(), "JD 본문")

    def test_prompt_limits_match_code_constants(self):
        # 프롬프트가 "최대 10개"라 지시하는데 코드가 12에서 자르면 truncated 가
        # 무슨 뜻인지 알 수 없어진다. 한 상수에서 나와야 한다.
        self.assertIn(f"최대 {matching.MAX_REQUIREMENTS}개", self.p)
        self.assertIn(f"최대{matching.MAX_BULLETS}", self.p)
        self.assertIn(f"최대 {matching.MAX_BLOCKERS}개", self.p)

    def test_structured_schema_keys_present(self):
        for key in ("requirements", "candidate_evidence", "hard_blockers",
                    "strengths", "risks", "summary"):
            self.assertIn(key, self.p, key)

    def test_match_enum_present(self):
        for v in matching.MATCHES:
            self.assertIn(v, self.p, v)
        for v in matching.IMPORTANCES:
            self.assertIn(v, self.p, v)

    def test_preserves_required_vs_preferred_weighting(self):
        # .claude/rules/project-conventions.md 가 깨뜨리지 말라고 못박은 지시.
        self.assertIn("필수와 우대를 구분해 가중치를 다르게", self.p)
        self.assertIn("우대 미충족은 경미하게", self.p)

    def test_preserves_deadline_extraction(self):
        self.assertIn("deadline", self.p)
        self.assertIn("상시", self.p)
        self.assertIn("미상", self.p)

    def test_preserves_strategy_instruction(self):
        self.assertIn("strategy", self.p)

    def test_career_gap_is_not_a_hard_blocker(self):
        self.assertIn("경력 연수가 1~2년 모자란 것은 blocker 가 아니다", self.p)


if __name__ == "__main__":
    unittest.main()
