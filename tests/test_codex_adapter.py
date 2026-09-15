"""CodexAdapter 조립·기동·라우팅·크래시 — 문서 기반 in-memory app-server fake(tests/contract/fakes/codex_fake.py)
+ 실제 루프백 SharedToolServer + 실제 훅 스크립트 서브프로세스. 실제 codex는 실행하지 않는다.

- argv(AC-10): priv/ro 스냅샷 — approval_policy·sandbox_mode·mcp_servers.<s>.url/tool_timeout_sec·hooks.PreToolUse·model.
- env/auth(AC-9): 자식 env에서 OPENAI_API_KEY/CODEX_API_KEY 제거 + OHRMIN_GATE_URL, account/read apiKey → StartupError.
- probe: 런타임과 동일한 argv/env로 훅 스크립트 실행, 불일치 → StartupError.
- 라우팅: approve_skill_writes is True만 priv 프로세스. 크래시: 해당 프로세스 세션 전부 무효화 + 다음 호출 1회 재기동.
- 기동 WARN UNVERIFIED 목록, developerInstructions = 렌더된 system prompt + backend_tool_note, per-thread config 미사용.
"""
import contextlib
import json
import os
import re
import shlex
import sys
import tomllib
from types import SimpleNamespace

import pytest

from core.gate_wiring import CODEX_HOOK_MATCHER
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
from core.runtimes.codex_adapter import (
    HOOK_SCRIPT,
    CodexAdapter,
    account_auth_marker,
    classify_codex_error,
)
from core.runtimes.jsonrpc_stdio import JsonRpcError
from core.runtimes.tool_names import backend_tool_note, render_tool_refs
from core.tool_server.capability import CallerCapability
from core.tool_server.http_server import SharedToolServer
from tests.contract.fakes.codex_fake import CodexFake
from tests.contract.harness import build_env, gate_lines
from tests.contract.scenario import crash, end, gate_probe, text

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVERS = ["garmin", "body_metrics", "memory", "session_search", "schedule", "skills"]
SYS = "코덱스 시스템 프롬프트"
BASE_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": "/home/owner",
    "LANG": "en_US.UTF-8",
    "OPENAI_API_KEY": "sk-should-not-leak",
    "CODEX_API_KEY": "codex-should-not-leak",
    "OHRMIN_TEST_MARKER": "inherited",
}


@contextlib.asynccontextmanager
async def codex_rig(tmp_path, *, start=True, model="gpt-test", account=None, hook_argv=None, gate_signals=True):
    env = build_env(tmp_path)
    specs = env.specs(registry=True, backend_id="codex")
    server = SharedToolServer(specs, backend="codex")
    await server.start()
    fake = CodexFake(server, gate_signals=gate_signals, account=account)
    adapter = CodexAdapter(
        tool_server=server,
        server_specs=specs,
        cwd=PROJECT_ROOT,
        model=model,
        bin="/opt/bin/codex",
        transport_factory=fake.transport_factory,
        hook_argv=hook_argv,
        env=dict(BASE_ENV),
    )
    env.memory_mgr.llm = adapter
    try:
        if start:
            await adapter.start()
        yield SimpleNamespace(adapter=adapter, fake=fake, server=server, env=env, specs=specs)
    finally:
        await adapter.close_all()
        await server.stop()


def _expected_argv(server, cap, model="gpt-test"):
    capability = CallerCapability.PRIVILEGED if cap == "priv" else CallerCapability.READ_ONLY
    sandbox = "danger-full-access" if cap == "priv" else "read-only"
    argv = ["/opt/bin/codex", "app-server", "-c", 'approval_policy="never"', "-c", f'sandbox_mode="{sandbox}"']
    for name in SERVERS:
        argv += [
            "-c", f'mcp_servers.{name}.url="{server.endpoint(name, capability)}"',
            "-c", f"mcp_servers.{name}.tool_timeout_sec=120",
        ]
    command = shlex.join([sys.executable, HOOK_SCRIPT])
    argv += [
        "-c",
        'hooks.PreToolUse=[{matcher="^(Bash|apply_patch|Write|Edit|MultiEdit|NotebookEdit|mcp__schedule__schedule_(create|pause|resume|remove)'
        '|mcp__memory__(add_memory|replace_memory|remove_memory))$", '
        f'hooks=[{{type="command", command="{command}", timeout=10}}]}}]',
    ]
    if model:
        argv += ["-c", f'model="{model}"']
    return argv


