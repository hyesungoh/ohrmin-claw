"""Claude skills 모드 — native(기본): 옵션 현행 동일 / registry: setting_sources=["project"]·skills=[]·Skill 없음·mcp__skills·카탈로그·WARN 1줄.

+ P3 봇 배선: _build_system_prompt 카탈로그(registry/codex/grok), skills ServerSpec, 인터랙티브 턴 image_paths kwarg.
"""
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

from core.llm import DEFAULT_ALLOWED_TOOLS, ClaudeSDKAdapter, create_llm_adapter_from_config
from core.llm_config import load_llm_config
from core.safety_gate import _MUTATION_TOOL_MATCHER, _UNATTENDED_TOOL_MATCHER
from core.skill_registry import SkillRegistry, create_skills_mcp_server
from core.tool_server.capability import CallerCapability
from core.tool_server.claude_sdk_bridge import to_sdk_servers

UNATTENDED = [
    "Read", "Glob", "Grep", "Skill", "WebSearch", "WebFetch",
    "mcp__garmin", "mcp__body_metrics", "mcp__session_search",
    "mcp__schedule__schedule_list",
]


def _config(tmp_path, llm):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": llm}))
    return load_llm_config(str(path), {})


def _skills_dir(tmp_path):
    skills = tmp_path / "skills"
    (skills / "sleep-analysis").mkdir(parents=True)
    (skills / "sleep-analysis" / "SKILL.md").write_text(
        "---\nname: sleep-analysis\ndescription: 수면 분석\n---\n# 수면\n", encoding="utf-8"
    )
    return str(skills)


def _without_hooks(options):
    return {f.name: getattr(options, f.name) for f in dataclasses.fields(options) if f.name != "hooks"}


async def _hook_decision(options, index, approve_input):
    return await options.hooks["PreToolUse"][index].hooks[0](approve_input, None, {})


class TestNativeUnchanged:
    """기본 native 모드 = P3 이전 옵션 조립과 동일 (hooks 콜백 제외 전 필드 + HookMatcher 2개·정규식)."""

    @pytest.mark.parametrize("approve", [None, False, True])
    @pytest.mark.parametrize("allowed", [None, UNATTENDED])
    def test_options_identical_to_pre_p3(self, approve, allowed):
        adapter = ClaudeSDKAdapter(model="m", cwd="/proj", mcp_servers={"a": {}}, readonly_mcp_servers={"b": {}})
        options = adapter._build_options("SYS", 7, approve, allowed)

        expected = ClaudeAgentOptions(
            system_prompt="SYS",
            model="m",
            max_turns=7,
            mcp_servers={"a": {}} if approve is True else {"b": {}},
            cwd="/proj",
            setting_sources=["user", "project"],
            allowed_tools=DEFAULT_ALLOWED_TOOLS if allowed is None else allowed,
            permission_mode="bypassPermissions",
        )
        assert adapter.skills_mode == "native"
        assert _without_hooks(options) == _without_hooks(expected)
        assert options.skills is None
        assert options.allowed_tools is (DEFAULT_ALLOWED_TOOLS if allowed is None else allowed)
        assert "Skill" in options.allowed_tools
        matchers = options.hooks["PreToolUse"]
        assert [m.matcher for m in matchers] == [_UNATTENDED_TOOL_MATCHER, _MUTATION_TOOL_MATCHER]
        assert all(len(m.hooks) == 1 and m.timeout is None for m in matchers)

    def test_no_cwd_options_unchanged(self):
        options = ClaudeSDKAdapter(model="m")._build_options("SYS")
        assert _without_hooks(options) == _without_hooks(ClaudeAgentOptions(system_prompt="SYS", model="m", max_turns=15))
        assert options.hooks is None

    def test_factory_default_config_is_native(self, tmp_path):
        adapter = create_llm_adapter_from_config(_config(tmp_path, {"backend": "claude"}), cwd="/proj")
        assert adapter.skills_mode == "native"

    @pytest.mark.asyncio
    async def test_on_tool_identity(self):
        async def fake_query(**kwargs):
            yield AssistantMessage(content=[ToolUseBlock(id="1", name="mcp__skills__load_skill", input={})], model="m")

        tools = []

        async def on_tool(name):
            tools.append(name)

        with patch("core.llm.query", side_effect=fake_query):
            await ClaudeSDKAdapter()._call_claude("S", "q", on_tool=on_tool)
        assert tools == ["mcp__skills__load_skill"]

    @pytest.mark.asyncio
    async def test_start_zero_warnings(self, capsys):
        await ClaudeSDKAdapter(model="m", cwd="/proj").start()
        out = capsys.readouterr().out
        assert "WARN UNVERIFIED" not in out
        assert "skills=native" in out


