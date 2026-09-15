"""LLM 오류 분류·메시지·is_llm_error_reply + ClaudeSDKAdapter 오류 경로(AC-4) + 실제 런타임 가드 테스트."""
import datetime
import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from claude_agent_sdk.types import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    TextBlock,
    ToolUseBlock,
)

from core.llm import (
    ClaudeSDKAdapter,
    LLMAdapter,
    _CLAUDE_FALLBACK_MESSAGE,
    classify_claude_message,
)
from core.llm_errors import (
    GENERIC_MESSAGE,
    LLMError,
    LLMErrorKind,
    RealRuntimeForbidden,
    StartupError,
    auth_expired_message,
    is_llm_error_reply,
    runtime_unavailable_message,
    usage_limit_message,
)


def _rate_limited_stream(raw_text="API Error: Claude AI usage limit reached|1760000000 internal"):
    async def fake_query(**kwargs):
        yield AssistantMessage(content=[TextBlock(text="첫 세그먼트")], model="m")
        yield AssistantMessage(content=[TextBlock(text=raw_text)], model="m", error="rate_limit")

    return fake_query


# ── 메시지 형식 ───────────────────────────────────────────────────────


class TestMessages:
    def test_kind_values(self):
        assert [k.value for k in LLMErrorKind] == [
            "usage_limit", "auth_expired", "runtime_unavailable", "generic",
        ]

    def test_usage_limit_without_reset(self):
        assert usage_limit_message("claude") == (
            "⚠️ Claude 사용 한도에 도달했어요. 다른 백엔드로 대체하지 않으니 한도 재설정 후 다시 시도해 주세요."
        )

    def test_usage_limit_with_reset_time(self):
        ts = 1760000000
        hhmm = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
        assert usage_limit_message("codex", ts) == (
            f"⚠️ Codex 사용 한도에 도달했어요 (재설정: {hhmm}). "
            "다른 백엔드로 대체하지 않으니 한도 재설정 후 다시 시도해 주세요."
        )

    def test_usage_limit_bad_reset_value_drops_suffix(self):
        assert "(재설정" not in usage_limit_message("claude", "not-a-time")

    def test_auth_expired(self):
        assert auth_expired_message("claude", "claude login") == (
            "⚠️ Claude 인증이 만료됐어요. 봇 서버에서 `claude login` 실행 후 봇을 재시작해 주세요."
        )

    def test_runtime_unavailable(self):
        assert runtime_unavailable_message("grok") == (
            "⚠️ Grok 런타임을 시작하지 못했어요. 봇 로그를 확인해 주세요."
        )

    def test_generic_is_legacy_fallback(self):
        assert GENERIC_MESSAGE == "지금 데이터를 못 불러왔어요, 잠시 후 다시 시도할게요."
        assert _CLAUDE_FALLBACK_MESSAGE == GENERIC_MESSAGE
        err = LLMError.generic()
        assert err.kind is LLMErrorKind.GENERIC and err.user_message == GENERIC_MESSAGE

    def test_llm_error_carries_resets_at(self):
        err = LLMError.usage_limit("claude", 1760000000)
        assert err.kind is LLMErrorKind.USAGE_LIMIT
        assert err.resets_at == 1760000000
        assert str(err) == err.user_message

    def test_startup_error_fields(self):
        err = StartupError("원인", "해결 명령")
        assert (err.cause, err.fix, str(err)) == ("원인", "해결 명령", "원인")


class TestIsLlmErrorReply:
    @pytest.mark.parametrize("text", [
        GENERIC_MESSAGE,
        usage_limit_message("claude"),
        usage_limit_message("codex", 1760000000),
        auth_expired_message("grok", ".env의 XAI_API_KEY 확인"),
        runtime_unavailable_message("codex"),
    ])
    def test_error_replies_detected(self, text):
        assert is_llm_error_reply(text) is True

    @pytest.mark.parametrize("text", [
        None,
        "",
        "어젯밤 수면은 7시간으로 양호해요.",
        "분석 결과\n⚠️ Claude 사용 한도에 도달했어요. 다른 백엔드로 대체하지 않으니 한도 재설정 후 다시 시도해 주세요.",
        GENERIC_MESSAGE + " 추가 텍스트",
    ])
    def test_normal_replies_not_detected(self, text):
        assert is_llm_error_reply(text) is False


# ── Claude SDK 신호 분류 (F17) ────────────────────────────────────────


