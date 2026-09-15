"""F21 — 유틸 llm.ask 실패가 memory.md/user.md를 덮어쓰거나 압축 요약으로 끼어들지 않는지 (AC-4 ask 경로).

기존 버그: ask 실패 시 폴백 문자열이 '통합 결과'로 반환되어 _write_raw가 memory.md 전체를 덮어썼다.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk.types import AssistantMessage, TextBlock

from core.context_compressor import ContextCompressor
from core.llm import ClaudeSDKAdapter
from core.llm_errors import GENERIC_MESSAGE, LLMError, LLMErrorKind
from core.memory import ENTRY_DELIMITER, MAX_MEMORY_CHARS, MAX_USER_CHARS, MemoryManager


def _fill(mgr: MemoryManager, tmp_path):
    """memory.md·user.md를 용량 한도 직전까지 채워 다음 추가가 통합(ask)을 타게 한다."""
    mgr.write_memory(f"기억 A{ENTRY_DELIMITER}기억 B{ENTRY_DELIMITER}" + "M" * (MAX_MEMORY_CHARS - 20))
    mgr.write_user(f"선호 A{ENTRY_DELIMITER}" + "U" * (MAX_USER_CHARS - 10))
    return (tmp_path / "memory.md").read_bytes(), (tmp_path / "user.md").read_bytes()


def _llm_raising(error: Exception):
    llm = MagicMock()
    llm.ask = AsyncMock(side_effect=error)
    return llm


class TestMemoryUnchangedOnLLMError:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [
        LLMError.generic(),
        LLMError.usage_limit("claude", 1760000000),
        LLMError.auth_expired("claude", "claude login"),
    ])
    async def test_extraction_failure_keeps_files(self, tmp_path, error):
        mgr = MemoryManager(str(tmp_path))
        before = _fill(mgr, tmp_path)

        result = await mgr.extract_and_save(_llm_raising(error), [{"role": "user", "content": "러닝 5km"}])

        assert result is None
        assert ((tmp_path / "memory.md").read_bytes(), (tmp_path / "user.md").read_bytes()) == before

    @pytest.mark.asyncio
    async def test_consolidation_failure_keeps_memory_bytes(self, tmp_path):
        mgr = MemoryManager(str(tmp_path))
        before_memory, _ = _fill(mgr, tmp_path)

        result = await mgr._save_or_consolidate(_llm_raising(LLMError.generic()), "memory", "새 기억")

        assert result == {"success": False, "error": "LLM 통합 실패"}
        assert (tmp_path / "memory.md").read_bytes() == before_memory


class TestRealAdapterF21:
    """실제 ClaudeSDKAdapter(ask → raise_errors=True) 경유 — 폴백 문자열이 파일에 쓰이지 않는다."""

    @pytest.mark.asyncio
    async def test_consolidation_query_failure_does_not_overwrite_memory(self, tmp_path):
        mgr = MemoryManager(str(tmp_path))
        before_memory, before_user = _fill(mgr, tmp_path)
        calls = []

        def fake_query(**kwargs):
            calls.append(kwargs["prompt"])
            if len(calls) == 1:
                async def extraction():
                    yield AssistantMessage(content=[TextBlock(text="MEMORY: 매일 5km 러닝")], model="m")
                return extraction()
            raise RuntimeError("provider down")  # 통합 호출 실패

        adapter = ClaudeSDKAdapter()
        with patch("core.llm.query", side_effect=fake_query):
            await mgr.extract_and_save(adapter, [{"role": "user", "content": "매일 5km 뛰어"}])

        assert len(calls) == 2  # 추출 1회 + 통합 1회(실패)
        memory_after = (tmp_path / "memory.md").read_bytes()
        assert memory_after == before_memory
        assert GENERIC_MESSAGE.encode() not in memory_after
        assert (tmp_path / "user.md").read_bytes() == before_user

    @pytest.mark.asyncio
    async def test_usage_limit_during_consolidation_does_not_overwrite_memory(self, tmp_path):
        mgr = MemoryManager(str(tmp_path))
        before_memory, _ = _fill(mgr, tmp_path)

        async def limited(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="usage limit reached")], model="m", error="rate_limit")

        adapter = ClaudeSDKAdapter()
        with patch("core.llm.query", side_effect=limited):
            result = await mgr._save_or_consolidate(adapter, "memory", "새 기억")

        assert result["success"] is False
        assert (tmp_path / "memory.md").read_bytes() == before_memory

    @pytest.mark.asyncio
    async def test_add_memory_tool_overflow_with_failing_llm_keeps_memory(self, tmp_path):
        """add_memory MCP 도구의 중첩 ask(F6)도 실패 시 memory.md 불변."""
        import json
        from core.memory_tools import TOOL_REGISTRY, create_memory_mcp_server

        mgr = MemoryManager(str(tmp_path))
        before_memory, _ = _fill(mgr, tmp_path)
        create_memory_mcp_server(mgr)
        mgr.llm = ClaudeSDKAdapter()

        def boom(**kwargs):
            raise RuntimeError("down")

        with patch("core.llm.query", side_effect=boom):
            result = await TOOL_REGISTRY["add_memory"].handler({"target": "memory", "content": "새 기억"})

        assert json.loads(result["content"][0]["text"])["success"] is False
        assert (tmp_path / "memory.md").read_bytes() == before_memory


class TestCompressorOnLLMError:
    def _history(self):
        return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"메시지 {i}"} for i in range(25)]

    @pytest.mark.asyncio
    async def test_llm_error_returns_original_history(self):
        history = self._history()
        compressor = ContextCompressor()

        result = await compressor.compress(history, _llm_raising(LLMError.usage_limit("claude")))

        assert result == history
        assert all(m["role"] != "system" for m in result)

    @pytest.mark.asyncio
    async def test_real_adapter_failure_not_inserted_as_summary(self):
        history = self._history()
        compressor = ContextCompressor()

        def boom(**kwargs):
            raise RuntimeError("provider down")

        with patch("core.llm.query", side_effect=boom):
            result = await compressor.compress(history, ClaudeSDKAdapter())

        assert result == history
        assert not any(GENERIC_MESSAGE in m["content"] for m in result)

    @pytest.mark.asyncio
    async def test_success_still_summarizes(self):
        history = self._history()
        llm = MagicMock()
        llm.ask = AsyncMock(return_value="요약")

        result = await ContextCompressor().compress(history, llm)

        assert result[1] == {"role": "system", "content": "[이전 대화 요약]\n요약"}
        assert len(result) == 1 + 1 + 6

    def test_kind_on_generic(self):
        assert LLMError.generic().kind is LLMErrorKind.GENERIC
