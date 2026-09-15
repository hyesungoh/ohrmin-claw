"""안전 게이트 — 백엔드 중립 순수 판정 (skill-write · 무인 읽기 전용 · mutation MCP) + 단일 판정 진입점 decide().

규칙 함수(evaluate_skill_write_gate·evaluate_tool_gate)와 상수는 core/llm.py에서 동작 그대로 옮겨왔고
core/llm.py가 re-export한다. 이 모듈은 런타임 SDK를 import하지 않는다(Codex/Grok HTTP 경로에서도 사용).

판정 입력은 정규 도구 어휘(Claude 명명: Bash·Write·Edit·…·mcp__<server>__<tool>)로 정규화된
CanonicalToolCall이다. 백엔드별 wiring(Claude 훅·Codex 훅 엔드포인트·Grok 승인)이 모두 decide()를 호출한다.
"""
import os
from dataclasses import dataclass, field

# skill-write 게이트가 감시하는 파일 쓰기 도구. matcher 문자열과 정렬 유지.
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


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


# ── 정규 도구 호출 + 단일 판정 진입점 ────────────────────────────────

# 정규화가 도구 종류를 확정하지 못한 호출(Grok kind=other 등). 무인 턴 차단, 특권 턴 허용.
UNKNOWN_TOOL = "Unknown"

RUNTIME_GUARD_REASON = "런타임이 .claude/ 경로 쓰기를 차단합니다 — .agent-made/<이름>/에 쓰세요."
UNRESOLVED_PATHS_REASON = "패치 경로를 확인할 수 없어 차단합니다."
UNKNOWN_TOOL_REASON = "확인할 수 없는 도구는 무인 턴에서 차단합니다."


@dataclass(frozen=True)
class CanonicalToolCall:
    """정규 도구 어휘로 정규화된 도구 호출.

    - name: 정규 도구명 (Bash, Write, …, mcp__<server>__<tool>, Unknown)
    - file_paths: 쓰기 대상 경로 전부 (다중 경로 패치 포함)
    - command: 셸 명령 문자열 (판정에는 쓰지 않음 — 로그/디버깅용)
    - unresolved_paths: 쓰기 호출인데 대상 경로를 추출하지 못함 → fail-closed
    """

    name: str
    file_paths: list[str] = field(default_factory=list)
    command: str | None = None
    unresolved_paths: bool = False

    @classmethod
    def from_claude(cls, input_data: dict | None) -> "CanonicalToolCall":
        """Claude PreToolUse 훅 입력({"tool_name", "tool_input"}) → 정규 호출 (Claude 명명은 항등)."""
        input_data = input_data or {}
        tool_input = input_data.get("tool_input") or {}
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        command = tool_input.get("command")
        return cls(
            name=input_data.get("tool_name", ""),
            file_paths=[file_path] if file_path else [],
            command=command if isinstance(command, str) else None,
        )


def decide(call: CanonicalToolCall, privileged: bool, runtime_guard: bool) -> tuple[bool, str]:
    """단일 게이트 판정 → (allow, reason).

    1. 경로 추출 실패(unresolved_paths), 또는 runtime_guard=True(Codex/Grok)인데 파일-쓰기 도구의 file_paths가 빔
       → deny (특권이어도 `.claude` 판정 불가).
    2. file_paths가 비면 evaluate_tool_gate(name, {}, privileged), 있으면 경로별 판정 — 하나라도 deny면 그 결과.
    3. Unknown 도구는 무인(비특권) 턴 deny.
    4. runtime_guard=True(Codex/Grok)이고 파일-쓰기 도구의 경로 세그먼트에 `.claude`가 있으면 deny
       (Claude CLI의 `.claude/` 구조화 쓰기 가드 에뮬레이션). Claude wiring은 False — CLI가 네이티브로 차단.
    """
    if call.unresolved_paths or (runtime_guard and call.name in _WRITE_TOOLS and not call.file_paths):
        return False, UNRESOLVED_PATHS_REASON
    if not call.file_paths:
        allow, reason = evaluate_tool_gate(call.name, {}, privileged)
        if not allow:
            return allow, reason
    for path in call.file_paths:
        allow, reason = evaluate_tool_gate(call.name, {"file_path": path}, privileged)
        if not allow:
            return allow, reason
    if call.name == UNKNOWN_TOOL and not privileged:
        return False, UNKNOWN_TOOL_REASON
    if runtime_guard and call.name in _WRITE_TOOLS:
        if any(".claude" in _skill_path_segments(path) for path in call.file_paths):
            return False, RUNTIME_GUARD_REASON
    return True, ""