def _write_script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


# ── argv (AC-10) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_argv_snapshot_priv_and_ro(tmp_path):
    async with codex_rig(tmp_path) as rig:
        for cap in ("priv", "ro"):
            expected = _expected_argv(rig.server, cap)
            assert rig.fake.process_argv(cap) == expected
            assert rig.adapter.build_argv(cap) == expected
        assert os.path.isabs(HOOK_SCRIPT) and os.path.isfile(HOOK_SCRIPT)
        assert HOOK_SCRIPT == os.path.join(PROJECT_ROOT, "core", "hooks", "codex_gate_hook.py")
        assert len(rig.fake.processes) == 2


def test_hook_matcher_covers_shell_patch_write_tools_and_mutation_mcp():
    gated = ["Bash", "apply_patch", "Write", "Edit", "MultiEdit", "NotebookEdit",
             "mcp__schedule__schedule_create", "mcp__memory__remove_memory"]
    assert all(re.search(CODEX_HOOK_MATCHER, name) for name in gated)
    assert not any(re.search(CODEX_HOOK_MATCHER, name) for name in ["Read", "WriteX", "mcp__schedule__schedule_list"])


@pytest.mark.asyncio
async def test_argv_without_model_has_no_model_override(tmp_path):
    async with codex_rig(tmp_path, model=None) as rig:
        argv = rig.fake.process_argv("priv")
        assert argv == _expected_argv(rig.server, "priv", model=None)
        assert not any(value.startswith("model=") for value in argv)


@pytest.mark.asyncio
async def test_argv_overrides_parse_as_toml_with_hook_matcher_and_command(tmp_path):
    async with codex_rig(tmp_path) as rig:
        for cap, token_cap in (("priv", CallerCapability.PRIVILEGED), ("ro", CallerCapability.READ_ONLY)):
            config = rig.fake.process(cap).config
            (group,) = config["hooks"]["PreToolUse"]
            assert group["matcher"] == CODEX_HOOK_MATCHER
            assert group["hooks"] == [
                {"type": "command", "command": shlex.join([sys.executable, HOOK_SCRIPT]), "timeout": 10}
            ]
            assert sorted(config["mcp_servers"]) == sorted(SERVERS)
            assert all(s["tool_timeout_sec"] == 120 for s in config["mcp_servers"].values())
            assert config["mcp_servers"]["schedule"]["url"] == rig.server.endpoint("schedule", token_cap)
            assert config["approval_policy"] == "never"
        # 인라인 TOML 직접 확인 (fake 파서와 무관)
        hooks_value = next(v for v in rig.adapter.build_argv("ro") if v.startswith("hooks.PreToolUse="))
        parsed = tomllib.loads("v = " + hooks_value.partition("=")[2])["v"]
        assert parsed[0]["matcher"] == CODEX_HOOK_MATCHER


# ── env · auth (AC-9) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_child_env_strips_api_keys_and_sets_gate_url(tmp_path):
    async with codex_rig(tmp_path) as rig:
        for cap, capability in (("priv", CallerCapability.PRIVILEGED), ("ro", CallerCapability.READ_ONLY)):
            env = rig.fake.process_env(cap)
            assert "OPENAI_API_KEY" not in env and "CODEX_API_KEY" not in env
            assert env["OHRMIN_GATE_URL"] == rig.server.gate_url(capability)
            assert {k: env[k] for k in ("PATH", "HOME", "LANG", "OHRMIN_TEST_MARKER")} == {
                k: BASE_ENV[k] for k in ("PATH", "HOME", "LANG", "OHRMIN_TEST_MARKER")
            }
            assert rig.fake.process(cap).cwd == PROJECT_ROOT
        assert rig.adapter._base_env["OPENAI_API_KEY"] == "sk-should-not-leak"  # 부모 env는 불변


