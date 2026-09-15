"""AC-17 — 게이트 케이스 표를 각 백엔드 wiring 진입점으로 실행 (Claude = HookMatcher 훅 콜백, Codex = 훅 스크립트
+ 게이트 엔드포인트, Grok = permission 핸들러). 런타임 fake가 도구 호출을 시작하면 wiring이 판정한다.

행별 관측값: 도구 실행 여부 + `[gate]` 로그의 decision·reason(= decide() 사유)·via·cap·backend.
CLI 가드 행(priv `Write .claude/skills/x/SKILL.md`)은 실행 여부만 비교한다 — Claude는 훅 allow 후 CLI가 차단하고,
Codex/Grok은 runtime_guard가 deny한다. `privileged` = approve_skill_writes is True (무인 초기자는 미전달).
도구명·사유는 백엔드 정규화 결과(harness.normalize_tool — 파일 쓰기 계열이 Write로 합쳐지는 런타임)로 계산하고,
CAPABILITY_MATRIX가 `unsupported`인 (백엔드, 권한) 조합(§11.3 — 예: Codex ro 턴의 셸 기반 Read)은 미실행만 비교한다.
"""
from dataclasses import dataclass, field

import pytest

from core.runtimes.tool_names import CAPABILITY_MATRIX, UNSUPPORTED
from core.safety_gate import (
    UNKNOWN_TOOL,
    _MUTATION_MCP_TOOLS,
    _UNATTENDED_DENIED_TOOLS,
    CanonicalToolCall,
    decide,
)
from tests.contract.harness import SYS, gate_lines
from tests.contract.scenario import gate_probe, text

ALLOW, DENY, CLI_GUARD = "allow", "deny", "cli_guard"
GATED_TOOLS = _UNATTENDED_DENIED_TOOLS | _MUTATION_MCP_TOOLS


@dataclass(frozen=True)
class Row:
    id: str
    tool: str
    input: dict
    ro: str
    priv: str
    canonical: str | None = None  # 런타임 표기가 정규명과 다를 때(apply_patch → Write)
    paths: list = field(default_factory=list)  # 정규 호출의 file_paths
    unresolved_paths: bool = False

    @property
    def canonical_name(self) -> str:
        return self.canonical or self.tool


def _write(tool_name, path):
    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    extra = {
        "Write": {"content": "x"},
        "Edit": {"old_string": "a", "new_string": "b"},
        "MultiEdit": {"edits": [{"old_string": "a", "new_string": "b"}]},
        "NotebookEdit": {"new_source": "x"},
    }[tool_name]
    return {key: path, **extra}


PLAIN_PATH = "prompts/memory.md"
CLAUDE_SKILL = ".claude/skills/x/SKILL.md"
SCIENCE_REFERENCE = ".claude/skills/science-reference/SKILL.md"
AGENT_MADE = ".agent-made/x/SKILL.md"

ROWS = [
    Row("bash", "Bash", {"command": "echo hi"}, DENY, ALLOW),
    *[
        Row(f"{t.lower()}-plain-path", t, _write(t, PLAIN_PATH), DENY, ALLOW, paths=[PLAIN_PATH])
        for t in ("Write", "Edit", "MultiEdit", "NotebookEdit")
    ],
    Row("write-claude-skills", "Write", _write("Write", CLAUDE_SKILL), DENY, CLI_GUARD, paths=[CLAUDE_SKILL]),
    Row("write-science-reference", "Write", _write("Write", SCIENCE_REFERENCE), DENY, DENY, paths=[SCIENCE_REFERENCE]),
    Row("write-agent-made", "Write", _write("Write", AGENT_MADE), DENY, ALLOW, paths=[AGENT_MADE]),
    Row("schedule-create", "mcp__schedule__schedule_create",
        {"prompt": "매일 수면 요약", "schedule": "0 9 * * *", "deliver_channel_id": None, "max_turns": None}, DENY, ALLOW),
    *[
        Row(f"schedule-{op}", f"mcp__schedule__schedule_{op}", {"id": "missing-job"}, DENY, ALLOW)
        for op in ("pause", "resume", "remove")
    ],
    Row("memory-add", "mcp__memory__add_memory", {"target": "memory", "content": "새 기억"}, DENY, ALLOW),
    Row("memory-replace", "mcp__memory__replace_memory", {"target": "memory", "index": 0, "content": "교체"}, DENY, ALLOW),
    Row("memory-remove", "mcp__memory__remove_memory", {"target": "memory", "index": 0}, DENY, ALLOW),
    Row("read", "Read", {"file_path": "prompts/goals.md"}, ALLOW, ALLOW),
    Row("glob", "Glob", {"pattern": "prompts/*.md"}, ALLOW, ALLOW),
    Row("grep", "Grep", {"pattern": "수면"}, ALLOW, ALLOW),
    Row("schedule-list", "mcp__schedule__schedule_list", {}, ALLOW, ALLOW),
    Row("memory-list", "mcp__memory__list_memory", {"target": "memory"}, ALLOW, ALLOW),
]
SKILLS_ROW = Row("skills-load", "mcp__skills__load_skill", {"name": "sleep-analysis"}, ALLOW, ALLOW)
PATCH_TWO_PATHS = (
    "*** Begin Patch\n"
    f"*** Add File: {PLAIN_PATH}\n+x\n"
    f"*** Update File: {SCIENCE_REFERENCE}\n@@\n-a\n+b\n"
    "*** End Patch\n"
)
APPLY_PATCH_ROWS = [
    Row("apply-patch-two-paths", "apply_patch", {"input": PATCH_TWO_PATHS}, DENY, DENY,
        canonical="Write", paths=[PLAIN_PATH, SCIENCE_REFERENCE]),
    Row("apply-patch-no-paths", "apply_patch", {"input": "*** Begin Patch\n*** End Patch\n"}, DENY, DENY,
        canonical="Write", unresolved_paths=True),
]
UNKNOWN_ROW = Row("unknown", UNKNOWN_TOOL, {}, DENY, ALLOW)
CAPS = pytest.mark.parametrize("cap", ["ro", "priv"])


