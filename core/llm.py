"""LLM 어댑터 레이어 — Claude Agent SDK 기반."""
import json
import os
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable

from claude_agent_sdk import query, ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock


# cwd(스킬/빌트인 도구) 활성화 시 기본 노출 도구셋. 초기자별로 축소 도구셋을
# 넘기고 싶으면 _call_claude(allowed_tools=...)로 재정의한다 (무인 초기자 = skill-write 제외 등).
# WebSearch/WebFetch는 읽기 전용이라 매트릭스상 전 초기자(무인 포함)에 허용된다.
DEFAULT_ALLOWED_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill", "WebSearch", "WebFetch"]

# skill-write 게이트가 감시하는 파일 쓰기 도구. matcher 문자열과 정렬 유지.
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# 생성 실패 시 원시 트레이스 대신 전달하는 한국어 폴백.
_CLAUDE_FALLBACK_MESSAGE = "지금 데이터를 못 불러왔어요, 잠시 후 다시 시도할게요."


def _skill_path_segments(file_path: str) -> list[str]:
    """파일 경로를 정규화해 세그먼트 리스트로 반환 (절대/상대/`..` 무관)."""
    norm = os.path.normpath(file_path).replace("\\", "/")
    return [p for p in norm.split("/") if p not in ("", ".")]


def _contains_subseq(parts: list[str], sub: list[str]) -> bool:
    """parts 안에 sub가 연속 부분수열로 존재하는지 (부분문자열 오탐 방지용 세그먼트 매칭)."""
    n, m = len(parts), len(sub)
    return any(parts[i:i + m] == sub for i in range(n - m + 1))


def evaluate_skill_write_gate(
    tool_name: str,
    tool_input: dict | None,
    approve_skill_writes: bool = False,
) -> tuple[bool, str]:
    """skill-write 안전 게이트의 순수 판정 함수.

    반환: (allow, reason). 규칙:
    - 파일 쓰기 도구가 아니거나 `.claude/skills/` 밖이면 허용.
    - `.claude/skills/science-reference/**` 는 무조건 차단 (승인 플래그 무시 — 공유 참조 허브 고정).
    - 그 외 `.claude/skills/**` 쓰기는 세션 승인 플래그(approve_skill_writes)가 있을 때만 허용.

    `permission_mode="bypassPermissions"`이므로 프롬프트가 아니라 이 코드가 불변식을 강제한다.
    """
    if tool_name not in _WRITE_TOOLS:
        return True, ""
    tool_input = tool_input or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not file_path:
        return True, ""
    parts = _skill_path_segments(file_path)
    if not _contains_subseq(parts, [".claude", "skills"]):
        return True, ""
    if _contains_subseq(parts, [".claude", "skills", "science-reference"]):
        return False, "science-reference 스킬은 읽기 전용입니다 (수정 불가)."
    if not approve_skill_writes:
        return False, "스킬 파일 쓰기는 세션 승인(approve_skill_writes)이 필요합니다."
    return True, ""


# 무인 턴에서 하드 차단할 MCP mutation 도구 — 인터랙티브 오너 승인 시에만 허용.
# schedule_list·list_memory 등 조회/읽기 도구는 이 집합에 없으므로 언제나 허용된다.
_MUTATION_MCP_TOOLS = {
    "mcp__schedule__schedule_create",
    "mcp__schedule__schedule_pause",
    "mcp__schedule__schedule_resume",
    "mcp__schedule__schedule_remove",
    "mcp__memory__add_memory",
    "mcp__memory__replace_memory",
    "mcp__memory__remove_memory",
}

# 무인 턴 = 읽기 전용 불변식. 셸(Bash)과 모든 파일-쓰기 도구를 하드 차단한다. bypassPermissions
# 하에서 무인 초기자(cron·자동분석)가 Bash로 스킬/메모리/data 파일을 우회 기록하거나(예:
# `echo … > .claude/skills/…`, prompts/memory.md, data/cron_jobs.json) 파일을 직접 쓰는 경로를
# 구조적으로 봉쇄한다. 웹 콘텐츠(WebSearch/WebFetch) 인젝션이 이를 유도해도 게이트에서 막힌다.
_UNATTENDED_DENIED_TOOLS = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}

