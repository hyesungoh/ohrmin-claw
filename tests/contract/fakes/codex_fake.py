# source=https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false
"""Codex app-server fake — 문서 기반 in-memory JSON-RPC 피어 (실제 codex 미실행, live 트래픽 대조 전).

CodexAdapter의 transport_factory seam에 주입한다. factory 호출 1회 = app-server 프로세스 1개(FakeAppServer)이며
argv의 `-c` 오버라이드를 TOML로 해석해(approval_policy·sandbox_mode·mcp_servers.*·hooks.PreToolUse·model) 그대로 따른다.

- 와이어: JSONL, `"jsonrpc"` 헤더 없음. 메서드 initialize · account/read · thread/start · turn/start · turn/interrupt ·
  thread/archive. 알림 thread/started · turn/started · item/started · item/agentMessage/delta · item/completed ·
  error · turn/completed(status completed|interrupted|failed, error.codexErrorInfo).
- 스크립트 번역(정규 어휘 → Codex item): text → agentMessage, Bash → commandExecution, Read/Glob/Grep → 셸
  commandExecution(commandActions read|listFiles|search), Write/Edit/MultiEdit/NotebookEdit·apply_patch → fileChange
  (훅 tool_name apply_patch, 패치 본문), WebSearch → webSearch, mcp__s__t → mcpToolCall. WebFetch 등 없는 도구 = 미실행.
- 훅(gate_signals=True): item/started 후 hooks.PreToolUse 매처가 훅 tool_name에 맞으면 command를 **실제 서브프로세스**로
  실행(프로세스 env 그대로 → OHRMIN_GATE_URL = 실제 루프백 게이트 엔드포인트). exit 2 또는 deny JSON = 미실행.
  gate_signals=False(비관적 fake) = 훅 미발화.
- MCP 실행: 프로세스 설정의 mcp_servers.<s>.url(실제 SharedToolServer)로 공식 mcp streamable HTTP 클라이언트 호출
  → 서버측 CallerCapability가 그대로 적용된다. 빌트인은 파일시스템을 건드리지 않고 실행된 것으로 기록한다.
  ro 프로세스(sandbox_mode=read-only)의 fileChange는 샌드박스가 거부한다.
- 세션 관측: session_starts = thread/start 수, session_closes = thread/archive 수, interrupts = turn/interrupt 수.
  fail_starts = 다음 N회 thread/start 오류, fail_spawns = 다음 N회 프로세스 기동(transport 생성) 실패.
- crash: 프로세스 종료(클라이언트 EOF). 프로세스 권한 표식(priv|ro)은 env의 게이트 토큰 URL로 판정한다(샌드박스 값과 독립).
"""
import asyncio
import contextlib
import json
import os
import re
import shlex
import tomllib
from collections import deque
from pathlib import PurePosixPath

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from core.llm_config import load_llm_config
from core.tool_server.capability import CallerCapability
from tests.contract.harness import BackendHarness, Execution, ToolResult
from tests.contract.scenario import RAW_PROVIDER_MARKER, split_turns

MODEL = "gpt-contract"
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_SETTLE_SPINS = 50
_HOOK_DEFAULT_TIMEOUT = 600
_ERROR_INFO = {"usage_limit": "usageLimitExceeded", "auth_expired": "unauthorized", "generic": "internalServerError"}
_PATCH_HEADER_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)
CHATGPT_ACCOUNT = {"account": {"type": "chatgpt", "email": "owner@example.com", "planType": "plus"}, "requiresOpenaiAuth": True}


class _RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class SpawnFailed(OSError):
    """fail_spawns 주입 — 실제 exec 실패와 같은 OSError 계열."""


async def _settle() -> None:
    """클라이언트가 직전 알림을 처리(on_tool·interrupt 전송 등)할 이벤트 루프 여유."""
    for _ in range(_SETTLE_SPINS):
        await asyncio.sleep(0)


