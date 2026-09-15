"""AC-19 — 비특권(무인) 라우팅: UNATTENDED_ALLOWED_TOOLS 번역 스냅샷 + 게이트 결정, 런타임이 받은 도구 transport의
서버측 capability(ro = mutation 거부 / priv = 허용), 동일 스크립트의 on_text·on_tool·counter 동일,
압축·추출·add_memory 오버플로우 중첩 ask 결과(memory 파일 바이트)가 참조 구현(stub llm)과 동일.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.context_compressor import ContextCompressor
from core.memory import ENTRY_DELIMITER, MAX_MEMORY_CHARS, MemoryManager
from core.runtimes.tool_names import translate_allowed_tools
from tests.contract.harness import SYS, Recorder, gate_lines
from tests.contract.scenario import end, gate_probe, text, tool

UNATTENDED_TRANSLATION = {
    "claude_native": [
        "Read", "Glob", "Grep", "Skill", "WebSearch", "WebFetch",
        "mcp__garmin", "mcp__body_metrics", "mcp__session_search", "mcp__schedule__schedule_list",
    ],
    "claude_registry": [
        "Read", "Glob", "Grep", "mcp__skills", "WebSearch", "WebFetch",
        "mcp__garmin", "mcp__body_metrics", "mcp__session_search", "mcp__schedule__schedule_list",
    ],
    "codex": None,  # 스티어링 미적용 — 강제는 wiring + capability + 샌드박스
    "grok": None,
}
SCHEDULE_ARGS = {"prompt": "매일 수면 요약", "schedule": "0 9 * * *", "deliver_channel_id": None, "max_turns": None}


@pytest.mark.asyncio
async def test_unattended_allowed_tools_translation_and_gate_decisions(harness, capsys):
    import bot.main as main

    unattended_before = list(main.UNATTENDED_ALLOWED_TOOLS)
    harness.fake.script(
        gate_probe("Bash", {"command": "echo x > data/cron_jobs.json"}),
        gate_probe("Write", {"file_path": "prompts/memory.md", "content": "x"}),
        gate_probe("mcp__schedule__schedule_create", SCHEDULE_ARGS),
        gate_probe("mcp__memory__add_memory", {"target": "memory", "content": "주입"}),
        gate_probe("mcp__schedule__schedule_list", {}),
        gate_probe("WebSearch", {"query": "zone 2"}),
        text("끝"),
    )
    capsys.readouterr()

    await harness.adapter.ask_with_context(SYS, "cron 프롬프트", {}, allowed_tools=main.UNATTENDED_ALLOWED_TOOLS)

    assert harness.fake.allowed_tools[-1] == UNATTENDED_TRANSLATION[harness.name]
    assert harness.fake.allowed_tools[-1] == translate_allowed_tools(
        harness.backend_id, unattended_before, skills_registry=harness.registry
    )
    assert main.UNATTENDED_ALLOWED_TOOLS == unattended_before
    assert [e.executed for e in harness.fake.executions] == [False, False, False, False, True, True]
    denies = [(l["tool"], l["cap"]) for l in gate_lines(capsys.readouterr().out) if l["decision"] == "deny"]
    assert denies == [
        ("Bash", "ro"), ("Write", "ro"), ("mcp__schedule__schedule_create", "ro"), ("mcp__memory__add_memory", "ro"),
    ]
    assert harness.env.cron_store.count() == 0 and harness.env.memory_mgr.read_memory() == ""


@pytest.mark.asyncio
async def test_unattended_turn_tool_transport_denies_mutation_server_side(harness, capsys):
    harness.fake.script(text("무인"), end(), text("오너"))
    capsys.readouterr()

    await harness.adapter.ask_with_context(SYS, "q", {})
    denied = await harness.fake.call_tool_endpoint("mcp__schedule__schedule_create", SCHEDULE_ARGS)

    assert json.loads(denied.text) == {
        "success": False,
        "error": "mcp__schedule__schedule_create는 인터랙티브 오너 세션 승인이 필요합니다 (무인 턴 차단).",
        "denied_by": "capability",
    }
    lines = [(l["cap"], l["decision"], l["via"]) for l in gate_lines(capsys.readouterr().out, tool="mcp__schedule__schedule_create")]
    assert lines == [("ro", "deny", "capability")]
    assert harness.env.cron_store.count() == 0

    await harness.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True)
    allowed = await harness.fake.call_tool_endpoint("mcp__schedule__schedule_create", SCHEDULE_ARGS)

    assert json.loads(allowed.text)["success"] is True
    assert harness.env.cron_store.count() == 1


@pytest.mark.asyncio
async def test_unattended_same_script_same_stream_as_interactive(harness):
    script = (
        text("수면 확인"),
        tool("mcp__garmin__get_sleep", {"start": None, "end": None}),
        tool("mcp__schedule__schedule_list", {}),
        text("요약"),
    )
    harness.fake.script(*script, end(), *script)
    unattended, interactive = Recorder(), Recorder()

    r1 = await harness.adapter.ask_with_context(
        SYS, "q", {}, on_text=unattended.on_text, on_tool=unattended.on_tool, counter=unattended.counter,
    )
    r2 = await harness.adapter.ask_with_context(
        SYS, "q", {}, on_text=interactive.on_text, on_tool=interactive.on_tool, counter=interactive.counter,
        approve_skill_writes=True,
    )

    assert r1 == r2 == "수면 확인\n요약"
    assert (unattended.texts, unattended.tools, unattended.counter) == (
        interactive.texts, interactive.tools, interactive.counter,
    )
    assert unattended.tools == ["mcp__garmin__get_sleep", "mcp__schedule__schedule_list"]
    assert [e.result_text for e in harness.fake.executions[:2]] == [e.result_text for e in harness.fake.executions[2:]]


def _stub_llm(reply):
    llm = MagicMock()
    llm.ask = AsyncMock(return_value=reply)
    return llm


def _fill_memory(mgr):
    mgr.write_memory(f"기억 A{ENTRY_DELIMITER}기억 B{ENTRY_DELIMITER}" + "M" * (MAX_MEMORY_CHARS - 20))


def _files(mgr):
    return mgr._read_raw("memory").encode(), mgr._read_raw("user").encode()


@pytest.mark.asyncio
async def test_memory_extraction_via_adapter_matches_reference(harness, tmp_path):
    reply = "MEMORY: 매일 5km 러닝\nUSER: 존댓말 선호"
    conversation = [{"role": "user", "content": "나 매일 5km 뛰어요"}]
    (tmp_path / "reference").mkdir()
    reference = MemoryManager(str(tmp_path / "reference"))
    await reference.extract_and_save(_stub_llm(reply), conversation)
    harness.fake.script(text(reply))

    await harness.env.memory_mgr.extract_and_save(harness.adapter, conversation)

    assert _files(harness.env.memory_mgr) == _files(reference)
    assert _files(reference) == ("매일 5km 러닝".encode(), "존댓말 선호".encode())
    assert "메모리 추출기" in harness.fake.system_prompts[-1]


@pytest.mark.asyncio
async def test_memory_compression_summary_matches_reference(harness):
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"메시지 {i}"} for i in range(25)]
    reference = await ContextCompressor().compress(history, _stub_llm("요약본"))
    harness.fake.script(text("요약본"))

    result = await ContextCompressor().compress(history, harness.adapter)

    assert result == reference
    assert "컨텍스트 압축기" in harness.fake.system_prompts[-1]


@pytest.mark.asyncio
async def test_memory_add_overflow_nested_ask_matches_reference(harness, tmp_path):
    consolidated = f"통합 기억 1{ENTRY_DELIMITER}통합 기억 2{ENTRY_DELIMITER}새 기억"
    (tmp_path / "reference").mkdir()
    reference = MemoryManager(str(tmp_path / "reference"))
    _fill_memory(reference)
    await reference._save_or_consolidate(_stub_llm(consolidated), "memory", "새 기억")
    mgr = harness.env.memory_mgr
    _fill_memory(mgr)
    harness.fake.script(
        tool("mcp__memory__add_memory", {"target": "memory", "content": "새 기억"}),
        text("저장했어요"),
        end(),
        text(consolidated),  # 도구 핸들러 안의 중첩 ask(메모리 통합기)
    )

    result = await harness.adapter.ask_with_context(SYS, "기억해", {}, approve_skill_writes=True, thread_id=9100)

    assert result == "저장했어요"
    assert _files(mgr) == _files(reference)
    assert _files(reference)[0] == consolidated.encode()
    assert json.loads(harness.fake.executions[0].result_text) == {"success": True, "consolidated": True}
    assert "메모리 통합기" in harness.fake.system_prompts[-1]
    assert harness.fake.pending_turns == 0


@pytest.mark.asyncio
async def test_memory_add_in_unattended_turn_denied_without_nested_ask(harness):
    mgr = harness.env.memory_mgr
    _fill_memory(mgr)
    before = _files(mgr)
    harness.fake.script(
        tool("mcp__memory__add_memory", {"target": "memory", "content": "새 기억"}),
        text("못 저장"),
        end(),
        text("통합 기억"),
    )

    await harness.adapter.ask_with_context(SYS, "cron", {})

    assert _files(mgr) == before
    assert not harness.fake.executions[0].executed
    assert harness.fake.pending_turns == 1  # 중첩 통합 ask 미발생
