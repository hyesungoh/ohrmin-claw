"""LLM 어댑터 레이어 — Claude Agent SDK 기반."""
import json
from abc import ABC, abstractmethod
from collections.abc import Callable

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, TextBlock


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
    ):
        if mcp_servers is not None and not isinstance(mcp_servers, dict):
            raise TypeError(
                f"mcp_servers must be a dict (e.g. {{'name': McpSdkServerConfig}}), "
                f"got {type(mcp_servers).__name__}"
            )
        self.model = model
        self.mcp_servers = mcp_servers or {}
        self.cwd = cwd

    async def _call_claude(
        self,
        system_prompt: str,
        user_message: str,
        on_text: Callable | None = None,
    ) -> str:
        result_texts = []
        options_kwargs = {
            "system_prompt": system_prompt,
            "model": self.model,
            "max_turns": 15,
        }
        if self.mcp_servers:
            options_kwargs["mcp_servers"] = self.mcp_servers
        if self.cwd:
            options_kwargs["cwd"] = self.cwd
            options_kwargs["setting_sources"] = ["user", "project"]
            options_kwargs["allowed_tools"] = [
                "Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill",
            ]
            options_kwargs["permission_mode"] = "bypassPermissions"
        async for msg in query(
            prompt=user_message,
            options=ClaudeAgentOptions(**options_kwargs),
        ):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        result_texts.append(block.text)
                        if on_text:
                            await on_text(block.text)
        return "\n".join(result_texts) if result_texts else ""

    async def ask(self, system_prompt: str, user_message: str, on_text: Callable | None = None) -> str:
        return await self._call_claude(system_prompt, user_message, on_text=on_text)

    async def ask_with_context(
        self,
        system_prompt: str,
        user_message: str,
        context: dict,
        history: list[dict] | None = None,
        on_text: Callable | None = None,
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
        return await self._call_claude(system_prompt, augmented_message, on_text=on_text)


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
