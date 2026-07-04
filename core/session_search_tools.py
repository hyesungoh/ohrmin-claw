"""세션 검색 MCP tool 정의 — Claude Agent SDK 인프로세스 서버.

SessionIndex(FTS5)를 감싸 과거 대화 기록을 전문 검색한다 → mcp__session_search__search.
"""
import asyncio
from typing import Annotated

from claude_agent_sdk import tool, create_sdk_mcp_server

from core.garmin_tools import _json_response


TOOL_REGISTRY: dict = {}

MAX_LIMIT = 50


def create_session_search_mcp_server(index):
    """SessionIndex를 감싸는 인프로세스 MCP 서버 생성."""
    TOOL_REGISTRY.clear()

    SEARCH_SCHEMA = {
        "query": Annotated[str, "검색어 (과거 대화에서 찾을 키워드/문장)"],
        "limit": Annotated[int, "최대 결과 수 (기본 10, 최대 50)"],
    }

    @tool("search", "과거 대화 기록을 전문 검색 (FTS5 bm25 랭킹, 관련도순)", SEARCH_SCHEMA)
    async def search(args):
        query = args.get("query", "")
        limit = min(int(args.get("limit", 10) or 10), MAX_LIMIT)
        results = await asyncio.to_thread(index.search, query, limit)
        return _json_response(results)

    all_tools = [search]
    TOOL_REGISTRY.update({t.name: t for t in all_tools})

    return create_sdk_mcp_server(
        name="session_search",
        tools=all_tools,
    )
