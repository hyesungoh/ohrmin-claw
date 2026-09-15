"""Codex/Grok transport — ServerSpec을 봇 이벤트 루프 안의 streamable HTTP MCP 서버로 노출.

- 경로 `/t/{token}/mcp/{server}`: per-boot 토큰 2개(priv/ro)가 CallerCapability를 결정한다.
  미등록 토큰·서버는 404. 서버 × capability마다 lowlevel MCP Server + StreamableHTTPSessionManager.
- 경로 `POST /t/{token}/gate`: Codex 훅 스크립트(core/hooks/codex_gate_hook.py)의 판정 요청.
  토큰 → capability → decide(..., runtime_guard=True) → `{"allow", "reason"}` + `[gate] ... via=hook` 로그.
  payload가 Unknown으로 정규화되면 priv·ro 모두 deny.
- 127.0.0.1 임시 포트, Host 헤더 `127.0.0.1:*`만 허용(DNS rebinding 보호).
- uvicorn 시그널 캡처 비활성(시그널은 discord.py 봇 프로세스가 소유). codex/grok 백엔드일 때만 기동한다.
- 한 인스턴스는 한 번만 기동할 수 있다(SessionManager.run()은 1회용).
"""
import asyncio
import contextlib
import json
import secrets
import socket

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from core.gate_wiring import PROBE_MARKER
from core.llm_errors import StartupError
from core.observability import emit, format_gate_decision
from core.runtimes.tool_names import normalize_codex_hook_payload
from core.safety_gate import UNKNOWN_TOOL, decide
from core.tool_server.capability import CallerCapability, wrap_handler
from core.tool_server.schema import to_strict_json_schema, validate_server_spec
from core.tool_server.spec import ServerSpec

HOST = "127.0.0.1"
START_TIMEOUT = 10.0
_STOP_TIMEOUT = 10.0
_GRACEFUL_SHUTDOWN_SECONDS = 2
_SECURITY_SETTINGS = TransportSecuritySettings(allowed_hosts=["127.0.0.1:*"])
# Codex 훅 payload가 Unknown(도구명 없음·형태 불일치)으로 정규화되면 권한과 무관하게 차단한다(fail-closed).
UNKNOWN_HOOK_TOOL_REASON = "확인할 수 없는 도구는 차단합니다."