def parse_overrides(argv: list[str]) -> dict:
    """`-c key=value` → 중첩 설정 dict (값은 TOML, 해석 실패 시 문자열 — codex CLI 규약)."""
    config: dict = {}
    for flag, raw in zip(argv, argv[1:]):
        if flag != "-c":
            continue
        key, _, value = raw.partition("=")
        try:
            parsed = tomllib.loads(f"v = {value}")["v"]
        except tomllib.TOMLDecodeError:
            parsed = value
        node = config
        parts = key.strip().split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = parsed
    return config


@contextlib.asynccontextmanager
async def _mcp_session(url: str):
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _call_mcp(url: str, tool_name: str, args: dict) -> ToolResult:
    async with _mcp_session(url) as session:
        result = await session.call_tool(tool_name, args)
    text = "".join(block.text for block in result.content if getattr(block, "type", None) == "text")
    return ToolResult(text=text, is_error=bool(result.isError))


def _split_mcp_name(name: str):
    if not name.startswith("mcp__"):
        return None
    server, sep, tool_name = name[len("mcp__"):].partition("__")
    return (server, tool_name) if sep else None


class _FakeTurn:
    def __init__(self, turn_id: str, thread_id: str):
        self.id = turn_id
        self.thread_id = thread_id
        self.wake = asyncio.Event()
        self.interrupted = False
        self.archived = False

    @property
    def stopped(self) -> bool:
        return self.interrupted or self.archived


class FakeTransport:
    """클라이언트 쪽 stdio — FakeAppServer와 in-memory 줄 큐로 연결."""

    def __init__(self, server: "FakeAppServer"):
        self._server = server
        self._eof = False

    async def readline(self) -> bytes:
        if self._eof:
            return b""
        line = await self._server.outbox.get()
        if line is None:
            self._eof = True
            return b""
        return line

    def write(self, data: bytes) -> None:
        self._server.receive(data)

    async def drain(self) -> None:
        if self._server.dead:
            raise BrokenPipeError("fake codex app-server exited")

    async def close(self) -> None:
        self._server.exit()


