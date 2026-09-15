"""JSON-RPC stdio 클라이언트 + 런타임 프로세스 가드 — 실제 서브프로세스 `[sys.executable, tmp 에코 에이전트]`.

- 요청/응답(헤더 포함·생략), error 응답, 알림 순서, 서버→클라이언트 요청(핸들러·미등록·JsonRpcError), 해석 불가 줄 무시.
- 크래시: 대기 요청 전부 RuntimeUnavailable, on_close 1회, 이후 요청도 RuntimeUnavailable.
- process 가드: OHRMIN_FORBID_REAL_RUNTIMES=1이면 현재 인터프리터(sys.executable, 심링크 해석) 외 프로그램은 exec 전에 거부.
- terminate: SIGTERM 무시 프로세스는 timeout 후 kill.
실제 codex/claude/grok은 실행하지 않는다.
"""
import asyncio
import os
import stat
import sys

import pytest
import pytest_asyncio

from core.llm_errors import RealRuntimeForbidden
from core.runtimes import process
from core.runtimes.jsonrpc_stdio import JsonRpcClient, JsonRpcError, RuntimeUnavailable

AGENT = r'''
import json
import sys


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    return json.loads(line) if line else None


while True:
    message = read()
    if message is None:
        break
    method, request_id, params = message.get("method"), message.get("id"), message.get("params")
    if request_id is None:
        continue
    if method == "echo":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"params": params, "had_header": "jsonrpc" in message}})
    elif method == "notify_twice":
        send({"method": "progress", "params": {"n": 1}})
        send({"jsonrpc": "2.0", "method": "progress", "params": {"n": 2}})
        send({"id": request_id, "result": "done"})
    elif method == "ask_client":
        send({"id": "srv-1", "method": params["method"], "params": {"q": "허용?"}})
        send({"id": request_id, "result": read()})
    elif method == "fail":
        send({"id": request_id, "error": {"code": -32000, "message": "boom", "data": {"x": 1}}})
    elif method == "garbage_then_echo":
        sys.stdout.write("not json at all\n")
        sys.stdout.flush()
        send({"id": request_id, "result": "after-garbage"})
    elif method == "crash":
        sys.exit(3)
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown"}})
'''

SIGTERM_IGNORING = r'''
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdout.write("ready\n")
sys.stdout.flush()
time.sleep(60)
'''


@pytest.fixture
def agent_script(tmp_path):
    path = tmp_path / "echo_agent.py"
    path.write_text(AGENT, encoding="utf-8")
    return str(path)


@pytest_asyncio.fixture
async def connect(agent_script, tmp_path):
    clients = []

    async def _connect(**kwargs):
        proc = await process.spawn([sys.executable, agent_script], env=dict(os.environ), cwd=str(tmp_path))
        client = JsonRpcClient(process.ProcessTransport(proc), **kwargs)
        client.start()
        clients.append(client)
        return client, proc

    yield _connect
    for client in clients:
        await client.close()