@pytest.mark.asyncio
@pytest.mark.parametrize("account", [
    {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True},
    {"authMode": "apikey"},
    {"account": {"apiKey": {"last4": "abcd"}}},
], ids=["type_apiKey", "authMode_apikey", "apiKey_key"])
async def test_account_read_api_key_is_startup_error(tmp_path, capsys, account):
    async with codex_rig(tmp_path, start=False, account=account) as rig:
        with pytest.raises(StartupError) as exc:
            await rig.adapter.start()

        assert "API 키" in exc.value.cause and "codex login" in exc.value.fix
        assert rig.fake.processes and all(p.dead for p in rig.fake.processes)
        assert "[llm] startup" not in capsys.readouterr().out
        assert rig.fake.hook_runs == []


@pytest.mark.asyncio
async def test_account_read_unknown_shape_warns_and_continues(tmp_path, capsys):
    async with codex_rig(tmp_path, start=False, account={"account": None}) as rig:
        await rig.adapter.start()

        out = capsys.readouterr().out.splitlines()
        detail = [line for line in out if "codex.account_read_shape" in line and not line.startswith("[llm] WARN UNVERIFIED")]
        assert len(detail) == 2  # 프로세스 2개
        assert sum(line.startswith("[llm] startup backend=codex") for line in out) == 1


@pytest.mark.parametrize("result, marker", [
    ({"account": {"type": "chatgpt", "email": "apikey@example.com", "planType": "pro"}}, "chatgpt"),
    ({"account": {"type": "apiKey"}}, "apikey"),
    ({"auth_mode": "ChatGPT"}, "chatgpt"),
    ({"account": None, "requiresOpenaiAuth": True}, None),
    (None, None),
])
def test_account_auth_marker(result, marker):
    assert account_auth_marker(result) == marker


# ── probe ────────────────────────────────────────────────────────────


