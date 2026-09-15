"""Grok 어댑터 — `grok agent stdio`(ACP, 줄 단위 JSON-RPC 2.0) 프로세스 1개 × GrokApprovalWiring × 격리 HOME.

- 격리 HOME = `<PROJECT_ROOT>/data/runtime/grok-home`. start()가 `<HOME>/.grok/config.toml`(per-model env_key =
  XAI_API_KEY, permission_mode = ask)을 매 기동 재생성·검증한다. `<HOME>/.grok/auth.json`이 있으면 StartupError.
  사용자 `~/.grok`은 읽지도 쓰지도 않는다. 자식 env = {HOME(격리), PATH, LANG, XAI_API_KEY}, cwd = PROJECT_ROOT.
- 프로젝트 Claude 설정 검사: `.claude/settings.json`·`settings.local.json`의 hooks가 비어있지 않거나 `.mcp.json`이
  있으면 StartupError(Grok이 읽어 서버측 게이트를 우회할 수 있음). 게이트 대상 `permissions.allow` 규칙은 WARN
  `grok.project_claude_permissions`.
- 핸드셰이크: initialize(fs·terminal 클라이언트 capability 없음) → `agentCapabilities.mcpCapabilities.http`가 true가
  아니면 StartupError → GrokApprovalWiring.probe().
- 권한 라우팅 키 = `approve_skill_writes is True` → 세션의 MCP URL = priv 토큰, 그 외 ro 토큰. 세션 권한 맵은
  wiring(session_id → privileged). 같은 thread_id가 다른 권한·system prompt로 오면 end_session 후 새 세션.
  thread_id=None = 호출마다 새 세션(매핑 없음).
- system prompt: 세션이 프롬프트를 처음 받아들일 때까지 `session/prompt`의 첫 텍스트 블록
  `"[시스템 지시]\\n" + render(system + backend_tool_note) + "\\n\\n"`로 전달(이력 folding도 같은 시점).
- 스트림: agent_message_chunk 버퍼 → 최초 tool_call·session/prompt 응답·오류·종료 시 flush(on_text 1회).
  청크가 _TEXT_IDLE_FLUSH초 끊기면(스트림 정지) 그때까지를 한 세그먼트로 flush한다. 최초 tool_call만 on_tool+counter.
  interrupt = session/cancel(재시작 = 같은 세션 새 session/prompt). max_turns 도달 = session/cancel.
- tripwire(사후 봉쇄): permission 요청 없이 게이트 deny 대상 빌트인 실행이 관측되면 session/cancel + GENERIC.
- 크래시: 세션 전부 무효화, 다음 호출에서 1회 재기동(실패 = RUNTIME_UNAVAILABLE, ask는 raise).

미검증 표면(UNVERIFIED_SURFACES["grok"], 기동 시 WARN): grok.acp.mcp_http · grok.acp.permission_requests ·
grok.acp.cancel_reprompt · grok.credentials.isolated_home · grok.tool_call_shape · grok.error_shape · grok.image_prompt ·
grok.reads_project_claude_files · grok.system_prompt_preamble · grok.acp.session_close (+ grok.project_claude_permissions).
# provenance: https://agentclientprotocol.com/protocol/schema verified=false
# provenance: https://docs.x.ai/build/cli/reference verified=false
"""
import asyncio
import base64
import json
import mimetypes
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core.gate_wiring import GrokApprovalWiring, toml_string
from core.llm import LLMAdapter
from core.llm_errors import LLMError, LLMErrorKind, RealRuntimeForbidden, StartupError
from core.observability import emit, emit_unverified_warnings, format_startup_summary, format_turn_result
from core.runtimes import process
from core.runtimes.common import CLOSED, DETACHED, SessionStartFailed, find_resets_at, prompt_hash, render_system_prompt, to_llm_error
from core.runtimes.jsonrpc_stdio import METHOD_NOT_FOUND, JsonRpcClient, JsonRpcError, RuntimeUnavailable
from core.runtimes.tool_names import normalize_grok_tool_call, on_tool_name, render_tool_refs
from core.tool_server.capability import CallerCapability
from core.tool_server.http_server import HOST

