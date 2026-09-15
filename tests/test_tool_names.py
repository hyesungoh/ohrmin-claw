"""정규 도구명 — 3 백엔드 정규화 · render_tool_refs · CAPABILITY_MATRIX 스냅샷 · backend_tool_note · translate_allowed_tools."""
import pytest

from core.llm import DEFAULT_ALLOWED_TOOLS
from core.runtimes.tool_names import (
    CAPABILITY_MATRIX,
    backend_tool_note,
    codex_item_on_tool_name,
    extract_patch_paths,
    normalize_codex_hook_payload,
    normalize_grok_tool_call,
    on_tool_name,
    render_tool_refs,
    translate_allowed_tools,
)
from core.safety_gate import CanonicalToolCall

# bot/main.py UNATTENDED_ALLOWED_TOOLS 사본 (bot import 없이 번역 스냅샷).
UNATTENDED = [
    "Read", "Glob", "Grep", "Skill", "WebSearch", "WebFetch",
    "mcp__garmin", "mcp__body_metrics", "mcp__session_search",
    "mcp__schedule__schedule_list",
]


class TestClaude:
    @pytest.mark.parametrize("name", ["Bash", "Skill", "mcp__garmin__get_sleep", "mcp__skills__load_skill", "Write"])
    def test_native_identity(self, name):
        assert on_tool_name(name, skills_registry=False) == name

    def test_registry_maps_skills_mcp_to_skill(self):
        assert on_tool_name("mcp__skills__load_skill", skills_registry=True) == "Skill"
        assert on_tool_name("mcp__skills__read_skill_file", skills_registry=True) == "Skill"
        assert on_tool_name("mcp__garmin__get_sleep", skills_registry=True) == "mcp__garmin__get_sleep"
        assert on_tool_name("mcp__skillsx__load", skills_registry=True) == "mcp__skillsx__load"


class TestCodexHookPayload:
    def test_bash(self):
        assert normalize_codex_hook_payload(
            {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        ) == CanonicalToolCall("Bash", command="ls -la")

    def test_bash_command_list_joined(self):
        call = normalize_codex_hook_payload({"tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", "ls"]}})
        assert call == CanonicalToolCall("Bash", command="bash -lc ls")

    def test_camel_case_keys(self):
        assert normalize_codex_hook_payload(
            {"toolName": "Bash", "toolInput": {"command": "pwd"}}
        ) == CanonicalToolCall("Bash", command="pwd")

    def test_apply_patch_all_header_paths(self):
        patch_text = (
            "*** Begin Patch\n"
            "*** Add File: a/new.md\n+hi\n"
            "*** Update File: prompts/memory.md\n@@\n-x\n+y\n"
            "*** Move to: prompts/moved.md\n"
            "*** Delete File: old.txt\n"
            "*** End Patch\n"
        )
        expected = ["a/new.md", "prompts/memory.md", "prompts/moved.md", "old.txt"]
        assert extract_patch_paths(patch_text) == expected
        for key in ("input", "patch", "command"):
            call = normalize_codex_hook_payload({"tool_name": "apply_patch", "tool_input": {key: patch_text}})
            assert call == CanonicalToolCall("Write", expected)

    def test_apply_patch_first_string_key_wins(self):
        call = normalize_codex_hook_payload({"tool_name": "apply_patch", "tool_input": {
            "input": "*** Begin Patch\n*** Add File: first.md\n*** End Patch",
            "patch": "*** Begin Patch\n*** Add File: second.md\n*** End Patch",
        }})
        assert call.file_paths == ["first.md"]

    def test_apply_patch_command_list_and_raw_string(self):
        patch_text = "*** Begin Patch\n*** Update File: x.md\n*** End Patch"
        assert normalize_codex_hook_payload(
            {"tool_name": "apply_patch", "tool_input": {"command": ["apply_patch", patch_text]}}
        ).file_paths == ["x.md"]
        assert normalize_codex_hook_payload(
            {"toolName": "apply_patch", "toolInput": patch_text}
        ).file_paths == ["x.md"]

    def test_apply_patch_unresolved(self):
        call = normalize_codex_hook_payload({"tool_name": "apply_patch", "tool_input": {"input": "no headers"}})
        assert call == CanonicalToolCall("Write", [], unresolved_paths=True)

    def test_mcp_name_kept(self):
        assert normalize_codex_hook_payload(
            {"tool_name": "mcp__schedule__schedule_create", "tool_input": {"prompt": "p"}}
        ) == CanonicalToolCall("mcp__schedule__schedule_create")

    def test_unknown_name_kept_with_file_path(self):
        assert normalize_codex_hook_payload({"tool_name": "Write", "tool_input": {"file_path": "a.md"}}) == (
            CanonicalToolCall("Write", ["a.md"])
        )
        assert normalize_codex_hook_payload({"tool_name": "local_shell"}) == CanonicalToolCall("local_shell")

    @pytest.mark.parametrize("payload", [{}, {"tool_input": {"command": "ls"}}, [], "x", None])
    def test_missing_name_is_unknown(self, payload):
        assert normalize_codex_hook_payload(payload).name == "Unknown"


