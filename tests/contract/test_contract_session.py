"""AC-18 — 스레드 세션: 재사용·이력 folding·system prompt/권한 변경 재생성, interrupt, end_session 멱등,
session_ids/has_session/close_all, 크래시 → 무효화 + 1회 재기동, max_turns 컷.
"""
import asyncio

import pytest

from core.llm_errors import GENERIC_MESSAGE, LLMError, is_llm_error_reply, runtime_unavailable_message
from tests.contract.harness import SYS, Recorder, eventually, gate_lines
from tests.contract.scenario import RAW_PROVIDER_MARKER, crash, end, gate_probe, text, tool, wait_interrupt

T = 7001
HISTORY = [{"role": "user", "content": "이전질문X"}, {"role": "assistant", "content": "이전답X"}]


def _crash_messages(harness):
    """크래시 턴의 오류 안내 — Claude는 §3.2 GENERIC, 프로세스 런타임은 RUNTIME_UNAVAILABLE (둘 중 하나로 고정)."""
    return {GENERIC_MESSAGE, runtime_unavailable_message(harness.backend_id)}


@pytest.mark.asyncio
async def test_new_thread_folds_history_live_reuse_does_not(harness):
    fake = harness.fake
    fake.script(text("r1"), end(), text("r2"))

    r1 = await harness.adapter.ask_with_context(SYS, "q1", {"k": "v"}, history=HISTORY, thread_id=T, approve_skill_writes=True)
    r2 = await harness.adapter.ask_with_context(
        SYS, "q2", {"k": "v"}, history=HISTORY + [{"role": "user", "content": "직전질문Y"}],
        thread_id=T, approve_skill_writes=True,
    )

    assert (r1, r2) == ("r1", "r2")
    assert "[대화 이력]" in fake.prompts[0] and "이전질문X" in fake.prompts[0]
    assert "[질문]\nq1" in fake.prompts[0]
    assert "[대화 이력]" not in fake.prompts[1] and "직전질문Y" not in fake.prompts[1]
    assert "[데이터 컨텍스트]" in fake.prompts[1] and "[질문]\nq2" in fake.prompts[1]
    assert fake.session_starts == 1 and fake.session_closes == 0
    assert harness.adapter.has_session(T) and harness.adapter.session_ids() == [T]


@pytest.mark.asyncio
async def test_system_prompt_change_recreates_session_and_refolds(harness):
    fake = harness.fake
    fake.script(text("a"), end(), text("b"))

    await harness.adapter.ask_with_context("SYS_A", "q1", {}, thread_id=T, approve_skill_writes=True)
    await harness.adapter.ask_with_context("SYS_B", "q2", {}, history=HISTORY, thread_id=T, approve_skill_writes=True)

    assert fake.session_starts == 2 and fake.session_closes == 1
    assert "SYS_A" in fake.system_prompts[0] and "SYS_B" in fake.system_prompts[1]
    assert "[대화 이력]" in fake.prompts[1] and "이전질문X" in fake.prompts[1]
    assert harness.adapter.session_ids() == [T]


@pytest.mark.asyncio
async def test_privilege_change_on_live_thread_recreates_session(harness, capsys):
    """권한 라우팅 키(approve_skill_writes is True)는 호출마다 적용 — 특권 세션을 무인 턴이 재사용하지 않는다."""
    fake = harness.fake
    fake.script(gate_probe("Bash", {"command": "echo 1"}), text("a"), end(), gate_probe("Bash", {"command": "echo 2"}), text("b"))
    capsys.readouterr()

    await harness.adapter.ask_with_context(SYS, "q1", {}, thread_id=T, approve_skill_writes=True)
    await harness.adapter.ask_with_context(SYS, "q2", {}, history=HISTORY, thread_id=T)

    decisions = [(l["cap"], l["decision"]) for l in gate_lines(capsys.readouterr().out, tool="Bash")]
    assert decisions == [("priv", "allow"), ("ro", "deny")]
    assert [e.executed for e in fake.executions] == [True, False]
    assert fake.session_starts == 2 and fake.session_closes == 1
    assert "[대화 이력]" in fake.prompts[1]


@pytest.mark.asyncio
async def test_one_shot_always_folds_and_keeps_no_session(harness):
    fake = harness.fake
    fake.script(text("a"), end(), text("b"))

    await harness.adapter.ask_with_context(SYS, "q1", {}, history=HISTORY)
    await harness.adapter.ask_with_context(SYS, "q2", {}, history=HISTORY)

    assert all("[대화 이력]" in p and "이전질문X" in p for p in fake.prompts)
    assert fake.session_starts == 2
    assert harness.adapter.session_ids() == [] and not harness.adapter.has_session(None)


@pytest.mark.asyncio
async def test_interrupt_drops_previous_turn_text_and_restarts_same_session(harness):
    fake = harness.fake
    fake.script(text("turn1-a"), wait_interrupt(), text("turn1-b"), end(), text("turn2"))
    first, second = Recorder(), Recorder()

    task = asyncio.create_task(harness.adapter.ask_with_context(
        SYS, "q1", {}, on_text=first.on_text, thread_id=T, approve_skill_writes=True,
    ))
    await eventually(lambda: first.texts == ["turn1-a"])
    await harness.adapter.interrupt_session(T)
    r1 = await task
    r2 = await harness.adapter.ask_with_context(
        SYS, "q2", {}, on_text=second.on_text, thread_id=T, approve_skill_writes=True,
    )

    assert fake.interrupts == 1
    assert first.texts == ["turn1-a"] and r1 == "turn1-a"
    assert second.texts == ["turn2"] and r2 == "turn2"
    assert fake.session_starts == 1 and fake.session_closes == 0