BACKEND = "grok"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API_KEY_ENV = "XAI_API_KEY"
AUTH_FIX = ".env의 XAI_API_KEY 확인"
ISOLATED_HOME = os.path.join("data", "runtime", "grok-home")
SYSTEM_PREAMBLE_HEADER = "[시스템 지시]\n"
IMAGE_UNSUPPORTED_NOTICE = "이 백엔드에서는 이미지 입력을 지원하지 않아 텍스트만 분석해요."
ACP_PROTOCOL_VERSION = 1
# 프로젝트 Claude 설정 permissions.allow 중 게이트 대상 규칙 접두.
GATED_ALLOW_PREFIXES = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "mcp__schedule__", "mcp__memory__")

_REQUEST_TIMEOUT = 60.0
_CONTROL_TIMEOUT = 10.0
_TEXT_IDLE_FLUSH = 1.0
_PROMPT_DONE = object()  # session/prompt 응답(또는 오류) 도착
_IDLE = object()  # 청크 정지 — (_IDLE, chunk_seq)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def grok_config_toml(model: str) -> str:
    """격리 HOME `.grok/config.toml` — per-model env_key(XAI_API_KEY) + permission_mode ask. api_key 필드는 두지 않는다.

    # provenance: https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/02-authentication.md verified=false (grok.credentials.isolated_home)
    # provenance: https://docs.x.ai/build/features/permissions verified=false (grok.acp.permission_requests)
    """
    quoted = toml_string(model)
    return f'[model.{quoted}]\nmodel = {quoted}\nenv_key = "{API_KEY_ENV}"\n\n[ui]\npermission_mode = "ask"\n'


