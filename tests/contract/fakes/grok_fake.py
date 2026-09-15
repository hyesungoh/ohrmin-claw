# source=https://agentclientprotocol.com/protocol/schema verified=false
"""Grok ACP agent fake — 문서 기반 in-memory `grok agent stdio` 피어 (실제 grok 미실행, live 트래픽 대조 전).

GrokAdapter의 transport_factory seam에 주입한다. factory 호출 1회 = 에이전트 프로세스 1개(FakeGrokAgent).

- 와이어: 줄 단위 JSON-RPC 2.0(`"jsonrpc": "2.0"`). 클라이언트 → 에이전트: initialize · session/new · session/prompt ·
  session/close 요청, session/cancel 알림. 에이전트 → 클라이언트: session/update 알림(agent_message_chunk · tool_call ·
  tool_call_update), session/request_permission 요청(옵션 allow_once · allow_always · reject_once).
- 스크립트 번역(정규 어휘 → ACP ToolCall): text → agent_message_chunk(chunk_size 분할), Bash → kind=execute,
  Read → read, Glob/Grep → search, Write/Edit/MultiEdit/NotebookEdit → edit(locations), WebSearch → fetch(query),
  WebFetch → fetch(url), mcp__s__t → other(title `s__t`), Unknown → other(title mystery_tool). 그 외 도구 = 미실행.
- 이벤트 순서 — permission on(gate_signals=True): tool_call(pending) → session/request_permission →
  tool_call_update(in_progress) → 실행 → tool_call_update(completed|failed). reject = tool_call_update(failed)·미실행,
  cancelled = 미실행·턴 중단. permission off(gate_signals=False, 비관적 fake): tool_call(pending) →
  tool_call_update(in_progress) → 실행(게이트 없음). hold_after_pending = tool_call(pending) 후 cancel까지 정지.
- MCP 실행: session/new mcpServers(type=http)의 URL(실제 SharedToolServer)로 공식 mcp streamable HTTP 클라이언트 호출
  → 서버측 CallerCapability가 그대로 적용된다. 세션 권한 표식(priv|ro)은 그 URL의 capability 토큰으로만 판정한다.
  빌트인은 파일시스템을 건드리지 않고 실행된 것으로 기록한다.
- 시스템 지시: 세션 첫 프롬프트의 `[시스템 지시]\\n` 텍스트 블록(나머지 텍스트 블록 = 사용자 메시지).
- 오류 스텝: session/prompt JSON-RPC error(429 / 401 / 500 문구 + data.status·resetsAt). crash: 프로세스 종료(EOF).
- 관측: session_starts = session/new 수, session_closes = session/close 수, interrupts(= cancels) = session/cancel 수.
  fail_starts = 다음 N회 session/new 오류, fail_spawns = 다음 N회 프로세스 기동 실패.
"""
import asyncio
import json
import os
from collections import deque

from core.llm_config import load_llm_config
from core.tool_server.capability import CallerCapability
from tests.contract.fakes.codex_fake import FakeTransport, SpawnFailed, _call_mcp, _mcp_session, _settle, _split_mcp_name
from tests.contract.harness import BackendHarness, Execution, ToolResult
from tests.contract.scenario import RAW_PROVIDER_MARKER, split_turns