class _NoSignalServer(uvicorn.Server):
    """uvicorn Server — 시그널 핸들러를 설치하지 않는다(N8)."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def _to_call_tool_result(result: dict) -> CallToolResult:
    """도구 핸들러 결과 dict → MCP CallToolResult (SDK 인프로세스 서버와 같은 변환)."""
    content = []
    for item in result.get("content", []):
        if item.get("type") == "text":
            content.append(TextContent(type="text", text=item["text"]))
        elif item.get("type") == "image":
            content.append(ImageContent(type="image", data=item["data"], mimeType=item["mimeType"]))
    return CallToolResult(content=content, isError=result.get("is_error", False))


def _build_mcp_server(spec: ServerSpec, capability: CallerCapability, backend: str) -> Server:
    server = Server(spec.name)
    tools = [
        Tool(name=t.name, description=t.description, inputSchema=to_strict_json_schema(t.params))
        for t in spec.tools
    ]
    handlers = {t.name: wrap_handler(spec.name, t, capability, backend=backend) for t in spec.tools}

    @server.list_tools()
    async def list_tools():
        return tools

    # strict schema는 모든 키를 required로 두므로 SDK 검증을 끈다 — Codex/Grok이 선택 키를 생략해도 호출되게 하고,
    # 필수(non-nullable) 키 누락·null은 wrap_handler가 같은 "Input validation error" 결과로 핸들러 호출 전에 거부한다.
    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        handler = handlers.get(name)
        if handler is None:
            raise ValueError(f"Tool '{name}' not found")
        return _to_call_tool_result(await handler(arguments))

    return server


class _TokenRoutedMcpApp:
    """ASGI 앱 — 경로 토큰·서버명으로 세션 매니저를 고른다. 미등록이면 404."""

    def __init__(self, managers: dict):
        self._managers = managers

    async def __call__(self, scope, receive, send):
        params = scope.get("path_params", {})
        manager = self._managers.get((params.get("token"), params.get("server")))
        if manager is None:
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)


class _GateEndpoint:
    """`POST /t/{token}/gate` — 훅 payload를 정규화해 decide()로 판정. 미등록 토큰 404, 잘못된 JSON 400(deny)."""

    def __init__(self, capabilities: dict, backend: str):
        self._capabilities = capabilities  # token → CallerCapability
        self._backend = backend

    async def handle(self, request):
        capability = self._capabilities.get(request.path_params.get("token"))
        if capability is None:
            return PlainTextResponse("Not Found", status_code=404)
        try:
            payload = json.loads(await request.body())
        except ValueError:
            return JSONResponse({"allow": False, "reason": "훅 요청 JSON을 해석할 수 없어 차단합니다."}, status_code=400)
        call = normalize_codex_hook_payload(payload)
        privileged = capability is CallerCapability.PRIVILEGED
        if call.name == UNKNOWN_TOOL:
            allow, reason = False, UNKNOWN_HOOK_TOOL_REASON
        else:
            allow, reason = decide(call, privileged, runtime_guard=True)
        # 기동 probe(CodexHookWiring) 요청은 via=probe로 기록 — 운영 via=hook 집계를 오염시키지 않는다(판정 불변).
        via = "probe" if isinstance(payload, dict) and payload.get(PROBE_MARKER) is True else "hook"
        emit(format_gate_decision(self._backend, capability.value, call.name, allow, via, reason))
        return JSONResponse({"allow": allow, "reason": reason})


class SharedToolServer:
    def __init__(self, specs: list[ServerSpec], backend: str):
        for spec in specs:
            validate_server_spec(spec)
        self.backend = backend
        self.port: int | None = None
        self._tokens = {cap: secrets.token_urlsafe(24) for cap in CallerCapability}
        # (token, server name) → StreamableHTTPSessionManager
        self._managers = {
            (token, spec.name): StreamableHTTPSessionManager(
                app=_build_mcp_server(spec, cap, backend),
                security_settings=_SECURITY_SETTINGS,
            )
            for cap, token in self._tokens.items()
            for spec in specs
        }
        self._app = Starlette(
            routes=[
                Route("/t/{token}/mcp/{server}", endpoint=_TokenRoutedMcpApp(self._managers)),
                Route(
                    "/t/{token}/gate",
                    endpoint=_GateEndpoint({token: cap for cap, token in self._tokens.items()}, backend).handle,
                    methods=["POST"],
                ),
            ],
            lifespan=self._lifespan,
        )
        self._server: _NoSignalServer | None = None
        self._task: asyncio.Task | None = None

    @contextlib.asynccontextmanager
    async def _lifespan(self, app):
        async with contextlib.AsyncExitStack() as stack:
            for manager in self._managers.values():
                await stack.enter_async_context(manager.run())
            yield

    async def start(self, timeout: float = START_TIMEOUT) -> None:
        """127.0.0.1:0에 바인드 후 기동 완료까지 대기. 실패·타임아웃 = StartupError."""
        if self._server is not None:
            raise RuntimeError("SharedToolServer는 한 번만 기동할 수 있습니다")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, 0))
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(
            self._app,
            lifespan="on",
            log_config=None,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_SECONDS,
        )
        self._server = _NoSignalServer(config)
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self._server.started:
            if self._task.done():
                error = None if self._task.cancelled() else self._task.exception()
                self._task = None
                sock.close()
                detail = f"{type(error).__name__}: {error}" if error else "lifespan startup failed"
                raise StartupError(f"공유 도구 서버 기동 실패: {detail}", "봇 로그를 확인하세요")
            if loop.time() >= deadline:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._task = None
                sock.close()
                raise StartupError(
                    f"공유 도구 서버가 {timeout:g}s 안에 기동하지 않았습니다 ({HOST}:{self.port})",
                    "봇 로그를 확인하세요",
                )
            await asyncio.sleep(0.01)

    def endpoint(self, server: str, capability: CallerCapability) -> str:
        """런타임에 넘길 MCP URL — capability별 토큰 경로."""
        if self.port is None:
            raise RuntimeError("SharedToolServer가 기동되지 않았습니다")
        token = self._tokens[capability]
        if (token, server) not in self._managers:
            raise KeyError(f"unknown MCP server: {server}")
        return f"http://{HOST}:{self.port}/t/{token}/mcp/{server}"

    def gate_url(self, capability: CallerCapability) -> str:
        """훅 스크립트에 넘길 게이트 URL(OHRMIN_GATE_URL) — capability별 토큰 경로."""
        if self.port is None:
            raise RuntimeError("SharedToolServer가 기동되지 않았습니다")
        return f"http://{HOST}:{self.port}/t/{self._tokens[capability]}/gate"

    async def stop(self) -> None:
        """종료 (멱등). graceful 종료가 늦으면 태스크를 취소한다."""
        if self._task is None:
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._task, timeout=_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"⚠️ 공유 도구 서버 종료 타임아웃({_STOP_TIMEOUT:g}s) — 강제 취소")
        except Exception as e:
            print(f"⚠️ 공유 도구 서버 종료 오류: {type(e).__name__}: {e}")
        finally:
            self._task = None