def check_project_claude_settings(project_root: str) -> bool:
    """Grok이 함께 읽는 프로젝트 Claude 설정 검사. hooks·.mcp.json = StartupError, 반환 = 게이트 대상 allow 규칙 존재 여부.

    # provenance: https://docs.x.ai/build/features/skills-plugins-marketplaces verified=false (grok.reads_project_claude_files)
    # provenance: https://docs.x.ai/build/features/permissions verified=false (grok.project_claude_permissions)
    """
    gated_allow = False
    for name in ("settings.json", "settings.local.json"):
        path = os.path.join(project_root, ".claude", name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise StartupError(
                f"프로젝트 Claude 설정을 해석할 수 없습니다 ({path}: {type(e).__name__}) — hooks 유무를 확인할 수 없음",
                f"{path}의 JSON을 수정하세요",
            ) from e
        data = _dict(data)
        if data.get("hooks"):
            raise StartupError(
                f"프로젝트 Claude 설정 {path}에 hooks가 있습니다 — Grok이 읽어 서버측 게이트를 우회할 수 있음",
                f"{path}의 hooks 항목 제거",
            )
        allow = _dict(data.get("permissions")).get("allow")
        if isinstance(allow, list) and any(isinstance(rule, str) and rule.startswith(GATED_ALLOW_PREFIXES) for rule in allow):
            gated_allow = True
    mcp_json = os.path.join(project_root, ".mcp.json")
    if os.path.exists(mcp_json):
        raise StartupError(
            f"프로젝트 {mcp_json}이 있습니다 — Grok이 읽어 서버측 capability를 우회하는 MCP 서버를 붙일 수 있음",
            f"{mcp_json} 제거",
        )
    return gated_allow


def classify_grok_error(error) -> LLMError:
    """JSON-RPC 오류 → LLMError. 메시지·data에 429/rate limit → USAGE_LIMIT(resetsAt), 401/unauthorized/invalid api key →
    AUTH_EXPIRED(fix `.env의 XAI_API_KEY 확인`), 그 외 GENERIC.

    # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.error_shape)
    """
    if not isinstance(error, JsonRpcError):
        return LLMError.generic()
    parts = [error.message if isinstance(error.message, str) else json.dumps(error.message, ensure_ascii=False)]
    if error.data is not None:
        parts.append(error.data if isinstance(error.data, str) else json.dumps(error.data, ensure_ascii=False))
    blob = " ".join(parts).lower()
    if re.search(r"\b429\b|rate[ _-]?limit", blob):
        return LLMError.usage_limit(BACKEND, find_resets_at(error.data))
    if re.search(r"\b401\b|unauthorized|invalid[ _-]?api[ _-]?key", blob):
        return LLMError.auth_expired(BACKEND, AUTH_FIX)
    return LLMError.generic()


def system_preamble(system_prompt: str) -> str:
    """세션 첫 프롬프트의 시스템 지시 블록 — system prompt + Grok 도구 부록, 도구 표기 렌더.

    # provenance: https://docs.x.ai/build/cli/reference verified=false (grok.system_prompt_preamble)
    """
    return f"{SYSTEM_PREAMBLE_HEADER}{render_system_prompt(system_prompt, BACKEND)}\n\n"


def _image_block(path: str) -> dict:
    """ACP image content block (base64). # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.image_prompt)"""
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return {"type": "image", "mimeType": mimetypes.guess_type(path)[0] or "image/png", "data": data}


@dataclass(eq=False)
class _Proc:
    client: JsonRpcClient | None = None
    alive: bool = True
    closing: bool = False
    image_supported: bool = False
    turns: dict = field(default_factory=dict)  # ACP session id → 진행 중 _Turn


@dataclass(eq=False)
class _Turn:
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    buffer: list = field(default_factory=list)
    chunk_seq: int = 0
    started_calls: set = field(default_factory=set)
    prompt_sent: bool = False
    stopped: bool = False  # interrupt·max_turns·tripwire·end_session 이후 on_text/on_tool 미발화
    cancel_sent: bool = False
    tripped: bool = False


@dataclass(eq=False)
class _Session:
    proc: _Proc
    session_id: str
    privileged: bool
    prompt_hash: str
    primed: bool = False  # 프롬프트를 한 번이라도 받아들였는지 — 전까지는 시스템 지시·이력 folding을 붙인다
    active: _Turn | None = None


class GrokAdapter(LLMAdapter):
    backend_id = BACKEND

    def __init__(
        self,
        *,
        tool_server,
        server_specs: list | None = None,
        cwd: str | None = None,
        model: str | None = None,
        bin: str = "~/.grok/bin/grok",
        transport_factory: Callable | None = None,
        env: dict | None = None,
    ):
        if tool_server is None:
            raise StartupError(
                "backend=grok에는 공유 도구 서버(SharedToolServer)가 필요합니다",
                "bot.main.build_llm_and_tool_server 배선을 확인하세요",
            )
        self.tool_server = tool_server
        self.server_specs = server_specs if server_specs is not None else []
        self.cwd = cwd
        self.model = model
        self.bin = bin
        # 런타임 주입 seam — (argv, env, cwd) → transport. None이면 실제 프로세스(process.spawn_transport, 테스트 가드 적용).
        self._transport_factory = transport_factory
        self._base_env = env  # None이면 호출 시점의 os.environ
        self._wiring = GrokApprovalWiring()
        self._proc: _Proc | None = None
        self._launch_lock = asyncio.Lock()
        self._sessions: dict = {}  # bot thread_id → _Session
        self._close_supported = True

    # ── 프로세스 조립 ──

    @property
    def project_root(self) -> str:
        return self.cwd or PROJECT_ROOT

    @property
    def home_dir(self) -> str:
        return os.path.join(self.project_root, ISOLATED_HOME)

    def _env(self) -> dict:
        return os.environ if self._base_env is None else self._base_env

    def child_env(self) -> dict:
        """런타임 자식 env — 격리 HOME + PATH·LANG·XAI_API_KEY만 (사용자 HOME·기타 비밀값 미상속)."""
        base = self._env()
        return {
            "HOME": self.home_dir,
            "PATH": base.get("PATH") or os.defpath,
            "LANG": base.get("LANG") or "en_US.UTF-8",
            API_KEY_ENV: base.get(API_KEY_ENV) or "",
        }

    def build_argv(self) -> list[str]:
        """`grok agent stdio` argv. # provenance: https://docs.x.ai/build/cli/reference verified=false"""
        return [os.path.expanduser(self.bin), "agent", "stdio"]

    def _prepare_home(self) -> None:
        """격리 HOME config.toml 재생성·검증. 격리 auth.json 존재 = StartupError(구독/로그인 세션 자격 사용 방지)."""
        grok_dir = os.path.join(self.home_dir, ".grok")
        auth_path = os.path.join(grok_dir, "auth.json")
        if os.path.lexists(auth_path):
            raise StartupError("격리 HOME에 auth.json이 있습니다 — 삭제 필요", f"{auth_path} 삭제")
        os.makedirs(grok_dir, exist_ok=True)
        config_path = os.path.join(grok_dir, "config.toml")
        content = grok_config_toml(self.model)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)
        with open(config_path, encoding="utf-8") as f:
            if f.read() != content:
                raise StartupError(f"격리 HOME config.toml 검증 실패 ({config_path})", f"{config_path} 권한을 확인하세요")

    async def _handshake(self, proc: _Proc) -> None:
        """initialize → mcpCapabilities.http 필수 · promptCapabilities.image 기록."""
        result = await proc.client.request(
            "initialize",
            {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                "clientInfo": {"name": "ohrmin_claw", "title": "ohrmin-claw", "version": "1.0"},
            },
            timeout=_REQUEST_TIMEOUT,
        )
        capabilities = _dict(_dict(result).get("agentCapabilities"))
        # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.acp.mcp_http)
        if _dict(capabilities.get("mcpCapabilities")).get("http") is not True:
            raise StartupError(
                "Grok ACP 에이전트가 HTTP MCP 서버를 지원하지 않습니다 (agentCapabilities.mcpCapabilities.http)",
                "Grok CLI를 최신 버전으로 업데이트하세요",
            )
        # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.image_prompt)
        proc.image_supported = _dict(capabilities.get("promptCapabilities")).get("image") is True

    async def _launch(self) -> _Proc:
        factory = self._transport_factory or process.spawn_transport
        transport = await factory(self.build_argv(), self.child_env(), self.project_root)
        proc = _Proc()
        proc.client = JsonRpcClient(
            transport,
            on_notification=lambda method, params: self._on_notification(proc, method, params),
            on_request=lambda method, params: self._on_server_request(proc, method, params),
            on_close=lambda error: self._on_proc_closed(proc, error),
        )
        proc.client.start()
        try:
            await self._handshake(proc)
        except BaseException:
            await self._close_proc(proc)
            raise
        self._proc = proc
        return proc

    async def _close_proc(self, proc: _Proc) -> None:
        proc.closing = True
        if proc.client is not None:
            await proc.client.close()
        proc.alive = False
        if self._proc is proc:
            self._proc = None

    async def _ensure_proc(self) -> _Proc:
        """살아있는 프로세스 반환. 없거나 죽었으면 이번 호출에서 1회 재기동 (실패 = RUNTIME_UNAVAILABLE)."""
        async with self._launch_lock:
            if self._proc is not None and self._proc.alive:
                return self._proc
            try:
                return await self._launch()
            except RealRuntimeForbidden:
                raise
            except Exception as e:
                print(f"⚠️ Grok ACP 에이전트 재기동 실패: {type(e).__name__}: {e}")
                raise LLMError.runtime_unavailable(BACKEND) from e

    # ── 런타임 이벤트 ──

    def _on_notification(self, proc: _Proc, method: str, params) -> None:
        if method != "session/update" or not isinstance(params, dict):
            return
        turn = proc.turns.get(params.get("sessionId"))
        update = params.get("update")
        if turn is not None and isinstance(update, dict):
            if update.get("sessionUpdate") in ("tool_call", "tool_call_update"):
                # read-loop 순서로 즉시 병합 — 스트림 소비가 밀려도 뒤이은 permission 요청이 병합된 필드로 판정된다.
                self._wiring.observe_tool_call(params.get("sessionId"), update)
            turn.events.put_nowait(update)

    def _on_server_request(self, proc: _Proc, method: str, params):
        """에이전트 → 클라이언트 요청. permission만 처리(진행 중이 아니거나 멈춘 턴 = cancelled), fs·terminal은 미제공."""
        if method == "session/request_permission":
            params = _dict(params)
            turn = proc.turns.get(params.get("sessionId"))
            return self._wiring.permission_response(params, cancelled=turn is None or turn.stopped)
        raise JsonRpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    def _on_proc_closed(self, proc: _Proc, error) -> None:
        """프로세스 종료 — 세션 전부 무효화, 진행 중 턴 실패 신호."""
        proc.alive = False
        if self._proc is proc:
            self._proc = None
        if not proc.closing:
            detail = f": {type(error).__name__}: {error}" if error else ""
            print(f"⚠️ Grok ACP 에이전트 종료 감지{detail} — 세션 무효화, 다음 호출에서 재기동")
        for turn in list(proc.turns.values()):
            turn.events.put_nowait(CLOSED)
        for thread_id, session in list(self._sessions.items()):
            if session.proc is proc:
                del self._sessions[thread_id]
                self._wiring.unregister_session(session.session_id)

    # ── 기동 ──

    async def start(self) -> None:
        """격리 HOME·프로젝트 설정 검사 → 에이전트 기동·핸드셰이크 → permission probe → 기동 요약 + 미검증 표면 WARN."""
        if not str(self._env().get(API_KEY_ENV) or "").strip():
            raise StartupError("XAI_API_KEY가 설정되지 않았습니다", ".env에 XAI_API_KEY=... 추가")
        project_claude_permissions = check_project_claude_settings(self.project_root)
        self._prepare_home()
        try:
            async with self._launch_lock:
                if self._proc is None or not self._proc.alive:
                    await self._launch()
            await self._wiring.probe()
        except (RealRuntimeForbidden, StartupError):
            await self._stop_proc()
            raise
        except Exception as e:
            await self._stop_proc()
            raise StartupError(
                f"Grok ACP 에이전트 기동 실패: {type(e).__name__}: {e}",
                "grok 설치와 .env의 XAI_API_KEY를 확인하세요",
            ) from e
        tools = sum(len(spec.tools) for spec in self.server_specs)
        emit(format_startup_summary(BACKEND, self.model or "default", "registry", tools, f"{HOST}:{self.tool_server.port}"))
        emit_unverified_warnings(BACKEND, "registry", project_claude_permissions=project_claude_permissions)

    async def _stop_proc(self) -> None:
        if self._proc is not None:
            await self._close_proc(self._proc)

    # ── 세션 ──

    async def _session_for(self, thread_id, proc: _Proc, system_prompt: str, privileged: bool) -> _Session:
        """스레드 세션 반환(없으면 session/new). 같은 thread_id가 다른 프로세스·권한·프롬프트로 오면 기존 매핑 종료 후 재생성."""
        digest = prompt_hash(system_prompt)
        if thread_id is not None:
            session = self._sessions.get(thread_id)
            if session is not None and (
                session.proc is not proc or session.privileged != privileged or session.prompt_hash != digest
            ):
                await self.end_session(thread_id)
                session = None
            if session is not None:
                return session
        capability = CallerCapability.PRIVILEGED if privileged else CallerCapability.READ_ONLY
        params = {
            "cwd": self.project_root,
            # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.acp.mcp_http)
            "mcpServers": [
                {"type": "http", "name": spec.name, "url": self.tool_server.endpoint(spec.name, capability), "headers": []}
                for spec in self.server_specs
            ],
        }
        try:
            result = await proc.client.request("session/new", params, timeout=_REQUEST_TIMEOUT)
            session_id = result["sessionId"]
        except RuntimeUnavailable:
            raise
        except JsonRpcError as e:
            error = classify_grok_error(e)
            if error.kind is not LLMErrorKind.GENERIC:
                raise error from e
            raise SessionStartFailed(f"{type(e).__name__}: {e}") from e
        except Exception as e:
            raise SessionStartFailed(f"{type(e).__name__}: {e}") from e
        session = _Session(proc=proc, session_id=session_id, privileged=privileged, prompt_hash=digest)
        self._wiring.register_session(session_id, privileged)
        if thread_id is not None:
            self._sessions[thread_id] = session
        return session

    async def _send_cancel(self, session: _Session, turn: _Turn) -> None:
        """session/cancel (턴당 1회, 프롬프트 송신 후에만)."""
        if turn.cancel_sent or not turn.prompt_sent or not session.proc.alive:
            return
        turn.cancel_sent = True
        try:
            # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.acp.cancel_reprompt)
            await session.proc.client.notify("session/cancel", {"sessionId": session.session_id})
        except Exception as e:
            print(f"⚠️ Grok session/cancel 실패: {type(e).__name__}: {e}")

    async def _prompt_blocks(self, session: _Session, system_prompt: str, message: str, image_paths, on_text) -> list:
        blocks = []
        if not session.primed:
            blocks.append({"type": "text", "text": system_preamble(system_prompt)})
        blocks.append({"type": "text", "text": render_tool_refs(message, BACKEND)})
        if image_paths:
            if session.proc.image_supported:
                blocks += [_image_block(path) for path in image_paths]
            elif on_text:
                await on_text(IMAGE_UNSUPPORTED_NOTICE)
        return blocks

    async def _stream(self, session: _Session, blocks, on_text, on_tool, counter, max_turns, stats) -> list[str]:
        proc = session.proc
        turn = _Turn()
        proc.turns[session.session_id] = turn
        session.active = turn
        texts: list[str] = []
        loop = asyncio.get_running_loop()
        idle = None

        async def flush():
            nonlocal idle
            if idle is not None:
                idle.cancel()
                idle = None
            text = "".join(turn.buffer)
            turn.buffer.clear()
            if text and not turn.stopped:
                texts.append(text)
                if on_text:
                    await on_text(text)

        prompt = loop.create_task(
            proc.client.request("session/prompt", {"sessionId": session.session_id, "prompt": blocks})
        )
        prompt.add_done_callback(lambda _: turn.events.put_nowait(_PROMPT_DONE))
        try:
            await asyncio.sleep(0)  # 프롬프트 송신 — 그 뒤의 session/cancel만 의미가 있다
            turn.prompt_sent = True
            if turn.stopped:
                await self._send_cancel(session, turn)
            while True:
                event = await turn.events.get()
                if event is DETACHED:
                    return texts
                if event is CLOSED:
                    await flush()
                    raise RuntimeUnavailable("grok agent exited during turn")
                if event is _PROMPT_DONE:
                    error = prompt.exception()
                    if error is None:
                        session.primed = True
                    if turn.tripped:
                        raise LLMError.generic()
                    await flush()
                    if isinstance(error, JsonRpcError):
                        raise classify_grok_error(error)
                    if error is not None:
                        raise error
                    return texts
                if isinstance(event, tuple):
                    if event[1] == turn.chunk_seq:
                        await flush()
                    continue
                kind = event.get("sessionUpdate")
                if kind == "agent_message_chunk":
                    content = _dict(event.get("content"))
                    if content.get("type") == "text" and isinstance(content.get("text"), str) and not turn.stopped:
                        turn.buffer.append(content["text"])
                        turn.chunk_seq += 1
                        if idle is not None:
                            idle.cancel()
                        idle = loop.call_later(_TEXT_IDLE_FLUSH, turn.events.put_nowait, (_IDLE, turn.chunk_seq))
                elif kind in ("tool_call", "tool_call_update"):
                    # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.tool_call_shape)
                    call_id = event.get("toolCallId")
                    call = self._wiring.merged_tool_call(session.session_id, call_id)
                    if kind == "tool_call" and call_id not in turn.started_calls:
                        turn.started_calls.add(call_id)
                        await flush()
                        if not turn.stopped:
                            name = on_tool_name(normalize_grok_tool_call(call).name, skills_registry=True)
                            if on_tool:
                                await on_tool(name)
                            if counter is not None:
                                counter[0] += 1
                            stats["tools"] += 1
                            if name == "Skill":
                                stats["skills_loaded"] += 1
                            if max_turns is not None and stats["tools"] >= max_turns:
                                turn.stopped = True
                                await self._send_cancel(session, turn)
                    if not turn.tripped and self._wiring.tripwire(session.session_id, event):
                        turn.tripped = turn.stopped = True
                        turn.buffer.clear()
                        await self._send_cancel(session, turn)
        finally:
            if idle is not None:
                idle.cancel()
            if not prompt.done():
                prompt.cancel()
            elif not prompt.cancelled():
                prompt.exception()  # 미회수 예외 경고 방지
            if proc.turns.get(session.session_id) is turn:
                del proc.turns[session.session_id]
            if session.active is turn:
                session.active = None
            self._wiring.forget_tool_calls(session.session_id)

    async def _turn(
        self,
        thread_id,
        system_prompt: str,
        build_message: Callable[[bool], str],
        *,
        privileged: bool,
        image_paths,
        on_text,
        on_tool,
        counter,
        max_turns,
        raise_errors: bool,
    ) -> str:
        cap = "priv" if privileged else "ro"
        stats = {"tools": 0, "skills_loaded": 0}
        session = None
        try:
            proc = await self._ensure_proc()
            session = await self._session_for(thread_id, proc, system_prompt, privileged)
            blocks = await self._prompt_blocks(
                session, system_prompt, build_message(not session.primed), image_paths, on_text
            )
            texts = await self._stream(session, blocks, on_text, on_tool, counter, max_turns, stats)
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
        finally:
            if thread_id is None and session is not None:
                self._wiring.unregister_session(session.session_id)
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
        """유틸 호출 — one-shot 세션, 실패 시 LLMError raise. allowed_tools는 Grok에 적용하지 않는다(스티어링 전용)."""
        return await self._turn(
            None, system_prompt, lambda fresh: user_message,
            privileged=approve_skill_writes is True, image_paths=None,
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
        """스트리밍 턴 — 프롬프트를 아직 받아들이지 않은 세션(one-shot 포함)에만 이력 folding. 실패 = 오류 메시지 on_text 1회 + 반환."""
        return await self._turn(
            thread_id, system_prompt,
            lambda fresh: self._augment_message(user_message, context, history if fresh else None),
            privileged=approve_skill_writes is True, image_paths=image_paths,
            on_text=on_text, on_tool=on_tool, counter=counter, max_turns=max_turns, raise_errors=False,
        )

    async def interrupt_session(self, thread_id) -> None:
        session = self._sessions.get(thread_id)
        turn = session.active if session is not None else None
        if turn is None:
            return
        turn.stopped = True
        await self._send_cancel(session, turn)

    async def end_session(self, thread_id) -> None:
        """스레드 세션 종료(멱등) — 진행 중 스트림 분리(+미전송이면 session/cancel) + session/close."""
        session = self._sessions.pop(thread_id, None)
        if session is None:
            return
        self._wiring.unregister_session(session.session_id)
        turn = session.active
        if turn is not None:
            turn.stopped = True
            await self._send_cancel(session, turn)
            turn.events.put_nowait(DETACHED)
        if not session.proc.alive or not self._close_supported:
            return
        try:
            # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.acp.session_close —
            # 미지원 에이전트면 -32601 이후 시도하지 않음, 세션은 봇 쪽 매핑만 제거)
            await session.proc.client.request(
                "session/close", {"sessionId": session.session_id}, timeout=_CONTROL_TIMEOUT
            )
        except JsonRpcError as e:
            if e.code == METHOD_NOT_FOUND:
                self._close_supported = False
                print("⚠️ Grok 에이전트가 session/close를 지원하지 않습니다 — 이후 세션 종료는 봇 매핑만 제거")
            else:
                print(f"⚠️ Grok 세션 정리 실패(thread={thread_id}): {e}")
        except Exception as e:
            print(f"⚠️ Grok 세션 정리 실패(thread={thread_id}): {type(e).__name__}: {e}")

    async def close_all(self) -> None:
        """모든 스레드 세션 종료 후 에이전트 프로세스 종료 (봇 종료 경로, 멱등)."""
        for thread_id in list(self._sessions):
            await self.end_session(thread_id)
        await self._stop_proc()

    def session_ids(self) -> list:
        return list(self._sessions)

    def has_session(self, thread_id) -> bool:
        return thread_id in self._sessions
