"""Codex 어댑터 — `codex app-server` 2개(priv/ro, S2 권한 분할) × 자체 JSON-RPC stdio 클라이언트 + CodexHookWiring.

- 프로세스: priv(`sandbox_mode="danger-full-access"`)·ro(`sandbox_mode="read-only"`). 둘 다 `approval_policy="never"`,
  MCP URL = 공유 도구 서버의 해당 capability 토큰 경로, PreToolUse 훅 = 게이트 스크립트(OHRMIN_GATE_URL = 해당 토큰).
  자식 env = 부모 env − OPENAI_API_KEY/CODEX_API_KEY + OHRMIN_GATE_URL. 사용자 ~/.codex 설정은 건드리지 않는다.
- 권한 라우팅 키 = `approve_skill_writes is True` → priv 프로세스, 그 외 → ro 프로세스.
- 핸드셰이크: initialize → initialized → account/read(apiKey 인증 = StartupError) → (start만) 훅 probe.
- 세션: bot thread_id → {proc, codex thread id, system prompt 해시}. 다른 권한·프롬프트로 오면 end_session 후 재생성
  (새 세션 = 이력 folding). thread_id=None = 호출마다 새 codex thread(매핑 없음).
  system prompt는 thread/start `developerInstructions`(render + backend_tool_note). per-thread `config`는 쓰지 않는다
  (https://github.com/openai/codex/issues/45361 hang 회피 — 설정은 프로세스 `-c`로).
- 턴: turn/start → item/started(도구 item) = on_tool+counter, item/completed(agentMessage) = on_text, turn/completed = 반환.
  interrupt = turn/interrupt(재시작은 같은 thread에 새 turn/start, turn/steer 미사용). max_turns 도달 = turn/interrupt.
- 크래시: 해당 프로세스 세션 전부 무효화, 다음 호출에서 1회 재기동(실패 = RUNTIME_UNAVAILABLE, ask는 raise).

미검증 표면(UNVERIFIED_SURFACES["codex"], 기동 시 WARN): codex.thread_start.developerInstructions · codex.hooks.cli_override ·
codex.hooks.fires_under_never · codex.hooks.payload_shape · codex.item_types · codex.error_info · codex.account_read_shape ·
codex.mcp.url_override · codex.hooks.env_inheritance.
# provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false
"""
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from core.gate_wiring import CodexHookWiring, toml_string
from core.llm import LLMAdapter
from core.llm_errors import LLMError, RealRuntimeForbidden, StartupError
from core.observability import emit, emit_unverified_warnings, format_startup_summary, format_turn_result
from core.runtimes import process
from core.runtimes.common import CLOSED, DETACHED, SessionStartFailed, find_resets_at, prompt_hash, render_system_prompt, to_llm_error
from core.runtimes.jsonrpc_stdio import METHOD_NOT_FOUND, JsonRpcClient, JsonRpcError, RuntimeUnavailable
from core.runtimes.tool_names import codex_item_on_tool_name, render_tool_refs
from core.tool_server.capability import CallerCapability
from core.tool_server.http_server import HOST

BACKEND = "codex"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK_SCRIPT = os.path.join(PROJECT_ROOT, "core", "hooks", "codex_gate_hook.py")
AUTH_FIX = "codex login"

PRIV, RO = "priv", "ro"
CAPS = (PRIV, RO)
_CAPABILITY = {PRIV: CallerCapability.PRIVILEGED, RO: CallerCapability.READ_ONLY}
# `-c sandbox_mode` (config.toml 표기) / thread/start `sandbox` (app-server 표기).
SANDBOX_MODE = {PRIV: "danger-full-access", RO: "read-only"}
THREAD_SANDBOX = {PRIV: "dangerFullAccess", RO: "readOnly"}
STRIPPED_ENV_KEYS = ("OPENAI_API_KEY", "CODEX_API_KEY")
GATE_URL_ENV = "OHRMIN_GATE_URL"
MCP_TOOL_TIMEOUT_SEC = 120