class FakeAppServer:
    """`codex app-server` 프로세스 1개."""

    def __init__(self, fake: "CodexFake", argv: list[str], env: dict, cwd: str | None):
        self.fake = fake
        self.argv = list(argv)
        self.env = dict(env)
        self.cwd = cwd
        self.config = parse_overrides(argv)
        self.cap = fake.cap_of(env)
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.dead = False
        self.threads: dict = {}  # codex thread id → {"developerInstructions", "turn"}
        self._buffer = b""
        self._tasks: set = set()

    # ── 와이어 ──
    def receive(self, data: bytes) -> None:
        if self.dead:
            raise BrokenPipeError("fake codex app-server exited")
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if line.strip():
                self._spawn(self._handle(json.loads(line)))

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _send(self, message: dict) -> None:
        if not self.dead:
            self.outbox.put_nowait((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))

    def _notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def exit(self) -> None:
        """프로세스 종료(크래시·terminate) — 클라이언트 EOF, 진행 중 턴 중단."""
        if self.dead:
            return
        self.dead = True
        self.outbox.put_nowait(None)
        for thread in self.threads.values():
            turn = thread["turn"]
            if turn is not None:
                turn.archived = True
                turn.wake.set()

    async def _handle(self, message: dict) -> None:
        self.fake.wire_log.append((self.cap, message))  # 헤더 규약("jsonrpc" 없음)은 테스트가 wire_log로 단언
        request_id, method = message.get("id"), message.get("method")
        if request_id is None or method is None:
            return  # initialized 알림 · 응답
        handler = {
            "initialize": self._initialize,
            "account/read": self._account_read,
            "thread/start": self._thread_start,
            "turn/start": self._turn_start,
            "turn/interrupt": self._turn_interrupt,
            "thread/archive": self._thread_archive,
        }.get(method)
        params = message.get("params") or {}
        try:
            if handler is None:
                raise _RpcError(-32601, f"method not found: {method}")
            result, after = handler(params)
        except _RpcError as e:
            self._send({"id": request_id, "error": {"code": e.code, "message": e.message}})
            return
        self._send({"id": request_id, "result": result})
        if after is not None:
            await after()

    # ── 메서드 ──
    def _initialize(self, params):
        return {"userAgent": "codex-fake/0.0.0"}, None

    def _account_read(self, params):
        return self.fake.account, None

    def _thread_start(self, params):
        self.fake.session_starts += 1
        self.fake.thread_starts.append((self.cap, dict(params)))
        if self.fake.fail_starts > 0:
            self.fake.fail_starts -= 1
            raise _RpcError(-32603, f"failed to start thread ({RAW_PROVIDER_MARKER})")
        thread_id = f"thr_{self.fake.next_id()}"
        self.threads[thread_id] = {"developerInstructions": params.get("developerInstructions"), "turn": None}

        async def after():
            self._notify("thread/started", {"thread": {"id": thread_id}})

        return {"thread": {"id": thread_id}}, after

    def _turn_start(self, params):
        thread = self.threads.get(params.get("threadId"))
        if thread is None or thread.get("archived"):
            raise _RpcError(-32600, f"unknown thread ({RAW_PROVIDER_MARKER})")
        inputs = params.get("input") or []
        fake = self.fake
        fake.prompts.append("\n".join(i.get("text", "") for i in inputs if i.get("type") == "text"))
        fake.turn_inputs.append(inputs)
        fake.system_prompts.append(thread["developerInstructions"])
        fake.allowed_tools.append(None)  # Codex에는 allowed_tools 스티어링 표면이 없다
        fake.turn_processes.append(self)
        steps = fake.next_turn()
        turn = _FakeTurn(f"turn_{fake.next_id()}", params["threadId"])
        thread["turn"] = turn
        info = {"id": turn.id, "status": "inProgress", "items": [], "error": None}

        async def after():
            self._notify("turn/started", {"threadId": turn.thread_id, "turn": info})
            self._spawn(self._run_turn(thread, turn, steps))

        return {"turn": info}, after

    def _turn_interrupt(self, params):
        self.fake.interrupts += 1
        thread = self.threads.get(params.get("threadId")) or {}
        turn = thread.get("turn")
        if turn is not None and turn.id == params.get("turnId") and not self.fake.ignore_interrupt:
            turn.interrupted = True
            turn.wake.set()
        return {}, None

    def _thread_archive(self, params):
        self.fake.session_closes += 1
        thread = self.threads.get(params.get("threadId"))
        if thread is None:
            raise _RpcError(-32600, "unknown thread")
        thread["archived"] = True
        turn = thread["turn"]
        if turn is not None:
            turn.archived = True
            turn.wake.set()
        return {}, None

    # ── 턴 실행 ──
    async def _run_turn(self, thread: dict, turn: _FakeTurn, steps) -> None:
        for step in steps:
            await _settle()
            if turn.stopped or self.dead:
                break
            if step.kind == "text":
                self._agent_message(turn, step.text)
            elif step.kind in ("tool", "gate_probe"):
                await self._tool_call(turn, step.name, step.input)
            elif step.kind == "error":
                self._provider_error(turn, step)
                thread["turn"] = None
                return
            elif step.kind == "wait_interrupt":
                await turn.wake.wait()
            elif step.kind == "crash":
                self.exit()
                return
            else:
                raise AssertionError(f"unsupported step: {step.kind}")
        if self.dead or turn.archived:
            return
        await _settle()
        status = "interrupted" if turn.interrupted else "completed"
        thread["turn"] = None
        self._notify("turn/completed", {
            "threadId": turn.thread_id, "turn": {"id": turn.id, "status": status, "items": [], "error": None},
        })

    def _item_params(self, turn: _FakeTurn, item: dict) -> dict:
        return {"threadId": turn.thread_id, "turnId": turn.id, "item": item}

    def _agent_message(self, turn: _FakeTurn, text: str) -> None:
        item_id = f"msg_{self.fake.next_id()}"
        self._notify("item/started", self._item_params(turn, {"type": "agentMessage", "id": item_id, "text": ""}))
        self._notify("item/agentMessage/delta", {"threadId": turn.thread_id, "turnId": turn.id, "itemId": item_id, "delta": text})
        self._notify("item/completed", self._item_params(turn, {"type": "agentMessage", "id": item_id, "text": text}))

    def _provider_error(self, turn: _FakeTurn, step) -> None:
        error = {
            "message": f"{step.error_kind} from provider ({RAW_PROVIDER_MARKER})",
            "codexErrorInfo": _ERROR_INFO[step.error_kind],
            "additionalDetails": None,
        }
        if step.resets_at is not None:
            error["resetsAt"] = step.resets_at
        self._notify("error", {"threadId": turn.thread_id, "turnId": turn.id, "error": error, "willRetry": False})
        self._notify("turn/completed", {
            "threadId": turn.thread_id, "turn": {"id": turn.id, "status": "failed", "items": [], "error": error},
        })

    def _translate(self, name: str, tool_input: dict):
        """정규 스텝 → (item, 훅 tool_name, 훅 tool_input, 실행 종류) — 이 런타임에 없는 도구면 None."""
        if name == "Bash":
            command = tool_input.get("command", "")
            item = {"type": "commandExecution", "command": command, "cwd": self.cwd,
                    "commandActions": [{"type": "unknown", "command": command}]}
            return item, "Bash", {"command": command}, "shell"
        if name in ("Read", "Glob", "Grep"):
            if name == "Read":
                path = tool_input.get("file_path", "")
                command = f"cat {shlex.quote(path)}"
                action = {"type": "read", "command": command, "name": PurePosixPath(path).name, "path": path}
            elif name == "Glob":
                command = f"rg --files -g {shlex.quote(tool_input.get('pattern', ''))}"
                action = {"type": "listFiles", "command": command, "path": None}
            else:
                query = tool_input.get("pattern", "")
                command = f"rg {shlex.quote(query)}"
                action = {"type": "search", "command": command, "query": query, "path": None}
            item = {"type": "commandExecution", "command": command, "cwd": self.cwd, "commandActions": [action]}
            return item, "Bash", {"command": command}, "shell"
        if name in WRITE_TOOLS or name == "apply_patch":
            if name == "apply_patch":
                patch = tool_input.get("input", "")
                hook_input = dict(tool_input)
            else:
                path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
                header = "Add" if name == "Write" else "Update"
                patch = f"*** Begin Patch\n*** {header} File: {path}\n+x\n*** End Patch\n"
                hook_input = {"input": patch}
            changes = [
                {"path": m.group(2), "kind": {"type": m.group(1).lower()}, "diff": ""}
                for m in _PATCH_HEADER_RE.finditer(patch)
            ]
            return {"type": "fileChange", "changes": changes}, "apply_patch", hook_input, "patch"
        if name == "WebSearch":
            return {"type": "webSearch", "query": tool_input.get("query", "")}, "web_search", tool_input, "builtin"
        mcp = _split_mcp_name(name)
        if mcp is not None:
            server, tool_name = mcp
            item = {"type": "mcpToolCall", "server": server, "tool": tool_name, "arguments": tool_input}
            return item, name, tool_input, "mcp"
        return None

    async def _tool_call(self, turn: _FakeTurn, name: str, tool_input: dict) -> None:
        translated = self._translate(name, tool_input)
        if translated is None:
            self.fake.executions.append(Execution(
                name, tool_input, executed=False, blocked_by="no_such_tool",
                result_text=f"unsupported tool: {name}", is_error=True,
            ))
            return
        item, hook_name, hook_input, kind = translated
        item = {**item, "id": f"call_{self.fake.next_id()}", "status": "inProgress"}
        self._notify("item/started", self._item_params(turn, item))
        denied, reason = (False, None)
        if self.fake.gate_signals:
            denied, reason = await self._run_pre_tool_use_hooks(turn, hook_name, hook_input)
        if denied:
            execution = Execution(name, tool_input, executed=False, blocked_by="hook", result_text=reason, is_error=True)
        elif kind == "patch" and self.config.get("sandbox_mode") == "read-only":
            execution = Execution(
                name, tool_input, executed=False, blocked_by="sandbox",
                result_text="patch rejected: read-only sandbox", is_error=True,
            )
        elif kind == "mcp":
            server, tool_name = _split_mcp_name(name)
            server_config = (self.config.get("mcp_servers") or {}).get(server)
            if server_config is None:
                execution = Execution(
                    name, tool_input, executed=False, blocked_by="no_such_tool",
                    result_text=f"unknown MCP server: {server}", is_error=True,
                )
            else:
                result = await _call_mcp(server_config["url"], tool_name, tool_input)
                execution = Execution(name, tool_input, executed=True, result_text=result.text, is_error=result.is_error)
        else:
            execution = Execution(name, tool_input, executed=True, result_text="ok")
        self.fake.executions.append(execution)
        status = "declined" if execution.blocked_by == "hook" else ("failed" if not execution.executed else "completed")
        self._notify("item/completed", self._item_params(turn, {**item, "status": status}))

    async def _run_pre_tool_use_hooks(self, turn: _FakeTurn, tool_name: str, tool_input: dict):
        """hooks.PreToolUse 설정 그대로 — 매처 정규식이 맞는 command 훅을 실제 서브프로세스로 실행."""
        # provenance: https://learn.chatgpt.com/docs/hooks.md verified=false
        for group in (self.config.get("hooks") or {}).get("PreToolUse") or []:
            matcher = group.get("matcher")
            if matcher and not re.search(matcher, tool_name):
                continue
            for hook in group.get("hooks") or []:
                if hook.get("type") != "command":
                    continue
                payload = {
                    "session_id": turn.thread_id,
                    "turn_id": turn.id,
                    "hook_event_name": "PreToolUse",
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "cwd": self.cwd,
                }
                proc = await asyncio.create_subprocess_exec(
                    *shlex.split(hook["command"]),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self.env,
                    cwd=self.cwd,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                    hook.get("timeout", _HOOK_DEFAULT_TIMEOUT),
                )
                self.fake.hook_runs.append((self.cap, tool_name, proc.returncode))
                decision = _hook_decision(stdout)
                if proc.returncode == 2 or (proc.returncode == 0 and decision is not None):
                    return True, decision or stderr.decode("utf-8", "replace").strip()
        return False, None