def _unsupported(harness, name, cap) -> bool:
    key = name if name in CAPABILITY_MATRIX else ("mcp__*" if name.startswith("mcp__") else None)
    return key is not None and CAPABILITY_MATRIX[key][harness.backend_id][cap] == UNSUPPORTED


async def _run_row(harness, capsys, row, cap):
    privileged = cap == "priv"
    expect = row.priv if privileged else row.ro
    name = harness.normalize_tool(row.canonical_name)
    before = harness.env.state_snapshot()
    capsys.readouterr()
    harness.fake.script(gate_probe(row.tool, row.input), text("끝"))

    await harness.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True if privileged else None)

    lines = gate_lines(capsys.readouterr().out, tool=name)
    (execution,) = harness.fake.executions
    observed = [(l["backend"], l["cap"], l["decision"], l["via"], l["reason"]) for l in lines]
    if _unsupported(harness, name, cap):  # 이 백엔드·권한에 수단 없음 — 미실행만 비교
        assert not execution.executed
        assert harness.env.state_snapshot() == before
    elif expect == DENY:
        call = CanonicalToolCall(name, file_paths=list(row.paths), unresolved_paths=row.unresolved_paths)
        _, reason = decide(call, privileged, runtime_guard=False)
        assert not execution.executed
        assert observed == [(harness.backend_id, cap, "deny", harness.gate_via, reason)]
        assert harness.env.state_snapshot() == before  # 핸들러 미호출
    elif expect == ALLOW:
        assert execution.executed
        assert "denied_by" not in (execution.result_text or "")
        assert all(decision == "allow" for _, _, decision, _, _ in observed)
        if name in GATED_TOOLS:
            assert observed == [(harness.backend_id, cap, "allow", harness.gate_via, "-")]
    else:  # CLI 가드 행 — 실행 여부만 비교
        assert not execution.executed


@pytest.mark.asyncio
@CAPS
@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.id)
async def test_gate_table_row(harness, capsys, row, cap):
    await _run_row(harness, capsys, row, cap)


@pytest.mark.asyncio
@pytest.mark.ac16
@CAPS
async def test_gate_table_skills_tool_row(harness, capsys, cap):
    """`mcp__skills__load_skill` 행 — skills 도구는 registry 모드에만 존재."""
    await _run_row(harness, capsys, SKILLS_ROW, cap)


@pytest.mark.asyncio
@pytest.mark.backends("codex")
@CAPS
@pytest.mark.parametrize("row", APPLY_PATCH_ROWS, ids=lambda r: r.id)
async def test_gate_table_apply_patch_row(harness, capsys, row, cap):
    await _run_row(harness, capsys, row, cap)


@pytest.mark.asyncio
@pytest.mark.backends("grok")
@CAPS
async def test_gate_table_unknown_tool_row(harness, capsys, cap):
    await _run_row(harness, capsys, UNKNOWN_ROW, cap)