class TestCodexItems:
    @pytest.mark.parametrize("item, expected", [
        ({"type": "commandExecution", "command": "ls"}, "Bash"),
        # 파싱된 명령 종류(commandActions)가 전부 같은 읽기 종류면 정규 읽기 도구명, 섞이거나 unknown이면 Bash.
        ({"type": "commandExecution", "commandActions": [{"type": "read", "path": "a.md"}]}, "Read"),
        ({"type": "commandExecution", "commandActions": [{"type": "listFiles"}]}, "Glob"),
        ({"type": "commandExecution", "commandActions": [{"type": "search", "query": "q"}]}, "Grep"),
        ({"type": "commandExecution", "commandActions": [{"type": "read"}, {"type": "search"}]}, "Bash"),
        ({"type": "commandExecution", "commandActions": [{"type": "unknown", "command": "rm x"}]}, "Bash"),
        ({"type": "commandExecution", "commandActions": []}, "Bash"),
        ({"type": "fileChange", "changes": []}, "Write"),
        ({"type": "webSearch", "query": "q"}, "WebSearch"),
        ({"type": "mcpToolCall", "server": "garmin", "tool": "get_sleep"}, "mcp__garmin__get_sleep"),
        ({"type": "mcpToolCall", "server": "skills", "tool": "load_skill"}, "Skill"),
        ({"type": "agentMessage", "text": "hi"}, None),
        ({"type": "reasoning"}, None),
        ({}, None),
    ])
    def test_item_on_tool_name(self, item, expected):
        assert codex_item_on_tool_name(item) == expected


class TestGrokToolCall:
    def test_execute(self):
        assert normalize_grok_tool_call(
            {"kind": "execute", "title": "Run ls", "rawInput": {"command": "ls"}}
        ) == CanonicalToolCall("Bash", command="ls")

    @pytest.mark.parametrize("kind", ["edit", "delete", "move"])
    def test_write_kinds_collect_locations_and_raw_paths(self, kind):
        call = normalize_grok_tool_call({
            "kind": kind,
            "locations": [{"path": "/proj/a.md"}, {"path": "/proj/b.md", "line": 3}, {"path": "/proj/a.md"}],
            "rawInput": {"path": "/proj/c.md", "file_path": "/proj/b.md"},
        })
        assert call == CanonicalToolCall("Write", ["/proj/a.md", "/proj/b.md", "/proj/c.md"])

    @pytest.mark.parametrize("kind", ["edit", "delete", "move"])
    @pytest.mark.parametrize("tool_call", [
        {},
        {"locations": [], "rawInput": {}},
        {"locations": [{"path": ""}, {"line": 3}, "x"], "rawInput": {"path": "", "file_path": None}},
        {"title": "garmin__get_sleep", "rawInput": {"content": "x"}},
    ], ids=["no_fields", "empty", "blank_paths", "mcp_like_title"])
    def test_write_kinds_without_paths_are_unresolved(self, kind, tool_call):
        call = normalize_grok_tool_call({"kind": kind, **tool_call})
        assert call == CanonicalToolCall("Write", [], unresolved_paths=True)

    @pytest.mark.parametrize("raw, expected", [
        ({"query": "zone 2"}, "WebSearch"),
        ({"query": "zone 2", "url": "https://example.com"}, "WebFetch"),
        ({"url": "https://example.com"}, "WebFetch"),
        ({"query": 3}, "WebFetch"),
    ])
    def test_fetch_query_without_url_is_web_search(self, raw, expected):
        assert normalize_grok_tool_call({"kind": "fetch", "title": "t", "rawInput": raw}).name == expected

    @pytest.mark.parametrize("kind, expected", [
        ("read", "Read"), ("search", "Grep"), ("fetch", "WebFetch"), ("think", "Think"),
    ])
    def test_read_kinds(self, kind, expected):
        assert normalize_grok_tool_call({"kind": kind, "title": "t"}).name == expected

    @pytest.mark.parametrize("title, expected", [
        ("garmin__get_sleep", "mcp__garmin__get_sleep"),
        ("body_metrics__get_body_metrics_history", "mcp__body_metrics__get_body_metrics_history"),
        ("memory__add_memory", "mcp__memory__add_memory"),
        ("schedule__schedule_create", "mcp__schedule__schedule_create"),
        ("session_search__search", "mcp__session_search__search"),
        ("skills__load_skill", "mcp__skills__load_skill"),
    ])
    def test_bot_mcp_titles(self, title, expected):
        assert normalize_grok_tool_call({"kind": "other", "title": title}).name == expected
        assert normalize_grok_tool_call({"kind": "fetch", "title": title}).name == expected

    def test_gated_kind_wins_over_mcp_like_title(self):
        assert normalize_grok_tool_call({"kind": "execute", "title": "garmin__get_sleep"}).name == "Bash"

    @pytest.mark.parametrize("tool_call", [
        {"kind": "other", "title": "mystery"},
        {"title": "other_server__tool"},
        {"kind": "switch_mode"},
        {},
        None,
    ])
    def test_unknown(self, tool_call):
        assert normalize_grok_tool_call(tool_call) == CanonicalToolCall("Unknown")


