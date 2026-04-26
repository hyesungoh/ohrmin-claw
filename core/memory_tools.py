"""Memory MCP tool 정의 — Claude Agent SDK 인프로세스 서버."""
from typing import Annotated

from claude_agent_sdk import tool, create_sdk_mcp_server

from core.garmin_tools import _json_response


TOOL_REGISTRY: dict = {}


def create_memory_mcp_server(memory_manager):
    """MemoryManager를 감싸는 인프로세스 MCP 서버 생성."""
    TOOL_REGISTRY.clear()

    LIST_SCHEMA = {
        "target": Annotated[str, "조회할 메모리 대상 ('memory' 또는 'user')"],
    }

    @tool("list_memory", "메모리 엔트리 목록 조회 (현재 사용량 포함)", LIST_SCHEMA)
    async def list_memory(args):
        target = args.get("target", "memory")
        entries = memory_manager.list_entries(target)
        raw = memory_manager._read_raw(target)
        limit = memory_manager._limit_for(target)
        return _json_response({
            "target": target,
            "entries": entries,
            "current_chars": len(raw),
            "limit": limit,
        })

    ADD_SCHEMA = {
        "target": Annotated[str, "저장할 메모리 대상 ('memory' 또는 'user')"],
        "content": Annotated[str, "저장할 내용"],
    }

    @tool("add_memory", "메모리 엔트리 추가", ADD_SCHEMA)
    async def add_memory(args):
        target = args.get("target", "memory")
        content = args.get("content", "")
        if target == "memory":
            result = memory_manager.append_memory(content)
        else:
            result = memory_manager.append_user(content)
        return _json_response(result)

    REPLACE_SCHEMA = {
        "target": Annotated[str, "수정할 메모리 대상 ('memory' 또는 'user')"],
        "index": Annotated[int, "교체할 엔트리 인덱스 (0부터 시작)"],
        "content": Annotated[str, "새 내용"],
    }

    @tool("replace_memory", "특정 메모리 엔트리를 새 내용으로 교체", REPLACE_SCHEMA)
    async def replace_memory(args):
        target = args.get("target", "memory")
        index = args.get("index", 0)
        content = args.get("content", "")
        result = memory_manager.replace_entry(target, index, content)
        return _json_response(result)

    REMOVE_SCHEMA = {
        "target": Annotated[str, "삭제할 메모리 대상 ('memory' 또는 'user')"],
        "index": Annotated[int, "삭제할 엔트리 인덱스 (0부터 시작)"],
    }

    @tool("remove_memory", "특정 메모리 엔트리 삭제", REMOVE_SCHEMA)
    async def remove_memory(args):
        target = args.get("target", "memory")
        index = args.get("index", 0)
        result = memory_manager.remove_entry(target, index)
        return _json_response(result)

    all_tools = [list_memory, add_memory, replace_memory, remove_memory]
    TOOL_REGISTRY.update({t.name: t for t in all_tools})

    return create_sdk_mcp_server(
        name="memory",
        tools=all_tools,
    )
