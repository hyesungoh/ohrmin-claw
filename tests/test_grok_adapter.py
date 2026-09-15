"""GrokAdapter 조립·기동·격리 HOME·게이트(approval·tripwire)·라우팅 — 문서 기반 in-memory ACP 에이전트 fake
(tests/contract/fakes/grok_fake.py) + 실제 루프백 SharedToolServer. 실제 grok은 실행하지 않고 사용자 ~/.grok도 건드리지
않는다(격리 HOME·프로젝트 루트 = tmp_path).

- 프로젝트 Claude 설정 검사: hooks·.mcp.json → StartupError, 게이트 대상 permissions.allow → WARN 1줄·기동 계속.
- credential(AC-12): 생성 config.toml 정확 일치·api_key 없음, 격리 auth.json → StartupError, 사용자 auth.json 무관,
  자식 env 키 = {HOME(격리), PATH, LANG, XAI_API_KEY}.
- argv(AC-13): [expanduser(bin), "agent", "stdio"]. 핸드셰이크: mcpCapabilities.http 필수, probe.
- 이미지 capability 분기, tripwire(pending만 = 미발동 / permission 없이 in_progress + deny = cancel + 로그 /
  permission 요청 있음 = 미발동), 라우팅 approve_skill_writes is True, 세션 맵, WARN 목록, 오류 분류.
"""
import asyncio
import base64
import contextlib
import json
import os
import tomllib
from types import SimpleNamespace

import pytest

import core.gate_wiring as gate_wiring
from core.gate_wiring import GrokApprovalWiring
from core.llm_errors import (
    GENERIC_MESSAGE,
    LLMError,
    LLMErrorKind,
    RealRuntimeForbidden,
    StartupError,
    auth_expired_message,
    runtime_unavailable_message,
    usage_limit_message,
)
from core.observability import format_unverified_warning, unverified_surfaces
from core.runtimes.grok_adapter import (
    IMAGE_UNSUPPORTED_NOTICE,
    GrokAdapter,
    classify_grok_error,
    grok_config_toml,
    system_preamble,
)
from core.runtimes.jsonrpc_stdio import JsonRpcError
from core.runtimes.tool_names import render_tool_refs
from core.safety_gate import UNKNOWN_TOOL_REASON, UNRESOLVED_PATHS_REASON, CanonicalToolCall, decide
from core.tool_server.capability import CallerCapability
from core.tool_server.http_server import SharedToolServer
from tests.contract.fakes.grok_fake import GrokFake
from tests.contract.harness import Recorder, build_env, eventually, gate_lines, turn_lines
from tests.contract.scenario import crash, end, error, gate_probe, text

SYS = "그록 시스템 프롬프트"
MODEL = "grok-test"
SERVERS = ["garmin", "body_metrics", "memory", "session_search", "schedule", "skills"]
BASE_ENV = {
    "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
    "HOME": "/home/owner",
    "LANG": "ko_KR.UTF-8",
    "XAI_API_KEY": "xai-test-key",
    "GARMIN_PASSWORD": "should-not-leak",
    "OPENAI_API_KEY": "sk-should-not-leak",
    "ANTHROPIC_API_KEY": "sk-ant-should-not-leak",
    "DISCORD_BOT_TOKEN": "discord-should-not-leak",
}
EXPECTED_CONFIG_TOML = (
    '[model."grok-test"]\n'
    'model = "grok-test"\n'
    'env_key = "XAI_API_KEY"\n'
    "\n"
    "[ui]\n"
    'permission_mode = "ask"\n'
)
GROK_WARN_IDS = [
    "grok.acp.mcp_http", "grok.acp.permission_requests", "grok.acp.cancel_reprompt", "grok.credentials.isolated_home",
    "grok.tool_call_shape", "grok.error_shape", "grok.image_prompt", "grok.reads_project_claude_files",
    "grok.system_prompt_preamble", "grok.acp.session_close",
]
T = 7300


@contextlib.asynccontextmanager
async def grok_rig(tmp_path, *, start=True, model=MODEL, bin="~/.grok/bin/grok", base_env=None, **fake_options):
    env = build_env(tmp_path)
    specs = env.specs(registry=True, backend_id="grok")
    server = SharedToolServer(specs, backend="grok")
    await server.start()
    fake = GrokFake(server, **fake_options)
    adapter = GrokAdapter(
        tool_server=server,
        server_specs=specs,
        cwd=env.root,
        model=model,
        bin=bin,
        transport_factory=fake.transport_factory,
        env=dict(BASE_ENV if base_env is None else base_env),
    )
    env.memory_mgr.llm = adapter
    try:
        if start:
            await adapter.start()
        yield SimpleNamespace(adapter=adapter, fake=fake, server=server, env=env, specs=specs)
    finally:
        await adapter.close_all()
        await server.stop()


