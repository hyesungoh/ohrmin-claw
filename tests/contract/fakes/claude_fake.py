"""Claude 런타임 fake — ClaudeSDKAdapter의 query_fn / client_factory seam에 주입 (실제 CLI 미실행).

Claude CLI의 도구 루프를 계약 관측에 필요한 만큼만 모사한다:
- 스트림: text → AssistantMessage(TextBlock), tool → AssistantMessage(ToolUseBlock) 후 PreToolUse 훅 →
  CLI `.claude` 가드 → 실행 → UserMessage(ToolResultBlock).
- 훅 선택: options.hooks["PreToolUse"]의 HookMatcher 중 매처 정규식이 도구명에 전체 일치하는 것만 발화
  (matcher None = 전체). 콜백 반환의 permissionDecision == "deny"면 미실행.
- CLI 가드(커밋 668e46e 실측): Write/Edit/MultiEdit/NotebookEdit의 **리터럴 경로**에 `.claude` 세그먼트가 있으면
  훅이 allow여도 CLI가 거부한다. 셸 문자열(Bash 리다이렉션) 가드는 모사하지 않는다(§1.2 범위 제외).
- MCP 실행: options.mcp_servers의 SDK 인프로세스 서버 request_handlers[CallToolRequest] (SDK와 같은 경로,
  입력 검증·CallerCapability 래퍼 포함). 빌트인은 파일시스템을 건드리지 않고 실행된 것으로 기록한다.
- max_turns: 도구 호출 시작 수가 options.max_turns에 이르면 그 도구 실행 후 턴 종료(ResultMessage error_max_turns).
- interrupt: 남은 스텝을 버리고 스트림 종료. disconnect(세션 종료)도 대기 중 스트림을 조용히 끝낸다.
- crash: 스트림 중 ProcessError, 해당 클라이언트는 이후 사용 불가.
"""
import asyncio
import json
import os
import re
from collections import deque
from pathlib import PurePosixPath

from claude_agent_sdk import CLIConnectionError, ProcessError
from claude_agent_sdk.types import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

from core.llm_config import load_llm_config
from tests.contract.harness import BackendHarness, Execution, ToolResult
from tests.contract.scenario import RAW_PROVIDER_MARKER, split_turns

MODEL = "claude-contract"
_STRUCTURED_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _cli_guard_blocks(name: str, tool_input: dict) -> bool:
    """Claude CLI `.claude` 구조화 쓰기 가드 — 리터럴 경로 문자열의 세그먼트로 판정(realpath 아님)."""
    if name not in _STRUCTURED_WRITE_TOOLS:
        return False
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return ".claude" in PurePosixPath(path).parts


def _split_mcp_name(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    server, sep, tool_name = name[len("mcp__"):].partition("__")
    return (server, tool_name) if sep else None


def _result(subtype: str, num_turns: int) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=0,
        duration_api_ms=0,
        is_error=subtype != "success",
        num_turns=num_turns,
        session_id="fake-session",
    )


async def _call_sdk_server(server_config: dict, tool_name: str, args: dict) -> ToolResult:
    handler = server_config["instance"].request_handlers[CallToolRequest]
    request = CallToolRequest(method="tools/call", params=CallToolRequestParams(name=tool_name, arguments=args))
    result = (await handler(request)).root
    text = "".join(block.text for block in result.content if getattr(block, "type", None) == "text")
    return ToolResult(text=text, is_error=bool(result.isError))


class FakeClaudeClient:
    """ClaudeSDKClient 대역 — 스레드 세션 1개(= CLI 서브프로세스 1개)."""

    def __init__(self, fake: "ClaudeFake", options=None):
        self._fake = fake
        self.options = options
        self._turn: list = []
        self._wake = asyncio.Event()
        self.stopped = False  # interrupt 또는 disconnect → 현재 스트림 종료
        self.closed = False
        self.dead = False

    async def connect(self, prompt=None) -> None:
        self._fake._start_session()

    async def query(self, prompt, session_id="default") -> None:
        if self.dead or self.closed:
            raise CLIConnectionError(f"ProcessTransport is not ready for writing ({RAW_PROVIDER_MARKER})")
        self.stopped = False
        self._wake = asyncio.Event()
        self._turn = self._fake._begin_turn(prompt, self.options)

    async def receive_response(self):
        async for message in self._fake._run_turn(self._turn, self.options, client=self):
            yield message

    async def interrupt(self) -> None:
        self._fake.interrupts += 1
        if not self._fake.ignore_interrupt:
            self.stopped = True
            self._wake.set()

    async def disconnect(self) -> None:
        self._fake.session_closes += 1
        self.closed = True
        self.stopped = True
        self._wake.set()