class TestRegistryMode:
    def _adapter(self, **kwargs):
        return ClaudeSDKAdapter(model="m", cwd="/proj", skills_mode="registry", **kwargs)

    @pytest.mark.parametrize("approve", [None, True])
    def test_options(self, approve):
        options = self._adapter()._build_options("SYS", approve_skill_writes=approve)
        assert options.setting_sources == ["project"]
        assert options.skills == []
        assert "Skill" not in options.allowed_tools
        assert "mcp__skills" in options.allowed_tools
        assert options.allowed_tools == [("mcp__skills" if t == "Skill" else t) for t in DEFAULT_ALLOWED_TOOLS]
        assert options.permission_mode == "bypassPermissions"
        assert [m.matcher for m in options.hooks["PreToolUse"]] == [_UNATTENDED_TOOL_MATCHER, _MUTATION_TOOL_MATCHER]

    def test_unattended_allowed_tools_translated_without_mutation(self):
        before = list(UNATTENDED)
        options = self._adapter()._build_options("SYS", allowed_tools=UNATTENDED)
        assert options.allowed_tools == [
            "Read", "Glob", "Grep", "mcp__skills", "WebSearch", "WebFetch",
            "mcp__garmin", "mcp__body_metrics", "mcp__session_search", "mcp__schedule__schedule_list",
        ]
        assert UNATTENDED == before

    @pytest.mark.asyncio
    async def test_hook_decisions_same_as_native(self):
        write_skill = {"tool_name": "Write", "tool_input": {"file_path": ".claude/skills/x/SKILL.md"}}
        mutation = {"tool_name": "mcp__schedule__schedule_create", "tool_input": {}}
        for approve in (False, True):
            native = ClaudeSDKAdapter(cwd="/proj")._build_options("S", approve_skill_writes=approve)
            registry = self._adapter()._build_options("S", approve_skill_writes=approve)
            assert await _hook_decision(native, 0, write_skill) == await _hook_decision(registry, 0, write_skill)
            assert await _hook_decision(native, 1, mutation) == await _hook_decision(registry, 1, mutation)

    def test_skills_sdk_server_in_both_capability_sets(self, tmp_path):
        specs = [create_skills_mcp_server(SkillRegistry(_skills_dir(tmp_path)))]
        adapter = self._adapter(
            mcp_servers=to_sdk_servers(specs, CallerCapability.PRIVILEGED),
            readonly_mcp_servers=to_sdk_servers(specs, CallerCapability.READ_ONLY),
        )
        assert list(adapter._build_options("S", approve_skill_writes=True).mcp_servers) == ["skills"]
        assert list(adapter._build_options("S").mcp_servers) == ["skills"]

    def test_factory_registry_config(self, tmp_path):
        config = _config(tmp_path, {"backend": "claude", "claude": {"model": None, "skills": "registry"}})
        adapter = create_llm_adapter_from_config(config, cwd="/proj")
        assert adapter.skills_mode == "registry"
        assert adapter._build_options("S").skills == []

    @pytest.mark.asyncio
    async def test_skills_tool_reported_as_skill_and_counted(self, capsys):
        async def fake_query(**kwargs):
            yield AssistantMessage(content=[
                ToolUseBlock(id="1", name="mcp__skills__load_skill", input={"name": "sleep-analysis"}),
                ToolUseBlock(id="2", name="mcp__garmin__get_sleep", input={}),
            ], model="m")
            yield AssistantMessage(content=[TextBlock(text="끝")], model="m")

        tools, counter = [], [0]

        async def on_tool(name):
            tools.append(name)

        with patch("core.llm.query", side_effect=fake_query):
            result = await self._adapter().ask_with_context("S", "q", {}, on_tool=on_tool, counter=counter)
        assert result == "끝"
        assert tools == ["Skill", "mcp__garmin__get_sleep"]
        assert counter == [2]
        assert "[llm] turn backend=claude thread=- cap=ro tools=2 skills_loaded=1 outcome=ok" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_start_exactly_one_skills_filter_warning(self, capsys, tmp_path):
        specs = [create_skills_mcp_server(SkillRegistry(_skills_dir(tmp_path)))]
        await self._adapter(server_specs=specs).start()
        lines = capsys.readouterr().out.splitlines()
        warns = [l for l in lines if "WARN" in l]
        assert warns == [
            "[llm] WARN UNVERIFIED claude.skills_filter "
            "source=https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py"
        ]
        assert "[llm] startup backend=claude model=m skills=registry gate=claude-hook tools=2 tool_server=off auth=claude.ai" in lines


