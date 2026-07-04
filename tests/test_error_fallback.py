"""A4 에러 폴백 테스트 — Claude 생성 실패 시 한국어 폴백 + Garmin 401/429 안전 번역.

F5: 인터랙티브 기본 컨텍스트 수집 중 Garmin 401/429가 나도 사용자에게 침묵이 아니라 응답이 가야 한다.
"""
import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from core.llm import ClaudeSDKAdapter, _CLAUDE_FALLBACK_MESSAGE
from core.garmin_data import (
    GarminConnectClient,
    GarminAuthError,
    GarminRateLimitError,
)


# ── _call_claude 생성 실패 폴백 ──────────────────────────────────


class TestClaudeFallback:
    @pytest.mark.asyncio
    async def test_query_raises_yields_korean_fallback(self):
        """query() 호출이 예외를 던지면 한국어 폴백이 on_text/반환에 전달되어야 함."""
        adapter = ClaudeSDKAdapter()
        received = []

        async def on_text(t):
            received.append(t)

        def boom(**kwargs):
            raise RuntimeError("provider internal 429 https://internal.api/secret")

        with patch("core.llm.query", side_effect=boom):
            result = await adapter._call_claude("시스템", "질문", on_text=on_text)

        assert result == _CLAUDE_FALLBACK_MESSAGE
        assert received == [_CLAUDE_FALLBACK_MESSAGE]
        # 원시 provider 내부가 사용자에게 노출되지 않음
        assert "internal.api" not in result
        assert "secret" not in result

    @pytest.mark.asyncio
    async def test_stream_error_midway_yields_fallback(self):
        """스트림 이터레이션 중 예외가 나도 폴백으로 마무리되어야 함."""
        adapter = ClaudeSDKAdapter()

        async def failing_query(**kwargs):
            raise RuntimeError("boom")
            yield  # async generator로 만들기 위한 도달 불가 yield

        with patch("core.llm.query", side_effect=failing_query):
            result = await adapter._call_claude("시스템", "질문")

        assert result == _CLAUDE_FALLBACK_MESSAGE

    @pytest.mark.asyncio
    async def test_no_error_no_fallback(self):
        """정상 응답 시 폴백이 아니라 실제 텍스트를 반환."""
        from claude_agent_sdk.types import AssistantMessage, TextBlock

        adapter = ClaudeSDKAdapter()

        async def ok_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="정상 응답")], model="m")

        with patch("core.llm.query", side_effect=ok_query):
            result = await adapter._call_claude("시스템", "질문")

        assert result == "정상 응답"


# ── Garmin 401/429 안전 번역 ─────────────────────────────────────


@pytest.fixture
def mock_garmin():
    with patch("core.garmin_data.Garmin") as MockGarmin:
        mock_api = MagicMock()
        MockGarmin.return_value = mock_api
        client = GarminConnectClient(email="test@test.com", password="pass")
        yield client, mock_api


class TestGarminErrorTranslation:
    def test_429_translated_to_rate_limit(self, mock_garmin):
        client, api = mock_garmin
        api.get_sleep_data.side_effect = GarminConnectTooManyRequestsError(
            "429 Too Many Requests https://connect.garmin.com/internal-endpoint"
        )
        with pytest.raises(GarminRateLimitError) as exc:
            client.get_sleep(datetime.date(2026, 4, 1), datetime.date(2026, 4, 1))
        msg = str(exc.value)
        assert "429" in msg
        # provider 내부(URL/엔드포인트) 미노출
        assert "connect.garmin.com" not in msg
        assert "internal-endpoint" not in msg

    def test_401_translated_to_auth_error(self, mock_garmin):
        client, api = mock_garmin
        api.get_activities_by_date.side_effect = GarminConnectAuthenticationError(
            "401 Unauthorized secret-token-abc"
        )
        with pytest.raises(GarminAuthError) as exc:
            client.get_activities(datetime.date(2026, 4, 1), datetime.date(2026, 4, 2))
        assert "secret-token-abc" not in str(exc.value)

    def test_generic_http_429_translated(self, mock_garmin):
        client, api = mock_garmin

        class _HTTPErr(Exception):
            def __init__(self):
                super().__init__("boom")
                self.response = type("R", (), {"status_code": 429})()

        api.get_hrv_data.side_effect = _HTTPErr()
        with pytest.raises(GarminRateLimitError):
            client.get_hrv(datetime.date(2026, 4, 1), datetime.date(2026, 4, 1))

    def test_unrelated_error_not_translated(self, mock_garmin):
        """401/429가 아닌 오류는 원형 그대로 전파(과잉 번역 금지)."""
        client, api = mock_garmin
        api.get_stress_data.side_effect = ValueError("unrelated bug")
        with pytest.raises(ValueError):
            client.get_stress(datetime.date(2026, 4, 1), datetime.date(2026, 4, 1))

    def test_nested_detail_call_not_double_translated(self, mock_garmin):
        """get_activity_detail 내부 호출이 429여도 GarminRateLimitError로 단일 번역."""
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityType": {"typeKey": "running"},
            "activityName": "run",
            "distance": 5000,
        }
        api.get_activity_splits.side_effect = GarminConnectTooManyRequestsError("429")
        with pytest.raises(GarminRateLimitError):
            client.get_activity_detail("123")


# ── F5: 인터랙티브 기본 컨텍스트 수집 Garmin 오류 → 침묵 아닌 응답 ─────────────


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class TestBaseContextGarminFallback:
    @pytest.mark.asyncio
    async def test_garmin_error_in_base_context_still_replies(self):
        """기본 7일 컨텍스트 수집이 429로 실패해도 턴은 빈 컨텍스트로 진행하고 사용자에게 응답한다."""
        import bot.main as main

        main._inflight_turns.clear()
        main._turn_state.clear()

        thread = MagicMock(spec=discord.Thread)
        thread.id = 909

        async def fake_history(limit=None, oldest_first=True):
            for _ in ():
                yield _

        thread.history = fake_history
        thread.send = AsyncMock()
        thread.typing = MagicMock(return_value=_FakeTyping())

        msg = MagicMock(spec=discord.Message)
        msg.content = "오늘 수면 어때"
        msg.id = 1
        msg.created_at = None
        msg.author = MagicMock()
        msg.author.bot = False
        msg.channel = thread

        captured = {}

        async def capture_ask(*args, on_text=None, **kwargs):
            captured["called"] = True
            captured["context"] = kwargs.get("context", args[2] if len(args) > 2 else None)
            if on_text:
                await on_text("안전 응답")  # 사용자에게 전송
            return "안전 응답"

        async def raise_rate_limit():
            raise GarminRateLimitError("429 too many")

        mock_llm = MagicMock()
        mock_llm.ask_with_context = capture_ask
        mock_llm.interrupt_session = AsyncMock()
        mock_llm.end_session = AsyncMock()

        with patch("bot.main.llm", mock_llm), \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main._collect_health_context_async", side_effect=raise_rate_limit), \
             patch("bot.main._index_turn_message", new=AsyncMock()), \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_mem.extract_and_save = AsyncMock()
            mock_sess.is_expired.return_value = False
            mock_comp.compress = AsyncMock(return_value=[])

            await main.handle_health_query(msg, "오늘 수면 어때")

        assert captured.get("called") is True     # 생성이 실행됨(= 침묵 아님)
        assert captured.get("context") == {}       # Garmin 429 → degrade된 빈 컨텍스트로 진행
        thread.send.assert_awaited()               # 사용자에게 응답이 전송됨
