"""세션 검색 MCP tool 정의 — 백엔드 중립 ServerSpec (transport는 core/tool_server).

SessionIndex(FTS5)를 감싸 과거 대화 기록을 전문 검색한다 → mcp__session_search__search.
"""
import asyncio

from core.garmin_tools import _json_response
from core.tool_server.spec import ParamSpec, ServerSpec, tool


TOOL_REGISTRY: dict = {}

MAX_LIMIT = 50


def create_session_search_mcp_server(index):
    """SessionIndex를 감싸는 MCP 서버 정의(ServerSpec) 생성."""
    TOOL_REGISTRY.clear()

    SEARCH_SCHEMA = {
        "query": ParamSpec("string", "검색어 (과거 대화에서 찾을 키워드/문장)"),
        "limit": ParamSpec("integer", "최대 결과 수 (기본 10, 최대 50)"),
    }

    @tool("search", "과거 대화 기록을 전문 검색 (FTS5 bm25 랭킹, 관련도순)", SEARCH_SCHEMA)
    async def search(args):
        query = args.get("query", "")
        # limit을 [1, MAX_LIMIT]로 클램프. 음수면 SQLite LIMIT -1(무제한)이 되므로 하한 1을 강제한다.
        limit = max(1, min(int(args.get("limit", 10) or 10), MAX_LIMIT))
        results = await asyncio.to_thread(index.search, query, limit)
        return _json_response(results)

    all_tools = [search]
    TOOL_REGISTRY.update({t.name: t for t in all_tools})

    return ServerSpec(
        name="session_search",
        tools=all_tools,
    )
