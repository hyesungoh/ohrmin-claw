"""백엔드 중립 도구 정의 — ToolSpec · ServerSpec · ParamSpec · @tool 데코레이터.

도구 모듈(`core/*_tools.py`)은 transport를 모른다. 같은 ServerSpec을 Claude는 SDK 인프로세스
서버(claude_sdk_bridge)로, Codex/Grok은 streamable HTTP(http_server)로 노출한다.
`ToolSpec.handler`는 원 핸들러 그대로라 `TOOL_REGISTRY[name].handler(args)` 패턴이 유지된다.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParamSpec:
    """도구 파라미터 — type은 JSON Schema 타입명(string|integer|number|boolean).

    required=True는 핸들러가 가드 없이 `args["x"]`로 접근하는 파라미터에만 쓴다
    (non-nullable 스키마 + null/누락 시 핸들러 호출 전 검증 오류 결과). 그 외는 전부 nullable.
    """

    type: str
    description: str
    required: bool = False


@dataclass
class ToolSpec:
    name: str
    description: str
    params: dict[str, ParamSpec]
    handler: Callable[[dict], Awaitable[dict]]


@dataclass
class ServerSpec:
    name: str
    tools: list[ToolSpec] = field(default_factory=list)


def tool(name: str, description: str, params: dict[str, ParamSpec]):
    """핸들러를 ToolSpec으로 감싸는 데코레이터 (claude_agent_sdk.tool 대체)."""

    def decorator(handler: Callable[[dict], Awaitable[dict]]) -> ToolSpec:
        return ToolSpec(name=name, description=description, params=dict(params), handler=handler)

    return decorator


def canonical_tool_name(server: str, tool_name: str) -> str:
    """정규 도구명 `mcp__<server>__<tool>` (게이트·on_tool 어휘)."""
    return f"mcp__{server}__{tool_name}"