def _isolated_home(root):
    return os.path.join(root, "data", "runtime", "grok-home")


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ── 프로젝트 Claude 설정 검사 ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["settings.json", "settings.local.json"])
async def test_project_claude_settings_non_empty_hooks_is_startup_error(tmp_path, capsys, filename):
    async with grok_rig(tmp_path, start=False) as rig:
        hooks = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}]}
        _write_json(os.path.join(rig.env.root, ".claude", filename), {"hooks": hooks})

        with pytest.raises(StartupError) as exc:
            await rig.adapter.start()

        assert "hooks" in exc.value.cause and filename in exc.value.cause
        assert "hooks 항목 제거" in exc.value.fix
        assert rig.fake.spawn_attempts == 0
        assert "[llm] startup" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_project_mcp_json_is_startup_error(tmp_path, capsys):
    async with grok_rig(tmp_path, start=False) as rig:
        _write_json(os.path.join(rig.env.root, ".mcp.json"), {"mcpServers": {}})

        with pytest.raises(StartupError) as exc:
            await rig.adapter.start()

        assert ".mcp.json" in exc.value.cause and ".mcp.json" in exc.value.fix
        assert rig.fake.spawn_attempts == 0
        assert "[llm] startup" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_project_permissions_allow_gated_rule_warns_once_and_start_continues(tmp_path, capsys):
    async with grok_rig(tmp_path, start=False) as rig:
        _write_json(
            os.path.join(rig.env.root, ".claude", "settings.local.json"),
            {"hooks": {}, "permissions": {"allow": ["Write(*)", "Read(*)"]}},
        )

        await rig.adapter.start()

        out = capsys.readouterr().out.splitlines()
        lines = [line for line in out if "grok.project_claude_permissions" in line]
        assert lines == ["[llm] WARN UNVERIFIED grok.project_claude_permissions source=https://docs.x.ai/build/features/permissions"]
        assert sum(line.startswith("[llm] startup backend=grok") for line in out) == 1
        assert len(rig.fake.processes) == 1 and not rig.fake.processes[0].dead


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [["Read(*)", "WebFetch(domain:example.com)"], []])
async def test_project_permissions_allow_non_gated_rules_do_not_warn(tmp_path, capsys, allow):
    async with grok_rig(tmp_path, start=False) as rig:
        _write_json(os.path.join(rig.env.root, ".claude", "settings.json"), {"permissions": {"allow": allow}})

        await rig.adapter.start()

        assert "grok.project_claude_permissions" not in capsys.readouterr().out


# ── credential (AC-12) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_credential_generated_config_toml_exact_and_no_api_key(tmp_path):
    async with grok_rig(tmp_path, start=False) as rig:
        config_path = os.path.join(_isolated_home(rig.env.root), ".grok", "config.toml")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('[model."old"]\napi_key = "xai-leaked"\n')  # 매 기동 재생성

        await rig.adapter.start()

        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        assert content == EXPECTED_CONFIG_TOML == grok_config_toml(MODEL)
        assert "api_key" not in content
        assert tomllib.loads(content) == {
            "model": {MODEL: {"model": MODEL, "env_key": "XAI_API_KEY"}},
            "ui": {"permission_mode": "ask"},
        }
        assert rig.fake.processes[0].config_toml == EXPECTED_CONFIG_TOML  # 에이전트가 HOME으로 보는 설정
        assert not os.path.exists(os.path.join(_isolated_home(rig.env.root), ".grok", "hooks"))


@pytest.mark.asyncio
async def test_credential_isolated_home_auth_json_is_startup_error(tmp_path, capsys):
    async with grok_rig(tmp_path, start=False) as rig:
        auth_path = os.path.join(_isolated_home(rig.env.root), ".grok", "auth.json")
        _write_json(auth_path, {"access_token": "session"})

        with pytest.raises(StartupError) as exc:
            await rig.adapter.start()

        assert exc.value.cause == "격리 HOME에 auth.json이 있습니다 — 삭제 필요"
        assert auth_path in exc.value.fix
        assert rig.fake.spawn_attempts == 0
        assert "[llm] startup" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_credential_user_home_grok_auth_json_has_no_effect(tmp_path):
    user_home = tmp_path / "user-home"
    (user_home / ".grok").mkdir(parents=True)
    user_auth = user_home / ".grok" / "auth.json"
    user_config = user_home / ".grok" / "config.toml"
    user_auth.write_text('{"access_token": "subscription-session"}', encoding="utf-8")
    user_config.write_text('[model."grok-test"]\napi_key = "xai-user-key"\n', encoding="utf-8")
    before = (user_auth.read_bytes(), user_config.read_bytes())

    async with grok_rig(tmp_path, base_env={**BASE_ENV, "HOME": str(user_home)}) as rig:
        rig.fake.script(text("ok"))
        assert await rig.adapter.ask_with_context(SYS, "q", {}) == "ok"

        child_env = rig.fake.processes[0].env
        assert child_env["HOME"] == rig.adapter.home_dir == _isolated_home(rig.env.root)
        assert child_env["HOME"] != str(user_home)
        assert rig.fake.processes[0].config_toml == EXPECTED_CONFIG_TOML
        assert not os.path.exists(os.path.join(rig.adapter.home_dir, ".grok", "auth.json"))
    assert (user_auth.read_bytes(), user_config.read_bytes()) == before