ALLOW_ALL = "import sys\nsys.stdin.buffer.read()\nsys.exit(0)\n"
DENY_ALL = (
    "import json, sys\nsys.stdin.buffer.read()\n"
    "sys.stdout.write(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', "
    "'permissionDecision': 'deny', 'permissionDecisionReason': 'no'}}))\nsys.exit(2)\n"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("body, failing_cap", [(ALLOW_ALL, "ro"), (DENY_ALL, "priv")], ids=["allow_all", "deny_all"])
async def test_probe_failure_is_startup_error(tmp_path, capsys, body, failing_cap):
    hook = _write_script(tmp_path, "bad_hook.py", body)
    async with codex_rig(tmp_path, start=False, hook_argv=[sys.executable, hook]) as rig:
        with pytest.raises(StartupError) as exc:
            await rig.adapter.start()

        assert "probe" in exc.value.cause and f"cap={failing_cap}" in exc.value.cause
        assert all(p.dead for p in rig.fake.processes)
        assert "[llm] startup" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_probe_runs_hook_with_runtime_argv_and_env(tmp_path, capsys):
    log = tmp_path / "hook_log.jsonl"
    wrapper = _write_script(tmp_path, "recording_hook.py", (
        "import json, os, runpy, sys\n"
        f"with open({str(log)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv, 'gate': os.environ.get('OHRMIN_GATE_URL'),\n"
        "        'keys': sorted(k for k in os.environ if k.endswith('_API_KEY')),\n"
        "        'marker': os.environ.get('OHRMIN_TEST_MARKER')}) + '\\n')\n"
        f"runpy.run_path({HOOK_SCRIPT!r}, run_name='__main__')\n"
    ))
    hook_argv = [sys.executable, wrapper]
    async with codex_rig(tmp_path, start=False, hook_argv=hook_argv) as rig:
        await rig.adapter.start()
        probe_records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

        assert [r["gate"] for r in probe_records] == [
            rig.fake.process_env("ro")["OHRMIN_GATE_URL"], rig.fake.process_env("priv")["OHRMIN_GATE_URL"],
        ]
        assert all(r["argv"] == [wrapper] and r["keys"] == [] and r["marker"] == "inherited" for r in probe_records)
        (group,) = rig.fake.process("ro").config["hooks"]["PreToolUse"]
        assert shlex.split(group["hooks"][0]["command"]) == hook_argv  # 런타임 훅 command == probe argv
        probe_gate = [(l["cap"], l["decision"], l["via"]) for l in gate_lines(capsys.readouterr().out)]
        assert probe_gate == [("ro", "deny", "probe"), ("priv", "allow", "probe")]

        # 턴 중 fake가 실행한 훅도 같은 command·env(ro 토큰)로 판정된다.
        rig.fake.script(gate_probe("Bash", {"command": "ls"}), text("끝"))
        await rig.adapter.ask_with_context(SYS, "q", {})
        turn_record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        assert turn_record["gate"] == rig.server.gate_url(CallerCapability.READ_ONLY)
        assert rig.fake.hook_runs == [("ro", "Bash", 2)]
        assert [(l["cap"], l["decision"], l["via"]) for l in gate_lines(capsys.readouterr().out)] == [("ro", "deny", "hook")]


# ── 기동 로그 · 와이어 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_summary_and_unverified_warning_list(tmp_path, capsys):
    async with codex_rig(tmp_path, start=False) as rig:
        await rig.adapter.start()
        out = capsys.readouterr().out.splitlines()

        warns = [line for line in out if line.startswith("[llm] WARN UNVERIFIED ")]
        assert warns == [format_unverified_warning(s, src) for s, src in unverified_surfaces("codex", "registry")]
        assert [w.split()[3] for w in warns] == [
            "codex.thread_start.developerInstructions", "codex.hooks.cli_override", "codex.hooks.fires_under_never",
            "codex.hooks.payload_shape", "codex.item_types", "codex.error_info", "codex.account_read_shape",
            "codex.mcp.url_override", "codex.hooks.env_inheritance", "codex.thread_archive",
        ]
        assert [line for line in out if line.startswith("[llm] startup")] == [
            f"[llm] startup backend=codex model=gpt-test skills=registry gate=codex-hook tools=24 "
            f"tool_server=127.0.0.1:{rig.server.port} auth=chatgpt"
        ]


@pytest.mark.asyncio
async def test_wire_has_no_jsonrpc_header_and_handshake_order(tmp_path):
    async with codex_rig(tmp_path) as rig:
        rig.fake.script(text("ok"))
        await rig.adapter.ask_with_context(SYS, "q", {})

        assert all("jsonrpc" not in message for _, message in rig.fake.wire_log)
        for cap in ("priv", "ro"):
            methods = [m.get("method") for c, m in rig.fake.wire_log if c == cap]
            assert methods[:3] == ["initialize", "initialized", "account/read"]
        assert "turn/steer" not in {m.get("method") for _, m in rig.fake.wire_log}


# ── thread/start · turn/start 파라미터 ───────────────────────────────


@pytest.mark.asyncio
async def test_developer_instructions_render_system_prompt_and_tool_note_without_thread_config(tmp_path):
    system = "시스템 mcp__garmin__get_sleep 도구로 조회"
    async with codex_rig(tmp_path) as rig:
        rig.fake.script(text("a"), end(), text("b"))
        await rig.adapter.ask_with_context(system, "q", {}, thread_id=11, approve_skill_writes=True)
        await rig.adapter.ask(system, "유틸")

        note = backend_tool_note("codex")
        assert note
        for (cap, params), sandbox in zip(rig.fake.thread_starts, ("dangerFullAccess", "readOnly")):
            assert params["developerInstructions"] == render_tool_refs(system, "codex") + "\n\n" + note
            assert params == {
                "cwd": PROJECT_ROOT, "approvalPolicy": "never", "sandbox": sandbox,
                "developerInstructions": params["developerInstructions"],
            }
            assert "config" not in params
        for _, message in rig.fake.wire_log:
            if message.get("method") in ("thread/start", "turn/start"):
                assert "config" not in (message.get("params") or {})
        assert rig.fake.system_prompts[0] == render_tool_refs(system, "codex") + "\n\n" + note


@pytest.mark.asyncio
async def test_image_paths_sent_as_local_image_inputs(tmp_path):
    async with codex_rig(tmp_path) as rig:
        rig.fake.script(text("이미지 분석"))
        await rig.adapter.ask_with_context(
            SYS, "사진 봐줘", {}, approve_skill_writes=True, image_paths=["/tmp/a.png", "/tmp/b.jpg"],
        )

        (inputs,) = rig.fake.turn_inputs
        assert inputs[0]["type"] == "text" and "[질문]\n사진 봐줘" in inputs[0]["text"]
        assert inputs[1:] == [{"type": "localImage", "path": "/tmp/a.png"}, {"type": "localImage", "path": "/tmp/b.jpg"}]


# ── 라우팅 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_routing_by_approve_skill_writes_is_true(tmp_path):
    cases = [(True, "priv"), (False, "ro"), (None, "ro"), (1, "ro"), ("yes", "ro")]
    async with codex_rig(tmp_path) as rig:
        for _ in range(len(cases) + 2):
            rig.fake.script(text("ok"), end())
        for approve, _ in cases:
            await rig.adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=approve)
        await rig.adapter.ask(SYS, "util")
        await rig.adapter.ask(SYS, "util", approve_skill_writes=True)

        expected = [cap for _, cap in cases] + ["ro", "priv"]
        assert rig.fake.turn_caps() == expected
        assert [cap for cap, _ in rig.fake.thread_starts] == expected
        assert rig.adapter.session_ids() == []


