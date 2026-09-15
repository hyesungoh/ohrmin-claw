"""Claude SDK bridge — SDK list_tools 스키마 == strict schema, priv/ro 서버 세트, 어댑터 세트 선택 (AC-7/AC-15)."""
import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.lowlevel import Server
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

from core.body_metrics_tools import create_body_metrics_mcp_server
from core.garmin_tools import create_garmin_mcp_server
from core.llm import ClaudeSDKAdapter, _MUTATION_MCP_TOOLS, create_llm_adapter_from_config, evaluate_tool_gate
from core.llm_config import load_llm_config
from core.memory_tools import create_memory_mcp_server
from core.schedule_tools import create_schedule_mcp_server
from core.session_search_tools import create_session_search_mcp_server
from core.tool_server.capability import CallerCapability
from core.tool_server.claude_sdk_bridge import to_sdk_servers
from core.tool_server.schema import to_strict_json_schema
from core.tool_server.spec import ServerSpec, tool

SERVER_NAMES = ["garmin", "body_metrics", "memory", "schedule", "session_search"]


def _specs(garmin=None, store=None):
    return [
        create_garmin_mcp_server(garmin or MagicMock()),
        create_body_metrics_mcp_server(MagicMock()),
        create_memory_mcp_server(MagicMock()),
        create_schedule_mcp_server(store or MagicMock()),
        create_session_search_mcp_server(MagicMock()),
    ]


async def _list_tools(config):
    handler = config["instance"].request_handlers[ListToolsRequest]
    return (await handler(ListToolsRequest(method="tools/list"))).root.tools


async def _call(config, name, arguments):
    """SDK가 in-process 서버에 tools/call을 라우팅하는 것과 같은 경로(request_handlers, 입력 검증 포함)."""
    handler = config["instance"].request_handlers[CallToolRequest]
    req = CallToolRequest(method="tools/call", params=CallToolRequestParams(name=name, arguments=arguments))
    return (await handler(req)).root


class TestSdkServers:
    @pytest.mark.parametrize("capability", list(CallerCapability))
    def test_server_configs(self, capability):
        servers = to_sdk_servers(_specs(), capability)
        assert list(servers) == SERVER_NAMES
        for name, config in servers.items():
            assert config["type"] == "sdk"
            assert config["name"] == name
            assert isinstance(config["instance"], Server)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("capability", list(CallerCapability))
    async def test_sdk_list_tools_schema_equals_strict_schema(self, capability):
        specs = _specs()
        servers = to_sdk_servers(specs, capability)
        total = 0
        for spec in specs:
            listed = await _list_tools(servers[spec.name])
            assert [t.name for t in listed] == [t.name for t in spec.tools]
            for sdk_tool, tool_spec in zip(listed, spec.tools):
                assert sdk_tool.description == tool_spec.description
                assert sdk_tool.inputSchema == to_strict_json_schema(tool_spec.params)
                total += 1
        assert total == 22

    def test_invalid_tool_name_rejected(self):
        @tool("bad.name", "d", {})
        async def bad(args):
            return {}

        with pytest.raises(ValueError):
            to_sdk_servers([ServerSpec("s", [bad])], CallerCapability.PRIVILEGED)


class TestSdkCalls:
    @pytest.mark.asyncio
    async def test_ro_mutation_denied_via_sdk_path(self, capsys):
        store = MagicMock()
        servers = to_sdk_servers(_specs(store=store), CallerCapability.READ_ONLY)
        args = {"prompt": "p", "schedule": "30m", "deliver_channel_id": None, "max_turns": None}

        result = await _call(servers["schedule"], "schedule_create", args)

        store.create.assert_not_called()
        store.count.assert_not_called()
        _, reason = evaluate_tool_gate("mcp__schedule__schedule_create", {}, False)
        assert json.loads(result.content[0].text) == {"success": False, "error": reason, "denied_by": "capability"}
        assert result.isError is False
        assert "via=capability" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_priv_mutation_calls_handler(self):
        store = MagicMock()
        store.count.return_value = 0
        store.create.return_value = {"id": "j1"}
        servers = to_sdk_servers(_specs(store=store), CallerCapability.PRIVILEGED)
        args = {"prompt": "p", "schedule": "30m", "deliver_channel_id": None, "max_turns": None}

        result = await _call(servers["schedule"], "schedule_create", args)

        store.create.assert_called_once()
        assert json.loads(result.content[0].text) == {"success": True, "job": {"id": "j1"}}

    @pytest.mark.asyncio
    async def test_every_mutation_denied_on_ro_and_listed_on_both(self):
        ro = to_sdk_servers(_specs(), CallerCapability.READ_ONLY)
        priv = to_sdk_servers(_specs(), CallerCapability.PRIVILEGED)
        for canonical in sorted(_MUTATION_MCP_TOOLS):
            _, server, name = canonical.split("__")
            assert name in [t.name for t in await _list_tools(ro[server])]
            assert name in [t.name for t in await _list_tools(priv[server])]
            listed = {t.name: t for t in await _list_tools(ro[server])}
            args = {k: None for k in listed[name].inputSchema["properties"]}
            result = await _call(ro[server], name, args)
            assert json.loads(result.content[0].text)["denied_by"] == "capability", canonical

    @pytest.mark.asyncio
    async def test_nullable_optional_passes_validation_and_matches_direct_handler(self):
        garmin = MagicMock()
        garmin.get_last_activity.return_value = {"activity_id": "999", "name": "러닝"}
        specs = _specs(garmin=garmin)
        servers = to_sdk_servers(specs, CallerCapability.READ_ONLY)

        result = await _call(servers["garmin"], "get_last_activity", {"count": None})

        assert result.isError is False
        direct = await next(t for t in specs[0].tools if t.name == "get_last_activity").handler({})
        assert result.content[0].text.encode() == direct["content"][0]["text"].encode()

    @pytest.mark.asyncio
    async def test_required_null_is_validation_error_not_handler_call(self):
        garmin = MagicMock()
        servers = to_sdk_servers(_specs(garmin=garmin), CallerCapability.PRIVILEGED)

        result = await _call(servers["garmin"], "get_activity_detail", {"activity_id": None})

        assert result.isError is True
        assert "Input validation error" in result.content[0].text
        garmin.get_activity_detail.assert_not_called()