class TestRenderToolRefs:
    TEXT = (
        "mcp__garmin__get_activities로 조회 후 `mcp__body_metrics__get_body_metrics_history`, "
        "mcp__skills__load_skill(name). 서버 접두 mcp__garmin 과 xmcp__a__b 는 그대로."
    )

    def test_grok_renders_server_tool_form(self):
        assert render_tool_refs(self.TEXT, "grok") == (
            "garmin__get_activities로 조회 후 `body_metrics__get_body_metrics_history`, "
            "skills__load_skill(name). 서버 접두 mcp__garmin 과 xmcp__a__b 는 그대로."
        )

    @pytest.mark.parametrize("backend", ["claude", "codex"])
    def test_other_backends_identity(self, backend):
        assert render_tool_refs(self.TEXT, backend) == self.TEXT

    def test_empty(self):
        assert render_tool_refs("", "grok") == ""


class TestCapabilityMatrix:
    def test_snapshot(self):
        s, g, u = "supported", "gated", "unsupported"

        def row(c, x, k):
            return {"claude": {"priv": c[0], "ro": c[1]}, "codex": {"priv": x[0], "ro": x[1]}, "grok": {"priv": k[0], "ro": k[1]}}

        write = row((s, g), (s, g), (s, g))
        read = row((s, s), (s, s), (s, s))
        shell_read = row((s, s), (s, u), (s, s))
        assert CAPABILITY_MATRIX == {
            "Bash": write,
            "Read": shell_read,
            "Write": write,
            "Edit": write,
            "MultiEdit": write,
            "NotebookEdit": write,
            "Glob": shell_read,
            "Grep": shell_read,
            "Skill": read,
            "WebSearch": read,
            "WebFetch": row((s, s), (u, u), (s, s)),
            "mcp__*": read,
            "mcp__memory__add_memory": write,
            "mcp__memory__remove_memory": write,
            "mcp__memory__replace_memory": write,
            "mcp__schedule__schedule_create": write,
            "mcp__schedule__schedule_pause": write,
            "mcp__schedule__schedule_remove": write,
            "mcp__schedule__schedule_resume": write,
        }

    def test_covers_default_builtins(self):
        assert set(DEFAULT_ALLOWED_TOOLS) <= set(CAPABILITY_MATRIX)

    def test_backend_tool_note(self):
        assert backend_tool_note("claude") == ""
        assert backend_tool_note("codex") == (
            "이 환경에는 WebFetch가 없습니다. Read/Glob/Grep은 셸로 수행되며 읽기 전용 턴에서는 사용할 수 없습니다."
        )
        assert backend_tool_note("grok") == ""


class TestTranslateAllowedTools:
    def test_claude_native_returns_input_as_is(self):
        assert translate_allowed_tools("claude", DEFAULT_ALLOWED_TOOLS) is DEFAULT_ALLOWED_TOOLS
        assert translate_allowed_tools("claude", UNATTENDED) is UNATTENDED
        assert translate_allowed_tools("claude", None) is None

    def test_claude_registry_skill_to_skills_server(self):
        before = list(UNATTENDED)
        assert translate_allowed_tools("claude", UNATTENDED, skills_registry=True) == [
            "Read", "Glob", "Grep", "mcp__skills", "WebSearch", "WebFetch",
            "mcp__garmin", "mcp__body_metrics", "mcp__session_search",
            "mcp__schedule__schedule_list",
        ]
        assert UNATTENDED == before  # 입력 불변
        assert translate_allowed_tools("claude", ["Read"], skills_registry=True) == ["Read"]

    @pytest.mark.parametrize("backend", ["codex", "grok"])
    def test_codex_grok_not_applied(self, backend):
        assert translate_allowed_tools(backend, UNATTENDED) is None
        assert translate_allowed_tools(backend, DEFAULT_ALLOWED_TOOLS, skills_registry=True) is None
