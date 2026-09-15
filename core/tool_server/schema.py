"""strict JSON Schema 변환 + 도구 이름 규칙 (OpenAI strict schema 호환).

- 모든 키를 required에 넣고 additionalProperties=false. 선택 파라미터는 `[T, "null"]`로 nullable.
- 이름: 서버·도구 `^[a-zA-Z0-9_-]+$`, `mcp__<s>__<t>`(Claude/Codex)·`<s>__<t>`(Grok) 모두 ≤64자.
"""
import re

from core.tool_server.spec import ParamSpec, ServerSpec, canonical_tool_name

MAX_TOOL_NAME_LENGTH = 64
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def to_strict_json_schema(params: dict[str, ParamSpec]) -> dict:
    properties = {}
    for key, param in params.items():
        json_type = param.type if param.required else [param.type, "null"]
        properties[key] = {"type": json_type, "description": param.description}
    return {
        "type": "object",
        "properties": properties,
        "required": list(params.keys()),
        "additionalProperties": False,
    }


def validate_tool_name(server: str, tool_name: str) -> None:
    """서버·도구 이름 규칙 위반 시 ValueError."""
    for label, value in (("server", server), ("tool", tool_name)):
        if not _NAME_RE.match(value or ""):
            raise ValueError(f"invalid {label} name {value!r}: must match {_NAME_RE.pattern}")
    for rendered in (canonical_tool_name(server, tool_name), f"{server}__{tool_name}"):
        if len(rendered) > MAX_TOOL_NAME_LENGTH:
            raise ValueError(
                f"tool name {rendered!r} is {len(rendered)} chars (max {MAX_TOOL_NAME_LENGTH})"
            )


def validate_server_spec(spec: ServerSpec) -> None:
    for tool_spec in spec.tools:
        validate_tool_name(spec.name, tool_spec.name)