class TestBotWiring:
    def _memory(self):
        mem = MagicMock()
        mem.read_memory.return_value = "기억 내용"
        mem.read_user.return_value = "프로필"
        return mem

    def test_native_bot_has_no_catalog_and_no_skills_spec(self, monkeypatch):
        import bot.main as main

        assert main.LLM_CONFIG.skills_mode == "native"
        assert "skills" not in [s.name for s in main.server_specs]
        monkeypatch.setattr(main, "memory_mgr", self._memory())
        monkeypatch.setattr(main, "load_prompt", lambda name: f"<{name}>")
        assert main._build_system_prompt() == "<system.md>\n\n<goals.md>\n\n[기억]\n기억 내용\n\n[사용자 프로필]\n프로필"

    @pytest.mark.parametrize("llm", [
        {"backend": "claude", "claude": {"model": None, "skills": "registry"}},
        {"backend": "codex"},
        {"backend": "grok", "grok": {"model": "grok-x", "bin": "~/.grok/bin/grok"}},
    ])
    def test_registry_backends_insert_catalog_before_memory(self, monkeypatch, tmp_path, llm):
        import bot.main as main

        monkeypatch.setattr(main, "LLM_CONFIG", _config(tmp_path, llm))
        monkeypatch.setattr(main, "SKILLS_DIR", _skills_dir(tmp_path))
        monkeypatch.setattr(main, "memory_mgr", self._memory())
        monkeypatch.setattr(main, "load_prompt", lambda name: f"<{name}>")
        assert main._uses_skill_registry() is True
        assert main._build_system_prompt() == (
            "<system.md>\n\n<goals.md>\n\n"
            "[전문 분석 스킬]\n- sleep-analysis: 수면 분석\n"
            "필요한 스킬은 load_skill 도구로 본문을 불러와 그 절차를 따르세요. 참조 파일은 read_skill_file로 읽으세요.\n\n"
            "[기억]\n기억 내용\n\n[사용자 프로필]\n프로필"
        )

    def test_registry_build_exposes_skills_sdk_server(self, tmp_path):
        import bot.main as main

        config = _config(tmp_path, {"backend": "claude", "claude": {"model": None, "skills": "registry"}})
        specs = [create_skills_mcp_server(SkillRegistry(_skills_dir(tmp_path), backend=config.backend))]
        adapter, shared = main.build_llm_and_tool_server(config, specs)
        assert shared is None
        assert adapter.skills_mode == "registry"
        assert list(adapter.mcp_servers) == list(adapter.readonly_mcp_servers) == ["skills"]
        options = adapter._build_options("S")
        assert options.setting_sources == ["project"] and options.skills == []

    @pytest.mark.asyncio
    async def test_interactive_turn_passes_image_paths_kwarg(self):
        from bot.main import handle_health_query

        message = MagicMock()
        message.content = "이미지"
        message.channel = MagicMock()  # 스레드 아님 → 새 스레드 생성
        thread = MagicMock()
        thread.id = 42
        thread.send = AsyncMock()
        thread.typing = MagicMock(return_value=AsyncMock())
        message.create_thread = AsyncMock(return_value=thread)
        captured = {}

        async def capture_ask(*args, **kwargs):
            captured.update(kwargs, message=args[1])
            return "ok"

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr"), \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_llm.ask_with_context = capture_ask
            paths = ["/tmp/a.png", "/tmp/b.jpg"]
            await handle_health_query(message, "이미지", image_paths=paths)

        assert captured["image_paths"] == paths
        assert captured["approve_skill_writes"] is True
        # Claude 경로의 이미지 안내 문구 조립은 그대로 유지(image_paths는 Codex/Grok 전용 입력).
        assert "/tmp/a.png" in captured["message"] and "Read 도구" in captured["message"]