class ClaudeFake:
    """FakeRuntime 구현 (tests/contract/harness.py 참고)."""

    def __init__(self):
        self._turns: deque = deque()
        self.fail_starts = 0
        self.ignore_interrupt = False
        self.prompts: list = []
        self.system_prompts: list = []
        self.allowed_tools: list = []
        self.options_log: list = []
        self.executions: list = []
        self.session_starts = 0
        self.session_closes = 0
        self.interrupts = 0
        self.unscripted_turns = 0

    # ── 스크립트 ──
    def script(self, *steps) -> None:
        self._turns.extend(split_turns(steps))

    @property
    def pending_turns(self) -> int:
        return len(self._turns)

    # ── SDK seam ──
    async def query(self, *, prompt, options=None, transport=None):
        """claude_agent_sdk.query 대역 — one-shot 호출마다 CLI 프로세스 1개."""
        self._start_session()
        turn = self._begin_turn(prompt, options)
        async for message in self._run_turn(turn, options, client=None):
            yield message

    def client(self, options=None, transport=None) -> FakeClaudeClient:
        """ClaudeSDKClient 대역 팩토리."""
        return FakeClaudeClient(self, options)

    # ── 내부 ──
    def _start_session(self) -> None:
        self.session_starts += 1
        if self.fail_starts > 0:
            self.fail_starts -= 1
            raise CLIConnectionError(f"Failed to start Claude Code ({RAW_PROVIDER_MARKER})")

    def _begin_turn(self, prompt, options) -> list:
        self.prompts.append(prompt)
        self.system_prompts.append(options.system_prompt)
        self.allowed_tools.append(options.allowed_tools)
        self.options_log.append(options)
        if self._turns:
            return self._turns.popleft()
        self.unscripted_turns += 1
        return []

    async def _run_turn(self, turn, options, client):
        tool_starts = 0
        for step in turn:
            if client is not None and client.stopped:
                return
            if step.kind == "text":
                yield AssistantMessage(content=[TextBlock(text=step.text)], model=MODEL)
            elif step.kind in ("tool", "gate_probe"):
                tool_starts += 1
                tool_use_id = f"toolu_{len(self.executions)}"
                yield AssistantMessage(
                    content=[ToolUseBlock(id=tool_use_id, name=step.name, input=step.input)], model=MODEL
                )
                execution = await self._execute(step.name, step.input, options, tool_use_id)
                self.executions.append(execution)
                yield UserMessage(content=[ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=execution.result_text,
                    is_error=execution.is_error or not execution.executed,
                )])
                if options.max_turns is not None and tool_starts >= options.max_turns:
                    yield _result("error_max_turns", tool_starts)
                    return
            elif step.kind == "error":
                yield self._provider_error(step)
            elif step.kind == "wait_interrupt":
                if client is None:
                    raise AssertionError("wait_interrupt는 스레드 세션(client) 턴에서만 쓸 수 있다")
                await client._wake.wait()
            elif step.kind == "crash":
                if client is not None:
                    client.dead = True
                raise ProcessError("Command failed with exit code 1", exit_code=1, stderr=RAW_PROVIDER_MARKER)
            else:
                raise AssertionError(f"unsupported step: {step.kind}")
        if client is not None and client.stopped:
            return
        yield _result("success", tool_starts)

    def _provider_error(self, step):
        """검증된 SDK 신호 형태(F17): AssistantMessage.error / RateLimitEvent rejected. generic = 스트림 예외."""
        if step.error_kind == "usage_limit":
            if step.resets_at is not None:
                return RateLimitEvent(
                    rate_limit_info=RateLimitInfo(
                        status="rejected", resets_at=step.resets_at, raw={"message": RAW_PROVIDER_MARKER}
                    ),
                    uuid="rate-limit-event",
                    session_id="fake-session",
                )
            return AssistantMessage(
                content=[TextBlock(text=f"Claude AI usage limit reached|{RAW_PROVIDER_MARKER}")],
                model=MODEL,
                error="rate_limit",
            )
        if step.error_kind == "auth_expired":
            return AssistantMessage(
                content=[TextBlock(text=f"Invalid API key · Please run /login {RAW_PROVIDER_MARKER}")],
                model=MODEL,
                error="authentication_failed",
            )
        raise RuntimeError(f"API Error: 500 {RAW_PROVIDER_MARKER}")

    async def _execute(self, name: str, tool_input: dict, options, tool_use_id: str) -> Execution:
        denied, reason = await self._run_pre_tool_use_hooks(name, tool_input, options, tool_use_id)
        if denied:
            return Execution(name, tool_input, executed=False, blocked_by="hook", result_text=reason, is_error=True)
        if _cli_guard_blocks(name, tool_input):
            return Execution(
                name, tool_input, executed=False, blocked_by="runtime_guard",
                result_text="Claude requested permissions to write, but you haven't granted it yet.", is_error=True,
            )
        mcp = _split_mcp_name(name)
        if mcp is None:
            return Execution(name, tool_input, executed=True, result_text="ok")
        server, tool_name = mcp
        server_config = (options.mcp_servers or {}).get(server)
        if server_config is None:
            return Execution(
                name, tool_input, executed=False, blocked_by="no_such_tool",
                result_text=f"No such tool available: {name}", is_error=True,
            )
        result = await _call_sdk_server(server_config, tool_name, tool_input)
        return Execution(name, tool_input, executed=True, result_text=result.text, is_error=result.is_error)

    async def _run_pre_tool_use_hooks(self, name, tool_input, options, tool_use_id):
        hook_input = {
            "session_id": "fake-session",
            "transcript_path": "",
            "cwd": options.cwd,
            "hook_event_name": "PreToolUse",
            "tool_name": name,
            "tool_input": tool_input,
        }
        for matcher in (options.hooks or {}).get("PreToolUse", []):
            if matcher.matcher is not None and not re.fullmatch(matcher.matcher, name):
                continue
            for callback in matcher.hooks:
                output = await callback(hook_input, tool_use_id, {"signal": None})
                specific = (output or {}).get("hookSpecificOutput") or {}
                if specific.get("permissionDecision") == "deny":
                    return True, specific.get("permissionDecisionReason")
        return False, None

    # ── 도구 transport 관측 ──
    def _latest_servers(self) -> dict:
        if not self.options_log:
            raise AssertionError("런타임이 아직 턴을 받지 않았다")
        return self.options_log[-1].mcp_servers or {}

    async def list_exposed_tools(self) -> dict:
        exposed = {}
        for server, config in self._latest_servers().items():
            handler = config["instance"].request_handlers[ListToolsRequest]
            tools = (await handler(ListToolsRequest(method="tools/list"))).root.tools
            exposed[server] = {t.name: (t.description, t.inputSchema) for t in tools}
        return exposed

    async def call_tool_endpoint(self, canonical: str, args: dict) -> ToolResult:
        server, tool_name = _split_mcp_name(canonical)
        return await _call_sdk_server(self._latest_servers()[server], tool_name, args)


