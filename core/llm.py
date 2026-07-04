"""LLM 어댑터 레이어 — Claude Agent SDK 기반."""
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Callable

from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher
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


def _make_skill_write_guard_hook(approve_skill_writes: bool) -> Callable:
    """evaluate_skill_write_gate를 감싸는 PreToolUse 훅 콜백을 만든다.

    can_use_tool 콜백은 streaming(AsyncIterable) prompt를 요구하지만 이 어댑터는 문자열 prompt
    경로를 쓴다 (SDK가 문자열 prompt + can_use_tool 조합에 ValueError). PreToolUse 훅은 문자열
    prompt에서도 컨트롤 프로토콜로 발화하며 bypassPermissions는 권한 프롬프트만 우회할 뿐
    훅 발화를 막지 않으므로, 게이트는 훅으로 강제한다.
    """
    async def _guard(input_data, tool_use_id, context):
        input_data = input_data or {}
        allow, reason = evaluate_skill_write_gate(
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
            # skill-write 안전 게이트 — bypassPermissions 하에서도 PreToolUse 훅은 발화한다.
            options_kwargs["hooks"] = {
                "PreToolUse": [
                    HookMatcher(
                        matcher="Write|Edit|MultiEdit|NotebookEdit",
                        hooks=[_make_skill_write_guard_hook(approve)],
                    )
                ]
            }
        try:
            msg_aiter = query(
                prompt=user_message,
                options=ClaudeAgentOptions(**options_kwargs),
            )
            result_texts = await self._consume_stream(
                msg_aiter, on_text=on_text, on_tool=on_tool, counter=counter
            )
        except Exception as e:
            # 원시 예외/트레이스 대신 한국어 폴백을 스트림·반환에 전달 (provider 내부 미노출).
            print(f"⚠️ Claude 생성 실패: {type(e).__name__}: {e}")
            if on_text:
                await on_text(_CLAUDE_FALLBACK_MESSAGE)
            return _CLAUDE_FALLBACK_MESSAGE
        return "\n".join(result_texts) if result_texts else ""

    async def ask(
        self,
        system_prompt: str,
        user_message: str,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
    ) -> str:
        return await self._call_claude(
            system_prompt,
            user_message,
            on_text=on_text,
            on_tool=on_tool,
            max_turns=max_turns,
            approve_skill_writes=approve_skill_writes,
            allowed_tools=allowed_tools,
        )

    async def ask_with_context(
        self,
        system_prompt: str,
        user_message: str,
        context: dict,
        history: list[dict] | None = None,
        on_text: Callable | None = None,
        on_tool: Callable | None = None,
        max_turns: int = 15,
        approve_skill_writes: bool | None = None,
        allowed_tools: list[str] | None = None,
    ) -> str:
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
        augmented_message = "\n\n".join(parts)
        return await self._call_claude(
            system_prompt,
            augmented_message,
            on_text=on_text,
            on_tool=on_tool,
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
