"""서버측 호출자 권한(CallerCapability) — 훅·승인 발생 여부와 무관한 mutation MCP 차단선.

권한 라우팅 키는 `approve_skill_writes is True` 하나다: True = PRIVILEGED 서버 세트/토큰,
그 외 = READ_ONLY. READ_ONLY에서 게이트가 거부하는 도구(schedule/memory mutation 7종)는 핸들러를
호출하지 않고 거부 결과를 돌려준다. 판정·사유는 core.safety_gate.decide()(= evaluate_tool_gate 규칙) 단일 경로.
런타임 SDK를 import하지 않는다(Codex/Grok HTTP 경로에서도 Claude SDK 미로딩).
"""
from enum import Enum

from core.garmin_tools import _json_response
from core.observability import emit, format_gate_decision
from core.safety_gate import CanonicalToolCall, decide
from core.tool_server.spec import ToolSpec, canonical_tool_name


class CallerCapability(str, Enum):
    PRIVILEGED = "priv"
    READ_ONLY = "ro"


def validation_error_result(message: str) -> dict:
    """핸들러 호출 전 입력 검증 실패 결과 (MCP isError, KeyError 대신)."""
    return {"content": [{"type": "text", "text": f"Input validation error: {message}"}], "is_error": True}


def wrap_handler(server: str, tool_spec: ToolSpec, capability: CallerCapability, backend: str = "claude"):
    """transport가 호출할 핸들러 — ① capability 판정 ② None 값 키 제거 ③ 필수 파라미터 확인 → 원 핸들러."""
    canonical = canonical_tool_name(server, tool_spec.name)
    privileged = capability is CallerCapability.PRIVILEGED
    required = [key for key, param in tool_spec.params.items() if param.required]

    async def handler(args):
        allow, reason = decide(CanonicalToolCall(canonical), privileged, runtime_guard=False)
        if not allow:
            emit(format_gate_decision(backend, capability.value, canonical, False, "capability", reason))
            return _json_response({"success": False, "error": reason, "denied_by": "capability"})
        # strict schema는 선택 키에 null을 허용한다 → 원 핸들러의 `args.get(k, default)` 의미 보존(F5).
        clean_args = {key: value for key, value in (args or {}).items() if value is not None}
        missing = [key for key in required if key not in clean_args]
        if missing:
            return validation_error_result(f"required parameter(s) missing or null: {', '.join(missing)}")
        return await tool_spec.handler(clean_args)

    return handler