class TestClassifyClaudeMessage:
    @pytest.mark.parametrize("error", ["rate_limit", "billing_error"])
    def test_usage_limit_errors(self, error):
        err = classify_claude_message(AssistantMessage(content=[], model="m", error=error))
        assert err.kind is LLMErrorKind.USAGE_LIMIT
        assert err.user_message == usage_limit_message("claude")

    def test_authentication_failed(self):
        err = classify_claude_message(AssistantMessage(content=[], model="m", error="authentication_failed"))
        assert err.kind is LLMErrorKind.AUTH_EXPIRED
        assert "`claude login`" in err.user_message

    @pytest.mark.parametrize("error", [None, "server_error", "invalid_request", "unknown"])
    def test_other_assistant_messages_not_classified(self, error):
        assert classify_claude_message(AssistantMessage(content=[], model="m", error=error)) is None

    def test_rate_limit_rejected_with_reset(self):
        event = RateLimitEvent(
            rate_limit_info=RateLimitInfo(status="rejected", resets_at=1760000000),
            uuid="u", session_id="s",
        )
        err = classify_claude_message(event)
        assert err.kind is LLMErrorKind.USAGE_LIMIT
        assert err.resets_at == 1760000000
        assert err.user_message == usage_limit_message("claude", 1760000000)

    @pytest.mark.parametrize("info", [
        RateLimitInfo(status="allowed_warning", utilization=0.9),
        RateLimitInfo(status="allowed"),
        RateLimitInfo(status="rejected", overage_status="allowed"),
    ])
    def test_non_blocking_rate_limit_events_skipped(self, info):
        assert classify_claude_message(RateLimitEvent(rate_limit_info=info, uuid="u", session_id="s")) is None

    def test_unrelated_objects(self):
        assert classify_claude_message(object()) is None


# ── 어댑터 오류 경로 (AC-4: 초기자별 표면) ─────────────────────────────