@pytest.mark.asyncio
async def test_credential_child_env_keys_exactly_isolated_home_path_lang_api_key(tmp_path):
    async with grok_rig(tmp_path) as rig:
        (agent,) = rig.fake.processes
        assert sorted(agent.env) == ["HOME", "LANG", "PATH", "XAI_API_KEY"]
        assert agent.env == {
            "HOME": _isolated_home(rig.env.root),
            "PATH": BASE_ENV["PATH"],
            "LANG": BASE_ENV["LANG"],
            "XAI_API_KEY": BASE_ENV["XAI_API_KEY"],
        }
        assert agent.cwd == rig.env.root
        assert rig.adapter._base_env["GARMIN_PASSWORD"] == "should-not-leak"  # 부모 env는 불변


@pytest.mark.asyncio
async def test_credential_missing_xai_api_key_is_startup_error(tmp_path):
    env = {k: v for k, v in BASE_ENV.items() if k != "XAI_API_KEY"}
    async with grok_rig(tmp_path, start=False, base_env={**env, "XAI_API_KEY": "  "}) as rig:
        with pytest.raises(StartupError) as exc:
            await rig.adapter.start()
        assert "XAI_API_KEY" in exc.value.cause and "XAI_API_KEY" in exc.value.fix
        assert rig.fake.spawn_attempts == 0


# ── argv (AC-13) · 핸드셰이크 · probe ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bin", ["~/.grok/bin/grok", "/opt/grok/bin/grok"])
async def test_argv_expanduser_bin_agent_stdio(tmp_path, bin):
    async with grok_rig(tmp_path, bin=bin) as rig:
        expected = [os.path.expanduser(bin), "agent", "stdio"]
        (agent,) = rig.fake.processes
        assert agent.argv == expected == rig.adapter.build_argv()
        assert len(rig.fake.processes) == 1  # 권한 분할 없이 프로세스 1개 (세션 토큰으로 분리)