MODEL = "grok-contract"
XAI_API_KEY = "xai-contract-key"
SYSTEM_HEADER = "[시스템 지시]\n"
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
PERMISSION_OPTIONS = [
    {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
    {"optionId": "allow-always", "name": "Always allow", "kind": "allow_always"},
    {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
]
_ALLOW_OPTION_IDS = {"allow-once", "allow-always"}
_PROVIDER_ERRORS = {
    "usage_limit": ("429 Too Many Requests: rate limit exceeded", 429),
    "auth_expired": ("401 Unauthorized: invalid api key", 401),
    "generic": ("upstream model error", 500),
}
_NO_RESPONSE = object()


class _RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class _FakeTurn:
    def __init__(self):
        self.wake = asyncio.Event()
        self.cancelled = False
        self.closed = False

    @property
    def stopped(self) -> bool:
        return self.cancelled or self.closed


class _FakeSession:
    def __init__(self, session_id: str, cap: str, mcp_servers: dict, cwd):
        self.id = session_id
        self.cap = cap
        self.mcp_servers = mcp_servers  # server name → url
        self.cwd = cwd
        self.system = None
        self.turn: _FakeTurn | None = None
        self.closed = False


class FakeGrokAgent:
    """`grok agent stdio` 프로세스 1개."""

    def __init__(self, fake: "GrokFake", argv: list[str], env: dict, cwd):
        self.fake = fake
        self.argv = list(argv)
        self.env = dict(env)
        self.cwd = cwd
        config_path = os.path.join(self.env.get("HOME") or "", ".grok", "config.toml")
        self.config_toml = None
        if os.path.isfile(config_path):
            with open(config_path, encoding="utf-8") as f:
                self.config_toml = f.read()
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.dead = False
        self.sessions: dict = {}
        self._waiting: dict = {}  # 에이전트 → 클라이언트 요청 id → future(응답 메시지)
        self._buffer = b""
        self._tasks: set = set()

    # ── 와이어 ──
    def receive(self, data: bytes) -> None:
        if self.dead:
            raise BrokenPipeError("fake grok agent exited")
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if line.strip():
                self._dispatch(json.loads(line))

    def _dispatch(self, message: dict) -> None:
        self.fake.wire_log.append(message)
        method = message.get("method")
        if method is None:
            future = self._waiting.pop(message.get("id"), None)
            if future is not None and not future.done():
                future.set_result(message)
            return
        if "id" not in message:
            if method == "session/cancel":
                self._cancel(message.get("params") or {})
            return
        task = asyncio.get_running_loop().create_task(self._handle(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _send(self, message: dict) -> None:
        if not self.dead:
            self.outbox.put_nowait((json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False) + "\n").encode("utf-8"))

    def _update(self, session: _FakeSession, update: dict) -> None:
        self._send({"method": "session/update", "params": {"sessionId": session.id, "update": update}})

    def exit(self) -> None:
        """프로세스 종료(크래시·terminate) — 클라이언트 EOF, 진행 중 턴·대기 요청 중단."""
        if self.dead:
            return
        self.dead = True
        self.outbox.put_nowait(None)
        for session in self.sessions.values():
            if session.turn is not None:
                session.turn.closed = True
                session.turn.wake.set()
        for future in self._waiting.values():
            if not future.done():
                future.set_result(None)
        self._waiting.clear()

    async def _handle(self, message: dict) -> None:
        request_id, method = message["id"], message["method"]
        handler = {
            "initialize": self._initialize,
            "session/new": self._session_new,
            "session/prompt": self._session_prompt,
            "session/close": self._session_close,
        }.get(method)
        try:
            if handler is None:
                raise _RpcError(-32601, f"Method not found: {method}")
            result = await handler(message.get("params") or {})
        except _RpcError as e:
            error = {"code": e.code, "message": e.message}
            if e.data is not None:
                error["data"] = e.data
            self._send({"id": request_id, "error": error})
            return
        if result is not _NO_RESPONSE:
            self._send({"id": request_id, "result": result})

    # ── 메서드 ──
    async def _initialize(self, params):
        self.fake.initialize_params.append(params)
        return {
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {"image": self.fake.image_support, "audio": False, "embeddedContext": False},
                "mcpCapabilities": {"http": self.fake.mcp_http, "sse": False},
            },
            "authMethods": [],
        }

    async def _session_new(self, params):
        fake = self.fake
        fake.session_starts += 1
        fake.session_news.append(params)
        if fake.fail_starts > 0:
            fake.fail_starts -= 1
            raise _RpcError(-32603, f"failed to create session ({RAW_PROVIDER_MARKER})")
        servers = {
            s.get("name"): s.get("url") for s in params.get("mcpServers") or [] if isinstance(s, dict) and s.get("type") == "http"
        }
        session = _FakeSession(f"sess_{fake.next_id()}", fake.cap_of(servers), servers, params.get("cwd"))
        self.sessions[session.id] = session
        return {"sessionId": session.id}

    async def _session_prompt(self, params):
        fake = self.fake
        session = self.sessions.get(params.get("sessionId"))
        if session is None or session.closed:
            raise _RpcError(-32602, f"unknown session ({RAW_PROVIDER_MARKER})")
        blocks = params.get("prompt") or []
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        if texts and texts[0].startswith(SYSTEM_HEADER):
            session.system = texts.pop(0)[len(SYSTEM_HEADER):].removesuffix("\n\n")
        fake.prompts.append("\n".join(texts))
        fake.turn_inputs.append(blocks)
        fake.system_prompts.append(session.system)
        fake.allowed_tools.append(None)  # ACP에는 allowed_tools 스티어링 표면이 없다
        fake.turn_sessions.append(session)
        steps = fake.next_turn()
        turn = _FakeTurn()
        session.turn = turn
        try:
            return await self._run_turn(session, turn, steps)
        finally:
            if session.turn is turn:
                session.turn = None

    async def _session_close(self, params):
        if not self.fake.close_supported:
            raise _RpcError(-32601, "Method not found: session/close")
        self.fake.session_closes += 1
        session = self.sessions.get(params.get("sessionId"))
        if session is None:
            raise _RpcError(-32602, "unknown session")
        session.closed = True
        if session.turn is not None:
            session.turn.closed = True
            session.turn.wake.set()
        return {}

    def _cancel(self, params) -> None:
        self.fake.interrupts += 1
        session = self.sessions.get(params.get("sessionId"))
        if session is not None and session.turn is not None and not self.fake.ignore_interrupt:
            session.turn.cancelled = True
            session.turn.wake.set()

    # ── 턴 실행 ──
    async def _run_turn(self, session: _FakeSession, turn: _FakeTurn, steps):
        for step in steps:
            await _settle()
            if turn.stopped or self.dead:
                break
            if step.kind == "text":
                self._chunks(session, step.text)
            elif step.kind in ("tool", "gate_probe"):
                await self._tool_call(session, turn, step.name, step.input)
            elif step.kind == "error":
                message, status = _PROVIDER_ERRORS[step.error_kind]
                data = {"status": status}
                if step.resets_at is not None:
                    data["resetsAt"] = step.resets_at
                raise _RpcError(-32603, f"{message} ({RAW_PROVIDER_MARKER})", data)
            elif step.kind == "wait_interrupt":
                await turn.wake.wait()
            elif step.kind == "crash":
                self.exit()
                return _NO_RESPONSE
            else:
                raise AssertionError(f"unsupported step: {step.kind}")
        if self.dead:
            return _NO_RESPONSE
        await _settle()
        return {"stopReason": "cancelled" if turn.stopped else "end_turn"}

    def _chunks(self, session: _FakeSession, text: str) -> None:
        size = self.fake.chunk_size or max(len(text), 1)
        for start in range(0, max(len(text), 1), size):
            self._update(session, {
                "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text[start:start + size]},
            })

    @staticmethod
    def _translate(name: str, tool_input: dict):
        """정규 스텝 → (ACP ToolCall 필드, 실행 종류) — 이 런타임에 없는 도구면 None."""
        if name == "Bash":
            return {"title": "Bash", "kind": "execute", "rawInput": {"command": tool_input.get("command", "")}}, "builtin"
        if name == "Read":
            path = tool_input.get("file_path", "")
            return {"title": f"Read {path}", "kind": "read", "rawInput": {"path": path}, "locations": [{"path": path}]}, "builtin"
        if name in ("Glob", "Grep"):
            pattern = tool_input.get("pattern", "")
            return {"title": f"{name} {pattern}", "kind": "search", "rawInput": {"pattern": pattern}}, "builtin"
        if name in WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            return {"title": f"{name} {path}", "kind": "edit", "rawInput": {"path": path}, "locations": [{"path": path}]}, "builtin"
        if name == "WebSearch":
            return {"title": "Web search", "kind": "fetch", "rawInput": {"query": tool_input.get("query", "")}}, "builtin"
        if name == "WebFetch":
            return {"title": "Web fetch", "kind": "fetch", "rawInput": {"url": tool_input.get("url", "")}}, "builtin"
        mcp = _split_mcp_name(name)
        if mcp is not None:
            return {"title": f"{mcp[0]}__{mcp[1]}", "kind": "other", "rawInput": dict(tool_input)}, "mcp"
        if name == "Unknown":
            return {"title": "mystery_tool", "kind": "other", "rawInput": dict(tool_input)}, "builtin"
        return None

    async def _tool_call(self, session: _FakeSession, turn: _FakeTurn, name: str, tool_input: dict) -> None:
        fake = self.fake
        translated = self._translate(name, tool_input)
        if translated is None:
            fake.executions.append(Execution(
                name, tool_input, executed=False, blocked_by="no_such_tool",
                result_text=f"unsupported tool: {name}", is_error=True,
            ))
            return
        fields, kind = translated
        call_id = f"call_{fake.next_id()}"
        self._update(session, {"sessionUpdate": "tool_call", "toolCallId": call_id, "status": "pending", **fields})
        if fake.hold_after_pending:
            fake.executions.append(Execution(name, tool_input, executed=False, blocked_by="held"))
            await turn.wake.wait()
            return
        if fake.gate_signals:
            outcome = await self._request_permission(session, {"toolCallId": call_id, "status": "pending", **fields})
            if outcome != "allow":
                if outcome == "cancelled":
                    turn.cancelled = True
                fake.executions.append(Execution(
                    name, tool_input, executed=False, blocked_by="approval" if outcome == "reject" else "cancelled",
                    result_text=f"permission {outcome}", is_error=True,
                ))
                self._update(session, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "failed"})
                return
        self._update(session, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "in_progress"})
        if kind == "mcp":
            server, tool_name = _split_mcp_name(name)
            url = session.mcp_servers.get(server)
            if url is None:
                execution = Execution(
                    name, tool_input, executed=False, blocked_by="no_such_tool",
                    result_text=f"unknown MCP server: {server}", is_error=True,
                )
            else:
                result = await _call_mcp(url, tool_name, tool_input)
                execution = Execution(name, tool_input, executed=True, result_text=result.text, is_error=result.is_error)
        else:
            execution = Execution(name, tool_input, executed=True, result_text="ok")
        fake.executions.append(execution)
        self._update(session, {
            "sessionUpdate": "tool_call_update",
            "toolCallId": call_id,
            "status": "completed" if execution.executed and not execution.is_error else "failed",
            "content": [{"type": "content", "content": {"type": "text", "text": execution.result_text or ""}}],
        })

    async def _request_permission(self, session: _FakeSession, tool_call: dict) -> str:
        """session/request_permission → "allow" | "reject" | "cancelled"."""
        fake = self.fake
        request_id = f"perm_{fake.next_id()}"
        future = asyncio.get_running_loop().create_future()
        self._waiting[request_id] = future
        fake.permission_requests.append((session.cap, tool_call))
        self._send({
            "id": request_id,
            "method": "session/request_permission",
            "params": {"sessionId": session.id, "toolCall": tool_call, "options": PERMISSION_OPTIONS},
        })
        response = await future
        outcome = ((response or {}).get("result") or {}).get("outcome") or {}
        fake.permission_outcomes.append(outcome)
        if outcome.get("outcome") == "selected":
            return "allow" if outcome.get("optionId") in _ALLOW_OPTION_IDS else "reject"
        return "cancelled"