@pytest.mark.asyncio
async def test_interrupt_without_session_is_noop(harness):
    await harness.adapter.interrupt_session(999)
    assert harness.fake.interrupts == 0
    assert harness.adapter.session_ids() == []


@pytest.mark.asyncio
async def test_end_session_idempotent_and_session_listing(harness):
    fake = harness.fake
    fake.script(text("a"), end(), text("b"))
    await harness.adapter.ask_with_context(SYS, "q", {}, thread_id=1, approve_skill_writes=True)
    await harness.adapter.ask_with_context(SYS, "q", {}, thread_id=2, approve_skill_writes=True)
    assert sorted(harness.adapter.session_ids()) == [1, 2]

    await harness.adapter.end_session(1)
    await harness.adapter.end_session(1)
    await harness.adapter.end_session(12345)

    assert fake.session_closes == 1
    assert not harness.adapter.has_session(1) and harness.adapter.has_session(2)
    assert harness.adapter.session_ids() == [2]


@pytest.mark.asyncio
async def test_close_all_ends_every_session(harness):
    fake = harness.fake
    fake.script(text("a"), end(), text("b"))
    await harness.adapter.ask_with_context(SYS, "q", {}, thread_id=1, approve_skill_writes=True)
    await harness.adapter.ask_with_context(SYS, "q", {}, thread_id=2, approve_skill_writes=True)

    await harness.adapter.close_all()
    await harness.adapter.close_all()

    assert fake.session_closes == 2
    assert harness.adapter.session_ids() == []


@pytest.mark.asyncio
async def test_crash_invalidates_session_and_restarts_once(harness):
    fake = harness.fake
    fake.script(text("부분"), crash(), end(), text("재기동 응답"))
    first, second = Recorder(), Recorder()

    r1 = await harness.adapter.ask_with_context(SYS, "q1", {}, on_text=first.on_text, thread_id=T, approve_skill_writes=True)

    assert r1 in _crash_messages(harness)
    assert first.texts == ["부분", r1]
    assert RAW_PROVIDER_MARKER not in r1
    assert not harness.adapter.has_session(T)

    r2 = await harness.adapter.ask_with_context(
        SYS, "q2", {}, history=HISTORY, on_text=second.on_text, thread_id=T, approve_skill_writes=True,
    )

    assert second.texts == ["재기동 응답"] and r2 == "재기동 응답"
    assert fake.session_starts == 2
    assert "[대화 이력]" in fake.prompts[-1]
    assert harness.adapter.has_session(T)


@pytest.mark.asyncio
async def test_restart_failure_reports_error_once_without_retry_loop(harness):
    fake = harness.fake
    fake.script(text("x"), crash(), end(), text("복구"))
    await harness.adapter.ask_with_context(SYS, "q1", {}, thread_id=T, approve_skill_writes=True)
    starts_after_crash = fake.session_starts
    fake.fail_starts = 1
    rec = Recorder()

    r2 = await harness.adapter.ask_with_context(SYS, "q2", {}, on_text=rec.on_text, thread_id=T, approve_skill_writes=True)

    assert is_llm_error_reply(r2) and rec.texts == [r2]
    assert RAW_PROVIDER_MARKER not in r2
    assert fake.session_starts == starts_after_crash + 1
    assert not harness.adapter.has_session(T)

    r3 = await harness.adapter.ask_with_context(SYS, "q3", {}, thread_id=T, approve_skill_writes=True)
    assert r3 == "복구"
    assert fake.session_starts == starts_after_crash + 2


@pytest.mark.asyncio
async def test_ask_start_failure_raises_typed_error(harness):
    harness.fake.fail_starts = 1
    rec = Recorder()

    with pytest.raises(LLMError) as exc:
        await harness.adapter.ask("메모리 추출기", "대화", on_text=rec.on_text)

    assert is_llm_error_reply(exc.value.user_message)
    assert RAW_PROVIDER_MARKER not in exc.value.user_message
    assert rec.texts == []
    assert harness.fake.session_starts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", [None, T], ids=["one_shot", "thread"])
async def test_max_turns_cuts_turn_at_tool_start_count(harness, thread_id):
    fake = harness.fake
    fake.script(
        tool("mcp__garmin__get_sleep", {"start": None, "end": None}),
        text("중간"),
        tool("mcp__garmin__get_hrv", {"start": None, "end": None}),
        tool("mcp__garmin__get_stress", {"start": None, "end": None}),
        text("never"),
    )
    rec = Recorder()

    result = await harness.adapter.ask_with_context(
        SYS, "q", {}, on_text=rec.on_text, on_tool=rec.on_tool, counter=rec.counter,
        max_turns=2, thread_id=thread_id, approve_skill_writes=True,
    )

    assert rec.tools == ["mcp__garmin__get_sleep", "mcp__garmin__get_hrv"]
    assert rec.counter == [2]
    assert rec.texts == ["중간"] and result == "중간"
    assert "get_stress" not in harness.env.garmin.calls