@pytest.mark.asyncio
async def test_same_thread_with_other_privilege_ends_mapping_and_recreates_on_other_process(tmp_path):
    async with codex_rig(tmp_path) as rig:
        rig.fake.script(text("a"), end(), text("b"), end(), text("c"))
        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=5, approve_skill_writes=True)
        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=5)
        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=5)

        assert rig.fake.turn_caps() == ["priv", "ro", "ro"]
        assert [cap for cap, _ in rig.fake.thread_starts] == ["priv", "ro"]
        assert rig.fake.session_closes == 1
        archived = [(c, m["params"]) for c, m in rig.fake.wire_log if m.get("method") == "thread/archive"]
        assert [c for c, _ in archived] == ["priv"]


# ── 크래시 · 재기동 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crash_invalidates_process_sessions_and_restarts_once_on_next_call(tmp_path):
    async with codex_rig(tmp_path) as rig:
        fake, adapter = rig.fake, rig.adapter
        fake.script(text("a"), end(), text("b"), end(), text("c"), end(), text("부분"), crash(), end(), text("재기동"))
        await adapter.ask_with_context(SYS, "q", {}, thread_id=1, approve_skill_writes=True)
        await adapter.ask_with_context(SYS, "q", {}, thread_id=2, approve_skill_writes=True)
        await adapter.ask_with_context(SYS, "q", {}, thread_id=3)
        old_priv = fake.process("priv")
        assert fake.spawn_attempts == 2

        crashed = await adapter.ask_with_context(SYS, "q", {}, thread_id=1, approve_skill_writes=True)

        assert crashed == runtime_unavailable_message("codex")
        assert old_priv.dead and not fake.process("ro").dead
        assert adapter.session_ids() == [3]  # priv 프로세스 세션(1, 2) 전부 무효화, ro 세션 유지
        assert fake.spawn_attempts == 2  # 자동 재기동 없음 — 다음 호출에서

        again = await adapter.ask_with_context(
            SYS, "q", {}, history=[{"role": "user", "content": "이전"}], thread_id=2, approve_skill_writes=True,
        )

        assert again == "재기동"
        assert fake.spawn_attempts == 3
        assert fake.process("priv") is not old_priv and fake.process_argv("priv") == old_priv.argv
        assert "[대화 이력]" in fake.prompts[-1]
        assert sorted(adapter.session_ids()) == [2, 3]


