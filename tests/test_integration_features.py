"""통합 테스트 — 메모리, 컨텍스트 압축, 세션 타임아웃이 main.py에 통합되었는지 검증."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord


def _make_mock_thread(messages):
    """스레드 mock 생성. messages는 (content, is_bot) 튜플 리스트."""
    thread = MagicMock(spec=discord.Thread)

    mock_msgs = []
    for content, is_bot in messages:
        m = MagicMock(spec=discord.Message)
        m.content = content
        m.author = MagicMock()
        m.author.bot = is_bot
        mock_msgs.append(m)

    async def fake_history(limit=None, oldest_first=True):
        for m in mock_msgs:
            yield m

    thread.history = fake_history
    thread.send = AsyncMock()
    thread.typing = MagicMock(return_value=_FakeTyping())
    thread.id = 12345
    return thread


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_mock_message(content, is_thread=False, thread=None):
    msg = MagicMock(spec=discord.Message)
    msg.content = content
    msg.author = MagicMock()
    msg.author.bot = False
    if is_thread and thread:
        msg.channel = thread
    else:
        msg.channel = MagicMock(spec=discord.TextChannel)
    msg.create_thread = AsyncMock()
    return msg


class TestMemoryInSystemPrompt:
    """메모리 내용이 시스템 프롬프트에 포함되는지 테스트."""

    @pytest.mark.asyncio
    async def test_memory_and_user_included_in_prompt(self):
        from bot.main import handle_health_query

        thread = _make_mock_thread([])
        mock_message = _make_mock_message("질문", is_thread=True, thread=thread)

        captured_system = None

        async def capture_ask(*args, on_text=None, **kwargs):
            nonlocal captured_system
            captured_system = args[0] if args else kwargs.get("system_prompt")
            return "응답"

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt") as mock_load, \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_load.return_value = "시스템 프롬프트"
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = "- 사용자는 러닝을 좋아함"
            mock_mem.read_user.return_value = "- 한국어 선호"
            mock_mem.extract_and_save = AsyncMock()
            mock_sess.is_expired.return_value = False
            mock_comp.compress = AsyncMock(return_value=[])
            mock_llm.ask_with_context = capture_ask

            await handle_health_query(mock_message, "질문")

        assert "러닝을 좋아함" in captured_system
        assert "한국어 선호" in captured_system


class TestSessionTimeout:
    """세션 타임아웃 시 히스토리를 로드하지 않는지 테스트."""

    @pytest.mark.asyncio
    async def test_expired_session_skips_history(self):
        from bot.main import handle_health_query

        thread = _make_mock_thread([
            ("이전 질문", False),
            ("이전 답변", True),
            ("현재 질문", False),
        ])
        mock_message = _make_mock_message("현재 질문", is_thread=True, thread=thread)

        captured_kwargs = {}

        async def capture_ask(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return "응답"

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp:
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_mem.extract_and_save = AsyncMock()
            mock_sess.is_expired.return_value = True  # 만료!
            mock_comp.compress = AsyncMock(return_value=[])
            mock_llm.ask_with_context = capture_ask

            await handle_health_query(mock_message, "현재 질문")

        # 만료 세션 → history는 None이어야 함
        assert captured_kwargs.get("history") is None

    @pytest.mark.asyncio
    async def test_active_session_loads_history(self):
        from bot.main import handle_health_query

        thread = _make_mock_thread([
            ("이전 질문", False),
            ("이전 답변", True),
            ("현재 질문", False),
        ])
        mock_message = _make_mock_message("현재 질문", is_thread=True, thread=thread)

        captured_kwargs = {}

        async def capture_ask(*args, **kwargs):
            captured_kwargs.update(kwargs)
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
            mock_sess.is_expired.return_value = False  # 활성!
            mock_comp.compress = AsyncMock(side_effect=lambda msgs, llm: msgs)
            mock_llm.ask_with_context = capture_ask

            await handle_health_query(mock_message, "현재 질문")

        # 활성 세션 → history 존재
        assert captured_kwargs.get("history") is not None


class TestContextCompression:
    """컨텍스트 압축이 적용되는지 테스트."""

    @pytest.mark.asyncio
    async def test_compressor_called_on_history(self):
        from bot.main import handle_health_query

        thread = _make_mock_thread([("질문", False), ("답변", True)] * 15)
        mock_message = _make_mock_message("새 질문", is_thread=True, thread=thread)

        compress_called = False

        async def track_compress(msgs, llm):
            nonlocal compress_called
            compress_called = True
            return msgs

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
            mock_comp.compress = track_compress
            mock_llm.ask_with_context = AsyncMock(return_value="응답")

            await handle_health_query(mock_message, "새 질문")

        assert compress_called


class TestMemoryMode:
    """MEMORY_MODE=auto 시 응답 후 메모리 추출이 실행되는지 테스트."""

    @pytest.mark.asyncio
    async def test_auto_mode_triggers_extraction(self):
        from bot.main import handle_health_query

        thread = _make_mock_thread([])
        mock_message = _make_mock_message("나 매일 러닝해", is_thread=False)
        mock_message.create_thread = AsyncMock(return_value=thread)

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "auto"):
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_mem.extract_and_save = AsyncMock()
            mock_sess.is_expired.return_value = False
            mock_comp.compress = AsyncMock(return_value=[])
            mock_llm.ask_with_context = AsyncMock(return_value="응답")

            await handle_health_query(mock_message, "나 매일 러닝해")

        mock_mem.extract_and_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_manual_mode_skips_extraction(self):
        from bot.main import handle_health_query

        thread = _make_mock_thread([])
        mock_message = _make_mock_message("나 매일 러닝해", is_thread=False)
        mock_message.create_thread = AsyncMock(return_value=thread)

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
            mock_llm.ask_with_context = AsyncMock(return_value="응답")

            await handle_health_query(mock_message, "나 매일 러닝해")

        mock_mem.extract_and_save.assert_not_called()