def _hook_decision(stdout: bytes) -> str | None:
    """훅 stdout의 deny 사유 (deny가 아니면 None)."""
    try:
        output = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    specific = output.get("hookSpecificOutput") if isinstance(output, dict) else None
    if isinstance(specific, dict) and specific.get("permissionDecision") == "deny":
        return specific.get("permissionDecisionReason") or ""
    return None


class CodexFake:
    """FakeRuntime 구현 (tests/contract/harness.py 참고) + Codex 전용 관측."""

    def __init__(self, tool_server, gate_signals: bool = True, account: dict | None = None):
        self.tool_server = tool_server
        self.gate_signals = gate_signals
        self.account = CHATGPT_ACCOUNT if account is None else account
        self._turns: deque = deque()
        self._ids = 0
        self.fail_starts = 0
        self.fail_spawns = 0
        self.ignore_interrupt = False
        self.prompts: list = []
        self.system_prompts: list = []
        self.allowed_tools: list = []
        self.executions: list = []
        self.session_starts = 0
        self.session_closes = 0
        self.interrupts = 0
        self.unscripted_turns = 0
        # Codex 전용 관측
        self.spawn_attempts = 0
        self.processes: list[FakeAppServer] = []
        self.thread_starts: list = []  # (cap, thread/start params)
        self.turn_inputs: list = []
        self.turn_processes: list[FakeAppServer] = []
        self.hook_runs: list = []  # (cap, hook tool_name, returncode)
        self.wire_log: list = []  # (cap, 클라이언트 → 서버 메시지)

    # ── 스크립트 ──
    def script(self, *steps) -> None:
        self._turns.extend(split_turns(steps))

    @property
    def pending_turns(self) -> int:
        return len(self._turns)

    def next_turn(self) -> list:
        if self._turns:
            return self._turns.popleft()
        self.unscripted_turns += 1
        return []

    def next_id(self) -> int:
        self._ids += 1
        return self._ids

    # ── transport seam ──
    async def transport_factory(self, argv, env, cwd) -> FakeTransport:
        self.spawn_attempts += 1
        if self.fail_spawns > 0:
            self.fail_spawns -= 1
            raise SpawnFailed(f"spawn failed ({RAW_PROVIDER_MARKER})")
        server = FakeAppServer(self, argv, env, cwd)
        self.processes.append(server)
        return FakeTransport(server)

    def cap_of(self, env: dict) -> str:
        """프로세스 권한 표식 — env의 게이트 URL이 어느 capability 토큰인지 (argv 샌드박스 값과 독립)."""
        url = env.get("OHRMIN_GATE_URL")
        if url == self.tool_server.gate_url(CallerCapability.PRIVILEGED):
            return "priv"
        if url == self.tool_server.gate_url(CallerCapability.READ_ONLY):
            return "ro"
        return "unknown"

    def process(self, cap: str) -> FakeAppServer:
        """해당 권한 토큰으로 기동된 가장 최근 프로세스."""
        matches = [p for p in self.processes if p.cap == cap]
        if not matches:
            raise AssertionError(f"{cap} 프로세스가 기동되지 않았다")
        return matches[-1]

    def process_argv(self, cap: str) -> list[str]:
        return self.process(cap).argv

    def process_env(self, cap: str) -> dict:
        return self.process(cap).env

    def turn_caps(self) -> list[str]:
        return [p.cap for p in self.turn_processes]

    # ── 도구 transport 관측 ──
    def _latest_servers(self) -> dict:
        if not self.turn_processes:
            raise AssertionError("런타임이 아직 턴을 받지 않았다")
        return self.turn_processes[-1].config.get("mcp_servers") or {}

    async def list_exposed_tools(self) -> dict:
        exposed = {}
        for server, config in self._latest_servers().items():
            async with _mcp_session(config["url"]) as session:
                tools = (await session.list_tools()).tools
            exposed[server] = {t.name: (t.description, t.inputSchema) for t in tools}
        return exposed

    async def call_tool_endpoint(self, canonical: str, args: dict) -> ToolResult:
        server, tool_name = _split_mcp_name(canonical)
        return await _call_mcp(self._latest_servers()[server]["url"], tool_name, args)


