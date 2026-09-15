"""§3.8 관측성 — 기동 요약 1줄·WARN UNVERIFIED 집합(claude native = 0줄, claude registry = claude.skills_filter만),
게이트 결정 로그 형식, 턴 결과 로그(outcome·skills_loaded·cap·thread).
"""
import re

import pytest

from core.observability import AUTH_LABEL, GATE_WIRING, format_unverified_warning, unverified_surfaces
from tests.contract.harness import SYS, gate_lines, turn_lines
from tests.contract.scenario import end, error, gate_probe, text, tool

STARTUP_RE = re.compile(
    r"^\[llm\] startup backend=(?P<backend>\S+) model=(?P<model>\S+) skills=(?P<skills>native|registry) "
    r"gate=(?P<gate>\S+) tools=(?P<tools>\d+) tool_server=(?P<tool_server>off|127\.0\.0\.1:\d+) auth=(?P<auth>\S+)$"
)
WARN_PREFIX = "[llm] WARN UNVERIFIED "
# 계획 §3.8 literal — claude 두 모드의 미검증 표면 집합.
CLAUDE_WARN_IDS = {"claude_native": [], "claude_registry": ["claude.skills_filter"]}


@pytest.mark.asyncio
async def test_startup_summary_line(harness):
    summaries = [m.groupdict() for m in map(STARTUP_RE.match, harness.startup_lines) if m]

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["backend"] == harness.backend_id
    assert summary["model"] == str(harness.config.model)
    assert summary["skills"] == harness.skills_mode
    assert summary["gate"] == GATE_WIRING[harness.backend_id]
    assert summary["auth"] == AUTH_LABEL[harness.backend_id]
    assert int(summary["tools"]) == sum(len(spec.tools) for spec in harness.specs) == (24 if harness.registry else 22)
    assert (summary["tool_server"] == "off") == (harness.tool_server is None)


@pytest.mark.asyncio
async def test_unverified_warning_set(harness):
    warns = [line for line in harness.startup_lines if line.startswith(WARN_PREFIX)]
    expected = [format_unverified_warning(s, src) for s, src in unverified_surfaces(harness.backend_id, harness.skills_mode)]

    assert sorted(warns) == sorted(expected)
    assert len(warns) == len(set(warns))
    if harness.name in CLAUDE_WARN_IDS:
        assert [w[len(WARN_PREFIX):].split(" ")[0] for w in warns] == CLAUDE_WARN_IDS[harness.name]


@pytest.mark.asyncio
async def test_gate_decision_log_format(harness, capsys):
    harness.fake.script(
        gate_probe("Bash", {"command": "ls"}),
        gate_probe("mcp__memory__remove_memory", {"target": "memory", "index": 0}),
        text("끝"),
        end(),
        gate_probe("Bash", {"command": "ls"}),
        text("끝"),
    )
    capsys.readouterr()

    await harness.adapter.ask_with_context(SYS, "q", {})
    await harness.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True)

    out = capsys.readouterr().out
    raw = [line for line in out.splitlines() if line.startswith("[gate]")]
    parsed = gate_lines(out)
    assert len(parsed) == len(raw) == 3
    assert [(l["backend"], l["cap"], l["tool"], l["decision"], l["via"]) for l in parsed] == [
        (harness.backend_id, "ro", "Bash", "deny", harness.gate_via),
        (harness.backend_id, "ro", "mcp__memory__remove_memory", "deny", harness.gate_via),
        (harness.backend_id, "priv", "Bash", "allow", harness.gate_via),
    ]
    assert parsed[2]["reason"] == "-" and all(l["reason"] != "-" for l in parsed[:2])
    assert not any(l["executed"] for l in parsed)


@pytest.mark.asyncio
async def test_turn_result_log_per_turn(harness, capsys):
    harness.fake.script(
        tool("mcp__garmin__get_sleep", {"start": None, "end": None}),
        text("a"),
        end(),
        error("auth_expired"),
        end(),
        text("c"),
    )
    capsys.readouterr()

    await harness.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True, thread_id=5150)
    await harness.adapter.ask_with_context(SYS, "q", {})
    await harness.adapter.ask(SYS, "q")

    assert turn_lines(capsys.readouterr().out) == [
        {"backend": harness.backend_id, "thread": "5150", "cap": "priv", "tools": "1", "skills_loaded": "0", "outcome": "ok"},
        {"backend": harness.backend_id, "thread": "-", "cap": "ro", "tools": "0", "skills_loaded": "0", "outcome": "auth_expired"},
        {"backend": harness.backend_id, "thread": "-", "cap": "ro", "tools": "0", "skills_loaded": "0", "outcome": "ok"},
    ]