# PreToolUse 매처(정규식) — Bash + 파일-쓰기 도구에 훅을 발화시킨다. 무인 턴은 전부 차단,
# 인터랙티브 승인 턴은 게이트 함수가 허용(단 .claude/skills/** 규칙은 별도 적용).
_UNATTENDED_TOOL_MATCHER = "Bash|Write|Edit|MultiEdit|NotebookEdit"

# PreToolUse 매처(정규식) — mutation MCP 도구에 훅을 발화시킨다. list/read 도구명은 매칭되지 않는다.
# 게이트 함수가 최종 판정을 하므로 매처가 다소 넓게 걸려도 안전하다.
_MUTATION_TOOL_MATCHER = (
    r"mcp__schedule__schedule_(create|pause|resume|remove)"
    r"|mcp__memory__(add_memory|replace_memory|remove_memory)"
)


def evaluate_tool_gate(
    tool_name: str,
    tool_input: dict | None,
    approve_privileged: bool = False,
) -> tuple[bool, str]:
    """통합 무인-권한 게이트 — 무인 턴을 읽기 전용으로 강제, 쓰기/특권은 인터랙티브 승인 턴만.

    approve_privileged(= 인터랙티브 오너 턴의 approve_skill_writes True)일 때만 허용:
    - Bash 및 파일-쓰기 도구(Write/Edit/MultiEdit/NotebookEdit).
    - `.claude/skills/**` 쓰기 (science-reference는 승인해도 무조건 차단).
    - schedule/memory mutation MCP 도구(create/pause/resume/remove, add/replace/remove_memory).

    무인 턴(승인 없음)에서는 위 전부를 하드 차단(permissionDecision: deny)한다 — 즉 무인 턴은
    읽기/조회/분석(Read/Glob/Grep/Skill/web read/read MCP·schedule_list·list_memory)만 가능하다.
    `permission_mode="bypassPermissions"` 하에서도 PreToolUse 훅은 발화하므로 이 게이트가 구조적
    강제선이다(allowed_tools 스티어링보다 강함).
    """
    # 1) 스킬 파일 쓰기 게이트(기존 로직 재사용) — science-reference는 특권이어도 무조건 차단.
    allow, reason = evaluate_skill_write_gate(tool_name, tool_input, approve_privileged)
    if not allow:
        return allow, reason
    # 2) 무인 턴 = 읽기 전용 — Bash + 파일-쓰기 도구를 전부 하드 차단(경로 무관).
    if not approve_privileged and tool_name in _UNATTENDED_DENIED_TOOLS:
        return False, f"무인 턴은 읽기 전용입니다 — {tool_name}는 인터랙티브 오너 세션에서만 허용됩니다."
    # 3) schedule/memory mutation — 무인 턴 하드 차단.
    if tool_name in _MUTATION_MCP_TOOLS and not approve_privileged:
        return False, f"{tool_name}는 인터랙티브 오너 세션 승인이 필요합니다 (무인 턴 차단)."
    return True, ""


def _make_unattended_gate_hook(approve_skill_writes: bool) -> Callable:
    """evaluate_tool_gate를 감싸는 PreToolUse 훅 콜백을 만든다 (통합 무인-권한 게이트).

    can_use_tool 콜백은 streaming(AsyncIterable) prompt를 요구하지만 이 어댑터는 문자열 prompt
    경로를 쓴다 (SDK가 문자열 prompt + can_use_tool 조합에 ValueError). PreToolUse 훅은 문자열
    prompt에서도 컨트롤 프로토콜로 발화하며 bypassPermissions는 권한 프롬프트만 우회할 뿐
    훅 발화를 막지 않으므로, 게이트는 훅으로 강제한다. approve_skill_writes는 "인터랙티브 특권
    턴" 신호로 재사용된다(skill-write + schedule/memory mutation을 함께 게이팅).
    """
    async def _guard(input_data, tool_use_id, context):
        input_data = input_data or {}
        allow, reason = evaluate_tool_gate(
            input_data.get("tool_name", ""),
            input_data.get("tool_input", {}),
            approve_skill_writes,
        )
        if allow:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return _guard


class LLMAdapter(ABC):
    """LLM 호출 공통 인터페이스."""

    @abstractmethod
    async def ask(self, system_prompt: str, user_message: str) -> str:
        ...

    @abstractmethod
    async def ask_with_context(
        self,
        system_prompt: str,
        user_message: str,
        context: dict,
        history: list[dict] | None = None,
    ) -> str:
        ...


