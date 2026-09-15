"""AC-16 — registry 모드(claude_registry·codex·grok): 카탈로그 블록 바이트 동일, load_skill 결과가 도구 표기 렌더 외
동일, read_skill_file 경계, on_tool `Skill`·skills_loaded, 런타임 네이티브 스킬 억제 설정(백엔드별 수단).

claude_native는 CLI 네이티브 스킬을 쓰므로 이 모듈의 파라미터를 생성하지 않는다(ac16).
"""
import pytest

from core.runtimes.tool_names import render_tool_refs
from tests.contract.harness import (
    BODY_SKILL_BODY,
    CUTOFFS_TEXT,
    EXPECTED_CATALOG,
    SLEEP_SKILL_BODY,
    Recorder,
    turn_lines,
)
from tests.contract.scenario import text, tool

pytestmark = pytest.mark.ac16


@pytest.fixture
def bot_prompt(harness, monkeypatch):
    """봇 _build_system_prompt를 하니스 설정·스킬 디렉터리로 조립."""
    import bot.main as main

    monkeypatch.setattr(main, "LLM_CONFIG", harness.config)
    monkeypatch.setattr(main, "SKILLS_DIR", harness.env.skills_dir)
    monkeypatch.setattr(main, "memory_mgr", harness.env.memory_mgr)
    monkeypatch.setattr(main, "load_prompt", lambda name: f"<{name}>")
    return main._build_system_prompt


@pytest.mark.asyncio
async def test_catalog_block_reaches_runtime_byte_identical(harness, bot_prompt):
    system = bot_prompt()
    assert system == f"<system.md>\n\n<goals.md>\n\n{EXPECTED_CATALOG}"
    harness.fake.script(text("ok"))

    await harness.adapter.ask_with_context(system, "q", {}, approve_skill_writes=True, thread_id=9001)

    assert EXPECTED_CATALOG.encode() in harness.fake.system_prompts[-1].encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("privileged", [True, False], ids=["priv", "ro"])
async def test_load_skill_result_identical_except_tool_notation(harness, capsys, privileged):
    harness.fake.script(
        tool("mcp__skills__load_skill", {"name": "sleep-analysis"}),
        tool("mcp__skills__load_skill", {"name": "body-composition"}),
        text("적용"),
    )
    rec = Recorder()
    capsys.readouterr()

    result = await harness.adapter.ask_with_context(
        "S", "q", {}, on_tool=rec.on_tool, counter=rec.counter, approve_skill_writes=True if privileged else None,
    )

    sleep, body = harness.fake.executions
    assert sleep.executed and not sleep.is_error
    assert sleep.result_text == render_tool_refs(SLEEP_SKILL_BODY, harness.backend_id)
    assert body.result_text == BODY_SKILL_BODY
    assert result == "적용"
    assert rec.tools == ["Skill", "Skill"] and rec.counter == [2]
    (turn,) = turn_lines(capsys.readouterr().out)
    assert (turn["tools"], turn["skills_loaded"], turn["outcome"]) == ("2", "2", "ok")


@pytest.mark.asyncio
async def test_read_skill_file_within_skill_dir_only(harness):
    harness.fake.script(
        tool("mcp__skills__read_skill_file", {"name": "body-composition", "path": "references/cutoffs.md"}),
        tool("mcp__skills__read_skill_file", {"name": "body-composition", "path": "../sleep-analysis/SKILL.md"}),
        tool("mcp__skills__load_skill", {"name": "missing-skill"}),
        text("끝"),
    )

    await harness.adapter.ask_with_context("S", "q", {})

    inside, escape, missing = harness.fake.executions
    assert inside.result_text == CUTOFFS_TEXT and not inside.is_error
    assert escape.is_error and "7일 수면을 조회" not in escape.result_text
    assert missing.is_error and "missing-skill" in missing.result_text


@pytest.mark.asyncio
async def test_runtime_native_skills_suppressed(harness):
    harness.fake.script(text("ok"))

    await harness.adapter.ask_with_context("S", "q", {}, approve_skill_writes=True)

    harness.check_native_skills_suppressed()