def make_harness(name: str, env, **options) -> BackendHarness:
    """codex — 봇 배선(bot.main.build_llm_and_tool_server) 그대로 + transport_factory seam에 in-memory app-server 주입.

    options: gate_signals=False(비관적 fake: PreToolUse 훅 미발화).
    """
    gate_signals = options.pop("gate_signals", True)
    if options:
        raise TypeError(f"codex harness options unsupported: {sorted(options)}")
    import bot.main as main

    config_path = os.path.join(env.root, "config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump({"llm": {"backend": "codex", "codex": {"model": MODEL, "bin": "codex"}}}, f)
    config = load_llm_config(config_path, {})
    specs = env.specs(registry=True, backend_id=config.backend)
    adapter, tool_server = main.build_llm_and_tool_server(config, specs)
    fake = CodexFake(tool_server, gate_signals=gate_signals)
    adapter._transport_factory = fake.transport_factory
    # 봇과 같은 늦은 주입 — add_memory 오버플로우 통합기가 이 어댑터의 ask를 쓴다(F6).
    env.memory_mgr.llm = adapter

    def check_native_skills_suppressed() -> None:
        # Codex: 네이티브 스킬 억제 수단 없음(OQ-3 기록만) — 두 프로세스 모두 봇 SkillRegistry(skills MCP)를 받는지 확인.
        for cap, capability in (("priv", CallerCapability.PRIVILEGED), ("ro", CallerCapability.READ_ONLY)):
            servers = fake.process(cap).config["mcp_servers"]
            assert servers["skills"]["url"] == tool_server.endpoint("skills", capability)
        assert "skills" not in fake.process("priv").config  # 네이티브 스킬 설정 오버라이드 없음(기록만)

    return BackendHarness(
        name=name,
        backend_id=config.backend,
        skills_mode=config.skills_mode,
        config=config,
        specs=specs,
        adapter=adapter,
        fake=fake,
        env=env,
        gate_via="hook",
        auth_fix="codex login",
        check_native_skills_suppressed=check_native_skills_suppressed,
        tool_server=tool_server,
        normalize_tool=lambda tool_name: "Write" if tool_name in WRITE_TOOLS else tool_name,
    )
