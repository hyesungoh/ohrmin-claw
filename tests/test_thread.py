"""Discord 스레드 기반 대화 세션 테스트."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import discord


def _make_mock_message(content, author_name="user1", is_bot=False, thread=None):
    """테스트용 Discord Message mock 생성."""
    msg = MagicMock(spec=discord.Message)
    msg.content = content
    msg.author = MagicMock()
    msg.author.bot = is_bot
    msg.author.display_name = author_name
    msg.channel = thread or MagicMock(spec=discord.TextChannel)
    msg.create_thread = AsyncMock()
    return msg


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_mock_thread(messages):
    """스레드 mock 생성. messages는 (content, is_bot) 튜플 리스트."""
    thread = MagicMock(spec=discord.Thread)

    mock_msgs = []
    for content, is_bot in messages:
        m = MagicMock(spec=discord.Message)
        m.content = content
        m.author = MagicMock()
        m.author.bot = is_bot
        m.author.display_name = "봇" if is_bot else "user1"
        mock_msgs.append(m)

    async def fake_history(limit=None, oldest_first=True):
        for m in mock_msgs:
            yield m

    thread.history = fake_history
    thread.send = AsyncMock()
    thread.typing = MagicMock(return_value=_FakeTyping())
    return thread


class TestBuildHistoryFromThread:
    """스레드 메시지를 대화 이력 형태로 변환하는 함수 테스트."""

    @pytest.mark.asyncio
    async def test_converts_thread_messages_to_history(self):
        """스레드의 메시지들이 role/content dict 리스트로 변환되어야 함."""
        from bot.main import build_history_from_thread

        thread = _make_mock_thread([
            ("내 체중 알려줘", False),
            ("최근 체중은 72kg입니다.", True),
        ])
        history = await build_history_from_thread(thread)
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "내 체중 알려줘"}
        assert history[1] == {"role": "assistant", "content": "최근 체중은 72kg입니다."}

    @pytest.mark.asyncio
    async def test_empty_thread_returns_empty_list(self):
        """빈 스레드는 빈 리스트를 반환해야 함."""
        from bot.main import build_history_from_thread

        thread = _make_mock_thread([])
        history = await build_history_from_thread(thread)
        assert history == []

    @pytest.mark.asyncio
    async def test_excludes_current_message(self):
        """마지막 메시지(현재 질문)는 이력에서 제외할 수 있어야 함."""
        from bot.main import build_history_from_thread

        thread = _make_mock_thread([
            ("이전 질문", False),
            ("이전 답변", True),
            ("현재 질문", False),
        ])
        history = await build_history_from_thread(thread, exclude_last=True)
        assert len(history) == 2
        assert history[-1]["content"] == "이전 답변"


class TestOnMessageCreatesThread:
    """일반 채널 메시지가 스레드를 생성하는지 테스트."""

    @pytest.mark.asyncio
    async def test_non_thread_message_creates_thread(self):
        """일반 채널 메시지는 스레드를 만들고 그 안에 응답해야 함."""
        from bot.main import handle_health_query

        mock_message = _make_mock_message("오늘 운동 분석해줘")
        mock_thread = _make_mock_thread([])
        mock_message.create_thread = AsyncMock(return_value=mock_thread)

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_body_metrics, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_body_metrics.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""

            async def fake_ask(*, on_text=None, **kwargs):
                if on_text:
                    await on_text("분석 결과입니다.")
                return "분석 결과입니다."

            mock_llm.ask_with_context = lambda *a, **kw: fake_ask(**kw)

            await handle_health_query(mock_message, "오늘 운동 분석해줘")

        mock_message.create_thread.assert_called_once()
        mock_thread.send.assert_called()

    @pytest.mark.asyncio
    async def test_thread_message_uses_history(self):
        """스레드 안의 메시지는 이력을 수집해서 LLM에 전달해야 함."""
        from bot.main import handle_health_query

        thread = _make_mock_thread([
            ("이전 질문", False),
            ("이전 답변", True),
        ])

        mock_message = _make_mock_message("후속 질문", thread=thread)

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_body_metrics, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_llm.ask_with_context = AsyncMock(return_value="후속 응답")
            mock_body_metrics.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_sess.is_expired.return_value = False
            mock_comp.compress = AsyncMock(side_effect=lambda msgs, llm: msgs)

            await handle_health_query(mock_message, "후속 질문")

        # history 파라미터가 전달되었는지 확인
        call_kwargs = mock_llm.ask_with_context.call_args
        assert "history" in call_kwargs.kwargs or len(call_kwargs.args) > 3


class TestStreamingSendToDiscord:
    """on_text 콜백으로 TextBlock을 즉시 Discord에 전송하는지 검증."""

    @pytest.mark.asyncio
    async def test_on_text_sends_each_block_to_thread(self):
        """각 TextBlock이 on_text 콜백을 통해 스레드에 즉시 전송되어야 함."""
        from bot.main import handle_health_query

        mock_message = _make_mock_message("가민 데이터 분석해줘")
        mock_thread = _make_mock_thread([])
        mock_message.create_thread = AsyncMock(return_value=mock_thread)

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_body_metrics, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_body_metrics.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""

            # ask_with_context가 on_text 콜백을 받아서 호출하는 것을 시뮬레이션
            async def fake_ask_with_context(*args, on_text=None, **kwargs):
                if on_text:
                    await on_text("확인해보겠습니다.")
                    await on_text("분석 결과입니다.")
                return "확인해보겠습니다.\n분석 결과입니다."

            mock_llm.ask_with_context = fake_ask_with_context

            await handle_health_query(mock_message, "가민 데이터 분석해줘")

        # on_text 콜백을 통해 각 블록이 개별 전송되었는지 확인
        send_calls = mock_thread.send.call_args_list
        sent_texts = [call.args[0] for call in send_calls]
        assert "확인해보겠습니다." in sent_texts
        assert "분석 결과입니다." in sent_texts

    @pytest.mark.asyncio
    async def test_on_text_passed_to_llm(self):
        """handle_health_query가 on_text 콜백을 LLM에 전달해야 함."""
        from bot.main import handle_health_query

        mock_message = _make_mock_message("질문")
        mock_thread = _make_mock_thread([])
        mock_message.create_thread = AsyncMock(return_value=mock_thread)

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_body_metrics, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_llm.ask_with_context = AsyncMock(return_value="응답")
            mock_body_metrics.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""

            await handle_health_query(mock_message, "질문")

        # on_text 키워드가 전달되었는지 확인
        call_kwargs = mock_llm.ask_with_context.call_args
        assert "on_text" in call_kwargs.kwargs
