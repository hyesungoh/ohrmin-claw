"""LLM 어댑터 레이어 — Claude Agent SDK 기반."""
import json
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable

from claude_agent_sdk import query, ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
from claude_agent_sdk.types import AssistantMessage, RateLimitEvent, TextBlock, ToolUseBlock

from core.llm_errors import (
    GENERIC_MESSAGE,
    LLMError,
    RealRuntimeForbidden,
    StartupError,
    forbid_real_runtime,
)
from core.gate_wiring import ClaudeHookWiring
from core.observability import (
    emit,
    emit_unverified_warnings,
    format_startup_summary,
    format_turn_result,
)
from core.runtimes.tool_names import on_tool_name, translate_allowed_tools
# 게이트 규칙·상수는 core.safety_gate로 이동 — 기존 import 경로(core.llm) 호환용 re-export.
from core.safety_gate import (  # noqa: F401
    _MUTATION_MCP_TOOLS,
    _MUTATION_TOOL_MATCHER,
    _UNATTENDED_DENIED_TOOLS,
    _UNATTENDED_TOOL_MATCHER,
    _WRITE_TOOLS,
    _contains_subseq,
    _skill_path_segments,
    evaluate_skill_write_gate,
    evaluate_tool_gate,
)

# SDK 원본 객체 — 테스트 가드(OHRMIN_FORBID_REAL_RUNTIMES)는 patch되지 않은 원본일 때만 발동한다.
_SDK_QUERY = query
_SDK_CLIENT = ClaudeSDKClient