class TestAdapterServerSetSelection:
    PRIV = {"garmin": {"type": "sdk", "name": "garmin", "instance": None}}
    RO = {"garmin": {"type": "sdk", "name": "garmin", "instance": None}, "ro_marker": {}}

    @pytest.mark.parametrize("cwd", [None, "/proj"])
    @pytest.mark.parametrize("approve, expected", [(True, "priv"), (False, "ro"), (None, "ro")])
    def test_build_options_routes_by_approve_is_true(self, cwd, approve, expected):
        adapter = ClaudeSDKAdapter(mcp_servers=self.PRIV, readonly_mcp_servers=self.RO, cwd=cwd)
        options = adapter._build_options("sys", approve_skill_writes=approve)
        assert options.mcp_servers is (self.PRIV if expected == "priv" else self.RO)

    def test_truthy_non_true_approve_is_not_privileged(self):
        adapter = ClaudeSDKAdapter(mcp_servers=self.PRIV, readonly_mcp_servers=self.RO, cwd="/proj")
        assert adapter._build_options("sys", approve_skill_writes=1).mcp_servers is self.RO

    def test_adapter_default_approve_applies_when_call_passes_none(self):
        adapter = ClaudeSDKAdapter(
            mcp_servers=self.PRIV, readonly_mcp_servers=self.RO, cwd="/proj", approve_skill_writes=True
        )
        assert adapter._build_options("sys").mcp_servers is self.PRIV

    @pytest.mark.asyncio
    async def test_one_shot_query_receives_ro_set_for_unattended_turn(self):
        async def empty(**kwargs):
            return
            yield

        adapter = ClaudeSDKAdapter(mcp_servers=self.PRIV, readonly_mcp_servers=self.RO, cwd="/proj")
        with patch("core.llm.query", side_effect=empty) as mock_query:
            await adapter._call_claude("sys", "hi")
            await adapter._call_claude("sys", "hi", approve_skill_writes=True)
        assert mock_query.call_args_list[0].kwargs["options"].mcp_servers is self.RO
        assert mock_query.call_args_list[1].kwargs["options"].mcp_servers is self.PRIV

    def test_factory_passes_readonly_set_and_specs(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"llm": {"backend": "claude"}}))
        specs = _specs()
        priv = to_sdk_servers(specs, CallerCapability.PRIVILEGED)
        ro = to_sdk_servers(specs, CallerCapability.READ_ONLY)
        adapter = create_llm_adapter_from_config(
            load_llm_config(str(path), {}), mcp_servers=priv, readonly_mcp_servers=ro, server_specs=specs, cwd="/proj"
        )
        assert adapter.mcp_servers is priv
        assert adapter.readonly_mcp_servers is ro
        assert adapter.server_specs is specs


class TestBotWiringClaude:
    def test_bot_builds_priv_and_ro_sdk_sets_from_same_specs(self):
        import bot.main as main

        assert main.tool_server is None  # claude 백엔드는 HTTP 도구 서버를 만들지 않는다
        names = [spec.name for spec in main.server_specs]
        assert list(main.llm.mcp_servers) == names
        assert list(main.llm.readonly_mcp_servers) == names
        assert main.llm.readonly_mcp_servers is not main.llm.mcp_servers
        for name in names:
            assert main.llm.readonly_mcp_servers[name]["instance"] is not main.llm.mcp_servers[name]["instance"]
        assert main.llm.server_specs is main.server_specs
        assert {"body_metrics", "memory", "session_search", "schedule"} <= set(names)  # garmin은 로그인 시에만
