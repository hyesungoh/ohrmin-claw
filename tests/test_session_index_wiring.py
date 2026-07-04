"""A2 봇 배선 테스트 — handle_health_query가 유저/봇 메시지를 세션 인덱스에 색인하는지.

(e) 봇 답변이 턴 반환값에서 1행으로 색인 (청크 다중행 아님)
(f) 첫 채널 메시지가 생성된 thread.id로 키잉
"""
import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_mock_thread():
    thread = MagicMock(spec=discord.Thread)
    thread.id = 777
    thread.send = AsyncMock()
    thread.typing = MagicMock(return_value=_FakeTyping())
    return thread


class TestHandleHealthQueryIndexing:
    @pytest.mark.asyncio
    async def test_indexes_user_and_bot_keyed_to_thread(self):
        import bot.main as main_module
        from bot.main import handle_health_query

        # 일반 채널 메시지 (스레드 아님) → 새 스레드 생성됨
        mock_message = MagicMock(spec=discord.Message)
        mock_message.content = "지난주 수면 효율 알려줘"
        mock_message.id = 12345
        mock_message.created_at = datetime.datetime(2026, 7, 1, 9, 0, tzinfo=datetime.timezone.utc)
        mock_message.channel = MagicMock(spec=discord.TextChannel)
        mock_thread = _make_mock_thread()
        mock_message.create_thread = AsyncMock(return_value=mock_thread)

        async def fake_ask(*args, on_text=None, **kwargs):
            # 봇이 여러 청크를 스트리밍하지만 반환값은 전체 텍스트 1개
            if on_text:
                await on_text("확인 중…")
                await on_text("지난주 수면 효율은 88%입니다")
            return "확인 중…\n지난주 수면 효율은 88%입니다"

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
            mock_llm.ask_with_context = fake_ask

            await handle_health_query(mock_message, "지난주 수면 효율 알려줘")

        # (f) 유저 메시지가 생성된 thread.id(777)로 키잉되어 검색됨
        results = main_module.session_index.search("수면 효율")
        thread_ids = {r["thread_id"] for r in results}
        assert "777" in thread_ids

        # (e) 봇 답변이 단일 행으로 색인 (청크 2개가 아니라 전체 1행)
        assistant_rows = [r for r in results if r["role"] == "assistant"]
        assert len(assistant_rows) == 1
        assert assistant_rows[0]["content"] == "확인 중…\n지난주 수면 효율은 88%입니다"

        # 유저 행도 존재
        user_rows = [r for r in results if r["role"] == "user"]
        assert len(user_rows) == 1
        assert user_rows[0]["turn_id"] == "12345"