class TestAdapterErrorPaths:
    @pytest.mark.asyncio
    async def test_streaming_turn_usage_limit_message_once_without_raw_text(self):
        """스트리밍 턴(raise_errors=False): 타입드 메시지를 on_text 1회 + 반환, 원시 provider 문자열 미포함."""
        adapter = ClaudeSDKAdapter()
        received = []

        async def on_text(t):
            received.append(t)

        with patch("core.llm.query", side_effect=_rate_limited_stream()):
            result = await adapter.ask_with_context("시스템", "질문", {}, on_text=on_text)

        expected = usage_limit_message("claude")
        assert result == expected
        assert received == ["첫 세그먼트", expected]
        assert received.count(expected) == 1
        assert all("internal" not in t and "API Error" not in t for t in received)

    @pytest.mark.asyncio
    async def test_persistent_turn_auth_expired_drops_session(self, monkeypatch):
        class AuthFailClient:
            def __init__(self, options=None):
                self.options = options

            async def connect(self):
                pass

            async def query(self, prompt):
                pass

            async def receive_response(self):
                yield AssistantMessage(content=[TextBlock(text="Invalid API key")], model="m",
                                       error="authentication_failed")

            async def disconnect(self):
                pass

        monkeypatch.setattr("core.llm.ClaudeSDKClient", AuthFailClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        received = []

        async def on_text(t):
            received.append(t)

        result = await adapter.ask_with_context("SYS", "q", {}, on_text=on_text, thread_id=5)

        expected = auth_expired_message("claude", "claude login")
        assert result == expected
        assert received == [expected]
        assert not adapter.has_session(5)

    @pytest.mark.asyncio
    async def test_mid_stream_error_closes_query_iterator_before_propagating(self):
        """PEP 533: 스트림 중 오류 신호 raise 전에 SDK async generator를 aclose — 오류 메시지 발화보다 먼저 닫힌다."""
        adapter = ClaudeSDKAdapter()
        events = []

        async def fake_query(**kwargs):
            try:
                yield AssistantMessage(content=[TextBlock(text="첫 세그먼트")], model="m")
                yield AssistantMessage(content=[TextBlock(text="raw")], model="m", error="rate_limit")
                yield AssistantMessage(content=[TextBlock(text="도달 안 함")], model="m")
            finally:
                events.append("aclose")

        async def on_text(t):
            events.append(t)

        with patch("core.llm.query", side_effect=fake_query):
            result = await adapter.ask_with_context("시스템", "질문", {}, on_text=on_text)

        expected = usage_limit_message("claude")
        assert result == expected
        assert events == ["첫 세그먼트", "aclose", expected]

        events.clear()
        with patch("core.llm.query", side_effect=fake_query):
            with pytest.raises(LLMError):
                await adapter.ask("시스템", "질문", on_text=on_text)
        assert events == ["첫 세그먼트", "aclose"]

    @pytest.mark.asyncio
    async def test_mid_stream_error_closes_receive_response_before_session_end(self, monkeypatch):
        events = []

        class RateLimitedClient:
            def __init__(self, options=None):
                self.options = options

            async def connect(self):
                pass

            async def query(self, prompt):
                pass

            async def receive_response(self):
                try:
                    yield AssistantMessage(content=[TextBlock(text="raw")], model="m", error="rate_limit")
                    yield AssistantMessage(content=[TextBlock(text="도달 안 함")], model="m")
                finally:
                    events.append("aclose")

            async def disconnect(self):
                events.append("disconnect")

        monkeypatch.setattr("core.llm.ClaudeSDKClient", RateLimitedClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")

        async def on_text(t):
            events.append(t)

        result = await adapter.ask_with_context("SYS", "q", {}, on_text=on_text, thread_id=5)

        expected = usage_limit_message("claude")
        assert result == expected
        assert events == ["aclose", "disconnect", expected]
        assert not adapter.has_session(5)

    @pytest.mark.asyncio
    async def test_mid_stream_error_with_iterator_without_aclose(self):
        """aclose가 없는 fake 이터레이터도 그대로 동작한다(호환)."""
        adapter = ClaudeSDKAdapter()

        class PlainIterator:
            def __init__(self):
                self._items = [AssistantMessage(content=[TextBlock(text="raw")], model="m", error="rate_limit")]

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._items:
                    raise StopAsyncIteration
                return self._items.pop(0)

        with patch("core.llm.query", side_effect=lambda **kwargs: PlainIterator()):
            result = await adapter.ask_with_context("시스템", "질문", {})

        assert result == usage_limit_message("claude")

    @pytest.mark.asyncio
    async def test_ask_raises_typed_error_without_on_text(self):
        adapter = ClaudeSDKAdapter()
        on_text = AsyncMock()

        with patch("core.llm.query", side_effect=_rate_limited_stream()):
            with pytest.raises(LLMError) as exc:
                await adapter.ask("시스템", "질문", on_text=on_text)

        assert exc.value.kind is LLMErrorKind.USAGE_LIMIT
        # 오류 이전 세그먼트만 흘렀고, 오류 메시지는 on_text로 발화되지 않는다.
        assert [c.args[0] for c in on_text.call_args_list] == ["첫 세그먼트"]

    @pytest.mark.asyncio
    async def test_ask_raises_generic_on_exception(self):
        adapter = ClaudeSDKAdapter()
        on_text = AsyncMock()

        def boom(**kwargs):
            raise RuntimeError("provider internal https://internal.api/secret")

        with patch("core.llm.query", side_effect=boom):
            with pytest.raises(LLMError) as exc:
                await adapter.ask("시스템", "질문", on_text=on_text)

        assert exc.value.kind is LLMErrorKind.GENERIC
        assert exc.value.user_message == GENERIC_MESSAGE
        assert "internal.api" not in exc.value.user_message
        on_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_ask_success_returns_text(self):
        adapter = ClaudeSDKAdapter()

        async def ok_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="요약")], model="m")

        with patch("core.llm.query", side_effect=ok_query):
            assert await adapter.ask("시스템", "질문") == "요약"

    @pytest.mark.asyncio
    async def test_ask_routes_through_call_claude_with_raise_errors(self):
        adapter = ClaudeSDKAdapter()
        with patch.object(adapter, "_call_claude", new_callable=AsyncMock, return_value="ok") as mock_call:
            await adapter.ask("시스템", "질문")
        assert mock_call.call_args.kwargs["raise_errors"] is True

    @pytest.mark.asyncio
    async def test_handle_health_query_error_reply_is_not_turn_success(self):
        """인터랙티브 턴이 오류 안내를 반환하면 학습 루프 성공(turn_ok)으로 보지 않는다."""
        from bot.main import handle_health_query

        thread = MagicMock(spec=discord.Thread)

        async def fake_history(limit=None, oldest_first=True):
            for _ in ():
                yield _

        class _Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        thread.history = fake_history
        thread.send = AsyncMock()
        thread.typing = MagicMock(return_value=_Typing())
        thread.id = 8888
        msg = MagicMock(spec=discord.Message)
        msg.content = "러닝 분석"
        msg.author = MagicMock()
        msg.author.bot = False
        msg.channel = thread
        msg.id = 1
        msg.created_at = None

        error_reply = usage_limit_message("claude", 1760000000)

        async def ask_with_context(*args, on_text=None, counter=None, **kwargs):
            counter[0] = 9
            await on_text(error_reply)
            return error_reply

        mock_llm = MagicMock()
        mock_llm.ask_with_context = ask_with_context
        captured = {}

        def fake_should_propose(mode, interactive, tool_count, success, explicit_request=False, threshold=5):
            captured["success"] = success
            return False

        with patch("bot.main.llm", mock_llm), \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.detect_skill_writes", return_value=[]), \
             patch("bot.main.should_propose_skill", side_effect=fake_should_propose), \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_sess.is_expired.return_value = False
            mock_comp.compress = AsyncMock(return_value=[])
            await handle_health_query(msg, "러닝 분석")

        assert captured["success"] is False
        sent = [c.args[0] for c in thread.send.call_args_list]
        assert error_reply in sent