class ClaudeSDKAdapter(LLMAdapter):
    """Claude Agent SDK — 구독 모델 기반."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        mcp_servers: dict | None = None,
        cwd: str | None = None,
        approve_skill_writes: bool = False,
    ):
        if mcp_servers is not None and not isinstance(mcp_servers, dict):
            raise TypeError(
                f"mcp_servers must be a dict (e.g. {{'name': McpSdkServerConfig}}), "
                f"got {type(mcp_servers).__name__}"
            )
        self.model = model
        self.mcp_servers = mcp_servers or {}
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

    async def _consume_stream(
        self,
        msg_aiter,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        counter: list | None = None,
    ) -> list[str]:
        """producer(async iterator)로부터 블록을 소비하는 producer-agnostic 컨슈머.

        블록 디스패치를 전적으로 소유한다:
        - TextBlock → 수집 + on_text(text)  (스트리밍 계약: TextBlock마다 즉시 콜백)
        - ToolUseBlock → on_tool(name) + counter 증가
        - 그 외(RateLimitEvent 등) → 스킵

        producer가 query()든 client.receive_response()든 동일하게 동작한다.
        counter는 [0] 같은 가변 홀더 — 호출자가 턴 후 tool_use 횟수를 읽는다.
        """
        result_texts: list[str] = []
        async for msg in msg_aiter:
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        result_texts.append(block.text)
                        if on_text:
                            await on_text(block.text)
                    elif isinstance(block, ToolUseBlock):
                        if on_tool:
                            await on_tool(block.name)
                        if counter is not None:
                            counter[0] += 1
        return result_texts

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
        if self.mcp_servers:
            options_kwargs["mcp_servers"] = self.mcp_servers
        if self.cwd:
            approve = (
                self.approve_skill_writes if approve_skill_writes is None else approve_skill_writes
            )
            options_kwargs["cwd"] = self.cwd
            options_kwargs["setting_sources"] = ["user", "project"]
            options_kwargs["allowed_tools"] = (
                DEFAULT_ALLOWED_TOOLS if allowed_tools is None else allowed_tools
            )
            options_kwargs["permission_mode"] = "bypassPermissions"
            # 통합 무인-권한 게이트 — bypassPermissions 하에서도 PreToolUse 훅은 발화한다.
            # matcher[0]: Bash + 파일-쓰기 도구(무인=읽기 전용 강제). matcher[1]: schedule/memory
            # mutation MCP 도구. 무인 턴(approve False)은 둘 다 하드 차단, 인터랙티브 승인 턴은 허용.
            options_kwargs["hooks"] = {
                "PreToolUse": [
                    HookMatcher(
                        matcher=_UNATTENDED_TOOL_MATCHER,
                        hooks=[_make_unattended_gate_hook(approve)],
                    ),
                    HookMatcher(
                        matcher=_MUTATION_TOOL_MATCHER,
                        hooks=[_make_unattended_gate_hook(approve)],
                    ),
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
    ) -> str:
        options = self._build_options(
            system_prompt, max_turns, approve_skill_writes, allowed_tools
        )
        try:
            msg_aiter = query(prompt=user_message, options=options)
            result_texts = await self._consume_stream(
                msg_aiter, on_text=on_text, on_tool=on_tool, counter=counter
            )
        except Exception as e:
            # 원시 예외/트레이스 대신 한국어 폴백을 스트림·반환에 전달 (provider 내부 미노출).
            # 트레이스백은 서버 로그에만 남겨 디버깅을 돕는다(사용자엔 미노출).
            print(f"⚠️ Claude 생성 실패: {type(e).__name__}: {e}")
            traceback.print_exc()
            if on_text:
                await on_text(_CLAUDE_FALLBACK_MESSAGE)
            return _CLAUDE_FALLBACK_MESSAGE
        return "\n".join(result_texts) if result_texts else ""

    async def _get_or_create_client(
        self, thread_id, options: ClaudeAgentOptions, system_prompt: str
    ) -> ClaudeSDKClient:
        """스레드별 stateful 클라이언트를 반환 (없으면 connect 후 등록).

        최초 인터랙티브 턴에 생성되어 후속 턴에 재사용된다. 단, 후속 턴의 system_prompt가
        connect 시점과 달라졌으면(메모리/목표 편집) 기존 클라이언트를 disconnect하고 새
        프롬프트로 재접속해 hot-reload를 유지한다. steer는 재접속 경계를 넘어 계속 동작한다
        (다음 턴은 새 클라이언트를 interrupt/재사용).
        """
        client = self._clients.get(thread_id)
        if client is not None and self._client_prompts.get(thread_id) != system_prompt:
            # 시스템 프롬프트 변경 → 새 프롬프트로 재접속(구 클라이언트 정리).
            await self.end_session(thread_id)
            client = None
        if client is None:
            client = ClaudeSDKClient(options=options)
            await client.connect()
            self._clients[thread_id] = client
            self._client_prompts[thread_id] = system_prompt
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
        try:
            client = await self._get_or_create_client(thread_id, options, system_prompt)
            await client.query(user_message)
            result_texts = await self._consume_stream(
                client.receive_response(), on_text=on_text, on_tool=on_tool, counter=counter
            )
        except Exception as e:
            # 깨진 클라이언트는 폐기 → 다음 턴에 새로 생성. 폴백을 스트림·반환에 전달.
            # 트레이스백은 서버 로그에만 남긴다(사용자엔 미노출).
            print(f"⚠️ Claude 생성 실패(persistent): {type(e).__name__}: {e}")
            traceback.print_exc()
            await self.end_session(thread_id)
            if on_text:
                await on_text(_CLAUDE_FALLBACK_MESSAGE)
            return _CLAUDE_FALLBACK_MESSAGE
        return "\n".join(result_texts) if result_texts else ""

    async def interrupt_session(self, thread_id) -> None:
        """진행 중인 스레드 턴에 interrupt 신호를 보낸다 (재시작 준비). 클라이언트 없으면 no-op."""
        client = self._clients.get(thread_id)
        if client is not None:
            await client.interrupt()

    async def end_session(self, thread_id) -> None:
        """스레드 세션 종료 — 클라이언트 disconnect + 풀에서 제거 (누수 방지, 멱등)."""
        self._client_prompts.pop(thread_id, None)
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

    def _will_reconnect(self, thread_id, system_prompt: str) -> bool:
        """이번 턴에 클라이언트가 새로(재)접속되는지 — 세션 없음이거나 system_prompt 변경 시 True.

        fresh(재접속) 턴은 대화 이력을 다시 folding해 클라이언트를 rehydrate해야 하고,
        라이브 재사용 턴은 클라이언트가 이력을 보유하므로 folding을 생략한다(F3, 중복 방지).
        """
        if thread_id not in self._clients:
            return True
        return self._client_prompts.get(thread_id) != system_prompt

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
        return await self._call_claude(
            system_prompt,
            user_message,
            on_text=on_text,
            on_tool=on_tool,
            counter=counter,
            max_turns=max_turns,
            approve_skill_writes=approve_skill_writes,
            allowed_tools=allowed_tools,
        )

    def _augment_message(
        self, user_message: str, context: dict, history: list[dict] | None = None
    ) -> str:
        """대화 이력 + 데이터 컨텍스트 + 질문을 하나의 프롬프트로 조립.

        one-shot query()와 persistent 클라이언트가 동일 문자열을 쓰도록 단일화한다.
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
    ) -> str:
        """컨텍스트 기반 생성.

        thread_id가 주어지면(인터랙티브 스레드) 스레드별 stateful 클라이언트를 재사용하는
        persistent 경로로 라우팅한다. None이면(무인 초기자·유틸 호출) 기존 one-shot query()
        경로를 유지한다 — steer/상태 세션은 인터랙티브 전용(매트릭스).

        persistent 경로에서 라이브 클라이언트를 재사용할 때는 대화 이력을 다시 folding하지
        않는다(F3): 클라이언트가 이미 이력을 보유하므로 중복 컨텍스트/토큰 증가를 피한다.
        fresh(재시작/프롬프트 변경 후 재접속) 턴만 이력을 folding해 rehydrate한다.
        """
        if thread_id is not None:
            fold_history = history if self._will_reconnect(thread_id, system_prompt) else None
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