# cwd(스킬/빌트인 도구) 활성화 시 기본 노출 도구셋. 초기자별로 축소 도구셋을
# 넘기고 싶으면 _call_claude(allowed_tools=...)로 재정의한다 (무인 초기자 = skill-write 제외 등).
# WebSearch/WebFetch는 읽기 전용이라 매트릭스상 전 초기자(무인 포함)에 허용된다.
DEFAULT_ALLOWED_TOOLS = ["Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "Skill", "WebSearch", "WebFetch"]

# 생성 실패 시 원시 트레이스 대신 전달하는 한국어 폴백 (= LLMErrorKind.GENERIC 메시지).
_CLAUDE_FALLBACK_MESSAGE = GENERIC_MESSAGE

# Claude 사용량 소진 신호 (SDK AssistantMessage.error 값).
_CLAUDE_USAGE_LIMIT_ERRORS = {"rate_limit", "billing_error"}

# Claude 게이트 wiring(인프로세스 PreToolUse 훅) — 판정은 core.safety_gate.decide().
_CLAUDE_GATE_WIRING = ClaudeHookWiring()


def _make_unattended_gate_hook(approve_skill_writes: bool) -> Callable:
    """통합 무인-권한 게이트 PreToolUse 훅 콜백 (ClaudeHookWiring → decide(..., runtime_guard=False)).

    approve_skill_writes is True = 인터랙티브 특권 턴(skill-write + schedule/memory mutation 허용).
    """
    return _CLAUDE_GATE_WIRING.make_hook(approve_skill_writes)


def classify_claude_message(msg) -> LLMError | None:
    """SDK 스트림 메시지의 사용량/인증 신호를 LLMError로 분류 (해당 없으면 None).

    - AssistantMessage.error ∈ {rate_limit, billing_error} → USAGE_LIMIT
    - AssistantMessage.error == authentication_failed → AUTH_EXPIRED (fix `claude login`)
    - RateLimitEvent.status == rejected → USAGE_LIMIT(resets_at). 단 overage(추가 사용량)가
      허용 상태면 요청이 계속 처리되므로 오류로 보지 않는다. allowed_warning은 스킵.
    """
    if isinstance(msg, AssistantMessage):
        if msg.error in _CLAUDE_USAGE_LIMIT_ERRORS:
            return LLMError.usage_limit("claude")
        if msg.error == "authentication_failed":
            return LLMError.auth_expired("claude", "claude login")
    elif isinstance(msg, RateLimitEvent):
        info = msg.rate_limit_info
        if info.status == "rejected" and info.overage_status not in ("allowed", "allowed_warning"):
            return LLMError.usage_limit("claude", info.resets_at)
    return None


class LLMAdapter(ABC):
    """LLM 백엔드 공통 계약 — bot/main.py·core가 쓰는 어댑터 표면 전체.

    - ask: 유틸 호출(압축·메모리 추출/통합). 실패 시 LLMError raise, on_text 미발화.
    - ask_with_context: 스트리밍 턴. 실패 시 예외 없이 오류 메시지를 on_text 1회 + 반환.
    - on_text = 도구 호출 사이의 완결 텍스트 세그먼트, on_tool = 정규 도구명(도구 호출 시작당 1회,
      counter 동일 증가).
    - thread_id 지정 = 스레드 세션 재사용(신규/재생성 턴에만 이력 folding), system prompt 변경 = 세션 재생성.
    - approve_skill_writes is True = 특권(인터랙티브 오너) 턴, 그 외 = 읽기 전용(무인) 턴.
    """

    backend_id: str

    def _augment_message(
        self, user_message: str, context: dict, history: list[dict] | None = None
    ) -> str:
        """대화 이력 + 데이터 컨텍스트 + 질문을 하나의 프롬프트로 조립.

        one-shot·스레드 세션 경로와 모든 백엔드가 동일 문자열을 쓰도록 공통 계약에 둔다.
        """
        parts = []
        if history:
            lines = []
            for msg in history:
                role = "사용자" if msg["role"] == "user" else "어시스턴트"
                lines.append(f"{role}: {msg['content']}")
            parts.append(f"[대화 이력]\n" + "\n".join(lines))
        context_str = json.dumps(context, ensure_ascii=False, indent=2)
        parts.append(f"[데이터 컨텍스트]\n{context_str}")
        parts.append(f"[질문]\n{user_message}")
        return "\n\n".join(parts)

    @abstractmethod
    async def start(self) -> None:
        """런타임 기동·핸드셰이크·게이트 probe·기동 요약 로그. 실패 시 StartupError."""
        ...

    @abstractmethod
    async def ask(
        self,
        system_prompt: str,
        user_message: str,
        *,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        counter: list | None = None,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
    ) -> str:
        ...

    @abstractmethod
    async def ask_with_context(
        self,
        system_prompt: str,
        user_message: str,
        context: dict,
        history: list[dict] | None = None,
        *,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        counter: list | None = None,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
        thread_id=None,
        image_paths: list[str] | None = None,
    ) -> str:
        ...

    @abstractmethod
    async def interrupt_session(self, thread_id) -> None:
        ...

    @abstractmethod
    async def end_session(self, thread_id) -> None:
        """스레드 세션 종료 (멱등)."""
        ...

    @abstractmethod
    async def close_all(self) -> None:
        ...

    @abstractmethod
    def session_ids(self) -> list:
        ...

    @abstractmethod
    def has_session(self, thread_id) -> bool:
        ...


class ClaudeSDKAdapter(LLMAdapter):
    """Claude Agent SDK — 구독 모델 기반."""

    backend_id = "claude"

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        mcp_servers: dict | None = None,
        cwd: str | None = None,
        approve_skill_writes: bool = False,
        readonly_mcp_servers: dict | None = None,
        query_fn: Callable | None = None,
        client_factory: Callable | None = None,
        server_specs: list | None = None,
        skills_mode: str = "native",
    ):
        for name, servers in (("mcp_servers", mcp_servers), ("readonly_mcp_servers", readonly_mcp_servers)):
            if servers is not None and not isinstance(servers, dict):
                raise TypeError(
                    f"{name} must be a dict (e.g. {{'name': McpSdkServerConfig}}), "
                    f"got {type(servers).__name__}"
                )
        self.model = model
        self.mcp_servers = mcp_servers or {}
        # 비특권(무인) 턴용 서버 세트(CallerCapability READ_ONLY) — 기본은 mcp_servers와 동일.
        self.readonly_mcp_servers = self.mcp_servers if readonly_mcp_servers is None else readonly_mcp_servers
        # 서버 세트의 원본 ServerSpec 목록 — 기동 요약의 tools=<n> 집계용.
        self.server_specs = server_specs or []
        # 스킬 로딩 모드 — native: CLI 네이티브 스킬(현행) / registry: 봇 SkillRegistry(skills MCP + 카탈로그).
        self.skills_mode = skills_mode
        # 런타임 주입 seam. None이면 호출 시점에 모듈 전역 query/ClaudeSDKClient를 조회한다
        # (patch("core.llm.query") 등 기존 테스트 패치가 그대로 유효).
        self._query_fn = query_fn
        self._client_factory = client_factory
        self.cwd = cwd
        # 어댑터 인스턴스는 전 초기자가 공유하므로 이 값은 기본값일 뿐이다.
        # 인터랙티브 오너 턴은 호출 시 approve_skill_writes=True를 넘겨 세션 승인한다.
        self.approve_skill_writes = approve_skill_writes
        # 인터랙티브 스레드 전용 stateful 클라이언트 풀 (thread_id → ClaudeSDKClient).
        # steer(interrupt-then-restart)를 위해 스레드별 단일 클라이언트를 재사용한다.
        # 무인 초기자(cron·자동분석)는 여기 등록하지 않고 one-shot query() 경로를 유지한다.
        self._clients: dict = {}
        # 각 라이브 클라이언트가 connect된 시점의 system_prompt (thread_id → prompt).
        # 후속 턴에 프롬프트(메모리/목표)가 바뀌면 재접속해 hot-reload를 유지한다.
        self._client_prompts: dict = {}
        # 각 라이브 클라이언트가 connect된 시점의 권한(priv|ro) — 훅·서버 세트는 connect 옵션에 고정되므로
        # 같은 스레드에 다른 권한 턴이 오면 재접속한다(권한 라우팅 키는 호출마다 적용).
        self._client_caps: dict = {}

    async def _consume_stream(
        self,
        msg_aiter,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        counter: list | None = None,
        stats: dict | None = None,
    ) -> list[str]:
        """producer(async iterator)로부터 블록을 소비하는 producer-agnostic 컨슈머.

        블록 디스패치를 전적으로 소유한다:
        - 사용량/인증 오류 신호(classify_claude_message) → LLMError raise (원시 오류 텍스트 미발화)
        - TextBlock → 수집 + on_text(text)  (스트리밍 계약: TextBlock마다 즉시 콜백)
        - ToolUseBlock → on_tool(name) + counter 증가
        - 그 외(RateLimitEvent allowed_warning 등) → 스킵

        producer가 query()든 client.receive_response()든 동일하게 동작한다.
        counter는 [0] 같은 가변 홀더 — 호출자가 턴 후 tool_use 횟수를 읽는다.
        stats({"tools", "skills_loaded"})는 턴 결과 로그용 내부 집계.
        """
        result_texts: list[str] = []
        try:
            async for msg in msg_aiter:
                error = classify_claude_message(msg)
                if error is not None:
                    raise error
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result_texts.append(block.text)
                            if on_text:
                                await on_text(block.text)
                        elif isinstance(block, ToolUseBlock):
                            # 정규 도구명 — native는 항등, registry 모드는 mcp__skills__* → Skill.
                            name = on_tool_name(block.name, self.skills_mode == "registry")
                            if on_tool:
                                await on_tool(name)
                            if counter is not None:
                                counter[0] += 1
                            if stats is not None:
                                stats["tools"] += 1
                                if name == "Skill":
                                    stats["skills_loaded"] += 1
        finally:
            # async for는 본문에서 raise해도 이터레이터를 닫지 않는다(PEP 533 미채택) — 오류 전파 전에
            # producer(SDK async generator)를 결정적으로 닫아 서브프로세스 정리를 GC에 맡기지 않는다.
            # 테스트 fake 이터레이터는 aclose가 없을 수 있다.
            aclose = getattr(msg_aiter, "aclose", None)
            if aclose is not None:
                await aclose()
        return result_texts

    def _resolve_query(self) -> Callable:
        """주입 seam 우선, 없으면 호출 시점의 모듈 전역 query. SDK 원본이면 테스트 가드 적용."""
        fn = self._query_fn or query
        if fn is _SDK_QUERY:
            forbid_real_runtime()
        return fn

    def _resolve_client_factory(self) -> Callable:
        """주입 seam 우선, 없으면 호출 시점의 모듈 전역 ClaudeSDKClient. SDK 원본이면 테스트 가드 적용."""
        factory = self._client_factory or ClaudeSDKClient
        if factory is _SDK_CLIENT:
            forbid_real_runtime()
        return factory

    def _cap(self, approve_skill_writes: bool | None) -> str:
        """권한 라우팅 키(approve is True) → 로그용 priv|ro."""
        approve = self.approve_skill_writes if approve_skill_writes is None else approve_skill_writes
        return "priv" if approve is True else "ro"

    def _log_failure(self, e: Exception, where: str = "") -> LLMError:
        """실패를 서버 로그에 남기고 LLMError로 정규화 (타입드 오류는 그대로, 그 외 = GENERIC)."""
        if isinstance(e, LLMError):
            print(f"⚠️ Claude 오류{where}: {e.kind.value}")
            return e
        # 트레이스백은 서버 로그에만 남겨 디버깅을 돕는다(사용자엔 미노출).
        print(f"⚠️ Claude 생성 실패{where}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return LLMError.generic()

    def _build_options(
        self,
        system_prompt: str,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
    ) -> ClaudeAgentOptions:
        """ClaudeAgentOptions를 조립 — one-shot query()와 persistent 클라이언트가 공유한다.

        cwd 활성 시 setting_sources/allowed_tools/permission_mode + PreToolUse 안전 게이트
        훅을 배선한다. 이 게이트는 persistent 클라이언트에도 그대로 실려야 하므로(무인/권한
        불변식 유지) 옵션 조립을 여기로 단일화한다.
        """
        options_kwargs = {
            "system_prompt": system_prompt,
            "model": self.model,
            "max_turns": max_turns,
        }
        approve = (
            self.approve_skill_writes if approve_skill_writes is None else approve_skill_writes
        )
        # 권한 라우팅 키 = approve is True → priv 서버 세트, 그 외 → ro 세트(서버측 mutation 차단).
        servers = self.mcp_servers if approve is True else self.readonly_mcp_servers
        if servers:
            options_kwargs["mcp_servers"] = servers
        registry = self.skills_mode == "registry"
        if registry:
            # registry 모드: 네이티브 스킬 목록 억제(older CLI는 무시할 수 있음 — WARN claude.skills_filter).
            # provenance: https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py verified=false
            options_kwargs["skills"] = []
        if self.cwd:
            options_kwargs["cwd"] = self.cwd
            options_kwargs["setting_sources"] = ["project"] if registry else ["user", "project"]
            tools = DEFAULT_ALLOWED_TOOLS if allowed_tools is None else allowed_tools
            options_kwargs["allowed_tools"] = translate_allowed_tools(self.backend_id, tools, skills_registry=registry)
            options_kwargs["permission_mode"] = "bypassPermissions"
            # 통합 무인-권한 게이트 — bypassPermissions 하에서도 PreToolUse 훅은 발화한다.
            # matcher[0]: Bash + 파일-쓰기 도구(무인=읽기 전용 강제). matcher[1]: schedule/memory
            # mutation MCP 도구. 무인 턴(approve False)은 둘 다 하드 차단, 인터랙티브 승인 턴은 허용.
            options_kwargs["hooks"] = {
                "PreToolUse": [
                    HookMatcher(matcher=matcher, hooks=[_make_unattended_gate_hook(approve)])
                    for matcher in ClaudeHookWiring.matchers
                ]
            }
        return ClaudeAgentOptions(**options_kwargs)

    async def _call_claude(
        self,
        system_prompt: str,
        user_message: str,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        counter: list | None = None,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
        raise_errors: bool = False,
    ) -> str:
        """one-shot query() 경로.

        raise_errors=False(스트리밍 턴): 실패 시 오류 메시지(타입드 또는 한국어 폴백)를 on_text 1회 + 반환.
        raise_errors=True(유틸 ask): 실패 시 LLMError raise, on_text 미발화 — 호출부가 폴백 문자열을
        결과로 오인해 저장하지 않게 한다(memory.md 덮어쓰기 방지).
        """
        options = self._build_options(
            system_prompt, max_turns, approve_skill_writes, allowed_tools
        )
        cap = self._cap(approve_skill_writes)
        stats = {"tools": 0, "skills_loaded": 0}
        try:
            msg_aiter = self._resolve_query()(prompt=user_message, options=options)
            result_texts = await self._consume_stream(
                msg_aiter, on_text=on_text, on_tool=on_tool, counter=counter, stats=stats
            )
        except RealRuntimeForbidden:
            raise
        except Exception as e:
            # 원시 예외/트레이스 대신 사용자용 메시지를 스트림·반환에 전달 (provider 내부 미노출).
            error = self._log_failure(e)
            emit(format_turn_result(self.backend_id, None, cap, stats["tools"], stats["skills_loaded"], error.kind.value))
            if raise_errors:
                if error is e:
                    raise
                raise error from e
            if on_text:
                await on_text(error.user_message)
            return error.user_message
        emit(format_turn_result(self.backend_id, None, cap, stats["tools"], stats["skills_loaded"], "ok"))
        return "\n".join(result_texts) if result_texts else ""

    async def _get_or_create_client(
        self, thread_id, options: ClaudeAgentOptions, system_prompt: str, cap: str
    ) -> ClaudeSDKClient:
        """스레드별 stateful 클라이언트를 반환 (없으면 connect 후 등록).

        최초 인터랙티브 턴에 생성되어 후속 턴에 재사용된다. 단, 후속 턴의 system_prompt 또는
        권한(cap)이 connect 시점과 달라졌으면(메모리/목표 편집, 특권↔무인) 기존 클라이언트를
        disconnect하고 새 옵션으로 재접속한다(hot-reload + 게이트 훅·서버 세트 권한 일치). steer는
        재접속 경계를 넘어 계속 동작한다(다음 턴은 새 클라이언트를 interrupt/재사용).
        """
        client = self._clients.get(thread_id)
        if client is not None and self._will_reconnect(thread_id, system_prompt, cap):
            # 시스템 프롬프트/권한 변경 → 새 옵션으로 재접속(구 클라이언트 정리).
            await self.end_session(thread_id)
            client = None
        if client is None:
            client = self._resolve_client_factory()(options=options)
            await client.connect()
            self._clients[thread_id] = client
            self._client_prompts[thread_id] = system_prompt
            self._client_caps[thread_id] = cap
        return client

    async def _call_claude_persistent(
        self,
        thread_id,
        system_prompt: str,
        user_message: str,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        counter: list | None = None,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """인터랙티브 스레드용 persistent 클라이언트 경로 — one-shot query()의 producer 교체.

        _consume_stream을 그대로 재사용(계약 불변): query() 대신 client.receive_response()를
        컨슈머에 먹인다. interrupt는 receive_response를 ResultMessage로 종료시켜 남은 블록을
        더 발화하지 않으므로, 재시작(새 프롬프트) 시 인터리브 없이 새 스트림만 흐른다.
        """
        options = self._build_options(
            system_prompt, max_turns, approve_skill_writes, allowed_tools
        )
        cap = self._cap(approve_skill_writes)
        stats = {"tools": 0, "skills_loaded": 0}
        try:
            client = await self._get_or_create_client(thread_id, options, system_prompt, cap)
            await client.query(user_message)
            result_texts = await self._consume_stream(
                client.receive_response(), on_text=on_text, on_tool=on_tool, counter=counter, stats=stats
            )
        except RealRuntimeForbidden:
            raise
        except Exception as e:
            # 깨진 클라이언트는 폐기 → 다음 턴에 새로 생성. 오류 메시지를 스트림·반환에 전달.
            error = self._log_failure(e, "(persistent)")
            emit(format_turn_result(self.backend_id, thread_id, cap, stats["tools"], stats["skills_loaded"], error.kind.value))
            await self.end_session(thread_id)
            if on_text:
                await on_text(error.user_message)
            return error.user_message
        emit(format_turn_result(self.backend_id, thread_id, cap, stats["tools"], stats["skills_loaded"], "ok"))
        return "\n".join(result_texts) if result_texts else ""

    async def interrupt_session(self, thread_id) -> None:
        """진행 중인 스레드 턴에 interrupt 신호를 보낸다 (재시작 준비). 클라이언트 없으면 no-op."""
        client = self._clients.get(thread_id)
        if client is not None:
            await client.interrupt()

    async def end_session(self, thread_id) -> None:
        """스레드 세션 종료 — 클라이언트 disconnect + 풀에서 제거 (누수 방지, 멱등)."""
        self._client_prompts.pop(thread_id, None)
        self._client_caps.pop(thread_id, None)
        client = self._clients.pop(thread_id, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception as e:
                print(f"⚠️ 세션 클라이언트 정리 실패(thread={thread_id}): {type(e).__name__}: {e}")

    async def close_all(self) -> None:
        """모든 스레드 클라이언트를 disconnect (봇 종료 시 서브프로세스 누수 방지)."""
        for thread_id in list(self._clients.keys()):
            await self.end_session(thread_id)

    def session_ids(self) -> list:
        """활성 persistent 클라이언트를 가진 스레드 ID 목록 (만료 스윕용)."""
        return list(self._clients.keys())

    def has_session(self, thread_id) -> bool:
        """스레드에 활성 persistent 클라이언트가 있는지."""
        return thread_id in self._clients

    def _will_reconnect(self, thread_id, system_prompt: str, cap: str) -> bool:
        """이번 턴에 클라이언트가 새로(재)접속되는지 — 세션 없음이거나 system_prompt·권한 변경 시 True.

        fresh(재접속) 턴은 대화 이력을 다시 folding해 클라이언트를 rehydrate해야 하고,
        라이브 재사용 턴은 클라이언트가 이력을 보유하므로 folding을 생략한다(F3, 중복 방지).
        권한 기록이 없는 클라이언트(외부 주입)는 현재 권한으로 connect된 것으로 본다.
        """
        if thread_id not in self._clients:
            return True
        if self._client_prompts.get(thread_id) != system_prompt:
            return True
        return self._client_caps.get(thread_id, cap) != cap

    async def start(self) -> None:
        """기동 확인 — PreToolUse 게이트 훅 probe(ClaudeHookWiring) + 기동 요약 로그 + 미검증 표면 WARN.

        Claude는 턴마다 CLI 서브프로세스를 띄우므로 사전 기동할 런타임이 없다(인증은 preflight가 확인).
        probe: 무인(ro) Bash → deny, 특권(priv) Bash → allow 여야 한다. 불일치 = StartupError.
        """
        await _CLAUDE_GATE_WIRING.probe()
        tools = sum(len(spec.tools) for spec in self.server_specs)
        emit(format_startup_summary(self.backend_id, self.model, self.skills_mode, tools))
        emit_unverified_warnings(self.backend_id, self.skills_mode)

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
        """유틸 호출(압축·메모리 추출/통합) — 실패 시 LLMError raise (폴백 문자열 반환 안 함)."""
        return await self._call_claude(
            system_prompt,
            user_message,
            on_text=on_text,
            on_tool=on_tool,
            counter=counter,
            max_turns=max_turns,
            approve_skill_writes=approve_skill_writes,
            allowed_tools=allowed_tools,
            raise_errors=True,
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
        """컨텍스트 기반 생성.

        image_paths는 무시한다 — Claude 경로는 봇이 이미지 경로 안내 문구를 메시지에 조립해 넘긴다.

        thread_id가 주어지면(인터랙티브 스레드) 스레드별 stateful 클라이언트를 재사용하는
        persistent 경로로 라우팅한다. None이면(무인 초기자·유틸 호출) 기존 one-shot query()
        경로를 유지한다 — steer/상태 세션은 인터랙티브 전용(매트릭스).

        persistent 경로에서 라이브 클라이언트를 재사용할 때는 대화 이력을 다시 folding하지
        않는다(F3): 클라이언트가 이미 이력을 보유하므로 중복 컨텍스트/토큰 증가를 피한다.
        fresh(재시작/프롬프트 변경 후 재접속) 턴만 이력을 folding해 rehydrate한다.
        """
        if thread_id is not None:
            fold_history = (
                history if self._will_reconnect(thread_id, system_prompt, self._cap(approve_skill_writes)) else None
            )
            augmented_message = self._augment_message(user_message, context, fold_history)
            return await self._call_claude_persistent(
                thread_id,
                system_prompt,
                augmented_message,
                on_text=on_text,
                on_tool=on_tool,
                counter=counter,
                max_turns=max_turns,
                approve_skill_writes=approve_skill_writes,
                allowed_tools=allowed_tools,
            )
        augmented_message = self._augment_message(user_message, context, history)
        return await self._call_claude(
            system_prompt,
            augmented_message,
            on_text=on_text,
            on_tool=on_tool,
            counter=counter,
            max_turns=max_turns,
            approve_skill_writes=approve_skill_writes,
            allowed_tools=allowed_tools,
        )


def create_llm_adapter(
    adapter_type: str = "claude",
    model: str | None = None,
    mcp_servers: list | None = None,
    cwd: str | None = None,
) -> LLMAdapter:
    if adapter_type == "claude":
        kwargs = {}
        if model:
            kwargs["model"] = model
        if mcp_servers:
            kwargs["mcp_servers"] = mcp_servers
        if cwd:
            kwargs["cwd"] = cwd
        return ClaudeSDKAdapter(**kwargs)
    raise ValueError(f"Unknown adapter type: {adapter_type}")


def create_llm_adapter_from_config(
    config,
    mcp_servers: dict | None = None,
    cwd: str | None = None,
    readonly_mcp_servers: dict | None = None,
    server_specs: list | None = None,
    tool_server=None,
) -> LLMAdapter:
    """검증된 LLMConfig(core.llm_config)로 선택 백엔드 어댑터를 만든다.

    Claude: mcp_servers = priv SDK 서버 세트, readonly_mcp_servers = ro 세트(claude_sdk_bridge),
    server_specs = 원본 ServerSpec 목록(기동 요약 도구 수, registry 모드면 skills 포함), skills_mode = llm.claude.skills.
    Codex: tool_server = SharedToolServer(priv/ro 토큰 URL·게이트 엔드포인트), server_specs, cwd (지연 import).
    Grok: tool_server = SharedToolServer(priv/ro 토큰 URL), server_specs, cwd(= PROJECT_ROOT, 격리 HOME 기준) (지연 import).
    다른 백엔드로의 자동 대체는 없다. 어댑터가 없는 백엔드는 StartupError(기동 실패).
    """
    if config.backend == "claude":
        kwargs = {
            "mcp_servers": mcp_servers,
            "readonly_mcp_servers": readonly_mcp_servers,
            "server_specs": server_specs,
            "cwd": cwd,
            "skills_mode": config.skills_mode,
        }
        if config.model:
            kwargs["model"] = config.model
        return ClaudeSDKAdapter(**kwargs)
    if config.backend == "codex":
        from core.runtimes.codex_adapter import CodexAdapter

        return CodexAdapter(
            tool_server=tool_server,
            server_specs=server_specs,
            cwd=cwd,
            model=config.model,
            bin=config.codex["bin"],
        )
    if config.backend == "grok":
        from core.runtimes.grok_adapter import GrokAdapter

        return GrokAdapter(
            tool_server=tool_server,
            server_specs=server_specs,
            cwd=cwd,
            model=config.model,
            bin=config.grok["bin"],
        )
    raise StartupError(
        f"backend={config.backend} 어댑터가 없습니다",
        "config.json의 llm.backend를 claude | codex | grok 중 하나로 설정하세요",
    )
