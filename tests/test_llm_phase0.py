"""Phase 0 공유 seam 테스트 — _consume_stream, max_turns 파라미터화, skill-write 안전 게이트."""
import pytest
from unittest.mock import patch

from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

from core.llm import (
    ClaudeSDKAdapter,
    evaluate_skill_write_gate,
    _make_unattended_gate_hook,
    DEFAULT_ALLOWED_TOOLS,
)


async def _fake_aiter(messages):
    """SDK 메시지 시퀀스를 async iterator로 시뮬레이션 (producer 주입용)."""
    for m in messages:
        yield m


# ── 0-1. _consume_stream (producer-agnostic 컨슈머) ──────────────────


class TestConsumeStream:
    @pytest.mark.asyncio
    async def test_dispatches_text_tool_and_counter(self):
        """TextBlock→on_text, ToolUseBlock→on_tool, tool_use→counter 모두 발화."""
        messages = [
            AssistantMessage(
                content=[
                    TextBlock(text="데이터를 확인합니다."),
                    ToolUseBlock(id="t1", name="mcp__garmin__get_sleep", input={}),
                ],
                model="claude-sonnet-4-20250514",
            ),
            AssistantMessage(
                content=[
                    ToolUseBlock(id="t2", name="WebSearch", input={"q": "x"}),
                    TextBlock(text="분석 결과입니다."),
                ],
                model="claude-sonnet-4-20250514",
            ),
        ]
        adapter = ClaudeSDKAdapter()
        texts, tools, counter = [], [], [0]

        async def on_text(t):
            texts.append(t)

        async def on_tool(name):
            tools.append(name)

        result = await adapter._consume_stream(
            _fake_aiter(messages), on_text=on_text, on_tool=on_tool, counter=counter
        )

        assert result == ["데이터를 확인합니다.", "분석 결과입니다."]
        assert texts == ["데이터를 확인합니다.", "분석 결과입니다."]
        assert tools == ["mcp__garmin__get_sleep", "WebSearch"]
        assert counter[0] == 2

    @pytest.mark.asyncio
    async def test_skips_non_assistant_and_no_callbacks(self):
        """AssistantMessage가 아닌 이벤트는 스킵, 콜백 없이도 텍스트 수집."""
        class _Other:
            pass

        messages = [
            _Other(),
            AssistantMessage(content=[TextBlock(text="응답")], model="m"),
            _Other(),
        ]
        adapter = ClaudeSDKAdapter()
        result = await adapter._consume_stream(_fake_aiter(messages))
        assert result == ["응답"]

    @pytest.mark.asyncio
    async def test_call_claude_uses_consume_stream_contract(self):
        """_call_claude가 _consume_stream을 경유해도 스트리밍 계약(on_text/반환값) 유지."""
        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="첫")], model="m")
            yield AssistantMessage(
                content=[ToolUseBlock(id="t", name="Read", input={})], model="m"
            )
            yield AssistantMessage(content=[TextBlock(text="둘")], model="m")

        adapter = ClaudeSDKAdapter()
        received = []

        async def on_text(t):
            received.append(t)

        with patch("core.llm.query", side_effect=fake_query):
            result = await adapter._call_claude("시스템", "질문", on_text=on_text)

        assert received == ["첫", "둘"]
        assert result == "첫\n둘"


# ── 0-4. max_turns 파라미터화 ────────────────────────────────────────


class TestMaxTurnsParam:
    @pytest.mark.asyncio
    async def test_override_reaches_options_via_call_claude(self):
        adapter = ClaudeSDKAdapter()

        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문", max_turns=42)

        options = mock_query.call_args.kwargs["options"]
        assert options.max_turns == 42

    @pytest.mark.asyncio
    async def test_override_reaches_options_via_ask_with_context(self):
        adapter = ClaudeSDKAdapter()

        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter.ask_with_context("시스템", "질문", {}, max_turns=7)

        options = mock_query.call_args.kwargs["options"]
        assert options.max_turns == 7

    @pytest.mark.asyncio
    async def test_default_is_15(self):
        adapter = ClaudeSDKAdapter()

        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter.ask_with_context("시스템", "질문", {})

        options = mock_query.call_args.kwargs["options"]
        assert options.max_turns == 15


# ── 0-3. skill-write 안전 게이트 (순수 함수) ─────────────────────────