@pytest.mark.asyncio
async def test_mcp_http_capability_false_is_startup_error(tmp_path, capsys):
    async with grok_rig(tmp_path, start=False, mcp_http=False) as rig:
        with pytest.raises(StartupError) as exc:
            await rig.adapter.start()

        assert "mcpCapabilities.http" in exc.value.cause
        assert rig.fake.processes and all(p.dead for p in rig.fake.processes)
        assert "[llm] startup" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_handshake_wire_and_session_new_params(tmp_path):
    async with grok_rig(tmp_path) as rig:
        rig.fake.script(text("ok"))
        await rig.adapter.ask_with_context(SYS, "q", {})

        assert all(message.get("jsonrpc") == "2.0" for message in rig.fake.wire_log)
        assert [m.get("method") for m in rig.fake.wire_log][:3] == ["initialize", "session/new", "session/prompt"]
        (init,) = rig.fake.initialize_params
        assert init["protocolVersion"] == 1
        assert init["clientCapabilities"] == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
        (params,) = rig.fake.session_news
        assert params == {
            "cwd": rig.env.root,
            "mcpServers": [
                {"type": "http", "name": name, "url": rig.server.endpoint(name, CallerCapability.READ_ONLY), "headers": []}
                for name in SERVERS
            ],
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict, failing_cap", [((True, ""), "ro"), ((False, "no"), "priv")], ids=["allow_all", "deny_all"])
async def test_probe_failure_is_startup_error(tmp_path, capsys, monkeypatch, verdict, failing_cap):
    monkeypatch.setattr(gate_wiring, "decide", lambda call, privileged, runtime_guard: verdict)
    async with grok_rig(tmp_path, start=False) as rig:
        with pytest.raises(StartupError) as exc:
            await rig.adapter.start()

        assert "probe" in exc.value.cause and f"cap={failing_cap}" in exc.value.cause
        assert all(p.dead for p in rig.fake.processes)
        assert "[llm] startup" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_startup_summary_probe_log_and_unverified_warning_list(tmp_path, capsys):
    async with grok_rig(tmp_path, start=False) as rig:
        await rig.adapter.start()
        out = capsys.readouterr().out

        lines = out.splitlines()
        assert [line for line in lines if line.startswith("[llm] startup")] == [
            f"[llm] startup backend=grok model={MODEL} skills=registry gate=grok-approval tools=24 "
            f"tool_server=127.0.0.1:{rig.server.port} auth=xai-env_key-isolated-home"
        ]
        warns = [line for line in lines if line.startswith("[llm] WARN UNVERIFIED ")]
        assert warns == [format_unverified_warning(s, src) for s, src in unverified_surfaces("grok", "registry")]
        assert [w.split()[3] for w in warns] == GROK_WARN_IDS
        probe = [(l["cap"], l["tool"], l["decision"], l["via"]) for l in gate_lines(out)]
        assert probe == [("ro", "Bash", "deny", "probe"), ("priv", "Bash", "allow", "probe")]
        assert rig.adapter._wiring._privileged == {} and rig.adapter._wiring._tool_calls == {}  # probe 세션 정리


# ── 이미지 capability 분기 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_image_supported_sends_base64_image_blocks(tmp_path):
    png, jpg = tmp_path / "a.png", tmp_path / "b.jpg"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    jpg.write_bytes(b"\xff\xd8\xfffake")
    async with grok_rig(tmp_path, image_support=True) as rig:
        rig.fake.script(text("이미지 분석"))
        rec = Recorder()

        result = await rig.adapter.ask_with_context(
            SYS, "사진 봐줘", {}, on_text=rec.on_text, approve_skill_writes=True, image_paths=[str(png), str(jpg)],
        )

        (blocks,) = rig.fake.turn_inputs
        assert blocks[0] == {"type": "text", "text": system_preamble(SYS)}
        assert blocks[1]["type"] == "text" and "[질문]\n사진 봐줘" in blocks[1]["text"]
        assert blocks[2:] == [
            {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png.read_bytes()).decode()},
            {"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(jpg.read_bytes()).decode()},
        ]
        assert rec.texts == ["이미지 분석"] and result == "이미지 분석"


@pytest.mark.asyncio
async def test_image_unsupported_sends_fixed_notice_once_and_text_only(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    async with grok_rig(tmp_path, image_support=False) as rig:
        rig.fake.script(text("텍스트 분석"))
        rec = Recorder()

        result = await rig.adapter.ask_with_context(
            SYS, "사진 봐줘", {}, on_text=rec.on_text, approve_skill_writes=True, image_paths=[str(png), str(png)],
        )

        assert rec.texts == [IMAGE_UNSUPPORTED_NOTICE, "텍스트 분석"]
        assert IMAGE_UNSUPPORTED_NOTICE == "이 백엔드에서는 이미지 입력을 지원하지 않아 텍스트만 분석해요."
        (blocks,) = rig.fake.turn_inputs
        assert [b["type"] for b in blocks] == ["text", "text"]
        assert result == "텍스트 분석"


# ── tripwire ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tripwire_pending_only_does_not_fire(tmp_path, capsys):
    async with grok_rig(tmp_path, gate_signals=False) as rig:
        rig.fake.hold_after_pending = True
        rig.fake.script(gate_probe("Bash", {"command": "touch data/x"}), text("never"))
        rec = Recorder()
        capsys.readouterr()

        task = asyncio.create_task(rig.adapter.ask_with_context(SYS, "cron", {}, on_tool=rec.on_tool, thread_id=T))
        await eventually(lambda: rec.tools == ["Bash"])
        for _ in range(100):
            await asyncio.sleep(0)

        assert rig.fake.cancels == 0 and not task.done()
        await rig.adapter.interrupt_session(T)
        result = await task

        assert rig.fake.cancels == 1  # interrupt만
        assert result == "" and result != GENERIC_MESSAGE
        assert gate_lines(capsys.readouterr().out) == []
        assert rig.fake.permission_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name, args, reason_call", [
    ("Bash", {"command": "touch data/x"}, CanonicalToolCall("Bash")),
    ("Write", {"file_path": "prompts/memory.md", "content": "x"}, CanonicalToolCall("Write", ["prompts/memory.md"])),
    ("Unknown", {}, CanonicalToolCall("Unknown")),
], ids=["bash", "write", "unknown"])
async def test_tripwire_in_progress_without_permission_request_fires_cancel_and_log(tmp_path, capsys, name, args, reason_call):
    async with grok_rig(tmp_path, gate_signals=False) as rig:
        rig.fake.script(gate_probe(name, args), text("never"))
        rec = Recorder()
        capsys.readouterr()

        result = await rig.adapter.ask_with_context(SYS, "cron", {}, on_text=rec.on_text)

        _, reason = decide(reason_call, privileged=False, runtime_guard=True)
        out = capsys.readouterr().out
        assert [(l["cap"], l["tool"], l["decision"], l["via"], l["reason"], l["executed"]) for l in gate_lines(out)] == [
            ("ro", reason_call.name, "deny", "tripwire", reason, " executed=likely"),
        ]
        assert reason in (UNKNOWN_TOOL_REASON, f"무인 턴은 읽기 전용입니다 — {reason_call.name}는 인터랙티브 오너 세션에서만 허용됩니다.")
        assert rig.fake.cancels == 1
        assert rig.fake.executions[0].executed  # 사후 봉쇄 — 실행은 이미 시작됐을 수 있다
        assert result == GENERIC_MESSAGE and rec.texts == [GENERIC_MESSAGE]
        assert [t["outcome"] for t in turn_lines(out)] == ["generic"]


