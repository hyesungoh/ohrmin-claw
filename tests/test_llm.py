"""LLM 어댑터 레이어 테스트."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from claude_agent_sdk.types import AssistantMessage, SystemMessage, ResultMessage, TextBlock, ToolUseBlock, RateLimitEvent, RateLimitInfo

from core.llm import LLMAdapter, ClaudeSDKAdapter, create_llm_adapter


class TestLLMAdapterInterface:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            LLMAdapter()

    def test_claude_sdk_adapter_is_llm_adapter(self):
        adapter = ClaudeSDKAdapter()
        assert isinstance(adapter, LLMAdapter)


class TestClaudeSDKAdapter:
    @pytest.mark.asyncio
    async def test_ask(self):
        adapter = ClaudeSDKAdapter()
        with patch.object(adapter, '_call_claude', new_callable=AsyncMock, return_value="테스트 응답입니다."):
            result = await adapter.ask(
                system_prompt="당신은 건강 전문가입니다.",
                user_message="오늘 컨디션 어때?",
            )
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_ask_with_context(self):
        adapter = ClaudeSDKAdapter()
        context = {"avg_sleep": 7.2, "rhr": 58}
        with patch.object(adapter, '_call_claude', new_callable=AsyncMock, return_value="수면 데이터 분석 결과입니다."):
            result = await adapter.ask_with_context(
                system_prompt="당신은 건강 전문가입니다.",
                user_message="수면 분석해줘",
                context=context,
            )
            assert isinstance(result, str)


class TestMaxTurns:
    """_call_claude가 max_turns=15로 SDK를 호출하는지 검증."""

    @pytest.mark.asyncio
    async def test_max_turns_is_15(self):
        """query() 호출 시 max_turns가 15여야 함."""
        adapter = ClaudeSDKAdapter()

        async def fake_query(**kwargs):
            yield AssistantMessage(
                content=[TextBlock(text="응답")],
                model="claude-sonnet-4-20250514",
            )

        with patch("core.llm.query", side_effect=fake_query) as mock_query:
            await adapter._call_claude("시스템", "질문")

        call_kwargs = mock_query.call_args
        options = call_kwargs.kwargs.get("options") or call_kwargs[1].get("options")
        assert options.max_turns == 15


class TestCallClaudeMessageParsing:
    """_call_claude가 SDK 메시지 타입을 올바르게 파싱하는지 검증."""

    async def _fake_query(self, messages):
        """비동기 제너레이터로 SDK 메시지 시퀀스를 시뮬레이션."""
        for msg in messages:
            yield msg

    @pytest.mark.asyncio
    async def test_extracts_text_from_assistant_message(self):
        """AssistantMessage의 TextBlock에서 텍스트를 추출해야 함."""
        messages = [
            SystemMessage(subtype="init", data={}),
            AssistantMessage(
                content=[TextBlock(text="건강 분석 결과입니다.")],
                model="claude-sonnet-4-20250514",
            ),
            ResultMessage(
                subtype="result", duration_ms=100, duration_api_ms=80,
                is_error=False, num_turns=1, session_id="test",
                result="건강 분석 결과입니다.",
            ),
        ]
        adapter = ClaudeSDKAdapter()
        with patch("core.llm.query", return_value=self._fake_query(messages)):
            result = await adapter._call_claude("시스템", "질문")
        assert result == "건강 분석 결과입니다."

    @pytest.mark.asyncio
    async def test_handles_system_message_without_crash(self):
        """SystemMessage가 와도 에러 없이 건너뛰어야 함."""
        messages = [
            SystemMessage(subtype="init", data={}),
            SystemMessage(subtype="status", data={"info": "processing"}),
            AssistantMessage(
                content=[TextBlock(text="응답")],
                model="claude-sonnet-4-20250514",
            ),
            ResultMessage(
                subtype="result", duration_ms=50, duration_api_ms=40,
                is_error=False, num_turns=1, session_id="test",
            ),
        ]
        adapter = ClaudeSDKAdapter()
        with patch("core.llm.query", return_value=self._fake_query(messages)):
            result = await adapter._call_claude("시스템", "질문")
        assert result == "응답"

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_text(self):
        """텍스트 블록이 없으면 빈 문자열을 반환해야 함."""
        messages = [
            SystemMessage(subtype="init", data={}),
            ResultMessage(
                subtype="result", duration_ms=50, duration_api_ms=40,
                is_error=False, num_turns=1, session_id="test",
            ),
        ]
        adapter = ClaudeSDKAdapter()
        with patch("core.llm.query", return_value=self._fake_query(messages)):
            result = await adapter._call_claude("시스템", "질문")
        assert result == ""

    @pytest.mark.asyncio
    async def test_concatenates_multiple_text_blocks(self):
        """여러 AssistantMessage의 텍스트를 합쳐야 함."""
        messages = [
            AssistantMessage(
                content=[TextBlock(text="첫 번째 응답")],
                model="claude-sonnet-4-20250514",
            ),
            AssistantMessage(
                content=[TextBlock(text="두 번째 응답")],
                model="claude-sonnet-4-20250514",
            ),
            ResultMessage(
                subtype="result", duration_ms=50, duration_api_ms=40,
                is_error=False, num_turns=1, session_id="test",
            ),
        ]
        adapter = ClaudeSDKAdapter()
        with patch("core.llm.query", return_value=self._fake_query(messages)):
            result = await adapter._call_claude("시스템", "질문")
        assert result == "첫 번째 응답\n두 번째 응답"

    @pytest.mark.asyncio
    async def test_skips_rate_limit_event(self):
        """RateLimitEvent가 와도 무시하고 후속 텍스트를 수집해야 함."""
        async def _query_with_rate_limit(**kwargs):
            yield AssistantMessage(
                content=[TextBlock(text="첫 응답")],
                model="claude-sonnet-4-20250514",
            )
            yield RateLimitEvent(
                rate_limit_info=RateLimitInfo(
                    status="allowed_warning",
                    rate_limit_type="token",
                    utilization=0.85,
                ),
                uuid="test-uuid",
                session_id="test-session",
            )
            yield AssistantMessage(
                content=[TextBlock(text="후속 응답")],
                model="claude-sonnet-4-20250514",
            )

        adapter = ClaudeSDKAdapter()
        with patch("core.llm.query", side_effect=_query_with_rate_limit):
            result = await adapter._call_claude("시스템", "질문")
        assert result == "첫 응답\n후속 응답"


class TestAskWithContextHistory:
    """ask_with_context에 history를 전달하면 대화 이력이 프롬프트에 포함되어야 함."""

    @pytest.mark.asyncio
    async def test_history_included_in_prompt(self):
        """history가 전달되면 _call_claude에 이전 대화 내용이 포함되어야 함."""
        adapter = ClaudeSDKAdapter()
        history = [
            {"role": "user", "content": "내 체중 알려줘"},
            {"role": "assistant", "content": "최근 체중은 72kg입니다."},
        ]
        called_with_message = None

        async def capture_call(system_prompt, user_message, on_text=None, **kwargs):
            nonlocal called_with_message
            called_with_message = user_message
            return "후속 응답"

        adapter._call_claude = capture_call
        result = await adapter.ask_with_context(
            system_prompt="시스템",
            user_message="그럼 지난달 대비 변화는?",
            context={"weight": 72},
            history=history,
        )
        assert result == "후속 응답"
        assert "내 체중 알려줘" in called_with_message
        assert "최근 체중은 72kg입니다." in called_with_message
        assert "그럼 지난달 대비 변화는?" in called_with_message

    @pytest.mark.asyncio
    async def test_no_history_works_as_before(self):
        """history 없이 호출하면 기존 동작과 동일해야 함."""
        adapter = ClaudeSDKAdapter()
        called_with_message = None

        async def capture_call(system_prompt, user_message, on_text=None, **kwargs):
            nonlocal called_with_message
            called_with_message = user_message
            return "응답"

        adapter._call_claude = capture_call
        result = await adapter.ask_with_context(
            system_prompt="시스템",
            user_message="수면 분석해줘",
            context={"sleep": 7},
        )
        assert result == "응답"
        assert "수면 분석해줘" in called_with_message
        # history 관련 섹션이 없어야 함
        assert "대화 이력" not in called_with_message

    @pytest.mark.asyncio
    async def test_empty_history_same_as_no_history(self):
        """빈 history 리스트는 history 없는 것과 동일해야 함."""
        adapter = ClaudeSDKAdapter()
        called_with_message = None

        async def capture_call(system_prompt, user_message, on_text=None, **kwargs):
            nonlocal called_with_message
            called_with_message = user_message
            return "응답"

        adapter._call_claude = capture_call
        await adapter.ask_with_context(
            system_prompt="시스템",
            user_message="질문",
            context={},
            history=[],
        )
        assert "대화 이력" not in called_with_message


class TestStreamingCallback:
    """on_text 콜백으로 TextBlock을 즉시 전달하는지 검증."""

    @pytest.mark.asyncio
    async def test_on_text_called_for_each_text_block(self):
        """각 TextBlock마다 on_text 콜백이 호출되어야 함."""
        async def fake_query(**kwargs):
            yield AssistantMessage(
                content=[TextBlock(text="확인해보겠습니다.")],
                model="claude-sonnet-4-20250514",
            )
            yield AssistantMessage(
                content=[TextBlock(text="분석 결과입니다.")],
                model="claude-sonnet-4-20250514",
            )

        adapter = ClaudeSDKAdapter()
        received = []

        async def on_text(text):
            received.append(text)

        with patch("core.llm.query", side_effect=fake_query):
            await adapter._call_claude("시스템", "질문", on_text=on_text)

        assert received == ["확인해보겠습니다.", "분석 결과입니다."]

    @pytest.mark.asyncio
    async def test_on_text_not_called_for_tool_use(self):
        """ToolUseBlock에 대해서는 on_text가 호출되지 않아야 함."""
        async def fake_query(**kwargs):
            yield AssistantMessage(
                content=[
                    TextBlock(text="데이터를 확인합니다."),
                    ToolUseBlock(id="tool_1", name="read_file", input={"path": "data.csv"}),
                ],
                model="claude-sonnet-4-20250514",
            )
            yield AssistantMessage(
                content=[TextBlock(text="결과입니다.")],
                model="claude-sonnet-4-20250514",
            )

        adapter = ClaudeSDKAdapter()
        received = []

        async def on_text(text):
            received.append(text)

        with patch("core.llm.query", side_effect=fake_query):
            await adapter._call_claude("시스템", "질문", on_text=on_text)

        assert received == ["데이터를 확인합니다.", "결과입니다."]

    @pytest.mark.asyncio
    async def test_no_callback_still_returns_full_text(self):
        """on_text 없이 호출해도 전체 텍스트를 반환해야 함 (하위 호환)."""
        async def fake_query(**kwargs):
            yield AssistantMessage(
                content=[TextBlock(text="첫 번째")],
                model="claude-sonnet-4-20250514",
            )
            yield AssistantMessage(
                content=[TextBlock(text="두 번째")],
                model="claude-sonnet-4-20250514",
            )

        adapter = ClaudeSDKAdapter()
        with patch("core.llm.query", side_effect=fake_query):
            result = await adapter._call_claude("시스템", "질문")

        assert result == "첫 번째\n두 번째"

    @pytest.mark.asyncio
    async def test_ask_with_context_passes_on_text(self):
        """ask_with_context가 on_text 콜백을 _call_claude에 전달해야 함."""
        adapter = ClaudeSDKAdapter()
        received_on_text = None

        original_call = adapter._call_claude

        async def capture_call(system_prompt, user_message, on_text=None, **kwargs):
            nonlocal received_on_text
            received_on_text = on_text
            return "응답"

        adapter._call_claude = capture_call

        async def my_callback(text):
            pass

        await adapter.ask_with_context(
            system_prompt="시스템",
            user_message="질문",
            context={},
            on_text=my_callback,
        )
        assert received_on_text is my_callback


class TestCreateLLMAdapter:
    def test_create_claude_adapter(self):
        adapter = create_llm_adapter("claude")
        assert isinstance(adapter, ClaudeSDKAdapter)

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError):
            create_llm_adapter("unknown")


# ── mock_async_gen 헬퍼 ──────────────────────────────

async def _async_gen(items):
    for item in items:
        yield item


def mock_async_gen(items):
    return _async_gen(items)


# ── Task 3: MCP servers passthrough ──────────────────

class TestMcpServersPassthrough:
    """ClaudeSDKAdapter가 mcp_servers를 dict[str, McpSdkServerConfig] 형태로 전달하는지 확인."""

    @pytest.mark.asyncio
    async def test_mcp_servers_passed_as_dict(self):
        """mcp_servers는 dict[str, config] 형태로 ClaudeAgentOptions에 전달되어야 함."""
        mock_servers = {
            "garmin": {"type": "sdk", "name": "garmin", "instance": None},
            "body_metrics": {"type": "sdk", "name": "body_metrics", "instance": None},
        }
        adapter = ClaudeSDKAdapter(mcp_servers=mock_servers)

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter._call_claude("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert isinstance(options.mcp_servers, dict), \
                f"mcp_servers must be dict, got {type(options.mcp_servers)}"
            assert "garmin" in options.mcp_servers
            assert "body_metrics" in options.mcp_servers

    @pytest.mark.asyncio
    async def test_mcp_servers_rejects_list(self):
        """mcp_servers에 리스트를 전달하면 TypeError가 발생해야 함."""
        mock_server = {"type": "sdk", "name": "garmin", "instance": None}
        with pytest.raises(TypeError, match="mcp_servers must be a dict"):
            ClaudeSDKAdapter(mcp_servers=[mock_server])

    @pytest.mark.asyncio
    async def test_no_mcp_servers_by_default(self):
        adapter = ClaudeSDKAdapter()

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter._call_claude("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert not hasattr(options, 'mcp_servers') or not options.mcp_servers

    @pytest.mark.asyncio
    async def test_create_llm_adapter_with_mcp_servers(self):
        mock_servers = {
            "garmin": {"type": "sdk", "name": "garmin", "instance": None},
        }
        adapter = create_llm_adapter("claude", mcp_servers=mock_servers)
        assert isinstance(adapter, ClaudeSDKAdapter)
        assert isinstance(adapter.mcp_servers, dict)
        assert "garmin" in adapter.mcp_servers


# ── Task 12: Extended options (cwd, setting_sources, allowed_tools) ──

class TestClaudeAgentOptionsExtended:

    @pytest.mark.asyncio
    async def test_cwd_passed_to_options(self):
        adapter = ClaudeSDKAdapter(cwd="/path/to/project")

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter._call_claude("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert options.cwd == "/path/to/project"

    @pytest.mark.asyncio
    async def test_setting_sources_includes_user_and_project(self):
        adapter = ClaudeSDKAdapter(cwd="/path/to/project")

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter._call_claude("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert "user" in options.setting_sources
            assert "project" in options.setting_sources

    @pytest.mark.asyncio
    async def test_allowed_tools_includes_skill(self):
        adapter = ClaudeSDKAdapter(cwd="/path/to/project")

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter._call_claude("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert "Skill" in options.allowed_tools

    @pytest.mark.asyncio
    async def test_allowed_tools_includes_web_tools(self):
        """A1: cwd 활성 시 조립된 allowed_tools에 WebSearch/WebFetch가 포함되어야 함."""
        adapter = ClaudeSDKAdapter(cwd="/path/to/project")

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter._call_claude("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert "WebSearch" in options.allowed_tools
            assert "WebFetch" in options.allowed_tools

    @pytest.mark.asyncio
    async def test_no_cwd_by_default(self):
        adapter = ClaudeSDKAdapter()

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter._call_claude("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert not hasattr(options, 'cwd') or options.cwd is None
