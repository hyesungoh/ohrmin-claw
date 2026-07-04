"""통합 무인-권한 게이트 테스트 — schedule/memory mutation 하드 차단 (Phase B 하드닝, S3/G003).

allowed_tools는 bypassPermissions 하에서 스티어링일 뿐이므로, 무인 턴의 mutation은 PreToolUse
훅(evaluate_tool_gate)이 구조적으로 하드 차단한다. 인터랙티브 오너 턴만 허용.
"""
import pytest
from unittest.mock import patch

from claude_agent_sdk.types import AssistantMessage, TextBlock

from core.llm import (
    ClaudeSDKAdapter,
    evaluate_tool_gate,
    _make_unattended_gate_hook,
    _make_skill_write_guard_hook,
    _MUTATION_TOOL_MATCHER,
    _MUTATION_MCP_TOOLS,
)


async def _fake_query(**kwargs):
    yield AssistantMessage(content=[TextBlock(text="ok")], model="m")


# ── evaluate_tool_gate (순수 함수) ───────────────────────────────────


class TestEvaluateToolGateUnattended:
    """무인 턴(approve_privileged=False) — mutation 하드 차단, list/read 허용."""

    @pytest.mark.parametrize(
        "tool",
        [
            "mcp__schedule__schedule_create",
            "mcp__schedule__schedule_pause",
            "mcp__schedule__schedule_resume",
            "mcp__schedule__schedule_remove",
            "mcp__memory__add_memory",
            "mcp__memory__replace_memory",
            "mcp__memory__remove_memory",
        ],
    )
    def test_mutation_denied_when_unattended(self, tool):
        allow, reason = evaluate_tool_gate(tool, {}, approve_privileged=False)
        assert allow is False
        assert reason

    @pytest.mark.parametrize(
        "tool",
        ["mcp__schedule__schedule_list", "mcp__memory__list_memory",
         "mcp__garmin__get_sleep", "Read", "WebSearch"],
    )
    def test_list_and_read_allowed_when_unattended(self, tool):
        allow, _ = evaluate_tool_gate(tool, {}, approve_privileged=False)
        assert allow is True

    def test_skill_write_still_denied_when_unattended(self):
        allow, _ = evaluate_tool_gate(
            "Write", {"file_path": ".claude/skills/x/SKILL.md"}, approve_privileged=False
        )
        assert allow is False


class TestEvaluateToolGateInteractive:
    """인터랙티브 오너 턴(approve_privileged=True) — mutation 허용."""

    @pytest.mark.parametrize("tool", sorted(_MUTATION_MCP_TOOLS))
    def test_mutation_allowed_when_interactive(self, tool):
        allow, _ = evaluate_tool_gate(tool, {}, approve_privileged=True)
        assert allow is True

    def test_skill_write_allowed_when_interactive(self):
        allow, _ = evaluate_tool_gate(
            "Write", {"file_path": ".claude/skills/x/SKILL.md"}, approve_privileged=True
        )
        assert allow is True

    def test_science_reference_denied_even_interactive(self):
        allow, reason = evaluate_tool_gate(
            "Write",
            {"file_path": ".claude/skills/science-reference/SKILL.md"},
            approve_privileged=True,
        )
        assert allow is False
        assert "science-reference" in reason


# ── 훅 콜백 (deny 출력 형태) ──────────────────────────────────────────


