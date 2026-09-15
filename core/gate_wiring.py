"""게이트 wiring — 백엔드당 정확히 1개. 판정은 모두 core.safety_gate.decide()가 한다.

- ClaudeHookWiring: 인프로세스 PreToolUse 훅 콜백(HookMatcher 2개 구성은 core/llm.py가 조립).
  runtime_guard=False — `.claude/` 쓰기는 Claude CLI가 네이티브로 차단한다.
- CodexHookWiring: Codex PreToolUse 훅 command(`[sys.executable, core/hooks/codex_gate_hook.py]`) → 공유 도구 서버
  `POST /t/<priv|ro token>/gate` → decide(..., runtime_guard=True). 어댑터가 `-c hooks.PreToolUse=...`로 배선한다.
- GrokApprovalWiring: Grok ACP `session/request_permission` → 세션 권한 맵(session_id → privileged) →
  decide(..., runtime_guard=True) → allow/reject 옵션 선택. permission 요청 없이 실행이 관측되면 사후 tripwire.

이 모듈은 런타임 SDK를 import하지 않는다.
"""
import asyncio
import json
import shlex
from abc import ABC, abstractmethod
from collections.abc import Callable

from core.llm_errors import StartupError
from core.observability import emit, format_gate_decision
from core.runtimes import process
from core.runtimes.tool_names import normalize_grok_tool_call
from core.safety_gate import (
    UNKNOWN_TOOL,
    _MUTATION_MCP_TOOLS,
    _MUTATION_TOOL_MATCHER,
    _UNATTENDED_DENIED_TOOLS,
    _UNATTENDED_TOOL_MATCHER,
    CanonicalToolCall,
    decide,
)


class GateWiring(ABC):
    """런타임의 도구 실행 직전 신호를 decide()로 연결하는 배선."""

    name: str
    runtime_guard: bool

    @abstractmethod
    async def probe(self) -> None:
        """기동 시 wiring 진입점이 ro=deny / priv=allow로 동작하는지 확인. 불일치 = StartupError."""
        ...


def _cap(privileged: bool) -> str:
    return "priv" if privileged else "ro"


class ClaudeHookWiring(GateWiring):
    """Claude PreToolUse 훅 — matcher[0] Bash+파일-쓰기, matcher[1] schedule/memory mutation MCP."""

    name = "claude-hook"
    backend = "claude"
    runtime_guard = False
    # HookMatcher 정규식 (순서 = options.hooks["PreToolUse"] 인덱스).
    matchers = (_UNATTENDED_TOOL_MATCHER, _MUTATION_TOOL_MATCHER)

    def make_hook(self, approve_skill_writes, via: str = "hook") -> Callable:
        """PreToolUse 훅 콜백 (input_data, tool_use_id, context) → {} (allow) 또는 deny hookSpecificOutput.

        can_use_tool 콜백은 streaming(AsyncIterable) prompt를 요구하지만 어댑터는 문자열 prompt
        경로를 쓴다 (SDK가 문자열 prompt + can_use_tool 조합에 ValueError). PreToolUse 훅은 문자열
        prompt에서도 컨트롤 프로토콜로 발화하며 bypassPermissions는 권한 프롬프트만 우회할 뿐
        훅 발화를 막지 않으므로, 게이트는 훅으로 강제한다. 권한 라우팅 키 = approve_skill_writes is True.
        """
        privileged = approve_skill_writes is True

        async def _guard(input_data, tool_use_id, context):
            call = CanonicalToolCall.from_claude(input_data)
            allow, reason = decide(call, privileged, runtime_guard=self.runtime_guard)
            emit(format_gate_decision(self.backend, _cap(privileged), call.name, allow, via, reason))
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

    async def probe(self) -> None:
        """훅 콜백에 합성 Bash 호출: approve=False → deny dict, approve=True → {} 여야 한다."""
        probe_input = {"tool_name": "Bash", "tool_input": {"command": "true"}}
        for approve in (False, True):
            out = await self.make_hook(approve, via="probe")(probe_input, None, {})
            allow = out == {}
            if allow != approve:
                raise StartupError(
                    f"Claude 게이트 훅 probe 실패: cap={_cap(approve)} Bash 판정이 {'allow' if allow else 'deny'}",
                    "core/safety_gate.py evaluate_tool_gate·PreToolUse 훅 설정을 확인하세요",
                )