@pytest.mark.asyncio
async def test_restart_failure_is_runtime_unavailable_without_retry_loop(tmp_path):
    async with codex_rig(tmp_path) as rig:
        fake, adapter = rig.fake, rig.adapter
        fake.script(text("x"), crash(), end(), text("복구"))
        await adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True)
        fake.fail_spawns = 2

        reply = await adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True)
        with pytest.raises(LLMError) as exc:
            await adapter.ask(SYS, "util", approve_skill_writes=True)

        assert reply == runtime_unavailable_message("codex")
        assert exc.value.kind is LLMErrorKind.RUNTIME_UNAVAILABLE
        assert fake.spawn_attempts == 4  # 기동 2 + 호출당 1회씩
        recovered = await adapter.ask_with_context(SYS, "q", {}, approve_skill_writes=True)
        assert recovered == "복구" and fake.spawn_attempts == 5


@pytest.mark.asyncio
async def test_close_all_stops_processes_and_next_call_relaunches(tmp_path):
    async with codex_rig(tmp_path) as rig:
        rig.fake.script(text("a"), end(), text("b"))
        await rig.adapter.ask_with_context(SYS, "q", {}, thread_id=9, approve_skill_writes=True)

        await rig.adapter.close_all()

        assert all(p.dead for p in rig.fake.processes) and rig.fake.session_closes == 1
        assert rig.adapter.session_ids() == []
        assert await rig.adapter.ask_with_context(SYS, "q", {}) == "b"
        assert rig.fake.spawn_attempts == 3


# ── 오류 분류 · 역요청 · 가드 ────────────────────────────────────────


@pytest.mark.parametrize("error, expected", [
    ({"message": "x", "codexErrorInfo": "usageLimitExceeded"}, usage_limit_message("codex")),
    ({"message": "x", "codexErrorInfo": "usageLimitExceeded", "resetsAt": 1760000000},
     usage_limit_message("codex", 1760000000)),
    ({"message": "x", "codexErrorInfo": {"usageLimitExceeded": {"resetsAt": 1760000000}}},
     usage_limit_message("codex", 1760000000)),
    ({"message": "x", "codexErrorInfo": "unauthorized"}, auth_expired_message("codex", "codex login")),
    ({"message": "x", "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 502}}}, GENERIC_MESSAGE),
    ({"message": "usage limit in text only"}, GENERIC_MESSAGE),
    (None, GENERIC_MESSAGE),
])
def test_classify_codex_error(error, expected):
    assert classify_codex_error(error).user_message == expected


@pytest.mark.asyncio
async def test_unexpected_server_requests_are_declined(tmp_path):
    async with codex_rig(tmp_path) as rig:
        proc = rig.adapter._procs["ro"]
        for method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            assert rig.adapter._on_server_request(proc, method, {}) == {"decision": "decline"}
        with pytest.raises(JsonRpcError) as exc:
            rig.adapter._on_server_request(proc, "some/unknown", {})
        assert exc.value.code == -32601


@pytest.mark.asyncio
async def test_default_transport_refuses_real_codex_in_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")
    env = build_env(tmp_path)
    specs = env.specs(registry=True, backend_id="codex")
    server = SharedToolServer(specs, backend="codex")
    await server.start()
    adapter = CodexAdapter(tool_server=server, server_specs=specs, cwd=PROJECT_ROOT, bin="codex")
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
        CodexAdapter(tool_server=None)