class TestUnattendedGateHook:
    @pytest.mark.asyncio
    async def test_hook_denies_schedule_create_unattended(self):
        hook = _make_unattended_gate_hook(approve_skill_writes=False)
        out = await hook(
            {"tool_name": "mcp__schedule__schedule_create", "tool_input": {}}, "tid", {}
        )
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_hook_denies_add_memory_unattended(self):
        hook = _make_unattended_gate_hook(approve_skill_writes=False)
        out = await hook(
            {"tool_name": "mcp__memory__add_memory", "tool_input": {}}, "tid", {}
        )
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_hook_allows_schedule_list_unattended(self):
        hook = _make_unattended_gate_hook(approve_skill_writes=False)
        out = await hook(
            {"tool_name": "mcp__schedule__schedule_list", "tool_input": {}}, "tid", {}
        )
        assert out == {}

    @pytest.mark.asyncio
    async def test_hook_allows_mutation_when_interactive(self):
        hook = _make_unattended_gate_hook(approve_skill_writes=True)
        assert await hook(
            {"tool_name": "mcp__schedule__schedule_create", "tool_input": {}}, "tid", {}
        ) == {}
        assert await hook(
            {"tool_name": "mcp__memory__add_memory", "tool_input": {}}, "tid", {}
        ) == {}

    def test_backward_compat_alias(self):
        # 기존 임포트 이름이 통합 훅을 가리킨다.
        assert _make_skill_write_guard_hook is _make_unattended_gate_hook


# ── _call_claude 배선 (matcher[1] = mutation 매처) ────────────────────


class TestMutationMatcherWiring:
    @pytest.mark.asyncio
    async def test_second_matcher_covers_mutations_and_denies_unattended(self):
        adapter = ClaudeSDKAdapter(cwd="/proj")
        with patch("core.llm.query", side_effect=_fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문")  # 무인(승인 없음)

        matchers = mock_query.call_args.kwargs["options"].hooks["PreToolUse"]
        # matcher[0]은 기존 write 게이트 유지, matcher[1]이 mutation 매처.
        assert matchers[0].matcher == "Write|Edit|MultiEdit|NotebookEdit"
        assert matchers[1].matcher == _MUTATION_TOOL_MATCHER
        guard = matchers[1].hooks[0]
        denied = await guard(
            {"tool_name": "mcp__schedule__schedule_create", "tool_input": {}}, None, {}
        )
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_interactive_wired_hook_allows_mutation(self):
        adapter = ClaudeSDKAdapter(cwd="/proj")
        with patch("core.llm.query", side_effect=_fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문", approve_skill_writes=True)

        guard = mock_query.call_args.kwargs["options"].hooks["PreToolUse"][1].hooks[0]
        allowed = await guard(
            {"tool_name": "mcp__memory__add_memory", "tool_input": {}}, None, {}
        )
        assert allowed == {}

    @pytest.mark.asyncio
    async def test_matcher_regex_matches_mutations_not_list(self):
        import re
        # 매처가 mutation은 걸고 list/read는 걸지 않는지(런타임 발화 대상 정합).
        for t in _MUTATION_MCP_TOOLS:
            assert re.match(_MUTATION_TOOL_MATCHER, t), t
        for t in ["mcp__schedule__schedule_list", "mcp__memory__list_memory"]:
            assert re.match(_MUTATION_TOOL_MATCHER, t) is None, t


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class TestInteractiveTurnPassesPrivilege:
    """인터랙티브 오너 턴(handle_health_query)은 approve_skill_writes=True를 전달한다."""

    @pytest.mark.asyncio
    async def test_handle_health_query_privileged(self):
        import discord
        from unittest.mock import AsyncMock, MagicMock
        from bot.main import handle_health_query

        thread = MagicMock(spec=discord.Thread)

        async def fake_history(limit=None, oldest_first=True):
            for _ in ():
                yield _

        thread.history = fake_history
        thread.send = AsyncMock()
        thread.typing = MagicMock(return_value=_FakeTyping())
        thread.id = 4242

        msg = MagicMock(spec=discord.Message)
        msg.content = "매일 아침 7시에 수면 브리핑 예약해줘"
        msg.author = MagicMock()
        msg.author.bot = False
        msg.channel = thread
        msg.id = 1
        msg.created_at = None

        captured = {}

        async def capture_ask(*args, on_text=None, **kwargs):
            captured["approve_skill_writes"] = kwargs.get("approve_skill_writes")
            return "응답"

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_mem.extract_and_save = AsyncMock()
            mock_sess.is_expired.return_value = False
            mock_comp.compress = AsyncMock(return_value=[])
            mock_llm.ask_with_context = capture_ask

            await handle_health_query(msg, "매일 아침 7시에 수면 브리핑 예약해줘")

        assert captured["approve_skill_writes"] is True