@pytest.mark.asyncio
async def test_tripwire_does_not_fire_for_privileged_session_or_read_tools(tmp_path, capsys):
    async with grok_rig(tmp_path, gate_signals=False) as rig:
        rig.fake.script(
            gate_probe("Bash", {"command": "ls"}), text("priv 끝"), end(),
            gate_probe("Read", {"file_path": "prompts/goals.md"}), gate_probe("WebSearch", {"query": "zone 2"}), text("ro 끝"),
        )
        capsys.readouterr()

        priv = await rig.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True)
        ro = await rig.adapter.ask_with_context(SYS, "q", {})

        assert (priv, ro) == ("priv 끝", "ro 끝")
        assert rig.fake.cancels == 0
        assert gate_lines(capsys.readouterr().out) == []


@pytest.mark.asyncio
async def test_permission_request_present_does_not_fire_tripwire(tmp_path, capsys):
    async with grok_rig(tmp_path, gate_signals=True) as rig:
        rig.fake.script(gate_probe("Bash", {"command": "touch data/x"}), text("끝"))
        capsys.readouterr()

        result = await rig.adapter.ask_with_context(SYS, "cron", {})

        _, reason = decide(CanonicalToolCall("Bash"), privileged=False, runtime_guard=True)
        lines = [(l["cap"], l["decision"], l["via"], l["reason"], l["executed"]) for l in gate_lines(capsys.readouterr().out)]
        assert lines == [("ro", "deny", "approval", reason, None)]
        assert rig.fake.cancels == 0 and result == "끝"
        assert not rig.fake.executions[0].executed
        assert rig.fake.permission_outcomes == [{"outcome": "selected", "optionId": "reject-once"}]
        # 거절 후 에이전트가 보낸 tool_call_update(failed)는 permission 요청이 있었으므로 tripwire 대상이 아니다.