# ── 계약 표면 + 주입 seam + 실제 런타임 가드 ─────────────────────────────


class TestContractAndSeams:
    def test_abstract_contract_methods(self):
        assert LLMAdapter.__abstractmethods__ == {
            "start", "ask", "ask_with_context", "interrupt_session",
            "end_session", "close_all", "session_ids", "has_session",
        }
        assert ClaudeSDKAdapter.backend_id == "claude"

    def test_ask_with_context_accepts_image_paths(self):
        assert "image_paths" in inspect.signature(ClaudeSDKAdapter.ask_with_context).parameters

    def test_readonly_servers_default_to_mcp_servers(self):
        servers = {"garmin": {"type": "sdk", "name": "garmin", "instance": None}}
        adapter = ClaudeSDKAdapter(mcp_servers=servers)
        assert adapter.readonly_mcp_servers is adapter.mcp_servers
        ro = {"body_metrics": {"type": "sdk", "name": "body_metrics", "instance": None}}
        assert ClaudeSDKAdapter(mcp_servers=servers, readonly_mcp_servers=ro).readonly_mcp_servers is ro
        with pytest.raises(TypeError, match="readonly_mcp_servers must be a dict"):
            ClaudeSDKAdapter(readonly_mcp_servers=[1])

    @pytest.mark.asyncio
    async def test_query_fn_seam_is_used(self):
        calls = []

        async def injected(**kwargs):
            calls.append(kwargs)
            yield AssistantMessage(content=[TextBlock(text="주입")], model="m")

        adapter = ClaudeSDKAdapter(query_fn=injected)
        assert await adapter._call_claude("시스템", "질문") == "주입"
        assert calls[0]["prompt"] == "질문"

    @pytest.mark.asyncio
    async def test_client_factory_seam_is_used(self):
        created = []

        class Client:
            def __init__(self, options=None):
                created.append(options)

            async def connect(self):
                pass

            async def query(self, prompt):
                pass

            async def receive_response(self):
                yield AssistantMessage(content=[TextBlock(text="팩토리")], model="m")

            async def disconnect(self):
                pass

        adapter = ClaudeSDKAdapter(cwd="/proj", client_factory=Client)
        assert await adapter.ask_with_context("SYS", "q", {}, thread_id=1) == "팩토리"
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_real_query_forbidden_in_tests(self, monkeypatch):
        monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")
        adapter = ClaudeSDKAdapter()
        with pytest.raises(RealRuntimeForbidden, match="real runtime forbidden in tests"):
            await adapter._call_claude("시스템", "질문")
        with pytest.raises(RealRuntimeForbidden):
            await adapter.ask("시스템", "질문")

    @pytest.mark.asyncio
    async def test_real_client_forbidden_in_tests(self, monkeypatch):
        monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")
        adapter = ClaudeSDKAdapter(cwd="/proj")
        with pytest.raises(RuntimeError, match="real runtime forbidden in tests"):
            await adapter.ask_with_context("SYS", "q", {}, thread_id=3)
        assert not adapter.has_session(3)

    @pytest.mark.asyncio
    async def test_patched_query_passes_guard(self, monkeypatch):
        monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")

        async def fake_query(**kwargs):
            yield ToolUseBlock(id="t", name="Read", input={})  # 비 AssistantMessage → 스킵

        with patch("core.llm.query", side_effect=fake_query):
            assert await ClaudeSDKAdapter()._call_claude("시스템", "질문") == ""
