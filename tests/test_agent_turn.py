"""Phase 0-2 테스트 — 생성 primitive(run_agent_turn) vs 오케스트레이션(run_agent_to_channel) 분리."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_notify_setup():
    """fetch_channel → notify_channel → create_thread → thread 체인 mock."""
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 555
    mock_thread.send = AsyncMock()
    mock_thread.typing = MagicMock(return_value=_FakeTyping())

    mock_notify_channel = MagicMock()
    mock_notify_channel.create_thread = AsyncMock(return_value=mock_thread)

    mock_bot_channel = MagicMock()
    mock_bot_channel._client.fetch_channel = AsyncMock(return_value=mock_notify_channel)
    mock_bot_channel._split_message = lambda text: [text]  # send_reply가 사용
    return mock_bot_channel, mock_notify_channel, mock_thread


class TestRunAgentTurn:
    """순수 생성 primitive는 context를 인자로 받아 LLM만 호출 (재수집 없음)."""

    @pytest.mark.asyncio
    async def test_forwards_args_to_llm_and_streams(self):
        from bot.main import run_agent_turn

        captured = {}

        async def fake_ask(system, message, context, *, on_text=None, **kwargs):
            captured.update(
                system=system, message=message, context=context,
                on_tool=kwargs.get("on_tool"), max_turns=kwargs.get("max_turns"),
            )
            if on_text:
                await on_text("결과")
            return "결과"

        received = []

        async def on_text(t):
            received.append(t)

        async def on_tool(name):
            pass

        with patch("bot.main.llm") as mock_llm:
            mock_llm.ask_with_context = fake_ask
            result = await run_agent_turn(
                "SYS", "MSG", {"a": 1}, on_text, on_tool=on_tool, max_turns=9
            )

        assert result == "결과"
        assert captured["system"] == "SYS"
        assert captured["message"] == "MSG"
        assert captured["context"] == {"a": 1}
        assert captured["max_turns"] == 9
        assert captured["on_tool"] is on_tool
        assert received == ["결과"]


class TestRunAgentToChannel:
    """스레드/채널 오케스트레이션이 primitive를 호출하고 스트리밍 게시."""

    @pytest.mark.asyncio
    async def test_creates_thread_and_streams(self):
        from bot.main import run_agent_to_channel

        mock_bot_channel, mock_notify_channel, mock_thread = _make_notify_setup()

        async def fake_ask(system, message, context, *, on_text=None, **kwargs):
            if on_text:
                await on_text("스트리밍")
            return "스트리밍"

        collect = AsyncMock(return_value={"c": 1})
        with patch("bot.main.channel", mock_bot_channel), \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main._build_system_prompt", return_value="SYS"), \
             patch("bot.main._collect_health_context_async", new=collect), \
             patch("bot.main.llm") as mock_llm:
            mock_llm.ask_with_context = fake_ask
            thread = await run_agent_to_channel("PROMPT", "999", "제목")

        assert thread is mock_thread
        mock_notify_channel.create_thread.assert_called_once()
        assert mock_notify_channel.create_thread.call_args.kwargs["name"] == "제목"
        mock_sess.update_activity.assert_called_once_with(555)
        mock_thread.send.assert_called_once_with("스트리밍")
        # system/context 미제공 → 여기서 조립·수집
        collect.assert_called_once()

    @pytest.mark.asyncio
    async def test_provided_context_skips_collection(self):
        from bot.main import run_agent_to_channel

        mock_bot_channel, _, _ = _make_notify_setup()

        async def fake_ask(system, message, context, *, on_text=None, **kwargs):
            return ""

        collect = AsyncMock(return_value={"c": 1})
        with patch("bot.main.channel", mock_bot_channel), \
             patch("bot.main.session_mgr"), \
             patch("bot.main._collect_health_context_async", new=collect), \
             patch("bot.main.llm") as mock_llm:
            mock_llm.ask_with_context = fake_ask
            await run_agent_to_channel(
                "PROMPT", "999", "제목", system="SYS", context={"pre": 1}
            )

        collect.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_channel_id_skips(self):
        from bot.main import run_agent_to_channel

        mock_bot_channel, mock_notify_channel, _ = _make_notify_setup()
        with patch("bot.main.channel", mock_bot_channel):
            result = await run_agent_to_channel("PROMPT", "", "제목")
        assert result is None
        mock_notify_channel.create_thread.assert_not_called()


class TestRunAutoAnalysisRewrite:
    """_run_auto_analysis가 run_agent_to_channel 호출자로 재작성되어도 외부 동작 보존."""

    @pytest.mark.asyncio
    async def test_collects_context_once_and_posts(self):
        from bot.main import _run_auto_analysis

        mock_bot_channel, mock_notify_channel, mock_thread = _make_notify_setup()
        collect = AsyncMock(return_value={"sleep": {}})
        captured = {}

        async def fake_ask(system, message, context, *, on_text=None, **kwargs):
            captured["message"] = message
            captured["approve_skill_writes"] = kwargs.get("approve_skill_writes")
            if on_text:
                await on_text("분석")
            return "분석"

        new_rows = [{"date": "2026-04-27", "weight_kg": 104.0, "source": "apple_health"}]
        with patch("bot.main.channel", mock_bot_channel), \
             patch("bot.main.NOTIFY_CHANNEL_ID", "999"), \
             patch("bot.main.session_mgr"), \
             patch("bot.main._build_system_prompt", return_value="SYS"), \
             patch("bot.main._collect_health_context_async", new=collect), \
             patch("bot.main.llm") as mock_llm:
            mock_llm.ask_with_context = fake_ask
            await _run_auto_analysis(new_rows)

        # 이중 조회 방지: context는 _run_auto_analysis에서 단 한 번만 수집
        collect.assert_called_once()
        assert (
            mock_notify_channel.create_thread.call_args.kwargs["name"]
            == "체성분 자동 분석 — 2026-04-27"
        )
        mock_thread.send.assert_called_with("분석")
        assert "104.0kg" in captured["message"]
        # 무인 초기자 → skill-write 승인 없음
        assert captured["approve_skill_writes"] in (None, False)

    @pytest.mark.asyncio
    async def test_no_notify_channel_skips(self):
        from bot.main import _run_auto_analysis

        new_rows = [{"date": "2026-04-27", "weight_kg": 104.0, "source": "apple_health"}]
        with patch("bot.main.NOTIFY_CHANNEL_ID", None), \
             patch("bot.main.channel") as mock_ch:
            await _run_auto_analysis(new_rows)
        mock_ch._client.fetch_channel.assert_not_called()
