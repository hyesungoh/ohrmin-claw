"""관측 로그 형식 고정 (capsys) — 기동 요약 · WARN UNVERIFIED · 게이트 결정 · 턴 결과 + Claude start() probe."""
import re

import pytest
from unittest.mock import patch

from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

from core.llm import ClaudeSDKAdapter
from core.llm_errors import StartupError
from core.tool_server.capability import CallerCapability
from core.tool_server.claude_sdk_bridge import to_sdk_servers
from core.tool_server.spec import ParamSpec, ServerSpec, tool
from core.observability import (
    UNVERIFIED_SURFACES,
    emit_unverified_warnings,
    format_gate_decision,
    format_startup_summary,
    format_turn_result,
    unverified_surfaces,
)

STARTUP_RE = re.compile(
    r"^\[llm\] startup backend=(claude|codex|grok) model=\S+ skills=(native|registry) "
    r"gate=(claude-hook|codex-hook|grok-approval) tools=\d+ "
    r"tool_server=(off|127\.0\.0\.1:\d+) auth=(claude\.ai|chatgpt|xai-env_key-isolated-home)$"
)
WARN_RE = re.compile(r"^\[llm\] WARN UNVERIFIED [a-z_.A-Z]+ source=https://\S+$")
GATE_RE = re.compile(
    r"^\[gate\] backend=(claude|codex|grok) cap=(priv|ro) tool=\S+ decision=(allow|deny) "
    r"via=(hook|approval|capability|tripwire|probe) reason=.+?( executed=likely)?$"
)
TURN_RE = re.compile(
    r"^\[llm\] turn backend=(claude|codex|grok) thread=\S+ cap=(priv|ro) tools=\d+ skills_loaded=\d+ "
    r"outcome=(ok|usage_limit|auth_expired|runtime_unavailable|generic)$"
)


def _lines(capsys, prefix):
    return [l for l in capsys.readouterr().out.splitlines() if l.startswith(prefix)]


class TestFormatters:
    @pytest.mark.parametrize("backend, tool_server, gate, auth", [
        ("claude", None, "claude-hook", "claude.ai"),
        ("codex", "127.0.0.1:53111", "codex-hook", "chatgpt"),
        ("grok", "127.0.0.1:53112", "grok-approval", "xai-env_key-isolated-home"),
    ])
    def test_startup_summary(self, backend, tool_server, gate, auth):
        line = format_startup_summary(backend, "m-1", "native", 22, tool_server)
        assert STARTUP_RE.match(line)
        assert line == (
            f"[llm] startup backend={backend} model=m-1 skills=native gate={gate} tools=22 "
            f"tool_server={tool_server or 'off'} auth={auth}"
        )

    def test_gate_decision_lines(self):
        deny = format_gate_decision("codex", "ro", "Bash", False, "hook", "무인 턴은 읽기 전용입니다 — Bash")
        allow = format_gate_decision("claude", "priv", "Write", True, "hook")
        trip = format_gate_decision("grok", "ro", "Unknown", False, "tripwire", "차단", executed_likely=True)
        assert deny == "[gate] backend=codex cap=ro tool=Bash decision=deny via=hook reason=무인 턴은 읽기 전용입니다 — Bash"
        assert allow == "[gate] backend=claude cap=priv tool=Write decision=allow via=hook reason=-"
        assert trip.endswith("via=tripwire reason=차단 executed=likely")
        assert all(GATE_RE.match(l) for l in (deny, allow, trip))

    def test_turn_result_lines(self):
        one_shot = format_turn_result("claude", None, "ro", 3, 1, "ok")
        thread = format_turn_result("grok", 12345, "priv", 0, 0, "usage_limit")
        assert one_shot == "[llm] turn backend=claude thread=- cap=ro tools=3 skills_loaded=1 outcome=ok"
        assert thread == "[llm] turn backend=grok thread=12345 cap=priv tools=0 skills_loaded=0 outcome=usage_limit"
        assert TURN_RE.match(one_shot) and TURN_RE.match(thread)


class TestUnverifiedWarnings:
    def test_claude_native_zero_lines(self, capsys):
        emit_unverified_warnings("claude", "native")
        assert capsys.readouterr().out == ""
        assert unverified_surfaces("claude") == []

    def test_claude_registry_one_line(self, capsys):
        emit_unverified_warnings("claude", "registry")
        lines = capsys.readouterr().out.splitlines()
        assert len(lines) == 1
        assert lines[0].startswith("[llm] WARN UNVERIFIED claude.skills_filter source=https://")
        assert WARN_RE.match(lines[0])

    def test_codex_surface_set(self, capsys):
        emit_unverified_warnings("codex")
        lines = capsys.readouterr().out.splitlines()
        ids = [l.split()[3] for l in lines]
        assert ids == [
            "codex.thread_start.developerInstructions", "codex.hooks.cli_override",
            "codex.hooks.fires_under_never", "codex.hooks.payload_shape", "codex.item_types",
            "codex.error_info", "codex.account_read_shape", "codex.mcp.url_override",
            "codex.hooks.env_inheritance", "codex.thread_archive",
        ]
        assert all(WARN_RE.match(l) for l in lines)

    def test_grok_surface_set_and_conditional_permissions(self, capsys):
        emit_unverified_warnings("grok")
        base = [l.split()[3] for l in capsys.readouterr().out.splitlines()]
        assert base == [
            "grok.acp.mcp_http", "grok.acp.permission_requests", "grok.acp.cancel_reprompt",
            "grok.credentials.isolated_home", "grok.tool_call_shape", "grok.error_shape",
            "grok.image_prompt", "grok.reads_project_claude_files", "grok.system_prompt_preamble",
            "grok.acp.session_close",
        ]
        emit_unverified_warnings("grok", project_claude_permissions=True)
        lines = capsys.readouterr().out.splitlines()
        assert len(lines) == len(base) + 1
        assert lines[-1] == "[llm] WARN UNVERIFIED grok.project_claude_permissions source=https://docs.x.ai/build/features/permissions"

    def test_surface_ids_unique(self):
        ids = [sid for group in UNVERIFIED_SURFACES.values() for sid, _ in group]
        assert len(ids) == len(set(ids))


