"""LLM 백엔드 공통 오류 — 타입드 오류 종류 + 사용자용 한국어 메시지 + 기동 실패.

provider 원시 문자열(트레이스·엔드포인트)은 사용자에게 노출하지 않는다. 한도/인증 오류는
다른 백엔드로 대체하지 않고 안내만 한다(자동 failover 없음).
"""
import datetime
import os
import re
from enum import Enum


class LLMErrorKind(str, Enum):
    USAGE_LIMIT = "usage_limit"
    AUTH_EXPIRED = "auth_expired"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    GENERIC = "generic"


# 사용자용 백엔드 표기. 모르는 id는 그대로 쓴다.
BACKEND_LABELS = {"claude": "Claude", "codex": "Codex", "grok": "Grok"}

# 생성 실패 시 원시 트레이스 대신 전달하는 한국어 폴백 (기존 core/llm.py _CLAUDE_FALLBACK_MESSAGE).
GENERIC_MESSAGE = "지금 데이터를 못 불러왔어요, 잠시 후 다시 시도할게요."

_USAGE_LIMIT_TEMPLATE = (
    "⚠️ {backend} 사용 한도에 도달했어요{reset_suffix}. "
    "다른 백엔드로 대체하지 않으니 한도 재설정 후 다시 시도해 주세요."
)
_AUTH_EXPIRED_TEMPLATE = (
    "⚠️ {backend} 인증이 만료됐어요. 봇 서버에서 `{fix}` 실행 후 봇을 재시작해 주세요."
)
_RUNTIME_UNAVAILABLE_TEMPLATE = "⚠️ {backend} 런타임을 시작하지 못했어요. 봇 로그를 확인해 주세요."

# is_llm_error_reply 판정용 접두 패턴 (템플릿과 정렬 유지).
_ERROR_REPLY_PATTERNS = (
    re.compile(r"⚠️ \S+ 사용 한도에 도달했어요( \(재설정: \d{2}:\d{2}\))?\. 다른 백엔드로 대체하지 않으니"),
    re.compile(r"⚠️ \S+ 인증이 만료됐어요\. 봇 서버에서 `"),
    re.compile(r"⚠️ \S+ 런타임을 시작하지 못했어요\. "),
)


def _label(backend: str) -> str:
    return BACKEND_LABELS.get(backend, backend)


def _reset_suffix(resets_at) -> str:
    """resets_at(Unix 초) → " (재설정: HH:MM)" (로컬 시각). 없거나 변환 불가면 ""."""
    if resets_at is None:
        return ""
    try:
        return f" (재설정: {datetime.datetime.fromtimestamp(float(resets_at)).strftime('%H:%M')})"
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def usage_limit_message(backend: str, resets_at=None) -> str:
    return _USAGE_LIMIT_TEMPLATE.format(backend=_label(backend), reset_suffix=_reset_suffix(resets_at))


def auth_expired_message(backend: str, fix: str) -> str:
    return _AUTH_EXPIRED_TEMPLATE.format(backend=_label(backend), fix=fix)


def runtime_unavailable_message(backend: str) -> str:
    return _RUNTIME_UNAVAILABLE_TEMPLATE.format(backend=_label(backend))


class LLMError(Exception):
    """LLM 호출 실패 — kind로 분류, user_message는 그대로 사용자에게 전달 가능한 문자열."""

    def __init__(self, kind: LLMErrorKind, user_message: str, resets_at=None):
        super().__init__(user_message)
        self.kind = kind
        self.user_message = user_message
        self.resets_at = resets_at

    @classmethod
    def usage_limit(cls, backend: str, resets_at=None) -> "LLMError":
        return cls(LLMErrorKind.USAGE_LIMIT, usage_limit_message(backend, resets_at), resets_at)

    @classmethod
    def auth_expired(cls, backend: str, fix: str) -> "LLMError":
        return cls(LLMErrorKind.AUTH_EXPIRED, auth_expired_message(backend, fix))

    @classmethod
    def runtime_unavailable(cls, backend: str) -> "LLMError":
        return cls(LLMErrorKind.RUNTIME_UNAVAILABLE, runtime_unavailable_message(backend))

    @classmethod
    def generic(cls) -> "LLMError":
        return cls(LLMErrorKind.GENERIC, GENERIC_MESSAGE)


def is_llm_error_reply(text: str | None) -> bool:
    """턴 반환 문자열이 LLM 오류 안내(4종)인지 — 학습 루프 성공 판정 등에서 사용."""
    if not text:
        return False
    if text == GENERIC_MESSAGE:
        return True
    return any(p.match(text) for p in _ERROR_REPLY_PATTERNS)


class StartupError(Exception):
    """기동 단계 실패 — cause(원인)와 fix(해결 명령/조치)를 로그로 남기고 exit 1."""

    def __init__(self, cause: str, fix: str):
        super().__init__(cause)
        self.cause = cause
        self.fix = fix


class RealRuntimeForbidden(RuntimeError):
    """테스트(OHRMIN_FORBID_REAL_RUNTIMES=1)에서 실제 런타임/CLI 실행 시도."""


def forbid_real_runtime() -> None:
    """OHRMIN_FORBID_REAL_RUNTIMES=1이면 실제 런타임 실행을 막는다 (SDK 원본 객체·기본 러너 전용)."""
    if os.environ.get("OHRMIN_FORBID_REAL_RUNTIMES") == "1":
        raise RealRuntimeForbidden("real runtime forbidden in tests")