def test_wiring_tripwire_status_rules_and_once_per_tool_call():
    wiring = GrokApprovalWiring()
    wiring.register_session("s-ro", False)

    def trip(session_id, update):
        wiring.observe_tool_call(session_id, update)  # 어댑터 알림 핸들러 순서: 관측(병합) → 스트림의 tripwire(병합 안 함)
        return wiring.tripwire(session_id, update)

    base = {"toolCallId": "c1", "title": "Bash", "kind": "execute", "rawInput": {"command": "rm -rf data"}}

    assert not trip("s-ro", {"sessionUpdate": "tool_call", **base})  # status 없음 = pending
    assert not trip("s-ro", {"sessionUpdate": "tool_call", **base, "status": "pending"})
    assert trip("s-ro", {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "in_progress"})
    assert not trip("s-ro", {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed"})
    assert wiring.merged_tool_call("s-ro", "c1") == {**base, "status": "completed"}
    wiring.tripwire("s-ro", {"toolCallId": "c6", "kind": "execute", "status": "in_progress"})
    assert wiring.merged_tool_call("s-ro", "c6") == {}  # tripwire는 병합하지 않는다(병합 = observe_tool_call)

    wiring.permission_response({"sessionId": "s-ro", "toolCall": {**base, "toolCallId": "c2"}, "options": []})
    for status in ("in_progress", "completed", "failed"):
        assert not trip("s-ro", {"toolCallId": "c2", "status": status})

    unregistered = {"toolCallId": "c3", "title": "Edit", "kind": "edit", "status": "failed", "locations": [{"path": "a.md"}]}
    assert trip("unknown-session", unregistered)  # 미등록 세션 = 비특권
    wiring.register_session("s-priv", True)
    assert not trip("s-priv", {**unregistered, "toolCallId": "c4"})
    assert trip("s-priv", {"toolCallId": "c5", "kind": "edit", "status": "in_progress"})  # 경로 없음 = deny


def test_wiring_permission_option_selection_and_unregistered_session(capsys):
    wiring = GrokApprovalWiring()
    wiring.register_session("s-priv", True)
    bash = {"toolCallId": "c1", "title": "Bash", "kind": "execute", "rawInput": {"command": "ls"}}
    always_only = [{"optionId": "aa", "kind": "allow_always"}, {"optionId": "ra", "kind": "reject_always"}]

    # allow → allow_once만 선택(allow_always 누수 방지). allow_once가 없으면 deny 로그 + cancelled.
    assert wiring.permission_response({"sessionId": "s-priv", "toolCall": bash, "options": always_only}) == {
        "outcome": {"outcome": "cancelled"}
    }
    both = [{"optionId": "aa", "kind": "allow_always"}, {"optionId": "ao", "kind": "allow_once"}, {"optionId": "ra", "kind": "reject_always"}]
    assert wiring.permission_response({"sessionId": "s-priv", "toolCall": bash, "options": both}) == {
        "outcome": {"outcome": "selected", "optionId": "ao"}
    }
    assert wiring.permission_response({"sessionId": "nobody", "toolCall": bash, "options": always_only}) == {
        "outcome": {"outcome": "selected", "optionId": "ra"}
    }
    assert wiring.permission_response({"sessionId": "nobody", "toolCall": bash, "options": [{"optionId": "x", "kind": "allow_once"}]}) == {
        "outcome": {"outcome": "cancelled"}
    }
    assert wiring.permission_response({"sessionId": "s-priv", "toolCall": bash, "options": always_only}, cancelled=True) == {
        "outcome": {"outcome": "cancelled"}
    }
    lines = gate_lines(capsys.readouterr().out)
    decisions = [(l["cap"], l["decision"], l["via"]) for l in lines]
    assert decisions == [("priv", "deny", "approval"), ("priv", "allow", "approval"), ("ro", "deny", "approval"), ("ro", "deny", "approval")]
    assert lines[0]["reason"] == gate_wiring.GROK_NO_ALLOW_ONCE_REASON == "allow_once 옵션이 없어 차단합니다 (allow_always는 선택하지 않습니다)."


@pytest.mark.asyncio
async def test_edit_without_paths_denied_even_for_privileged_session(tmp_path, capsys):
    async with grok_rig(tmp_path) as rig:
        rig.fake.script(gate_probe("Write", {"content": "경로 없음"}), text("끝"))
        capsys.readouterr()

        await rig.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True)

        lines = [(l["cap"], l["tool"], l["decision"], l["reason"]) for l in gate_lines(capsys.readouterr().out)]
        assert lines == [("priv", "Write", "deny", UNRESOLVED_PATHS_REASON)]
        assert not rig.fake.executions[0].executed


@pytest.mark.asyncio
async def test_permission_uses_tool_call_merged_in_read_order_while_stream_consumer_blocked(tmp_path, capsys):
    """tool_call 관측은 알림 핸들러(read-loop 순서)에서 병합된다 — 스트림 소비자가 on_text에서 막혀 있어도 뒤이은
    sparse session/request_permission은 병합된 kind·locations로 판정한다(science-reference 쓰기 = priv에서도 deny)."""
    science_reference = ".claude/skills/science-reference/SKILL.md"
    async with grok_rig(tmp_path) as rig:
        agent = rig.fake.processes[-1]
        request_permission = agent._request_permission

        async def sparse_request_permission(session, tool_call):
            return await request_permission(session, {"toolCallId": tool_call["toolCallId"]})

        agent._request_permission = sparse_request_permission
        rig.fake.script(
            text("앞"),
            gate_probe("Read", {"file_path": "prompts/goals.md"}),
            gate_probe("Write", {"file_path": science_reference, "content": "x"}),
            text("끝"),
        )
        release = asyncio.Event()
        texts = []

        async def blocked_on_text(t):
            texts.append(t)
            await release.wait()

        capsys.readouterr()
        task = asyncio.create_task(
            rig.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True, on_text=blocked_on_text)
        )
        await eventually(lambda: len(rig.fake.permission_outcomes) == 2)
        assert texts == ["앞"] and not task.done()  # 소비자는 첫 flush에서 막혀 있다
        release.set()
        result = await task

        _, reason = decide(CanonicalToolCall("Write", [science_reference]), privileged=True, runtime_guard=True)
        assert reason == "science-reference 스킬은 읽기 전용입니다 (수정 불가)."
        assert rig.fake.permission_requests[1] == ("priv", {"toolCallId": rig.fake.permission_requests[1][1]["toolCallId"]})
        assert rig.fake.permission_outcomes == [
            {"outcome": "selected", "optionId": "allow-once"},
            {"outcome": "selected", "optionId": "reject-once"},
        ]
        assert [e.executed for e in rig.fake.executions] == [True, False]
        lines = [(l["cap"], l["tool"], l["decision"], l["via"], l["reason"]) for l in gate_lines(capsys.readouterr().out)]
        assert lines == [("priv", "Write", "deny", "approval", reason)]
        assert result == "앞\n끝"


