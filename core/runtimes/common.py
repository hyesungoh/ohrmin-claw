"""Codex·Grok 어댑터 공용 소품 — 턴 스트림 센티널 · 세션 시작 실패 · 프롬프트 해시 · system prompt 렌더 · 오류 정규화."""
import hashlib
import traceback

from core.llm_errors import BACKEND_LABELS, LLMError
from core.runtimes.jsonrpc_stdio import RuntimeUnavailable
from core.runtimes.tool_names import backend_tool_note, render_tool_refs

DETACHED = object()  # end_session — 턴 스트림 분리(수집 텍스트 반환)
CLOSED = object()  # 프로세스 종료 — 턴 실패(RUNTIME_UNAVAILABLE)


class SessionStartFailed(Exception):
    """런타임 세션 시작 실패 — Codex thread/start · Grok session/new (JSON-RPC 오류·형태 불일치)."""


def prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def render_system_prompt(system_prompt: str, backend: str) -> str:
    """system prompt + 백엔드 도구 부록(구분 빈 줄), 도구 표기 렌더."""
    note = backend_tool_note(backend)
    text = f"{system_prompt}\n\n{note}" if note else system_prompt
    return render_tool_refs(text, backend)


def find_resets_at(node):
    """오류 payload에서 처음 발견되는 숫자 resetsAt|resets_at (없으면 None)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("resetsAt", "resets_at") and isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            found = find_resets_at(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_resets_at(value)
            if found is not None:
                return found
    return None


def to_llm_error(e: Exception, backend: str, cap: str) -> LLMError:
    """턴 실패를 서버 로그에 남기고 LLMError로 정규화 (타입드 오류는 그대로, 런타임·세션 시작 실패 = RUNTIME_UNAVAILABLE, 그 외 GENERIC)."""
    label = BACKEND_LABELS[backend]
    if isinstance(e, LLMError):
        print(f"⚠️ {label} 오류({cap}): {e.kind.value}")
        return e
    if isinstance(e, (RuntimeUnavailable, SessionStartFailed)):
        print(f"⚠️ {label} 런타임 사용 불가({cap}): {type(e).__name__}: {e}")
        return LLMError.runtime_unavailable(backend)
    print(f"⚠️ {label} 생성 실패({cap}): {type(e).__name__}: {e}")
    traceback.print_exc()
    return LLMError.generic()