_REQUEST_TIMEOUT = 60.0
_CONTROL_TIMEOUT = 10.0


@dataclass(eq=False)
class _Proc:
    cap: str
    client: JsonRpcClient | None = None
    alive: bool = True
    closing: bool = False
    turns: dict = field(default_factory=dict)  # codex thread id → 진행 중 _Turn


@dataclass(eq=False)
class _Turn:
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    turn_id: str | None = None
    stopped: bool = False  # interrupt·max_turns·end_session 이후 on_text/on_tool 미발화
    interrupt_requested: bool = False
    interrupt_sent: bool = False
    last_error: dict | None = None


@dataclass(eq=False)
class _Session:
    proc: _Proc
    codex_thread_id: str
    prompt_hash: str
    active: _Turn | None = None


def developer_instructions(system_prompt: str) -> str:
    """thread/start developerInstructions — system prompt + Codex 도구 부록(구분 빈 줄), 도구 표기 렌더."""
    return render_system_prompt(system_prompt, BACKEND)


def _normalized_marker(value) -> str:
    return re.sub(r"[^a-z]", "", value.lower()) if isinstance(value, str) else ""


def account_auth_marker(result) -> str | None:
    """account/read 결과에서 인증 방식 표시 — "apikey" | "chatgpt" | None(형태 미확인).

    # provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false (codex.account_read_shape)
    """
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(_normalized_marker(key))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        else:
            found.add(_normalized_marker(node))

    walk(result)
    if "apikey" in found:
        return "apikey"
    if "chatgpt" in found:
        return "chatgpt"
    return None


def classify_codex_error(error) -> LLMError:
    """turn/completed(status=failed).error → LLMError. codexErrorInfo에 usageLimit → USAGE_LIMIT, unauthorized → AUTH_EXPIRED.

    # provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false (codex.error_info)
    """
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    blob = json.dumps(info).lower() if info is not None else ""
    if "usagelimit" in blob:
        return LLMError.usage_limit(BACKEND, find_resets_at(error))
    if "unauthorized" in blob:
        return LLMError.auth_expired(BACKEND, AUTH_FIX)
    return LLMError.generic()