# ── 라우팅 · 세션 맵 · 시스템 지시 ────────────────────────────────────


@pytest.mark.asyncio
async def test_routing_by_approve_skill_writes_is_true(tmp_path):
    cases = [(True, "priv"), (False, "ro"), (None, "ro"), (1, "ro"), ("yes", "ro")]
    async with grok_rig(tmp_path) as rig:
        for _ in range(len(cases) + 2):
            rig.fake.script(text("ok"), end())
        for approve, _ in cases:
            await rig.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=approve)
        await rig.adapter.ask(SYS, "util")
        await rig.adapter.ask(SYS, "util", approve_skill_writes=True)

        expected = [cap for _, cap in cases] + ["ro", "priv"]
        assert rig.fake.session_caps() == expected
        assert len(rig.fake.session_news) == len(expected) and len(rig.fake.processes) == 1
        assert rig.adapter.session_ids() == []
        assert rig.adapter._wiring._privileged == {}  # one-shot 세션은 턴 후 권한 맵에서 제거


@pytest.mark.asyncio
async def test_session_map_privilege_change_on_same_thread_closes_and_recreates(tmp_path):
    async with grok_rig(tmp_path) as rig:
        rig.fake.script(text("a"), end(), text("b"), end(), text("c"))
        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=5, approve_skill_writes=True)
        priv_session = rig.adapter._sessions[5].session_id
        assert rig.adapter._wiring.is_privileged(priv_session)

        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=5)
        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=5)

        ro_session = rig.adapter._sessions[5].session_id
        assert rig.fake.session_caps() == ["priv", "ro", "ro"]
        assert len(rig.fake.session_news) == 2 and rig.fake.session_closes == 1
        closed = [m["params"]["sessionId"] for m in rig.fake.wire_log if m.get("method") == "session/close"]
        assert closed == [priv_session]
        assert ro_session != priv_session
        assert rig.adapter._wiring._privileged == {ro_session: False}


@pytest.mark.asyncio
async def test_system_preamble_until_session_primed_and_rendered_tool_refs(tmp_path):
    system = "시스템 mcp__garmin__get_sleep 도구로 조회"
    async with grok_rig(tmp_path) as rig:
        rig.fake.script(text("a"), end(), text("b"))
        await rig.adapter.ask_with_context(system, "mcp__schedule__schedule_list 확인", {}, thread_id=11)
        await rig.adapter.ask_with_context(system, "q2", {}, thread_id=11)

        first, second = rig.fake.turn_inputs
        assert first[0] == {"type": "text", "text": "[시스템 지시]\n시스템 garmin__get_sleep 도구로 조회\n\n"}
        assert first[0]["text"] == system_preamble(system) and render_tool_refs(system, "grok") in first[0]["text"]
        assert "schedule__schedule_list 확인" in first[1]["text"] and "mcp__" not in first[1]["text"]
        assert [b["type"] for b in second] == ["text"] and "[시스템 지시]" not in second[0]["text"]
        assert rig.fake.system_prompts == [render_tool_refs(system, "grok")] * 2


@pytest.mark.asyncio
async def test_first_prompt_error_keeps_preamble_and_history_for_next_turn(tmp_path):
    history = [{"role": "user", "content": "이전질문X"}]
    async with grok_rig(tmp_path) as rig:
        rig.fake.script(error("usage_limit"), end(), text("복구"))

        r1 = await rig.adapter.ask_with_context(SYS, "q1", {}, history=history, thread_id=12)
        r2 = await rig.adapter.ask_with_context(SYS, "q2", {}, history=history, thread_id=12)

        assert r1 == usage_limit_message("grok") and r2 == "복구"
        assert len(rig.fake.session_news) == 1
        assert all(b[0]["text"].startswith("[시스템 지시]\n") for b in rig.fake.turn_inputs)
        assert "이전질문X" in rig.fake.prompts[1]


@pytest.mark.asyncio
async def test_session_close_unsupported_is_tolerated_and_not_retried(tmp_path, capsys):
    async with grok_rig(tmp_path, close_supported=False) as rig:
        rig.fake.script(text("a"), end(), text("b"))
        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=1)
        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=2)

        await rig.adapter.end_session(1)
        await rig.adapter.end_session(2)

        attempts = [m for m in rig.fake.wire_log if m.get("method") == "session/close"]
        assert len(attempts) == 1 and rig.fake.session_closes == 0
        assert rig.adapter.session_ids() == []
        assert capsys.readouterr().out.count("session/close를 지원하지 않습니다") == 1