class GrokFake:
    """FakeRuntime 구현 (tests/contract/harness.py 참고) + Grok 전용 관측."""

    def __init__(
        self,
        tool_server,
        gate_signals: bool = True,
        *,
        image_support: bool = False,
        mcp_http: bool = True,
        chunk_size: int | None = None,
        close_supported: bool = True,
    ):
        self.tool_server = tool_server
        self.gate_signals = gate_signals
        self.image_support = image_support
        self.mcp_http = mcp_http
        self.chunk_size = chunk_size
        self.close_supported = close_supported
        self.hold_after_pending = False
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
        # Grok 전용 관측
        self.spawn_attempts = 0
        self.processes: list[FakeGrokAgent] = []
        self.initialize_params: list = []
        self.session_news: list = []  # session/new params
        self.turn_inputs: list = []  # session/prompt prompt 블록
        self.turn_sessions: list[_FakeSession] = []
        self.permission_requests: list = []  # (세션 cap, toolCall)
        self.permission_outcomes: list = []
        self.wire_log: list = []  # 클라이언트 → 에이전트 메시지

    @property
    def cancels(self) -> int:
        """session/cancel 수 (= interrupts)."""
        return self.interrupts

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
        agent = FakeGrokAgent(self, argv, env, cwd)
        self.processes.append(agent)
        return FakeTransport(agent)

    def cap_of(self, servers: dict) -> str:
        """세션 권한 표식 — session/new mcpServers URL이 전부 어느 capability 토큰 경로인지 (그 외 설정과 독립)."""
        for cap, capability in (("priv", CallerCapability.PRIVILEGED), ("ro", CallerCapability.READ_ONLY)):
            try:
                if servers and all(url == self.tool_server.endpoint(name, capability) for name, url in servers.items()):
                    return cap
            except KeyError:
                return "unknown"
        return "unknown"

    def session_caps(self) -> list[str]:
        """턴 순서대로 프롬프트를 받은 세션의 권한 표식."""
        return [session.cap for session in self.turn_sessions]

    # ── 도구 transport 관측 ──
    def _latest_servers(self) -> dict:
        if not self.turn_sessions:
            raise AssertionError("런타임이 아직 턴을 받지 않았다")
        return self.turn_sessions[-1].mcp_servers

    async def list_exposed_tools(self) -> dict:
        exposed = {}
        for server, url in self._latest_servers().items():
            async with _mcp_session(url) as session:
                tools = (await session.list_tools()).tools
            exposed[server] = {t.name: (t.description, t.inputSchema) for t in tools}
        return exposed

    async def call_tool_endpoint(self, canonical: str, args: dict) -> ToolResult:
        server, tool_name = _split_mcp_name(canonical)
        return await _call_mcp(self._latest_servers()[server], tool_name, args)


