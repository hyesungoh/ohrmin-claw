"""AC-15 · AC-18 — 런타임에 노출되는 ServerSpec·strict schema, 도구 결과 바이트, 이름 규칙, text→tool→text 스트림."""
import re

import pytest

from core.tool_server.schema import to_strict_json_schema
from core.tool_server.spec import canonical_tool_name
from tests.contract.harness import SYS, Recorder, turn_lines
from tests.contract.scenario import text, tool

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
BASE_SERVERS = ["body_metrics", "garmin", "memory", "schedule", "session_search"]
PRIVILEGE = pytest.mark.parametrize("privileged", [True, False], ids=["priv", "ro"])


def _approve(privileged):
    """봇 표면 그대로 — 인터랙티브 오너 턴만 True, 무인 초기자는 미전달(None)."""
    return True if privileged else None


def _snapshot(specs):
    return {
        spec.name: {t.name: (t.description, to_strict_json_schema(t.params)) for t in spec.tools}
        for spec in specs
    }


def _spec_tool(harness, server, name):
    spec = next(s for s in harness.specs if s.name == server)
    return next(t for t in spec.tools if t.name == name)


@pytest.mark.asyncio
@PRIVILEGE
async def test_runtime_exposes_server_specs_with_strict_schemas(harness, privileged):
    harness.fake.script(text("ok"))
    await harness.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=_approve(privileged))

    exposed = await harness.fake.list_exposed_tools()

    expected_servers = BASE_SERVERS + (["skills"] if harness.registry else [])
    assert sorted(exposed) == sorted(expected_servers)
    assert exposed == _snapshot(harness.specs)
    assert sum(len(tools) for tools in exposed.values()) == (24 if harness.registry else 22)
    # activity_id 3곳만 non-nullable (그 외 선택 파라미터는 전부 [T, "null"]).
    non_nullable = sorted(
        f"{server}.{name}.{key}"
        for server, tools in exposed.items()
        for name, (_, schema) in tools.items()
        for key, prop in schema["properties"].items()
        if not isinstance(prop["type"], list)
    )
    expected_non_nullable = [
        "garmin.get_activity_detail.activity_id",
        "garmin.get_activity_hr_zones.activity_id",
        "garmin.get_activity_splits.activity_id",
    ]
    if harness.registry:
        expected_non_nullable += [
            "skills.load_skill.name", "skills.read_skill_file.name", "skills.read_skill_file.path",
        ]
    assert non_nullable == sorted(expected_non_nullable)


@pytest.mark.asyncio
async def test_exposed_tool_names_follow_name_rules(harness):
    harness.fake.script(text("ok"))
    await harness.adapter.ask_with_context(SYS, "q", {})

    exposed = await harness.fake.list_exposed_tools()

    for server, tools in exposed.items():
        for name in tools:
            for rendered in (canonical_tool_name(server, name), f"{server}__{name}"):
                assert NAME_RE.match(rendered), rendered


@pytest.mark.asyncio
@PRIVILEGE
async def test_read_tool_result_bytes_equal_direct_handler(harness, privileged):
    harness.env.body_metrics_mgr.add_entry(date="2026-09-10", weight_kg=80.5, body_fat_pct=21.0)
    harness.env.body_metrics_mgr.add_entry(date="2026-09-14", weight_kg=79.9, body_fat_pct=20.6)
    harness.fake.script(
        tool("mcp__body_metrics__get_body_metrics_history", {"count": 5, "days": None}),
        tool("mcp__garmin__get_sleep", {"start": None, "end": None}),
        text("끝"),
    )

    await harness.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=_approve(privileged))

    history, sleep = harness.fake.executions
    direct_history = await _spec_tool(harness, "body_metrics", "get_body_metrics_history").handler({"count": 5})
    direct_sleep = await _spec_tool(harness, "garmin", "get_sleep").handler({})
    assert history.executed and not history.is_error
    assert history.result_text.encode() == direct_history["content"][0]["text"].encode()
    assert sleep.executed and not sleep.is_error
    assert sleep.result_text.encode() == direct_sleep["content"][0]["text"].encode()


@pytest.mark.asyncio
async def test_required_param_null_is_validation_error_without_handler_call(harness):
    harness.fake.script(tool("mcp__garmin__get_activity_detail", {"activity_id": None}), text("끝"))

    await harness.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True)

    (execution,) = harness.fake.executions
    assert execution.is_error
    assert "validation error" in execution.result_text.lower()
    assert "get_activity_detail" not in harness.env.garmin.calls


@pytest.mark.asyncio
async def test_text_tool_text_stream_one_shot(harness, capsys):
    harness.fake.script(text("첫"), tool("mcp__garmin__get_sleep", {"start": None, "end": None}), text("둘"))
    rec = Recorder()
    capsys.readouterr()

    result = await harness.adapter.ask_with_context(
        SYS, "q", {}, on_text=rec.on_text, on_tool=rec.on_tool, counter=rec.counter
    )

    assert rec.texts == ["첫", "둘"]
    assert result == "첫\n둘"
    assert rec.tools == ["mcp__garmin__get_sleep"]
    assert rec.counter == [1]
    assert turn_lines(capsys.readouterr().out) == [{
        "backend": harness.backend_id, "thread": "-", "cap": "ro", "tools": "1", "skills_loaded": "0", "outcome": "ok",
    }]


@pytest.mark.asyncio
async def test_text_tool_text_stream_thread_session_canonical_tool_names(harness, capsys):
    harness.fake.script(
        text("첫"),
        tool("WebSearch", {"query": "zone 2"}),
        tool("mcp__body_metrics__get_body_metrics_history", {"count": None, "days": None}),
        text("둘"),
        tool("Read", {"file_path": "prompts/goals.md"}),
        text("셋"),
    )
    rec = Recorder()
    capsys.readouterr()

    result = await harness.adapter.ask_with_context(
        SYS, "q", {}, on_text=rec.on_text, on_tool=rec.on_tool, counter=rec.counter,
        approve_skill_writes=True, thread_id=4242,
    )

    assert rec.texts == ["첫", "둘", "셋"]
    assert result == "첫\n둘\n셋"
    assert rec.tools == ["WebSearch", "mcp__body_metrics__get_body_metrics_history", "Read"]
    assert rec.counter == [3]
    assert turn_lines(capsys.readouterr().out) == [{
        "backend": harness.backend_id, "thread": "4242", "cap": "priv", "tools": "3", "skills_loaded": "0",
        "outcome": "ok",
    }]