# Codex 훅 매처(정규식) — 셸·패치·파일-쓰기 도구·schedule/memory mutation MCP. 판정은 게이트 엔드포인트의 decide()가 한다.
CODEX_HOOK_MATCHER = (
    "^(Bash|apply_patch|Write|Edit|MultiEdit|NotebookEdit|mcp__schedule__schedule_(create|pause|resume|remove)"
    "|mcp__memory__(add_memory|replace_memory|remove_memory))$"
)
CODEX_HOOK_TIMEOUT_SEC = 10
_PROBE_TIMEOUT = 30.0
# 게이트 엔드포인트가 [gate] via=probe로 기록하도록 표시하는 payload 키 (판정에는 영향 없음).
PROBE_MARKER = "ohrmin_probe"


def toml_string(value: str) -> str:
    """TOML basic string 리터럴 (JSON 문자열 이스케이프는 TOML basic string과 호환)."""
    return json.dumps(value, ensure_ascii=False)


class CodexHookWiring(GateWiring):
    """Codex PreToolUse command 훅 — 스크립트가 OHRMIN_GATE_URL(프로세스별 priv/ro 토큰)로 판정을 위임한다.

    envs = 런타임 프로세스에 넘기는 env 그대로({"priv": env, "ro": env}, OHRMIN_GATE_URL 포함).
    probe는 **런타임에 넘길 것과 동일한 argv/env**로 스크립트를 합성 Bash payload로 실행한다:
    ro → exit 2 + deny JSON, priv → exit 0. 불일치 = StartupError.
    probe는 스크립트·엔드포인트만 증명하며 Codex가 실제로 훅을 호출하는지는 증명하지 못한다(아침 live 확인).
    """

    name = "codex-hook"
    runtime_guard = True
    matcher = CODEX_HOOK_MATCHER

    def __init__(self, hook_argv: list[str], envs: dict, cwd: str | None = None):
        self.hook_argv = list(hook_argv)
        self.envs = envs
        self.cwd = cwd

    def config_override(self) -> str:
        """`codex app-server -c` 값 — hooks.PreToolUse 인라인 TOML.

        # provenance: https://learn.chatgpt.com/docs/hooks.md verified=false (codex.hooks.cli_override)
        """
        return (
            f"hooks.PreToolUse=[{{matcher={toml_string(self.matcher)}, "
            f"hooks=[{{type=\"command\", command={toml_string(shlex.join(self.hook_argv))}, timeout={CODEX_HOOK_TIMEOUT_SEC}}}]}}]"
        )

    async def _run_hook(self, env: dict, payload: dict) -> tuple[int, bytes]:
        proc = await process.spawn(self.hook_argv, env=env, cwd=self.cwd, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")), _PROBE_TIMEOUT
            )
        except asyncio.TimeoutError:
            await process.terminate(proc, timeout=1.0)
            return -1, b""
        return proc.returncode, stdout

    async def probe(self) -> None:
        payload = {
            "session_id": "ohrmin-probe",
            "turn_id": "ohrmin-probe",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
            PROBE_MARKER: True,
        }
        for cap in ("ro", "priv"):
            returncode, stdout = await self._run_hook(self.envs[cap], payload)
            if cap == "priv":
                ok = returncode == 0
            else:
                ok = returncode == 2 and _is_deny_output(stdout)
            if not ok:
                raise StartupError(
                    f"Codex 게이트 훅 probe 실패: cap={cap} Bash 훅 exit={returncode}",
                    "core/hooks/codex_gate_hook.py·공유 도구 서버 게이트 엔드포인트(OHRMIN_GATE_URL)를 확인하세요",
                )


def _is_deny_output(stdout: bytes) -> bool:
    try:
        output = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    specific = output.get("hookSpecificOutput") if isinstance(output, dict) else None
    return isinstance(specific, dict) and specific.get("permissionDecision") == "deny"


# ── Grok (ACP permission 요청) ───────────────────────────────────────

