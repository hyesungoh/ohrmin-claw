"""CallerCapability — 서버측 mutation MCP 차단 (ro deny·핸들러 미호출, ro 조회 허용, priv 허용, 사유 = evaluate_tool_gate)."""
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.body_metrics_tools import create_body_metrics_mcp_server
from core.garmin_tools import create_garmin_mcp_server
from core.llm import _MUTATION_MCP_TOOLS, evaluate_tool_gate
from core.memory import MemoryManager
from core.memory_tools import create_memory_mcp_server
from core.schedule_tools import create_schedule_mcp_server
from core.scheduler import CronStore
from core.session_search_tools import create_session_search_mcp_server
from core.tool_server.capability import CallerCapability, wrap_handler
from core.tool_server.spec import canonical_tool_name

MUTATIONS = sorted(_MUTATION_MCP_TOOLS)
OK_RESULT = {"content": [{"type": "text", "text": "{\"ok\": true}"}]}


def _all_specs():
    return [
        create_garmin_mcp_server(MagicMock()),
        create_body_metrics_mcp_server(MagicMock()),
        create_memory_mcp_server(MagicMock()),
        create_schedule_mcp_server(MagicMock()),
        create_session_search_mcp_server(MagicMock()),
    ]


def _spied_tools():
    """정규명 → (server, 핸들러를 AsyncMock 스파이로 바꾼 ToolSpec)."""
    out = {}
    for spec in _all_specs():
        for t in spec.tools:
            spy = AsyncMock(return_value=OK_RESULT)
            out[canonical_tool_name(spec.name, t.name)] = (spec.name, dataclasses.replace(t, handler=spy))
    return out


READ_TOOLS = sorted(set(_spied_tools()) - _MUTATION_MCP_TOOLS)


def _parse(result):
    return json.loads(result["content"][0]["text"])


class TestCapabilityEnum:
    def test_values_are_log_labels(self):
        assert CallerCapability.PRIVILEGED.value == "priv"
        assert CallerCapability.READ_ONLY.value == "ro"
        assert len(CallerCapability) == 2

    def test_mutation_set_is_seven_and_all_exist_in_specs(self):
        assert len(MUTATIONS) == 7
        assert set(MUTATIONS) <= set(_spied_tools())
        assert len(READ_TOOLS) == 15


class TestReadOnlyDeniesMutations:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("canonical", MUTATIONS)
    async def test_ro_mutation_denied_without_calling_handler(self, canonical, capsys):
        server, t = _spied_tools()[canonical]
        result = await wrap_handler(server, t, CallerCapability.READ_ONLY)({"target": "memory", "id": "x"})

        t.handler.assert_not_awaited()
        _, expected_reason = evaluate_tool_gate(canonical, {}, False)
        assert expected_reason
        assert _parse(result) == {"success": False, "error": expected_reason, "denied_by": "capability"}
        assert "is_error" not in result
        lines = capsys.readouterr().out.splitlines()
        assert lines == [
            f"[gate] backend=claude cap=ro tool={canonical} decision=deny via=capability reason={expected_reason}"
        ]

    @pytest.mark.asyncio
    async def test_gate_log_uses_given_backend(self, capsys):
        server, t = _spied_tools()["mcp__schedule__schedule_create"]
        await wrap_handler(server, t, CallerCapability.READ_ONLY, backend="codex")({})
        out = capsys.readouterr().out
        assert out.startswith("[gate] backend=codex cap=ro tool=mcp__schedule__schedule_create decision=deny via=capability ")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("canonical", READ_TOOLS)
    async def test_ro_read_tools_allowed(self, canonical, capsys):
        server, t = _spied_tools()[canonical]
        args = {k: ("1" if p.type == "string" else 1) for k, p in t.params.items()}
        result = await wrap_handler(server, t, CallerCapability.READ_ONLY)(args)
        t.handler.assert_awaited_once_with(args)
        assert result == OK_RESULT
        assert evaluate_tool_gate(canonical, {}, False) == (True, "")
        assert "[gate]" not in capsys.readouterr().out


class TestPrivilegedAllowsAll:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("canonical", MUTATIONS + READ_TOOLS)
    async def test_priv_calls_handler(self, canonical, capsys):
        server, t = _spied_tools()[canonical]
        args = {k: ("1" if p.type == "string" else 1) for k, p in t.params.items()}
        result = await wrap_handler(server, t, CallerCapability.PRIVILEGED)(args)
        t.handler.assert_awaited_once_with(args)
        assert result == OK_RESULT
        assert evaluate_tool_gate(canonical, {}, True) == (True, "")
        assert "[gate]" not in capsys.readouterr().out


class TestRealHandlersUnderReadOnly:
    @pytest.mark.asyncio
    async def test_ro_schedule_create_does_not_persist(self, tmp_path):
        store = CronStore(str(tmp_path / "cron_jobs.json"))
        spec = create_schedule_mcp_server(store)
        create = next(t for t in spec.tools if t.name == "schedule_create")
        args = {"prompt": "p", "schedule": "30m", "deliver_channel_id": None, "max_turns": None}

        denied = await wrap_handler("schedule", create, CallerCapability.READ_ONLY)(args)
        assert _parse(denied)["denied_by"] == "capability"
        assert store.count() == 0

        allowed = await wrap_handler("schedule", create, CallerCapability.PRIVILEGED)(args)
        assert _parse(allowed)["success"] is True
        assert store.count() == 1

    @pytest.mark.asyncio
    async def test_ro_add_memory_keeps_memory_file(self, tmp_path):
        mgr = MemoryManager(str(tmp_path))
        mgr.write_memory("기존 기억")
        before = (tmp_path / "memory.md").read_bytes()
        spec = create_memory_mcp_server(mgr)
        add = next(t for t in spec.tools if t.name == "add_memory")

        result = await wrap_handler("memory", add, CallerCapability.READ_ONLY)({"target": "memory", "content": "새 기억"})
        assert _parse(result)["success"] is False
        assert (tmp_path / "memory.md").read_bytes() == before

        listed = next(t for t in spec.tools if t.name == "list_memory")
        entries = _parse(await wrap_handler("memory", listed, CallerCapability.READ_ONLY)({"target": None}))
        assert [e["content"] for e in entries["entries"]] == ["기존 기억"]