def make_harness(name: str, env, **options) -> BackendHarness:
    """claude_native | claude_registry — 봇 배선(bot.main.build_llm_and_tool_server) 그대로 + fake seam 주입."""
    if options:
        raise TypeError(f"claude harness options unsupported: {sorted(options)}")
    import bot.main as main

    skills_mode = "registry" if name == "claude_registry" else "native"
    config_path = os.path.join(env.root, "config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump({"llm": {"backend": "claude", "claude": {"model": MODEL, "skills": skills_mode}}}, f)
    config = load_llm_config(config_path, {})
    specs = env.specs(registry=skills_mode == "registry", backend_id=config.backend)
    adapter, tool_server = main.build_llm_and_tool_server(config, specs)
    fake = ClaudeFake()
    adapter._query_fn = fake.query
    adapter._client_factory = fake.client
    # 봇과 같은 늦은 주입 — add_memory 오버플로우 통합기가 이 어댑터의 ask를 쓴다(F6).
    env.memory_mgr.llm = adapter

    def check_native_skills_suppressed() -> None:
        options_seen = fake.options_log[-1]
        assert options_seen.skills == []
        assert options_seen.setting_sources == ["project"]
        assert "Skill" not in options_seen.allowed_tools
        assert "mcp__skills" in options_seen.allowed_tools

    return BackendHarness(
        name=name,
        backend_id=config.backend,
        skills_mode=skills_mode,
        config=config,
        specs=specs,
        adapter=adapter,
        fake=fake,
        env=env,
        gate_via="hook",
        auth_fix="claude login",
        check_native_skills_suppressed=check_native_skills_suppressed,
        tool_server=tool_server,
    )
