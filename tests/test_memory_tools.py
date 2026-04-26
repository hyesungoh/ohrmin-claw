"""Memory MCP tool 테스트."""
import json
from unittest.mock import MagicMock

import pytest

from core.memory import MemoryManager, ENTRY_DELIMITER
from core.memory_tools import create_memory_mcp_server, TOOL_REGISTRY as MEM_TOOL_REGISTRY


@pytest.fixture
def memory_mgr(tmp_path):
    return MemoryManager(str(tmp_path))


@pytest.fixture
def memory_server(memory_mgr):
    return create_memory_mcp_server(memory_mgr)


@pytest.fixture
def memory_tools(memory_mgr, memory_server):
    return MEM_TOOL_REGISTRY.copy()


def _parse_result(result):
    return json.loads(result["content"][0]["text"])


class TestMemoryMcpServer:
    def test_server_created(self, memory_server):
        assert memory_server is not None
        assert memory_server["name"] == "memory"

    def test_has_required_tools(self, memory_tools):
        assert "list_memory" in memory_tools
        assert "add_memory" in memory_tools
        assert "replace_memory" in memory_tools
        assert "remove_memory" in memory_tools


@pytest.mark.asyncio
class TestListMemoryTool:
    async def test_list_empty(self, memory_tools):
        result = await memory_tools["list_memory"].handler({"target": "memory"})
        data = _parse_result(result)
        assert data["entries"] == []

    async def test_list_with_entries(self, memory_mgr, memory_tools):
        memory_mgr.write_memory(f"기억 A{ENTRY_DELIMITER}기억 B")
        result = await memory_tools["list_memory"].handler({"target": "memory"})
        data = _parse_result(result)
        assert len(data["entries"]) == 2
        assert data["entries"][0]["content"] == "기억 A"

    async def test_list_shows_usage(self, memory_mgr, memory_tools):
        memory_mgr.write_memory("기억 A")
        result = await memory_tools["list_memory"].handler({"target": "memory"})
        data = _parse_result(result)
        assert "current_chars" in data
        assert "limit" in data


@pytest.mark.asyncio
class TestAddMemoryTool:
    async def test_add_success(self, memory_mgr, memory_tools):
        result = await memory_tools["add_memory"].handler(
            {"target": "memory", "content": "새 기억"}
        )
        data = _parse_result(result)
        assert data["success"] is True
        assert "새 기억" in memory_mgr.read_memory()

    async def test_add_overflow_returns_entries(self, memory_mgr, memory_tools):
        from core.memory import MAX_MEMORY_CHARS
        memory_mgr.write_memory("A" * (MAX_MEMORY_CHARS - 10))
        result = await memory_tools["add_memory"].handler(
            {"target": "memory", "content": "B" * 100}
        )
        data = _parse_result(result)
        assert data["success"] is False
        assert "entries" in data
        assert "limit" in data


@pytest.mark.asyncio
class TestReplaceMemoryTool:
    async def test_replace_success(self, memory_mgr, memory_tools):
        memory_mgr.write_memory(f"오래된 것{ENTRY_DELIMITER}유지할 것")
        result = await memory_tools["replace_memory"].handler(
            {"target": "memory", "index": 0, "content": "새로운 것"}
        )
        data = _parse_result(result)
        assert data["success"] is True
        entries = memory_mgr.list_entries("memory")
        assert entries[0]["content"] == "새로운 것"

    async def test_replace_invalid_index(self, memory_mgr, memory_tools):
        memory_mgr.write_memory("하나뿐")
        result = await memory_tools["replace_memory"].handler(
            {"target": "memory", "index": 5, "content": "새 내용"}
        )
        data = _parse_result(result)
        assert data["success"] is False


@pytest.mark.asyncio
class TestRemoveMemoryTool:
    async def test_remove_success(self, memory_mgr, memory_tools):
        memory_mgr.write_memory(f"삭제할 것{ENTRY_DELIMITER}유지할 것")
        result = await memory_tools["remove_memory"].handler(
            {"target": "memory", "index": 0}
        )
        data = _parse_result(result)
        assert data["success"] is True
        entries = memory_mgr.list_entries("memory")
        assert len(entries) == 1
        assert entries[0]["content"] == "유지할 것"

    async def test_remove_invalid_index(self, memory_mgr, memory_tools):
        memory_mgr.write_memory("하나뿐")
        result = await memory_tools["remove_memory"].handler(
            {"target": "memory", "index": 99}
        )
        data = _parse_result(result)
        assert data["success"] is False
