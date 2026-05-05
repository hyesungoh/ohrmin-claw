"""재현 테스트: add_memory MCP 툴이 용량 초과 시 LLM 통합 로직을 우회함."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.memory import MemoryManager, MAX_MEMORY_CHARS, ENTRY_DELIMITER
from core.memory_tools import create_memory_mcp_server, TOOL_REGISTRY as MEM_TOOL_REGISTRY


def _parse_result(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def memory_mgr(tmp_path):
    return MemoryManager(str(tmp_path))


@pytest.fixture
def memory_server(memory_mgr):
    return create_memory_mcp_server(memory_mgr)


@pytest.fixture
def memory_tools(memory_mgr, memory_server):
    return MEM_TOOL_REGISTRY.copy()


class TestAddMemoryOverflowBypassesMcpTool:
    """add_memory MCP 툴은 append_memory를 직접 호출 → LLM 통합 우회 버그 재현."""

    @pytest.mark.asyncio
    async def test_add_memory_mcp_fails_silently_on_overflow(self, memory_mgr, memory_tools):
        """꽉 찬 메모리에 add_memory 호출 시 success=False 리턴, 항목 무시됨."""
        # MAX_MEMORY_CHARS만큼 채움
        filler = "기존기억" * (MAX_MEMORY_CHARS // 4)
        memory_mgr.write_memory(filler[:MAX_MEMORY_CHARS - 5])

        result = await memory_tools["add_memory"].handler(
            {"target": "memory", "content": "새로운 중요한 기억"}
        )
        data = _parse_result(result)

        # 버그: success=False이고 항목은 그냥 무시됨 (LLM 통합 없음)
        assert data["success"] is False
        assert "entries" in data  # 통합 기회 정보는 있지만 사용 안 됨
        assert "새로운 중요한 기억" not in memory_mgr.read_memory()

    @pytest.mark.asyncio
    async def test_add_memory_mcp_has_no_llm_parameter(self, memory_mgr, memory_tools):
        """add_memory 툴 핸들러는 llm 파라미터가 없어서 통합 요청 자체가 불가능."""
        # memory_tools["add_memory"]의 핸들러 시그니처에 llm이 없음을 확인
        import inspect
        handler = memory_tools["add_memory"].handler
        sig = inspect.signature(handler)
        # 핸들러는 args dict 하나만 받음 — llm을 받을 방법이 없음
        assert len(sig.parameters) == 1

    def test_append_memory_direct_fails_on_overflow_no_consolidation(self, memory_mgr):
        """append_memory 직접 호출도 동일하게 통합 없이 실패."""
        filler = "X" * (MAX_MEMORY_CHARS - 5)
        memory_mgr.write_memory(filler)

        result = memory_mgr.append_memory("새 항목")

        assert result["success"] is False
        assert result["current_chars"] >= MAX_MEMORY_CHARS - 5
        assert result["limit"] == MAX_MEMORY_CHARS
        assert "entries" in result
        # 항목은 파일에 저장되지 않음
        assert "새 항목" not in memory_mgr.read_memory()


class TestExtractAndSaveDoesConsolidate:
    """extract_and_save 경로는 _save_or_consolidate를 통해 LLM 통합이 작동함 (대조군)."""

    @pytest.mark.asyncio
    async def test_auto_extraction_path_triggers_consolidation(self, tmp_path):
        """extract_and_save 경로에서 용량 초과 시 LLM 통합이 호출됨 — 정상 작동."""
        mgr = MemoryManager(str(tmp_path))
        filler = "C" * (MAX_MEMORY_CHARS - 5)
        mgr.write_memory(filler)

        llm = AsyncMock()
        llm.ask.side_effect = [
            "MEMORY: 새로운 중요한 기억",
            f"통합된기억A{ENTRY_DELIMITER}새로운 중요한 기억",
        ]

        await mgr.extract_and_save(llm, [{"role": "user", "content": "test"}])

        # LLM이 2번 호출됨: 1번째=추출, 2번째=통합
        assert llm.ask.call_count == 2
        content = mgr.read_memory()
        # 통합 결과가 저장됨
        assert "통합된기억A" in content or "새로운 중요한 기억" in content

    @pytest.mark.asyncio
    async def test_mcp_path_never_calls_llm_on_overflow(self, memory_mgr, memory_tools):
        """MCP add_memory 경로는 용량 초과 시 LLM을 전혀 호출하지 않음 — 버그 확인."""
        filler = "X" * (MAX_MEMORY_CHARS - 5)
        memory_mgr.write_memory(filler)

        llm_mock = MagicMock()
        llm_mock.ask = AsyncMock(return_value="통합 결과")

        # MCP 툴은 llm을 받지 않으므로 통합 불가
        result = await memory_tools["add_memory"].handler(
            {"target": "memory", "content": "중요한 새 기억"}
        )
        data = _parse_result(result)

        # LLM은 호출되지 않음 (툴이 llm 접근 자체가 없음)
        llm_mock.ask.assert_not_called()
        assert data["success"] is False
