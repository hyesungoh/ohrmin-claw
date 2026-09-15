"""Codex 훅 스크립트 — 실제 서브프로세스([sys.executable, script]) × 루프백 게이트 엔드포인트 (fail-closed).

- SharedToolServer `POST /t/{token}/gate`: ro 토큰 → exit 2 + deny JSON, priv 토큰 → exit 0.
- 엔드포인트 다운·타임아웃·잘못된 JSON·비200 → exit 2.
- 스크립트: Python 3.9 문법(ast), stdlib만, `core` import 없음, 어노테이션 없음, core/hooks에 __init__ 없음.
외부 네트워크 없음: 127.0.0.1만 사용한다.
"""
import ast
import asyncio
import contextlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from core.safety_gate import RUNTIME_GUARD_REASON, UNRESOLVED_PATHS_REASON, evaluate_tool_gate
from core.tool_server.capability import CallerCapability
from core.tool_server import http_server
from core.tool_server.http_server import SharedToolServer

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "core", "hooks", "codex_gate_hook.py")
FALLBACK_REASON = "게이트 판정을 확인할 수 없어 안전을 위해 차단합니다."
BASH_PAYLOAD = {"session_id": "s1", "turn_id": "t1", "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": "ls"}}


def _run_hook(url, payload=BASH_PAYLOAD, raw_stdin=None, timeout=30):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if url is not None:
        env["OHRMIN_GATE_URL"] = url
    stdin = raw_stdin if raw_stdin is not None else json.dumps(payload).encode("utf-8")
    return subprocess.run([sys.executable, SCRIPT], input=stdin, env=env, capture_output=True, timeout=timeout)


def _deny_json(reason):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def _assert_denied(result, reason):
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout.decode("utf-8")) == _deny_json(reason)


@contextlib.asynccontextmanager
async def _gate_server(backend="codex"):
    server = SharedToolServer([], backend=backend)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


# ── 정적 제약 ─────────────────────────────────────────────────────