def make_harness(name: str, env, **options) -> BackendHarness:
    """grok — 봇 배선(bot.main.build_llm_and_tool_server) 그대로 + transport_factory seam에 in-memory ACP 에이전트 주입.

    격리 HOME·프로젝트 Claude 설정 검사 기준(cwd)은 tmp 프로젝트(env.root), 부모 env는 tmp 사용자 HOME + 테스트 키.
    options: gate_signals=False(비관적 fake: session/request_permission 미요청).
    """
    gate_signals = options.pop("gate_signals", True)
    if options:
        raise TypeError(f"grok harness options unsupported: {sorted(options)}")
    import bot.main as main

    config_path = os.path.join(env.root, "config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump({"llm": {"backend": "grok", "grok": {"model": MODEL, "bin": "~/.grok/bin/grok"}}}, f)
    config = load_llm_config(config_path, {})
    specs = env.specs(registry=True, backend_id=config.backend)
    adapter, tool_server = main.build_llm_and_tool_server(config, specs)
    fake = GrokFake(tool_server, gate_signals=gate_signals, chunk_size=3)
    adapter._transport_factory = fake.transport_factory
    adapter.cwd = env.root
    user_home = os.path.join(os.path.dirname(env.root), "user-home")
    adapter._base_env = {"HOME": user_home, "PATH": os.defpath, "LANG": "en_US.UTF-8", "XAI_API_KEY": XAI_API_KEY}
    # 봇과 같은 늦은 주입 — add_memory 오버플로우 통합기가 이 어댑터의 ask를 쓴다(F6).
    env.memory_mgr.llm = adapter

    def check_native_skills_suppressed() -> None:
        # Grok: 격리 HOME — 사용자 ~/.grok·~/.claude(스킬·플러그인·훅) 미로딩, 봇 SkillRegistry(skills MCP)만 노출.
        agent = fake.processes[-1]
        assert agent.env["HOME"] == adapter.home_dir != user_home
        assert agent.env["HOME"].startswith(os.path.join(env.root, "data", "runtime", "grok-home"))
        assert sorted(agent.env) == ["HOME", "LANG", "PATH", "XAI_API_KEY"]
        assert agent.config_toml is not None and "skills" not in agent.config_toml
        session = fake.turn_sessions[-1]
        assert session.mcp_servers["skills"] == tool_server.endpoint("skills", CallerCapability.PRIVILEGED)

    return BackendHarness(
        name=name,
        backend_id=config.backend,
        skills_mode=config.skills_mode,
        config=config,
        specs=specs,
        adapter=adapter,
        fake=fake,
        env=env,
        gate_via="approval",
        auth_fix=".env의 XAI_API_KEY 확인",
        check_native_skills_suppressed=check_native_skills_suppressed,
        tool_server=tool_server,
        normalize_tool=lambda tool_name: "Write" if tool_name in WRITE_TOOLS else ("Grep" if tool_name == "Glob" else tool_name),
    )