def _executable(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


# ── 요청 · 응답 · 알림 ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [True, False], ids=["with_header", "without_header"])
async def test_request_response_and_header_mode(connect, header):
    client, _ = await connect(jsonrpc_header=header)

    result = await client.request("echo", {"a": 1, "한글": "값"}, timeout=10)

    assert result == {"params": {"a": 1, "한글": "값"}, "had_header": header}


@pytest.mark.asyncio
async def test_concurrent_requests_matched_by_id(connect):
    client, _ = await connect()

    results = await asyncio.gather(*(client.request("echo", {"n": n}, timeout=10) for n in range(5)))

    assert [r["params"]["n"] for r in results] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_error_response_raises_jsonrpc_error(connect):
    client, _ = await connect()

    with pytest.raises(JsonRpcError) as exc:
        await client.request("fail", {}, timeout=10)

    assert (exc.value.code, exc.value.message, exc.value.data) == (-32000, "boom", {"x": 1})
    assert await client.request("echo", {"still": "alive"}, timeout=10)  # 연결 유지


@pytest.mark.asyncio
async def test_notifications_dispatched_in_order_before_response(connect):
    events = []
    client, _ = await connect(on_notification=lambda method, params: events.append((method, params)))

    result = await client.request("notify_twice", {}, timeout=10)

    assert result == "done"
    assert events == [("progress", {"n": 1}), ("progress", {"n": 2})]


@pytest.mark.asyncio
async def test_unparseable_line_is_ignored(connect):
    client, _ = await connect()

    assert await client.request("garbage_then_echo", {}, timeout=10) == "after-garbage"
    assert not client.closed


# ── 서버 → 클라이언트 요청 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_server_to_client_request_answered_by_handler(connect):
    seen = []

    async def on_request(method, params):
        seen.append((method, params))
        return {"outcome": "allow_once"}

    client, _ = await connect(on_request=on_request, jsonrpc_header=False)

    reply = await client.request("ask_client", {"method": "session/request_permission"}, timeout=10)

    assert seen == [("session/request_permission", {"q": "허용?"})]
    assert reply == {"id": "srv-1", "result": {"outcome": "allow_once"}}


@pytest.mark.asyncio
async def test_server_to_client_request_without_handler_is_method_not_found(connect):
    client, _ = await connect()

    reply = await client.request("ask_client", {"method": "nope/unknown"}, timeout=10)

    assert reply["id"] == "srv-1" and reply["jsonrpc"] == "2.0"
    assert reply["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_server_to_client_request_handler_error_is_forwarded(connect):
    def on_request(method, params):
        raise JsonRpcError(-32602, "bad params")

    client, _ = await connect(on_request=on_request)

    reply = await client.request("ask_client", {"method": "x/y"}, timeout=10)

    assert reply["error"] == {"code": -32602, "message": "bad params"}


# ── 크래시 · 종료 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crash_fails_pending_requests_and_closes_once(connect):
    closes = []
    client, proc = await connect(on_close=lambda error: closes.append(error))

    with pytest.raises(RuntimeUnavailable):
        await client.request("crash", {}, timeout=10)

    assert await asyncio.wait_for(proc.wait(), 10) == 3
    assert client.closed
    assert len(closes) == 1
    with pytest.raises(RuntimeUnavailable):
        await client.request("echo", {}, timeout=10)
    with pytest.raises(RuntimeUnavailable):
        await client.notify("anything")
    await client.close()
    assert len(closes) == 1


@pytest.mark.asyncio
async def test_close_terminates_process(connect):
    client, proc = await connect()
    assert await client.request("echo", {}, timeout=10)

    await client.close()

    assert proc.returncode is not None
    assert client.closed


@pytest.mark.asyncio
async def test_terminate_kills_process_ignoring_sigterm(tmp_path):
    script = tmp_path / "stubborn.py"
    script.write_text(SIGTERM_IGNORING, encoding="utf-8")
    proc = await process.spawn([sys.executable, str(script)], env=dict(os.environ))
    assert (await asyncio.wait_for(proc.stdout.readline(), 10)) == b"ready\n"

    await process.terminate(proc, timeout=0.3)

    assert proc.returncode == -9
    await process.terminate(proc)  # 멱등


# ── 실제 런타임 실행 가드 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_refuses_non_interpreter_program_under_forbid_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")
    marker = tmp_path / "ran"
    fake_codex = _executable(tmp_path / "codex", f"#!/bin/sh\ntouch {marker}\n")

    with pytest.raises(RealRuntimeForbidden):
        await process.spawn([fake_codex, "app-server"])
    with pytest.raises(RealRuntimeForbidden):
        await process.spawn(["codex", "app-server"])  # PATH 이름형도 동일
    with pytest.raises(RealRuntimeForbidden):
        await process.spawn_transport(["grok", "agent", "stdio"], None, None)

    assert not marker.exists()


@pytest.mark.asyncio
async def test_spawn_allows_current_interpreter_under_forbid_flag(tmp_path, monkeypatch, agent_script):
    monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")
    link = tmp_path / "python-link"
    link.symlink_to(sys.executable)

    assert process.is_current_interpreter([sys.executable, agent_script])
    assert process.is_current_interpreter([str(link), agent_script])
    assert not process.is_current_interpreter(["/bin/sh", agent_script])
    assert not process.is_current_interpreter([])

    transport = await process.spawn_transport([str(link), agent_script], dict(os.environ), str(tmp_path))
    client = JsonRpcClient(transport)
    client.start()
    try:
        assert (await client.request("echo", {"ok": True}, timeout=10))["params"] == {"ok": True}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_guard_is_inactive_without_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("OHRMIN_FORBID_REAL_RUNTIMES", raising=False)
    marker = tmp_path / "ran"
    program = _executable(tmp_path / "not-a-runtime", f"#!/bin/sh\ntouch {marker}\n")

    proc = await process.spawn([program])
    await asyncio.wait_for(proc.wait(), 10)

    assert marker.exists()