class TestScriptConstraints:
    def _tree(self):
        with open(SCRIPT, encoding="utf-8") as f:
            return ast.parse(f.read(), feature_version=(3, 9))

    def test_parses_with_python39_grammar(self):
        assert isinstance(self._tree(), ast.Module)

    def test_imports_are_stdlib_only_and_no_core(self):
        modules = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                modules += [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0
                modules.append(node.module.split(".")[0])
        assert modules
        assert "core" not in modules
        assert all(m in sys.stdlib_module_names for m in modules), modules

    def test_no_annotations_for_py39_runtime(self):
        for node in ast.walk(self._tree()):
            assert not isinstance(node, ast.AnnAssign)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.returns is None
                assert all(a.annotation is None for a in node.args.args + node.args.kwonlyargs)

    def test_hooks_dir_is_not_a_package(self):
        assert not os.path.exists(os.path.join(PROJECT_ROOT, "core", "hooks", "__init__.py"))


# ── 실제 게이트 엔드포인트 ─────────────────────────────────────────


class TestAgainstGateEndpoint:
    @pytest.mark.asyncio
    async def test_ro_token_denies_bash_priv_token_allows(self, capsys):
        _, ro_reason = evaluate_tool_gate("Bash", {}, False)
        async with _gate_server() as server:
            ro = await asyncio.to_thread(_run_hook, server.gate_url(CallerCapability.READ_ONLY))
            priv = await asyncio.to_thread(_run_hook, server.gate_url(CallerCapability.PRIVILEGED))

        _assert_denied(ro, ro_reason)
        assert priv.returncode == 0, priv.stderr
        assert priv.stdout == b""
        lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("[gate]")]
        assert lines == [
            f"[gate] backend=codex cap=ro tool=Bash decision=deny via=hook reason={ro_reason}",
            "[gate] backend=codex cap=priv tool=Bash decision=allow via=hook reason=-",
        ]

    @pytest.mark.asyncio
    async def test_priv_apply_patch_rules(self):
        science = {"tool_name": "apply_patch", "tool_input": {"input": (
            "*** Begin Patch\n*** Update File: prompts/memory.md\n*** Add File: .claude/skills/science-reference/SKILL.md\n*** End Patch"
        )}}
        skills = {"tool_name": "apply_patch", "tool_input": {"input": "*** Begin Patch\n*** Add File: .claude/skills/x/SKILL.md\n*** End Patch"}}
        agent_made = {"tool_name": "apply_patch", "tool_input": {"input": "*** Begin Patch\n*** Add File: .agent-made/x/SKILL.md\n*** End Patch"}}
        no_paths = {"tool_name": "apply_patch", "tool_input": {"input": "garbage"}}
        async with _gate_server() as server:
            url = server.gate_url(CallerCapability.PRIVILEGED)
            results = [await asyncio.to_thread(_run_hook, url, p) for p in (science, skills, agent_made, no_paths)]

        _assert_denied(results[0], "science-reference 스킬은 읽기 전용입니다 (수정 불가).")
        _assert_denied(results[1], RUNTIME_GUARD_REASON)
        assert results[2].returncode == 0
        _assert_denied(results[3], UNRESOLVED_PATHS_REASON)

    @pytest.mark.asyncio
    async def test_mutation_mcp_and_camel_case_payload(self):
        payload = {"toolName": "mcp__memory__add_memory", "toolInput": {"content": "x"}}
        _, reason = evaluate_tool_gate("mcp__memory__add_memory", {}, False)
        async with _gate_server(backend="codex") as server:
            ro = await asyncio.to_thread(_run_hook, server.gate_url(CallerCapability.READ_ONLY), payload)
            priv = await asyncio.to_thread(_run_hook, server.gate_url(CallerCapability.PRIVILEGED), payload)
        _assert_denied(ro, reason)
        assert priv.returncode == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [
        {"hook_event_name": "PreToolUse", "tool_input": {"command": "rm -rf data"}},
        {"tool_name": "Unknown", "tool_input": {}},
    ], ids=["no-tool-name", "unknown-name"])
    async def test_unknown_tool_denied_for_both_caps(self, capsys, payload):
        """Codex 훅 payload가 Unknown으로 정규화되면 priv·ro 모두 deny (Grok Unknown 규칙과 무관)."""
        reason = http_server.UNKNOWN_HOOK_TOOL_REASON
        assert reason == "확인할 수 없는 도구는 차단합니다."
        async with _gate_server() as server:
            ro = await asyncio.to_thread(_run_hook, server.gate_url(CallerCapability.READ_ONLY), payload)
            priv = await asyncio.to_thread(_run_hook, server.gate_url(CallerCapability.PRIVILEGED), payload)

        _assert_denied(ro, reason)
        _assert_denied(priv, reason)
        lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("[gate]")]
        assert lines == [
            f"[gate] backend=codex cap=ro tool=Unknown decision=deny via=hook reason={reason}",
            f"[gate] backend=codex cap=priv tool=Unknown decision=deny via=hook reason={reason}",
        ]

    @pytest.mark.asyncio
    async def test_unknown_token_is_404_and_denied(self):
        async with _gate_server() as server:
            bogus = server.gate_url(CallerCapability.PRIVILEGED).replace("/t/", "/t/x")
            result = await asyncio.to_thread(_run_hook, bogus)
        _assert_denied(result, FALLBACK_REASON)

    @pytest.mark.asyncio
    async def test_bad_stdin_json_denied_without_request(self, capsys):
        async with _gate_server() as server:
            result = await asyncio.to_thread(_run_hook, server.gate_url(CallerCapability.PRIVILEGED), None, b"{not json")
        _assert_denied(result, FALLBACK_REASON)
        assert "[gate]" not in capsys.readouterr().out


# ── 장애 주입 (fail-closed) ─────────────────────────────────────────


@contextlib.contextmanager
def _stub_http(status, body):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/t/tok/gate"
    finally:
        server.shutdown()
        server.server_close()


class TestFailClosed:
    def test_endpoint_down(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # 바인드 해제 → 연결 거부
        _assert_denied(_run_hook(f"http://127.0.0.1:{port}/t/tok/gate"), FALLBACK_REASON)

    def test_timeout(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)  # 연결은 받지만 응답하지 않음
        try:
            started = time.monotonic()
            result = _run_hook(f"http://127.0.0.1:{listener.getsockname()[1]}/t/tok/gate", timeout=30)
            elapsed = time.monotonic() - started
        finally:
            listener.close()
        _assert_denied(result, FALLBACK_REASON)
        assert 4.5 <= elapsed < 15

    def test_bad_response_json(self):
        with _stub_http(200, "not json") as url:
            _assert_denied(_run_hook(url), FALLBACK_REASON)

    def test_non_200_even_if_body_allows(self):
        with _stub_http(500, json.dumps({"allow": True, "reason": ""})) as url:
            _assert_denied(_run_hook(url), FALLBACK_REASON)

    @pytest.mark.parametrize("body", ['{"allow": "yes"}', '{"allow": 1}', "[true]", "{}"])
    def test_non_true_allow_denied(self, body):
        with _stub_http(200, body) as url:
            _assert_denied(_run_hook(url), FALLBACK_REASON)

    def test_deny_reason_passed_through(self):
        with _stub_http(200, json.dumps({"allow": False, "reason": "막음"})) as url:
            _assert_denied(_run_hook(url), "막음")

    def test_allow_true_exit_zero(self):
        with _stub_http(200, json.dumps({"allow": True, "reason": ""})) as url:
            result = _run_hook(url)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_missing_gate_url_env(self):
        _assert_denied(_run_hook(None), FALLBACK_REASON)
