"""세션 검색 MCP tool 테스트 — search 도구가 SessionIndex 결과를 반환하는지."""
import json

import pytest

from core.session_index import SessionIndex
from core.session_search_tools import create_session_search_mcp_server, TOOL_REGISTRY


@pytest.fixture
def index(tmp_path):
    idx = SessionIndex(str(tmp_path / "session_index.db"))
    idx.index_message("t1", "ts1", "user", "지난주 수면 효율이 어땠는지 궁금해")
    idx.index_message("t1", "ts2", "assistant", "지난주 평균 수면 효율은 88% 였습니다")
    return idx


def _extract(result: dict):
    """MCP tool 응답에서 JSON payload를 파싱."""
    return json.loads(result["content"][0]["text"])


class TestSessionSearchTool:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, index):
        create_session_search_mcp_server(index)
        search_tool = TOOL_REGISTRY["search"]
        result = await search_tool.handler({"query": "수면 효율"})
        payload = _extract(result)
        assert isinstance(payload, list)
        assert len(payload) >= 1
        assert any("수면" in row["content"] for row in payload)

    @pytest.mark.asyncio
    async def test_search_limit_capped(self, index):
        create_session_search_mcp_server(index)
        search_tool = TOOL_REGISTRY["search"]
        # 과도한 limit도 오류 없이 처리
        result = await search_tool.handler({"query": "수면", "limit": 9999})
        payload = _extract(result)
        assert isinstance(payload, list)

    @pytest.mark.asyncio
    async def test_server_name_and_tool_registered(self, index):
        create_session_search_mcp_server(index)
        assert "search" in TOOL_REGISTRY


class _RecordingIndex:
    """search 호출의 limit 인자를 기록하는 스텁."""

    def __init__(self):
        self.calls = []

    def search(self, query, limit):
        self.calls.append(limit)
        return []


class TestSearchLimitClamp:
    """F7: limit을 [1, MAX_LIMIT]로 클램프 — 음수는 SQLite LIMIT -1(무제한)이 되므로 하한 1."""

    @pytest.mark.asyncio
    async def test_negative_limit_clamped_to_one(self):
        idx = _RecordingIndex()
        create_session_search_mcp_server(idx)
        search_tool = TOOL_REGISTRY["search"]
        await search_tool.handler({"query": "수면", "limit": -5})
        assert idx.calls == [1]

    @pytest.mark.asyncio
    async def test_large_negative_limit_clamped_to_one(self):
        idx = _RecordingIndex()
        create_session_search_mcp_server(idx)
        search_tool = TOOL_REGISTRY["search"]
        await search_tool.handler({"query": "수면", "limit": -9999})
        assert idx.calls == [1]

    @pytest.mark.asyncio
    async def test_excessive_limit_capped_to_max(self):
        idx = _RecordingIndex()
        create_session_search_mcp_server(idx)
        search_tool = TOOL_REGISTRY["search"]
        await search_tool.handler({"query": "수면", "limit": 9999})
        assert idx.calls == [50]  # MAX_LIMIT