# 사후 tripwire 대상 정규명 — 빌트인 셸·파일 쓰기·확인 불가 도구. mutation MCP는 서버측 capability가 사전 차단한다.
GROK_TRIPWIRE_TOOLS = frozenset({"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", UNKNOWN_TOOL})
# [gate] 로그 대상(게이트 대상 도구) — 그 외 조회 도구의 allow는 기록하지 않는다(Claude/Codex 매처와 같은 범위).
_GROK_GATE_LOG_TOOLS = _UNATTENDED_DENIED_TOOLS | _MUTATION_MCP_TOOLS | {UNKNOWN_TOOL}
# 실행 시작 이후 상태 — 상태 없음 = pending(정상 대기).
_GROK_EXECUTING_STATUSES = frozenset({"in_progress", "completed", "failed"})
# allow는 allow_once만 고른다 — allow_always는 이후 같은 종류 호출을 permission 요청 없이 허용시켜 게이트를 우회할 수 있다.
_GROK_ALLOW_KINDS = ("allow_once",)
_GROK_REJECT_KINDS = ("reject_once", "reject_always")
GROK_NO_ALLOW_ONCE_REASON = "allow_once 옵션이 없어 차단합니다 (allow_always는 선택하지 않습니다)."
_GROK_PROBE_TOOL_CALL_ID = "ohrmin-probe"


def grok_cancelled_outcome() -> dict:
    return {"outcome": {"outcome": "cancelled"}}


