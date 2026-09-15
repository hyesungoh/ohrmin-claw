"""Claude transport — ServerSpec을 claude_agent_sdk 인프로세스 MCP 서버로 노출.

완성 JSON Schema dict(`type` + `properties`)는 SDK가 변환 없이 그대로 통과시킨다(F4).
`claude_agent_sdk` import는 이 파일과 core/llm.py에만 둔다.
"""
from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server

from core.tool_server.capability import CallerCapability, wrap_handler
from core.tool_server.schema import to_strict_json_schema, validate_server_spec
from core.tool_server.spec import ServerSpec


def to_sdk_servers(specs: list[ServerSpec], capability: CallerCapability) -> dict:
    """ServerSpec 목록 → {name: McpSdkServerConfig} (ClaudeAgentOptions.mcp_servers 형태)."""
    servers = {}
    for spec in specs:
        validate_server_spec(spec)
        tools = [
            SdkMcpTool(
                name=tool_spec.name,
                description=tool_spec.description,
                input_schema=to_strict_json_schema(tool_spec.params),
                handler=wrap_handler(spec.name, tool_spec, capability, backend="claude"),
            )
            for tool_spec in spec.tools
        ]
        servers[spec.name] = create_sdk_mcp_server(name=spec.name, tools=tools)
    return servers
