"""SafetyGate — core/safety_gate.py 이동(re-export) · decide() · runtime_guard · 다중 경로 · apply_patch fail-closed · Unknown.

AC-17 게이트 케이스 표(privileged = approve_skill_writes is True)를 decide()와 Claude 훅 wiring으로 실행한다.
"""
import os
import subprocess
import sys

import pytest

import core.llm as llm_module
import core.safety_gate as safety_gate
from core.gate_wiring import ClaudeHookWiring, GateWiring
from core.llm_errors import StartupError
from core.runtimes.tool_names import normalize_codex_hook_payload, normalize_grok_tool_call
from core.safety_gate import (
    RUNTIME_GUARD_REASON,
    UNKNOWN_TOOL,
    UNKNOWN_TOOL_REASON,
    UNRESOLVED_PATHS_REASON,
    CanonicalToolCall,
    _MUTATION_MCP_TOOLS,
    decide,
    evaluate_tool_gate,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRITE_TOOLS = ["Write", "Edit", "MultiEdit", "NotebookEdit"]
SKILL_APPROVAL_REASON = "스킬 파일 쓰기는 세션 승인(approve_skill_writes)이 필요합니다."
SCIENCE_REASON = "science-reference 스킬은 읽기 전용입니다 (수정 불가)."


def _ro_write_reason(tool):
    return f"무인 턴은 읽기 전용입니다 — {tool}는 인터랙티브 오너 세션에서만 허용됩니다."


def _patch(*headers):
    body = "\n".join(f"*** {h}\n+x" for h in headers)
    return {"tool_name": "apply_patch", "tool_input": {"input": f"*** Begin Patch\n{body}\n*** End Patch\n"}}


# ── 이동 · re-export ────────────────────────────────────────────────


class TestMovedAndReExported:
    @pytest.mark.parametrize("name", [
        "evaluate_skill_write_gate", "evaluate_tool_gate", "_MUTATION_MCP_TOOLS", "_UNATTENDED_DENIED_TOOLS",
        "_UNATTENDED_TOOL_MATCHER", "_MUTATION_TOOL_MATCHER", "_WRITE_TOOLS", "_skill_path_segments", "_contains_subseq",
    ])
    def test_core_llm_reexports_same_objects(self, name):
        assert getattr(llm_module, name) is getattr(safety_gate, name)

    def test_constants_unchanged(self):
        assert safety_gate._UNATTENDED_TOOL_MATCHER == "Bash|Write|Edit|MultiEdit|NotebookEdit"
        assert safety_gate._MUTATION_TOOL_MATCHER == (
            r"mcp__schedule__schedule_(create|pause|resume|remove)|mcp__memory__(add_memory|replace_memory|remove_memory)"
        )
        assert safety_gate._UNATTENDED_DENIED_TOOLS == {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}
        assert len(_MUTATION_MCP_TOOLS) == 7

    def test_gate_modules_do_not_load_claude_sdk(self):
        code = (
            "import sys; import core.safety_gate, core.gate_wiring, core.runtimes.tool_names, core.skill_registry, "
            "core.tool_server.capability, core.tool_server.http_server; print('claude_agent_sdk' in sys.modules)"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "False"


# ── decide() = evaluate_tool_gate 규칙 (경로 없음/단일 경로) ──────────


class TestDecideMatchesExistingRules:
    @pytest.mark.parametrize("privileged", [False, True])
    @pytest.mark.parametrize("runtime_guard", [False, True])
    @pytest.mark.parametrize("tool", sorted(_MUTATION_MCP_TOOLS) + [
        "Bash", "Read", "Glob", "Grep", "Skill", "WebSearch", "WebFetch",
        "mcp__schedule__schedule_list", "mcp__memory__list_memory", "mcp__garmin__get_sleep",
    ])
    def test_no_path_calls_equal_evaluate_tool_gate(self, tool, privileged, runtime_guard):
        assert decide(CanonicalToolCall(tool), privileged, runtime_guard) == evaluate_tool_gate(tool, {}, privileged)

    @pytest.mark.parametrize("privileged", [False, True])
    @pytest.mark.parametrize("path", [
        "prompts/memory.md", "data/cron_jobs.json", ".claude/skills/x/SKILL.md",
        ".claude/skills/science-reference/SKILL.md", "/proj/.claude/skills/science-reference/references/x.md",
        ".agent-made/x/SKILL.md", "/tmp/anything.txt",
    ])
    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_single_path_without_runtime_guard_equals_evaluate_tool_gate(self, tool, path, privileged):
        expected = evaluate_tool_gate(tool, {"file_path": path}, privileged)
        assert decide(CanonicalToolCall(tool, [path]), privileged, runtime_guard=False) == expected

    def test_from_claude_reads_file_or_notebook_path(self):
        assert CanonicalToolCall.from_claude(
            {"tool_name": "Write", "tool_input": {"file_path": "a.md"}}
        ) == CanonicalToolCall("Write", ["a.md"])
        assert CanonicalToolCall.from_claude(
            {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "n.ipynb"}}
        ) == CanonicalToolCall("NotebookEdit", ["n.ipynb"])
        assert CanonicalToolCall.from_claude(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        ) == CanonicalToolCall("Bash", [], command="ls")
        assert CanonicalToolCall.from_claude(None) == CanonicalToolCall("")


# ── AC-17 게이트 케이스 표 (decide 진입점) ────────────────────────────


class TestAc17GateTable:
    @pytest.mark.parametrize("runtime_guard", [False, True])
    def test_bash(self, runtime_guard):
        assert decide(CanonicalToolCall("Bash"), False, runtime_guard) == (False, _ro_write_reason("Bash"))
        assert decide(CanonicalToolCall("Bash"), True, runtime_guard) == (True, "")

    @pytest.mark.parametrize("runtime_guard", [False, True])
    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_plain_path_write(self, tool, runtime_guard):
        call = CanonicalToolCall(tool, ["prompts/memory.md"])
        assert decide(call, False, runtime_guard) == (False, _ro_write_reason(tool))
        assert decide(call, True, runtime_guard) == (True, "")

    def test_apply_patch_two_paths_including_science_reference(self):
        call = normalize_codex_hook_payload(_patch(
            "Update File: prompts/memory.md", "Add File: .claude/skills/science-reference/SKILL.md",
        ))
        assert call.file_paths == ["prompts/memory.md", ".claude/skills/science-reference/SKILL.md"]
        assert decide(call, False, runtime_guard=True) == (False, _ro_write_reason("Write"))
        assert decide(call, True, runtime_guard=True) == (False, SCIENCE_REASON)

    @pytest.mark.parametrize("payload", [
        {"tool_name": "apply_patch", "tool_input": {"input": "*** Begin Patch\n*** End Patch\n"}},
        {"tool_name": "apply_patch", "tool_input": {}},
        {"tool_name": "apply_patch"},
    ])
    def test_apply_patch_path_extraction_failure_fail_closed(self, payload):
        call = normalize_codex_hook_payload(payload)
        assert call.unresolved_paths is True
        assert decide(call, False, runtime_guard=True) == (False, UNRESOLVED_PATHS_REASON)
        assert decide(call, True, runtime_guard=True) == (False, UNRESOLVED_PATHS_REASON)

    def test_claude_skills_write_cli_guard_row(self):
        call = CanonicalToolCall("Write", [".claude/skills/x/SKILL.md"])
        assert decide(call, False, runtime_guard=False) == (False, SKILL_APPROVAL_REASON)
        assert decide(call, False, runtime_guard=True) == (False, SKILL_APPROVAL_REASON)
        # Claude wiring: 훅은 allow (CLI가 네이티브로 차단). Codex/Grok: runtime_guard deny.
        assert decide(call, True, runtime_guard=False) == (True, "")
        assert decide(call, True, runtime_guard=True) == (False, RUNTIME_GUARD_REASON)

    @pytest.mark.parametrize("runtime_guard", [False, True])
    def test_science_reference_write(self, runtime_guard):
        call = CanonicalToolCall("Write", [".claude/skills/science-reference/SKILL.md"])
        assert decide(call, False, runtime_guard) == (False, SCIENCE_REASON)
        assert decide(call, True, runtime_guard) == (False, SCIENCE_REASON)

    @pytest.mark.parametrize("runtime_guard", [False, True])
    def test_agent_made_write(self, runtime_guard):
        call = CanonicalToolCall("Write", [".agent-made/x/SKILL.md"])
        assert decide(call, False, runtime_guard) == (False, _ro_write_reason("Write"))
        assert decide(call, True, runtime_guard) == (True, "")

    @pytest.mark.parametrize("tool", sorted(_MUTATION_MCP_TOOLS))
    def test_mutation_mcp(self, tool):
        assert decide(CanonicalToolCall(tool), False, True) == (
            False, f"{tool}는 인터랙티브 오너 세션 승인이 필요합니다 (무인 턴 차단)."
        )
        assert decide(CanonicalToolCall(tool), True, True) == (True, "")

    @pytest.mark.parametrize("tool", [
        "Read", "Glob", "Grep", "mcp__schedule__schedule_list", "mcp__memory__list_memory", "mcp__skills__load_skill",
    ])
    def test_read_tools_always_allowed(self, tool):
        for privileged in (False, True):
            assert decide(CanonicalToolCall(tool), privileged, True) == (True, "")

    def test_grok_unknown(self):
        call = normalize_grok_tool_call({"kind": "other", "title": "mystery"})
        assert call.name == UNKNOWN_TOOL
        assert decide(call, False, runtime_guard=True) == (False, UNKNOWN_TOOL_REASON)
        assert decide(call, True, runtime_guard=True) == (True, "")

    @pytest.mark.parametrize("kind", ["edit", "delete", "move"])
    def test_grok_write_kind_without_paths_fail_closed(self, kind):
        """Codex apply_patch 경로 추출 실패와 동일 — 경로 없는 Grok 쓰기는 priv·ro 모두 deny."""
        call = normalize_grok_tool_call({"kind": kind, "title": "Edit file", "rawInput": {"content": "x"}})
        assert call.unresolved_paths
        assert decide(call, False, runtime_guard=True) == (False, UNRESOLVED_PATHS_REASON)
        assert decide(call, True, runtime_guard=True) == (False, UNRESOLVED_PATHS_REASON)
        assert UNRESOLVED_PATHS_REASON == "패치 경로를 확인할 수 없어 차단합니다."


# ── runtime_guard · 다중 경로 ───────────────────────────────────────


class TestRuntimeGuardAndMultiPath:
    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    @pytest.mark.parametrize("path", [".claude/settings.json", "/proj/.claude/hooks/x.sh", "a/.claude/../.claude/x"])
    def test_any_dot_claude_segment_denied_when_guarded(self, tool, path):
        assert decide(CanonicalToolCall(tool, [path]), True, runtime_guard=True) == (False, RUNTIME_GUARD_REASON)
        assert decide(CanonicalToolCall(tool, [path]), True, runtime_guard=False) == (True, "")

    @pytest.mark.parametrize("path", [".claudex/x.md", "docs/claude/x.md", "prompts/.claude.md"])
    def test_segment_match_not_substring(self, path):
        assert decide(CanonicalToolCall("Write", [path]), True, runtime_guard=True) == (True, "")

    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_guarded_write_without_paths_fail_closed(self, tool):
        call = CanonicalToolCall(tool)
        assert decide(call, False, runtime_guard=True) == (False, UNRESOLVED_PATHS_REASON)
        assert decide(call, True, runtime_guard=True) == (False, UNRESOLVED_PATHS_REASON)
        # Claude wiring(runtime_guard=False)은 기존 규칙 그대로.
        assert decide(call, False, runtime_guard=False) == evaluate_tool_gate(tool, {}, False)
        assert decide(call, True, runtime_guard=False) == (True, "")
        hook_call = normalize_codex_hook_payload({"tool_name": tool, "tool_input": {"content": "x"}})
        assert decide(hook_call, True, runtime_guard=True) == (False, UNRESOLVED_PATHS_REASON)

    def test_runtime_guard_only_for_write_tools(self):
        assert decide(CanonicalToolCall("Bash", command="echo x > .claude/skills/a"), True, True) == (True, "")
        assert decide(CanonicalToolCall("Read", [".claude/skills/a/SKILL.md"]), True, True) == (True, "")

    def test_multi_path_any_deny_wins(self):
        call = CanonicalToolCall("Write", ["prompts/memory.md", ".agent-made/y/SKILL.md", ".claude/skills/z/SKILL.md"])
        assert decide(call, True, runtime_guard=True) == (False, RUNTIME_GUARD_REASON)
        assert decide(call, True, runtime_guard=False) == (True, "")

    def test_multi_path_first_rule_deny_reason(self):
        call = CanonicalToolCall("Edit", [".claude/skills/a/SKILL.md", ".claude/skills/science-reference/SKILL.md"])
        # 경로별 evaluate_tool_gate가 runtime_guard보다 먼저 — science-reference 사유.
        assert decide(call, True, runtime_guard=True) == (False, SCIENCE_REASON)

    def test_all_paths_allowed(self):
        call = CanonicalToolCall("Write", ["prompts/memory.md", ".agent-made/y/SKILL.md"])
        assert decide(call, True, runtime_guard=True) == (True, "")


# ── ClaudeHookWiring ───────────────────────────────────────────────


class TestClaudeHookWiring:
    def test_wiring_shape(self):
        wiring = ClaudeHookWiring()
        assert isinstance(wiring, GateWiring)
        assert wiring.name == "claude-hook"
        assert wiring.runtime_guard is False
        assert wiring.matchers == (safety_gate._UNATTENDED_TOOL_MATCHER, safety_gate._MUTATION_TOOL_MATCHER)

    @pytest.mark.asyncio
    async def test_hook_logs_decisions_via_hook(self, capsys):
        wiring = ClaudeHookWiring()
        denied = await wiring.make_hook(False)({"tool_name": "Bash", "tool_input": {"command": "ls"}}, None, {})
        allowed = await wiring.make_hook(True)(
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/skills/x/SKILL.md"}}, None, {}
        )
        assert denied == {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": _ro_write_reason("Bash"),
        }}
        assert allowed == {}  # runtime_guard=False — CLI가 네이티브로 차단(tests/test_llm_phase0.py 보존)
        assert capsys.readouterr().out.splitlines() == [
            f"[gate] backend=claude cap=ro tool=Bash decision=deny via=hook reason={_ro_write_reason('Bash')}",
            "[gate] backend=claude cap=priv tool=Write decision=allow via=hook reason=-",
        ]

    @pytest.mark.asyncio
    async def test_privileged_only_when_approve_is_true(self):
        hook = ClaudeHookWiring().make_hook(1)
        out = await hook({"tool_name": "Bash", "tool_input": {}}, None, {})
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_legacy_hook_factory_uses_decide(self, monkeypatch):
        seen = []

        def fake_decide(call, privileged, runtime_guard):
            seen.append((call, privileged, runtime_guard))
            return True, ""

        monkeypatch.setattr("core.gate_wiring.decide", fake_decide)
        out = await llm_module._make_unattended_gate_hook(approve_skill_writes=True)(
            {"tool_name": "Edit", "tool_input": {"file_path": "a.md"}}, None, {}
        )
        assert out == {}
        assert seen == [(CanonicalToolCall("Edit", ["a.md"]), True, False)]

    @pytest.mark.asyncio
    async def test_probe_ok_logs_probe_lines(self, capsys):
        await ClaudeHookWiring().probe()
        assert capsys.readouterr().out.splitlines() == [
            f"[gate] backend=claude cap=ro tool=Bash decision=deny via=probe reason={_ro_write_reason('Bash')}",
            "[gate] backend=claude cap=priv tool=Bash decision=allow via=probe reason=-",
        ]

    @pytest.mark.asyncio
    async def test_probe_mismatch_raises(self, monkeypatch):
        monkeypatch.setattr("core.gate_wiring.decide", lambda call, privileged, runtime_guard: (False, "x"))
        with pytest.raises(StartupError) as exc:
            await ClaudeHookWiring().probe()
        assert "cap=priv" in exc.value.cause