@pytest.mark.asyncio
async def test_crash_invalidates_sessions_and_restarts_once_on_next_call(tmp_path):
    async with grok_rig(tmp_path) as rig:
        fake, adapter = rig.fake, rig.adapter
        fake.script(text("a"), end(), text("부분"), crash(), end(), text("재기동"))
        await adapter.ask_with_context(SYS, "q", {}, thread_id=1, approve_skill_writes=True)
        old = fake.processes[0]

        crashed = await adapter.ask_with_context(SYS, "q", {}, thread_id=2)

        assert crashed == runtime_unavailable_message("grok")
        assert old.dead and adapter.session_ids() == [] and fake.spawn_attempts == 1
        assert adapter._wiring._privileged == {}

        again = await adapter.ask_with_context(SYS, "q", {}, history=[{"role": "user", "content": "이전"}], thread_id=1)
        assert again == "재기동" and fake.spawn_attempts == 2
        assert fake.processes[-1] is not old and fake.processes[-1].argv == old.argv
        assert "[대화 이력]" in fake.prompts[-1]


@pytest.mark.asyncio
async def test_restart_failure_is_runtime_unavailable_and_ask_raises(tmp_path):
    async with grok_rig(tmp_path) as rig:
        fake, adapter = rig.fake, rig.adapter
        fake.script(text("x"), crash(), end(), text("복구"))
        await adapter.ask_with_context(SYS, "q", {})
        fake.fail_spawns = 2

        reply = await adapter.ask_with_context(SYS, "q", {})
        with pytest.raises(LLMError) as exc:
            await adapter.ask(SYS, "util")

        assert reply == runtime_unavailable_message("grok")
        assert exc.value.kind is LLMErrorKind.RUNTIME_UNAVAILABLE
        assert fake.spawn_attempts == 3  # 기동 1 + 호출당 1회씩
        assert await adapter.ask_with_context(SYS, "q", {}) == "복구" and fake.spawn_attempts == 4


# ── 오류 분류 · 역요청 · 가드 ────────────────────────────────────────


@pytest.mark.parametrize("error, expected", [
    (JsonRpcError(-32603, "429 Too Many Requests"), usage_limit_message("grok")),
    (JsonRpcError(-32603, "Rate limit exceeded", {"resetsAt": 1760000000}), usage_limit_message("grok", 1760000000)),
    (JsonRpcError(-32603, "upstream", {"status": 429, "detail": {"resets_at": 1760000000}}), usage_limit_message("grok", 1760000000)),
    (JsonRpcError(-32603, "401 Unauthorized"), auth_expired_message("grok", ".env의 XAI_API_KEY 확인")),
    (JsonRpcError(-32603, "bad request", {"error": "Invalid API key"}), auth_expired_message("grok", ".env의 XAI_API_KEY 확인")),
    (JsonRpcError(-32603, "upstream model error", {"status": 500, "resetsAt": 1760142900}), GENERIC_MESSAGE),
    (JsonRpcError(-32603, "request 4291 failed"), GENERIC_MESSAGE),
    (RuntimeError("429"), GENERIC_MESSAGE),
])
def test_classify_grok_error(error, expected):
    assert classify_grok_error(error).user_message == expected


@pytest.mark.asyncio
async def test_unknown_server_requests_are_method_not_found(tmp_path):
    async with grok_rig(tmp_path) as rig:
        proc = rig.adapter._proc
        for method in ("fs/write_text_file", "terminal/create"):
            with pytest.raises(JsonRpcError) as exc:
                rig.adapter._on_server_request(proc, method, {})
            assert exc.value.code == -32601
        # 진행 중 턴이 없는 세션의 permission 요청 = cancelled
        assert rig.adapter._on_server_request(proc, "session/request_permission", {"sessionId": "gone"}) == {
            "outcome": {"outcome": "cancelled"}
        }


@pytest.mark.asyncio
async def test_default_transport_refuses_real_grok_in_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")
    env = build_env(tmp_path)
    specs = env.specs(registry=True, backend_id="grok")
    server = SharedToolServer(specs, backend="grok")
    await server.start()
    adapter = GrokAdapter(
        tool_server=server, server_specs=specs, cwd=env.root, model=MODEL, bin=str(tmp_path / "no-such-grok"),
        env=dict(BASE_ENV),
    )
    try:
        with pytest.raises(RealRuntimeForbidden):
            await adapter.start()
        with pytest.raises(RealRuntimeForbidden):
            await adapter.ask_with_context(SYS, "q", {})
        assert adapter.session_ids() == []
    finally:
        await adapter.close_all()
        await server.stop()


def test_missing_tool_server_is_startup_error():
    with pytest.raises(StartupError):
        GrokAdapter(tool_server=None)