class CodexAdapter(LLMAdapter):
    backend_id = BACKEND

    def __init__(
        self,
        *,
        tool_server,
        server_specs: list | None = None,
        cwd: str | None = None,
        model: str | None = None,
        bin: str = "codex",
        transport_factory: Callable | None = None,
        hook_argv: list[str] | None = None,
        env: dict | None = None,
    ):
        if tool_server is None:
            raise StartupError(
                "backend=codex에는 공유 도구 서버(SharedToolServer)가 필요합니다",
                "bot.main.build_llm_and_tool_server 배선을 확인하세요",
            )
        self.tool_server = tool_server
        self.server_specs = server_specs if server_specs is not None else []
        self.cwd = cwd
        self.model = model
        self.bin = bin
        # 런타임 주입 seam — (argv, env, cwd) → transport. None이면 실제 프로세스(process.spawn_transport, 테스트 가드 적용).
        self._transport_factory = transport_factory
        self.hook_argv = list(hook_argv) if hook_argv is not None else [sys.executable, HOOK_SCRIPT]
        self._base_env = env  # None이면 기동 시점의 os.environ
        self._envs: dict | None = None
        self._wiring: CodexHookWiring | None = None
        self._procs: dict = {}  # cap → _Proc
        self._launch_locks = {cap: asyncio.Lock() for cap in CAPS}
        self._sessions: dict = {}  # bot thread_id → _Session

    # ── 프로세스 조립 ──

    def child_env(self, cap: str) -> dict:
        """런타임 자식 env — API 키 제거 + 해당 capability 게이트 URL."""
        base = os.environ if self._base_env is None else self._base_env
        env = {key: value for key, value in base.items() if key not in STRIPPED_ENV_KEYS}
        # 훅 command는 app-server env를 상속한다고 가정 — 스크립트가 이 URL(프로세스별 토큰)로 판정을 위임한다.
        # provenance: https://learn.chatgpt.com/docs/hooks.md verified=false (codex.hooks.env_inheritance)
        env[GATE_URL_ENV] = self.tool_server.gate_url(_CAPABILITY[cap])
        return env

    def _prepare(self) -> None:
        if self._envs is None:
            self._envs = {cap: self.child_env(cap) for cap in CAPS}
            self._wiring = CodexHookWiring(self.hook_argv, self._envs, cwd=self.cwd)

    def build_argv(self, cap: str) -> list[str]:
        """`codex app-server` argv — 프로세스 레벨 `-c` 오버라이드(권한·MCP URL·훅·모델).

        # provenance: https://learn.chatgpt.com/docs/app-server verified=false (codex.mcp.url_override)
        # provenance: https://learn.chatgpt.com/docs/hooks.md verified=false (codex.hooks.cli_override)
        """
        self._prepare()
        capability = _CAPABILITY[cap]
        # approval_policy=never에서도 PreToolUse 훅이 발화한다고 가정(문서 미기재) — 미발화 시에도 capability·샌드박스가 차단.
        # provenance: https://learn.chatgpt.com/docs/hooks.md verified=false (codex.hooks.fires_under_never)
        argv = [
            os.path.expanduser(self.bin), "app-server",
            "-c", 'approval_policy="never"',
            "-c", f"sandbox_mode={toml_string(SANDBOX_MODE[cap])}",
        ]
        for spec in self.server_specs:
            argv += [
                "-c", f"mcp_servers.{spec.name}.url={toml_string(self.tool_server.endpoint(spec.name, capability))}",
                "-c", f"mcp_servers.{spec.name}.tool_timeout_sec={MCP_TOOL_TIMEOUT_SEC}",
            ]
        argv += ["-c", self._wiring.config_override()]
        if self.model:
            argv += ["-c", f"model={toml_string(self.model)}"]
        return argv

    async def _handshake(self, client: JsonRpcClient) -> None:
        """initialize → initialized → account/read. API 키 인증 = StartupError."""
        await client.request(
            "initialize",
            {"clientInfo": {"name": "ohrmin_claw", "title": "ohrmin-claw", "version": "1.0"}},
            timeout=_REQUEST_TIMEOUT,
        )
        await client.notify("initialized")
        # provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false (codex.account_read_shape)
        account = await client.request("account/read", {"refreshToken": False}, timeout=_REQUEST_TIMEOUT)
        marker = account_auth_marker(account)
        if marker == "apikey":
            raise StartupError(
                "Codex가 API 키로 인증되어 있습니다 — ChatGPT 구독 로그인만 허용",
                "codex logout 후 codex login (ChatGPT 계정)",
            )
        if marker is None:
            emit("[llm] WARN codex account/read 결과에서 인증 방식을 확인하지 못했습니다 (codex.account_read_shape) — 계속 진행")

    async def _launch(self, cap: str) -> _Proc:
        self._prepare()
        argv, env = self.build_argv(cap), self._envs[cap]
        factory = self._transport_factory or process.spawn_transport
        transport = await factory(argv, env, self.cwd)
        proc = _Proc(cap=cap)
        proc.client = JsonRpcClient(
            transport,
            on_notification=lambda method, params: self._on_notification(proc, method, params),
            on_request=lambda method, params: self._on_server_request(proc, method, params),
            on_close=lambda error: self._on_proc_closed(proc, error),
            # source: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md (문서 기재 — JSONL, "jsonrpc" 헤더 생략)
            jsonrpc_header=False,
        )
        proc.client.start()
        try:
            await self._handshake(proc.client)
        except BaseException:
            await self._close_proc(proc)
            raise
        self._procs[cap] = proc
        return proc

    async def _close_proc(self, proc: _Proc) -> None:
        proc.closing = True
        if proc.client is not None:
            await proc.client.close()
        proc.alive = False
        if self._procs.get(proc.cap) is proc:
            del self._procs[proc.cap]

    async def _ensure_proc(self, cap: str) -> _Proc:
        """살아있는 프로세스 반환. 없거나 죽었으면 이번 호출에서 1회 재기동 (실패 = RUNTIME_UNAVAILABLE)."""
        async with self._launch_locks[cap]:
            proc = self._procs.get(cap)
            if proc is not None and proc.alive:
                return proc
            try:
                return await self._launch(cap)
            except RealRuntimeForbidden:
                raise
            except Exception as e:
                print(f"⚠️ Codex app-server({cap}) 재기동 실패: {type(e).__name__}: {e}")
                raise LLMError.runtime_unavailable(BACKEND) from e

    # ── 런타임 이벤트 ──

    def _on_notification(self, proc: _Proc, method: str, params) -> None:
        params = params if isinstance(params, dict) else {}
        thread_id = params.get("threadId")
        if thread_id is None and isinstance(params.get("turn"), dict):
            thread_id = params["turn"].get("threadId")
        turn = proc.turns.get(thread_id)
        if turn is not None:
            turn.events.put_nowait((method, params))

    def _on_server_request(self, proc: _Proc, method: str, params):
        """app-server → 클라이언트 요청. approval_policy=never라 승인 요청은 오지 않아야 한다 — 오면 거절(fail-closed)."""
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            print(f"⚠️ Codex({proc.cap}) 예상 밖 승인 요청 거절: {method}")
            return {"decision": "decline"}
        raise JsonRpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    def _on_proc_closed(self, proc: _Proc, error) -> None:
        """프로세스 종료 — 해당 프로세스 세션 전부 무효화, 진행 중 턴 실패 신호."""
        proc.alive = False
        if self._procs.get(proc.cap) is proc:
            del self._procs[proc.cap]
        if not proc.closing:
            detail = f": {type(error).__name__}: {error}" if error else ""
            print(f"⚠️ Codex app-server({proc.cap}) 종료 감지{detail} — 세션 무효화, 다음 호출에서 재기동")
        for turn in list(proc.turns.values()):
            turn.events.put_nowait(CLOSED)
        for thread_id, session in list(self._sessions.items()):
            if session.proc is proc:
                del self._sessions[thread_id]

    # ── 기동 ──

    async def start(self) -> None:
        """두 app-server 기동·핸드셰이크 → 게이트 훅 probe → 기동 요약 + 미검증 표면 WARN."""
        self._prepare()
        try:
            for cap in CAPS:
                await self._ensure_started(cap)
            await self._wiring.probe()
        except (RealRuntimeForbidden, StartupError):
            await self._stop_procs()
            raise
        except Exception as e:
            await self._stop_procs()
            raise StartupError(
                f"Codex app-server 기동 실패: {type(e).__name__}: {e}",
                f"codex 설치와 `{AUTH_FIX}` 상태를 확인하세요",
            ) from e
        tools = sum(len(spec.tools) for spec in self.server_specs)
        emit(format_startup_summary(
            BACKEND, self.model or "default", "registry", tools, f"{HOST}:{self.tool_server.port}"
        ))
        emit_unverified_warnings(BACKEND, "registry")

    async def _ensure_started(self, cap: str) -> None:
        async with self._launch_locks[cap]:
            proc = self._procs.get(cap)
            if proc is None or not proc.alive:
                await self._launch(cap)

    async def _stop_procs(self) -> None:
        for proc in list(self._procs.values()):
            await self._close_proc(proc)

    # ── 세션 ──

    async def _session_for(self, thread_id, proc: _Proc, system_prompt: str) -> tuple[_Session, bool]:
        """(세션, 새로 만들었는지). 같은 thread_id가 다른 프로세스·프롬프트로 오면 기존 매핑 종료 후 재생성."""
        digest = prompt_hash(system_prompt)
        if thread_id is not None:
            session = self._sessions.get(thread_id)
            if session is not None and (session.proc is not proc or session.prompt_hash != digest):
                await self.end_session(thread_id)
                session = None
            if session is not None:
                return session, False
        params = {
            "approvalPolicy": "never",
            "sandbox": THREAD_SANDBOX[proc.cap],
            # provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false (codex.thread_start.developerInstructions)
            "developerInstructions": developer_instructions(system_prompt),
        }
        if self.cwd:
            params = {"cwd": self.cwd, **params}
        try:
            result = await proc.client.request("thread/start", params, timeout=_REQUEST_TIMEOUT)
            codex_thread_id = result["thread"]["id"]
        except RuntimeUnavailable:
            raise
        except Exception as e:
            raise SessionStartFailed(f"{type(e).__name__}: {e}") from e
        session = _Session(proc=proc, codex_thread_id=codex_thread_id, prompt_hash=digest)
        if thread_id is not None:
            self._sessions[thread_id] = session
        return session, True

    async def _send_interrupt(self, session: _Session, turn: _Turn) -> None:
        if turn.interrupt_sent or turn.turn_id is None:
            return
        turn.interrupt_sent = True
        try:
            await session.proc.client.request(
                "turn/interrupt", {"threadId": session.codex_thread_id, "turnId": turn.turn_id}, timeout=_CONTROL_TIMEOUT
            )
        except Exception as e:
            print(f"⚠️ Codex turn/interrupt 실패: {type(e).__name__}: {e}")

    async def _stream(self, session, message, image_paths, on_text, on_tool, counter, max_turns, stats) -> list[str]:
        proc = session.proc
        turn = _Turn()
        proc.turns[session.codex_thread_id] = turn
        session.active = turn
        try:
            inputs = [{"type": "text", "text": render_tool_refs(message, BACKEND)}]
            inputs += [{"type": "localImage", "path": path} for path in image_paths or []]
            result = await proc.client.request(
                "turn/start", {"threadId": session.codex_thread_id, "input": inputs}, timeout=_REQUEST_TIMEOUT
            )
            turn.turn_id = ((result or {}).get("turn") or {}).get("id")
            if turn.interrupt_requested:
                await self._send_interrupt(session, turn)
            texts: list[str] = []
            while True:
                event = await turn.events.get()
                if event is DETACHED:
                    return texts
                if event is CLOSED:
                    raise RuntimeUnavailable("codex app-server exited during turn")
                method, params = event
                event_turn = params.get("turnId") or (params.get("turn") or {}).get("id")
                if turn.turn_id is not None and event_turn is not None and event_turn != turn.turn_id:
                    continue
                if method == "item/started":
                    # provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false (codex.item_types)
                    name = codex_item_on_tool_name(params.get("item") or {})
                    if name is None or turn.stopped:
                        continue
                    if on_tool:
                        await on_tool(name)
                    if counter is not None:
                        counter[0] += 1
                    stats["tools"] += 1
                    if name == "Skill":
                        stats["skills_loaded"] += 1
                    if max_turns is not None and stats["tools"] >= max_turns:
                        turn.stopped = True
                        await self._send_interrupt(session, turn)
                elif method == "item/completed":
                    item = params.get("item") or {}
                    if item.get("type") == "agentMessage" and not turn.stopped and item.get("text"):
                        texts.append(item["text"])
                        if on_text:
                            await on_text(item["text"])
                elif method == "error":
                    # provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false (codex.error_info)
                    if not params.get("willRetry"):
                        turn.last_error = params.get("error")
                elif method == "turn/completed":
                    info = params.get("turn") or {}
                    if info.get("status") == "failed":
                        raise classify_codex_error(info.get("error") or turn.last_error)
                    return texts
        finally:
            if proc.turns.get(session.codex_thread_id) is turn:
                del proc.turns[session.codex_thread_id]
            if session.active is turn:
                session.active = None

    async def _turn(
        self,
        thread_id,
        system_prompt: str,
        build_message: Callable[[bool], str],
        *,
        cap: str,
        image_paths,
        on_text,
        on_tool,
        counter,
        max_turns,
        raise_errors: bool,
    ) -> str:
        stats = {"tools": 0, "skills_loaded": 0}
        try:
            proc = await self._ensure_proc(cap)
            session, fresh = await self._session_for(thread_id, proc, system_prompt)
            texts = await self._stream(
                session, build_message(fresh), image_paths, on_text, on_tool, counter, max_turns, stats
            )
        except RealRuntimeForbidden:
            raise
        except Exception as e:
            error = to_llm_error(e, BACKEND, cap)
            emit(format_turn_result(BACKEND, thread_id, cap, stats["tools"], stats["skills_loaded"], error.kind.value))
            if raise_errors:
                if error is e:
                    raise
                raise error from e
            if on_text:
                await on_text(error.user_message)
            return error.user_message
        emit(format_turn_result(BACKEND, thread_id, cap, stats["tools"], stats["skills_loaded"], "ok"))
        return "\n".join(texts)

    # ── LLMAdapter 계약 ──

    async def ask(
        self,
        system_prompt: str,
        user_message: str,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        counter: list | None = None,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """유틸 호출 — one-shot thread, 실패 시 LLMError raise. allowed_tools는 Codex에 적용하지 않는다(스티어링 전용)."""
        return await self._turn(
            None, system_prompt, lambda fresh: user_message,
            cap=PRIV if approve_skill_writes is True else RO, image_paths=None,
            on_text=on_text, on_tool=on_tool, counter=counter, max_turns=max_turns, raise_errors=True,
        )

    async def ask_with_context(
        self,
        system_prompt: str,
        user_message: str,
        context: dict,
        history: list[dict] | None = None,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        counter: list | None = None,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
        thread_id=None,
        image_paths: list[str] | None = None,
    ) -> str:
        """스트리밍 턴 — 새로 만든 세션(one-shot 포함)에만 이력 folding. 실패 = 오류 메시지 on_text 1회 + 반환."""
        return await self._turn(
            thread_id, system_prompt,
            lambda fresh: self._augment_message(user_message, context, history if fresh else None),
            cap=PRIV if approve_skill_writes is True else RO, image_paths=image_paths,
            on_text=on_text, on_tool=on_tool, counter=counter, max_turns=max_turns, raise_errors=False,
        )

    async def interrupt_session(self, thread_id) -> None:
        session = self._sessions.get(thread_id)
        turn = session.active if session is not None else None
        if turn is None:
            return
        turn.stopped = True
        if turn.turn_id is None:
            turn.interrupt_requested = True
            return
        await self._send_interrupt(session, turn)

    async def end_session(self, thread_id) -> None:
        """스레드 세션 종료(멱등) — 진행 중 스트림 분리 + thread/archive(런타임 쪽 대화 종료)."""
        session = self._sessions.pop(thread_id, None)
        if session is None:
            return
        turn = session.active
        if turn is not None:
            turn.stopped = True
            turn.events.put_nowait(DETACHED)
        if not session.proc.alive:
            return
        try:
            # provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false (codex.thread_archive)
            # 문서 기재 메서드 thread/archive — 대화 종료·rollout 보관, live 미확인: 아침 체크리스트
            await session.proc.client.request(
                "thread/archive", {"threadId": session.codex_thread_id}, timeout=_CONTROL_TIMEOUT
            )
        except Exception as e:
            print(f"⚠️ Codex 세션 정리 실패(thread={thread_id}): {type(e).__name__}: {e}")

    async def close_all(self) -> None:
        """모든 스레드 세션 종료 후 app-server 프로세스 종료 (봇 종료 경로, 멱등)."""
        for thread_id in list(self._sessions):
            await self.end_session(thread_id)
        await self._stop_procs()

    def session_ids(self) -> list:
        return list(self._sessions)

    def has_session(self, thread_id) -> bool:
        return thread_id in self._sessions
