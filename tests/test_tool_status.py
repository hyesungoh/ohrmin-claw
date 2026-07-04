"""A3 tool-status 피드 테스트 — 이름→상태 매핑 + transient 메시지 생명주기."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.main import map_tool_status, ToolStatusLine


class _FakeStatusMessage:
    """transient 상태 메시지 mock — edit/delete 호출을 기록."""

    def __init__(self):
        self.content = None
        self.edits = []
        self.deleted = False

    async def edit(self, content=None):
        self.content = content
        self.edits.append(content)

    async def delete(self):
        self.deleted = True


class _FakeTarget:
    """send 시 _FakeStatusMessage를 반환하는 채널/스레드 mock."""

    def __init__(self):
        self.sent = []
        self.message = None

    async def send(self, content):
        self.sent.append(content)
        self.message = _FakeStatusMessage()
        self.message.content = content
        return self.message


class TestMapToolStatus:
    def test_garmin(self):
        assert map_tool_status("mcp__garmin__get_sleep") == "💻 Garmin 조회 중…"

    def test_web(self):
        assert map_tool_status("WebSearch") == "🔍 검색 중…"
        assert map_tool_status("WebFetch") == "🔍 검색 중…"

    def test_body_metrics(self):
        assert map_tool_status("mcp__body_metrics__add_entry") == "📊 체성분 확인 중…"

    def test_session_search(self):
        assert map_tool_status("mcp__session_search__search") == "🔎 과거 기록 검색 중…"

    def test_skill(self):
        assert map_tool_status("Skill") == "🧠 분석 중…"

    def test_default(self):
        assert map_tool_status("Read") == "⚙️ 작업 중…"
        assert map_tool_status("mcp__memory__add_memory") == "⚙️ 작업 중…"


class TestToolStatusLineLifecycle:
    @pytest.mark.asyncio
    async def test_created_edited_cleared(self):
        target = _FakeTarget()
        status = ToolStatusLine(target)

        # 첫 도구 → 메시지 생성
        await status.update("mcp__garmin__get_sleep")
        assert len(target.sent) == 1
        assert target.sent[0] == "💻 Garmin 조회 중…"
        msg = target.message

        # 이후 도구 → 편집(재전송 아님)
        await status.update("WebSearch")
        assert len(target.sent) == 1  # 새 send 없음
        assert msg.edits == ["🔍 검색 중…"]

        # 턴 종료 → 정리(삭제)
        await status.clear()
        assert msg.deleted is True

    @pytest.mark.asyncio
    async def test_clear_without_update_is_noop(self):
        target = _FakeTarget()
        status = ToolStatusLine(target)
        await status.clear()  # 도구 미사용 턴 — 예외 없어야 함
        assert target.sent == []


class TestOnToolWiredIntoQuery:
    """_consume_stream이 ToolUseBlock마다 on_tool을 올바른 이름으로 호출하는지."""

    @pytest.mark.asyncio
    async def test_on_tool_called_with_tool_names(self):
        from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock
        from core.llm import ClaudeSDKAdapter

        async def fake_query(**kwargs):
            yield AssistantMessage(
                content=[
                    TextBlock(text="확인합니다"),
                    ToolUseBlock(id="t1", name="mcp__garmin__get_sleep", input={}),
                ],
                model="m",
            )
            yield AssistantMessage(
                content=[ToolUseBlock(id="t2", name="WebSearch", input={})],
                model="m",
            )

        adapter = ClaudeSDKAdapter()
        names = []

        async def on_tool(name):
            names.append(name)

        with patch("core.llm.query", side_effect=fake_query):
            await adapter._call_claude("시스템", "질문", on_tool=on_tool)

        assert names == ["mcp__garmin__get_sleep", "WebSearch"]

    @pytest.mark.asyncio
    async def test_handle_health_query_passes_on_tool(self):
        """handle_health_query가 on_tool 콜백을 LLM에 전달해야 함."""
        from bot.main import handle_health_query

        mock_message = MagicMock(spec=discord.Message)
        mock_message.content = "질문"
        mock_message.id = 1
        mock_message.created_at = None
        mock_message.channel = MagicMock(spec=discord.TextChannel)
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 42
        mock_thread.send = AsyncMock()

        class _FakeTyping:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        mock_thread.typing = MagicMock(return_value=_FakeTyping())
        mock_message.create_thread = AsyncMock(return_value=mock_thread)

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr"), \
             patch("bot.main.context_compressor"), \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_llm.ask_with_context = AsyncMock(return_value="응답")

            await handle_health_query(mock_message, "질문")

        call_kwargs = mock_llm.ask_with_context.call_args
        assert "on_tool" in call_kwargs.kwargs
        assert callable(call_kwargs.kwargs["on_tool"])