class TestSkillWriteGateFunction:
    def test_blocks_skill_write_without_approval(self):
        allow, reason = evaluate_skill_write_gate(
            "Write", {"file_path": ".claude/skills/body-composition/SKILL.md"}, False
        )
        assert allow is False
        assert reason

    def test_allows_skill_write_with_approval(self):
        allow, _ = evaluate_skill_write_gate(
            "Write", {"file_path": ".claude/skills/body-composition/SKILL.md"}, True
        )
        assert allow is True

    def test_edit_blocked_without_approval(self):
        allow, _ = evaluate_skill_write_gate(
            "Edit", {"file_path": "/abs/proj/.claude/skills/x/SKILL.md"}, False
        )
        assert allow is False

    def test_science_reference_blocked_unconditionally(self):
        # 승인 플래그가 있어도 science-reference는 차단
        allow, reason = evaluate_skill_write_gate(
            "Write",
            {"file_path": ".claude/skills/science-reference/references/hrv-detail.md"},
            True,
        )
        assert allow is False
        assert "science-reference" in reason

    def test_science_reference_edit_blocked_unconditionally(self):
        allow, _ = evaluate_skill_write_gate(
            "Edit",
            {"file_path": "/x/.claude/skills/science-reference/SKILL.md"},
            True,
        )
        assert allow is False

    def test_allows_non_skill_path(self):
        allow, _ = evaluate_skill_write_gate(
            "Write", {"file_path": "data/notes.md"}, False
        )
        assert allow is True

    def test_allows_non_write_tool(self):
        # Read/Bash 등은 게이트 대상 아님
        allow, _ = evaluate_skill_write_gate(
            "Read", {"file_path": ".claude/skills/x/SKILL.md"}, False
        )
        assert allow is True

    def test_absolute_path_with_dotdot_normalized(self):
        allow, _ = evaluate_skill_write_gate(
            "Write",
            {"file_path": "/proj/sub/../.claude/skills/x/SKILL.md"},
            False,
        )
        assert allow is False

    def test_no_file_path_allowed(self):
        allow, _ = evaluate_skill_write_gate("Write", {}, False)
        assert allow is True


class TestSkillWriteGuardHook:
    """게이트를 감싼 PreToolUse 훅 콜백의 deny 출력 형태 검증."""

    @pytest.mark.asyncio
    async def test_hook_denies_skill_write(self):
        hook = _make_unattended_gate_hook(approve_skill_writes=False)
        out = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/skills/x/SKILL.md"}},
            "tid",
            {},
        )
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    @pytest.mark.asyncio
    async def test_hook_denies_normal_write_unattended_allows_privileged(self):
        # 무인 턴은 읽기 전용(F1) — 비-스킬 경로 write도 차단. 인터랙티브 승인 턴은 허용.
        denied = await _make_unattended_gate_hook(approve_skill_writes=False)(
            {"tool_name": "Write", "tool_input": {"file_path": "data/x.md"}},
            "tid",
            {},
        )
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        allowed = await _make_unattended_gate_hook(approve_skill_writes=True)(
            {"tool_name": "Write", "tool_input": {"file_path": "data/x.md"}},
            "tid",
            {},
        )
        assert allowed == {}

    @pytest.mark.asyncio
    async def test_hook_science_reference_denies_even_with_approval(self):
        hook = _make_unattended_gate_hook(approve_skill_writes=True)
        out = await hook(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": ".claude/skills/science-reference/SKILL.md"},
            },
            "tid",
            {},
        )
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestGateWiredIntoOptions:
    """cwd 활성 시 PreToolUse 훅이 옵션에 배선되는지, allowed_tools 재정의 경로 존재하는지."""

    @pytest.mark.asyncio
    async def test_hook_registered_when_cwd(self):
        adapter = ClaudeSDKAdapter(cwd="/proj")

        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문")

        options = mock_query.call_args.kwargs["options"]
        matchers = options.hooks["PreToolUse"]
        assert matchers[0].matcher == "Bash|Write|Edit|MultiEdit|NotebookEdit"
        # 배선된 훅이 실제로 skill-write를 차단하는지(승인 없음)
        guard = matchers[0].hooks[0]
        out = await guard(
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/skills/x/SKILL.md"}},
            None,
            {},
        )
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_approved_call_allows_skill_write_in_wired_hook(self):
        # 인터랙티브 오너 턴: approve_skill_writes=True → 게이트 통과(단 science-reference 제외)
        adapter = ClaudeSDKAdapter(cwd="/proj")

        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문", approve_skill_writes=True)

        options = mock_query.call_args.kwargs["options"]
        guard = options.hooks["PreToolUse"][0].hooks[0]
        allowed = await guard(
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/skills/x/SKILL.md"}},
            None,
            {},
        )
        assert allowed == {}
        # science-reference는 승인해도 여전히 차단
        denied = await guard(
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/skills/science-reference/SKILL.md"}},
            None,
            {},
        )
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_restricted_allowed_tools_override(self):
        """무인 초기자용 축소 도구셋을 넘길 수 있어야 한다 (매트릭스 메커니즘)."""
        adapter = ClaudeSDKAdapter(cwd="/proj")

        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        restricted = ["Bash", "Read", "Glob", "Grep"]  # Write/Edit/Skill 제외
        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문", allowed_tools=restricted)

        options = mock_query.call_args.kwargs["options"]
        assert options.allowed_tools == restricted
        assert "Write" not in options.allowed_tools

    @pytest.mark.asyncio
    async def test_default_allowed_tools_when_not_overridden(self):
        adapter = ClaudeSDKAdapter(cwd="/proj")

        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문")

        options = mock_query.call_args.kwargs["options"]
        assert options.allowed_tools == DEFAULT_ALLOWED_TOOLS

    @pytest.mark.asyncio
    async def test_no_hooks_without_cwd(self):
        adapter = ClaudeSDKAdapter()

        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문")

        options = mock_query.call_args.kwargs["options"]
        assert not getattr(options, "hooks", None)