class TestClaudeStart:
    @pytest.mark.asyncio
    async def test_start_logs_probe_and_summary(self, capsys):
        adapter = ClaudeSDKAdapter(model="claude-opus-4-8", cwd="/proj")
        await adapter.start()

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "[gate] backend=claude cap=ro tool=Bash decision=deny via=probe reason=무인 턴은 읽기 전용입니다 — Bash는 인터랙티브 오너 세션에서만 허용됩니다."
        assert lines[1] == "[gate] backend=claude cap=priv tool=Bash decision=allow via=probe reason=-"
        assert lines[2] == "[llm] startup backend=claude model=claude-opus-4-8 skills=native gate=claude-hook tools=0 tool_server=off auth=claude.ai"
        assert STARTUP_RE.match(lines[2])
        assert not any("WARN UNVERIFIED" in l for l in lines)  # claude native = 0줄
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_start_counts_server_spec_tools(self, capsys):
        @tool("t_one", "one", {"x": ParamSpec("string", "x")})
        async def t_one(args):
            return {"content": [{"type": "text", "text": "1"}]}

        @tool("t_two", "two", {})
        async def t_two(args):
            return {"content": [{"type": "text", "text": "2"}]}

        @tool("t_three", "three", {})
        async def t_three(args):
            return {"content": [{"type": "text", "text": "3"}]}

        specs = [ServerSpec("a", [t_one, t_two]), ServerSpec("b", [t_three])]
        servers = to_sdk_servers(specs, CallerCapability.PRIVILEGED)
        await ClaudeSDKAdapter(mcp_servers=servers, server_specs=specs).start()
        summary = _lines(capsys, "[llm] startup")
        assert len(summary) == 1
        assert " tools=3 " in summary[0]

    @pytest.mark.asyncio
    async def test_start_probe_mismatch_raises_startup_error(self, capsys):
        with patch("core.safety_gate.evaluate_tool_gate", return_value=(True, "")):
            with pytest.raises(StartupError) as exc:
                await ClaudeSDKAdapter().start()
        assert "probe" in exc.value.cause
        gate = _lines(capsys, "[gate]")
        assert gate == ["[gate] backend=claude cap=ro tool=Bash decision=allow via=probe reason=-"]


class TestTurnLogs:
    @pytest.mark.asyncio
    async def test_one_shot_turn_ok_counts_tools_and_skills(self, capsys):
        async def fake_query(**kwargs):
            yield AssistantMessage(content=[
                ToolUseBlock(id="1", name="Skill", input={}),
                ToolUseBlock(id="2", name="mcp__garmin__get_sleep", input={}),
            ], model="m")
            yield AssistantMessage(content=[TextBlock(text="끝")], model="m")

        with patch("core.llm.query", side_effect=fake_query):
            await ClaudeSDKAdapter().ask_with_context("S", "q", {})

        assert _lines(capsys, "[llm] turn") == [
            "[llm] turn backend=claude thread=- cap=ro tools=2 skills_loaded=1 outcome=ok"
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error, outcome", [
        ("rate_limit", "usage_limit"),
        ("authentication_failed", "auth_expired"),
    ])
    async def test_one_shot_turn_error_outcomes(self, capsys, error, outcome):
        async def fake_query(**kwargs):
            yield AssistantMessage(content=[TextBlock(text="x")], model="m", error=error)

        with patch("core.llm.query", side_effect=fake_query):
            await ClaudeSDKAdapter().ask_with_context("S", "q", {}, approve_skill_writes=True)

        lines = _lines(capsys, "[llm] turn")
        assert lines == [f"[llm] turn backend=claude thread=- cap=priv tools=0 skills_loaded=0 outcome={outcome}"]

    @pytest.mark.asyncio
    async def test_generic_outcome_on_exception(self, capsys):
        def boom(**kwargs):
            raise RuntimeError("down")

        with patch("core.llm.query", side_effect=boom):
            with pytest.raises(Exception):
                await ClaudeSDKAdapter().ask("S", "q")

        assert _lines(capsys, "[llm] turn") == [
            "[llm] turn backend=claude thread=- cap=ro tools=0 skills_loaded=0 outcome=generic"
        ]

    @pytest.mark.asyncio
    async def test_persistent_turn_logs_thread_id(self, capsys, monkeypatch):
        class Client:
            def __init__(self, options=None):
                pass

            async def connect(self):
                pass

            async def query(self, prompt):
                pass

            async def receive_response(self):
                yield AssistantMessage(content=[ToolUseBlock(id="1", name="Read", input={})], model="m")
                yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

            async def disconnect(self):
                pass

        monkeypatch.setattr("core.llm.ClaudeSDKClient", Client)
        await ClaudeSDKAdapter(cwd="/proj").ask_with_context("S", "q", {}, approve_skill_writes=True, thread_id=4242)

        lines = _lines(capsys, "[llm] turn")
        assert lines == ["[llm] turn backend=claude thread=4242 cap=priv tools=1 skills_loaded=0 outcome=ok"]
        assert TURN_RE.match(lines[0])