class GrokApprovalWiring(GateWiring):
    """Grok ACP permission 핸들러 — `permission_mode="ask"`에서 에이전트가 보내는 session/request_permission에 답한다.

    - 세션 권한 맵: 어댑터가 session/new 직후 register_session(session_id, privileged)(권한 라우팅 키 =
      approve_skill_writes is True). 미등록 세션 = 비특권.
    - 판정: request의 toolCall(같은 toolCallId로 관측된 tool_call/tool_call_update와 병합) → normalize_grok_tool_call
      → decide(..., runtime_guard=True) → allow면 allow_once(allow_always는 고르지 않음 — 없으면 deny 로그 + cancelled),
      deny면 reject_once > reject_always, 해당 종류 옵션이 없으면 cancelled. 응답하지 않으면 실행되지 않는다(구조적 fail-closed).
    - tripwire(사후 봉쇄, 실행 전 강제 아님): 정규명이 GROK_TRIPWIRE_TOOLS이고 decide가 deny이며, 같은 toolCallId의
      permission 요청 없이 status ∈ {in_progress, completed, failed}가 관측될 때만 발동 → [gate] via=tripwire
      executed=likely 로그. session/cancel·GENERIC 턴 결과는 어댑터가 수행한다.
    - probe: 합성 요청(kind=execute, title Bash, 옵션 allow_once/reject_once)을 ro 세션 → reject_once,
      priv 세션 → allow_once. probe는 핸들러 매핑만 증명하며 Grok이 실제로 permission을 요청하는지는 증명하지 못한다.

    # provenance: https://agentclientprotocol.com/protocol/schema verified=false (grok.acp.permission_requests · grok.tool_call_shape)
    """

    name = "grok-approval"
    backend = "grok"
    runtime_guard = True

    def __init__(self):
        self._privileged: dict = {}  # ACP session id → privileged
        self._tool_calls: dict = {}  # (session id, toolCallId) → {"call": 병합 dict, "requested": bool, "tripped": bool}

    def register_session(self, session_id, privileged) -> None:
        self._privileged[session_id] = privileged is True

    def unregister_session(self, session_id) -> None:
        self._privileged.pop(session_id, None)
        self.forget_tool_calls(session_id)

    def forget_tool_calls(self, session_id) -> None:
        for key in [key for key in self._tool_calls if key[0] == session_id]:
            del self._tool_calls[key]

    def is_privileged(self, session_id) -> bool:
        return self._privileged.get(session_id) is True

    def _entry(self, session_id, update, merge: bool = True) -> dict:
        update = update if isinstance(update, dict) else {}
        entry = self._tool_calls.setdefault(
            (session_id, update.get("toolCallId")), {"call": {}, "requested": False, "tripped": False}
        )
        if merge:
            entry["call"].update({k: v for k, v in update.items() if v is not None and k != "sessionUpdate"})
        return entry

    def observe_tool_call(self, session_id, update) -> dict:
        """tool_call / tool_call_update 관측 → 같은 toolCallId로 병합된 tool call.

        어댑터는 알림 수신 즉시(read-loop 순서) 호출해야 한다 — 뒤이은 permission 요청이 병합된 kind·locations·rawInput을
        보도록. 지연 소비 경로에서 다시 병합하면 이후 update를 과거 값으로 되돌릴 수 있으므로 merged_tool_call로 읽는다.
        """
        return self._entry(session_id, update)["call"]

    def merged_tool_call(self, session_id, tool_call_id) -> dict:
        """이미 관측·병합된 tool call (병합하지 않음)."""
        return self._entry(session_id, {"toolCallId": tool_call_id}, merge=False)["call"]

    def permission_response(self, params, cancelled: bool = False, via: str = "approval") -> dict:
        """session/request_permission 응답(RequestPermissionResponse). cancelled=True(턴 취소·종료) = 판정 없이 cancelled."""
        params = params if isinstance(params, dict) else {}
        session_id = params.get("sessionId")
        entry = self._entry(session_id, params.get("toolCall"))
        entry["requested"] = True
        if cancelled:
            return grok_cancelled_outcome()
        privileged = self.is_privileged(session_id)
        call = normalize_grok_tool_call(entry["call"])
        allow, reason = decide(call, privileged, runtime_guard=self.runtime_guard)
        options = [option for option in params.get("options") or [] if isinstance(option, dict)]
        option_id = None
        for kind in _GROK_ALLOW_KINDS if allow else _GROK_REJECT_KINDS:
            option_id = next(
                (o["optionId"] for o in options if o.get("kind") == kind and o.get("optionId") is not None), None
            )
            if option_id is not None:
                break
        if allow and option_id is None:
            allow, reason = False, GROK_NO_ALLOW_ONCE_REASON
        if not allow or call.name in _GROK_GATE_LOG_TOOLS:
            emit(format_gate_decision(self.backend, _cap(privileged), call.name, allow, via, reason))
        if option_id is None:
            return grok_cancelled_outcome()
        return {"outcome": {"outcome": "selected", "optionId": option_id}}

    def tripwire(self, session_id, update) -> bool:
        """permission 요청 없이 게이트 deny 대상 도구의 실행이 관측됐는지 (발동 시 [gate] via=tripwire 로그, toolCallId당 1회)."""
        update = update if isinstance(update, dict) else {}
        entry = self._entry(session_id, update, merge=False)  # 병합은 observe_tool_call(read-loop 순서)이 한다
        if update.get("status") not in _GROK_EXECUTING_STATUSES or entry["requested"] or entry["tripped"]:
            return False
        call = normalize_grok_tool_call(entry["call"])
        if call.name not in GROK_TRIPWIRE_TOOLS:
            return False
        privileged = self.is_privileged(session_id)
        allow, reason = decide(call, privileged, runtime_guard=self.runtime_guard)
        if allow:
            return False
        entry["tripped"] = True
        emit(format_gate_decision(
            self.backend, _cap(privileged), call.name, False, "tripwire", reason, executed_likely=True
        ))
        return True

    async def probe(self) -> None:
        options = [
            {"optionId": "ohrmin-probe-allow", "name": "Allow once", "kind": "allow_once"},
            {"optionId": "ohrmin-probe-reject", "name": "Reject once", "kind": "reject_once"},
        ]
        for privileged, expected in ((False, "ohrmin-probe-reject"), (True, "ohrmin-probe-allow")):
            session_id = f"ohrmin-probe-{_cap(privileged)}"
            self.register_session(session_id, privileged)
            try:
                response = self.permission_response({
                    "sessionId": session_id,
                    "toolCall": {
                        "toolCallId": _GROK_PROBE_TOOL_CALL_ID, "title": "Bash", "kind": "execute",
                        "status": "pending", "rawInput": {"command": "true"},
                    },
                    "options": options,
                }, via="probe")
            finally:
                self.unregister_session(session_id)
            outcome = response.get("outcome") or {}
            if outcome.get("outcome") != "selected" or outcome.get("optionId") != expected:
                raise StartupError(
                    f"Grok 게이트 permission probe 실패: cap={_cap(privileged)} Bash 응답이 {outcome}",
                    "core/safety_gate.py decide·GrokApprovalWiring permission 핸들러를 확인하세요",
                )
