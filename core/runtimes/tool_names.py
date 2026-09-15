"""정규 도구 어휘 ↔ 백엔드 표기 변환 — 어댑터 경계에서만 쓴다.

정규 어휘 = Claude 명명(Bash·Read·Write·Edit·MultiEdit·NotebookEdit·Glob·Grep·Skill·WebSearch·WebFetch·
mcp__<server>__<tool>). 봇 코드(UNATTENDED_ALLOWED_TOOLS·map_tool_status·게이트 집합)는 이 어휘만 쓴다.

- Claude: 항등. skills registry 모드에서만 on_tool의 mcp__skills__* → Skill, allowed_tools의 Skill → mcp__skills.
- Codex: 훅 payload → CanonicalToolCall(apply_patch 경로 전부 추출, 실패 fail-closed), item 이벤트 → on_tool 이름.
- Grok: ACP tool_call → CanonicalToolCall, 프롬프트/스킬 본문의 mcp__<s>__<t> → <s>__<t> 렌더.

Codex/Grok 표면은 문서 기반(live 미확인).
# provenance: https://learn.chatgpt.com/docs/hooks.md verified=false
# provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false
# provenance: https://agentclientprotocol.com/protocol/schema verified=false
# provenance: https://docs.x.ai/build/features/mcp-servers verified=false
"""
import re

from core.safety_gate import UNKNOWN_TOOL, CanonicalToolCall

SKILL_TOOL = "Skill"
SKILLS_SERVER = "skills"
_SKILLS_PREFIX = f"mcp__{SKILLS_SERVER}__"
# Claude registry 모드 allowed_tools 항목 (skills MCP 서버 전체).
SKILLS_ALLOWED_TOOL = f"mcp__{SKILLS_SERVER}"


def on_tool_name(canonical: str, skills_registry: bool) -> str:
    """on_tool에 넘길 이름 — registry 모드면 skills MCP 도구를 Skill로 표기(map_tool_status 호환)."""
    if skills_registry and canonical.startswith(_SKILLS_PREFIX):
        return SKILL_TOOL
    return canonical


# ── Codex ────────────────────────────────────────────────────────────

_PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)


def extract_patch_paths(patch_text: str) -> list[str]:
    """apply_patch 본문의 Add/Update/Delete File·Move to 헤더 경로 전부 (등장 순서)."""
    return [m.group(1) for m in _PATCH_PATH_RE.finditer(patch_text or "")]


def _patch_text(tool_input) -> str:
    """apply_patch 입력에서 패치 본문 — input|patch|command 중 첫 문자열(문자열 리스트는 줄 결합)."""
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return ""
    for key in ("input", "patch", "command"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return "\n".join(value)
    return ""


def normalize_codex_hook_payload(payload: dict) -> CanonicalToolCall:
    """Codex PreToolUse 훅 payload → 정규 호출. tool_name/toolName, tool_input/toolInput 모두 수용.

    - Bash → Bash(command)
    - apply_patch → Write(패치 헤더 경로 전부). 경로 0개 = unresolved_paths(특권이어도 deny)
    - mcp__<s>__<t> → 동일
    - 그 외 → 이름 그대로(file_path가 있으면 경로 포함, evaluate_tool_gate 규칙 적용)
    - 도구명 없음(payload 형태 불일치) → Unknown(무인 턴 deny, fail-closed)

    # provenance: https://learn.chatgpt.com/docs/hooks.md verified=false (codex.hooks.payload_shape)
    """
    payload = payload if isinstance(payload, dict) else {}
    name = payload.get("tool_name") or payload.get("toolName") or UNKNOWN_TOOL
    tool_input = payload.get("tool_input")
    if tool_input is None:
        tool_input = payload.get("toolInput")
    if name == "apply_patch":
        paths = extract_patch_paths(_patch_text(tool_input))
        return CanonicalToolCall("Write", file_paths=paths, unresolved_paths=not paths)
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    command = tool_input.get("command")
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    if name == "Bash":
        return CanonicalToolCall("Bash", command=command if isinstance(command, str) else None)
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    return CanonicalToolCall(name, file_paths=[file_path] if isinstance(file_path, str) and file_path else [])


_CODEX_ITEM_TOOLS = {"commandExecution": "Bash", "fileChange": "Write", "webSearch": "WebSearch"}
# commandExecution의 파싱된 명령 종류(commandActions[].type) → 정규 읽기 도구명 (on_tool 표기 전용, 게이트는 훅의 Bash).
# provenance: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md verified=false (codex.item_types)
_CODEX_COMMAND_ACTION_TOOLS = {"read": "Read", "listFiles": "Glob", "search": "Grep"}


def _codex_command_on_tool_name(item: dict) -> str:
    """셸 명령 item — commandActions가 전부 같은 읽기 종류(read·listFiles·search)면 Read·Glob·Grep, 그 외 Bash."""
    actions = item.get("commandActions")
    if isinstance(actions, list) and actions:
        kinds = {action.get("type") if isinstance(action, dict) else None for action in actions}
        if len(kinds) == 1:
            return _CODEX_COMMAND_ACTION_TOOLS.get(kinds.pop(), "Bash")
    return "Bash"


def codex_item_on_tool_name(item: dict) -> str | None:
    """Codex item 이벤트 → on_tool 이름 (도구 호출이 아닌 item은 None). Codex는 항상 skills registry."""
    item_type = (item or {}).get("type")
    if item_type == "commandExecution":
        return _codex_command_on_tool_name(item)
    if item_type in _CODEX_ITEM_TOOLS:
        return _CODEX_ITEM_TOOLS[item_type]
    if item_type == "mcpToolCall":
        return on_tool_name(f"mcp__{item.get('server', '')}__{item.get('tool', '')}", skills_registry=True)
    return None


# ── Grok ─────────────────────────────────────────────────────────────

_GROK_MCP_TITLE_RE = re.compile(r"^(garmin|body_metrics|memory|schedule|session_search|skills)__([A-Za-z0-9_-]+)$")
_GROK_WRITE_KINDS = ("edit", "delete", "move")
_GROK_READ_KINDS = {"read": "Read", "search": "Grep", "fetch": "WebFetch", "think": "Think"}


def normalize_grok_tool_call(tool_call: dict) -> CanonicalToolCall:
    """Grok ACP tool_call → 정규 호출.

    게이트 대상 kind(execute·edit·delete·move)를 먼저 판정하고, 그다음 봇 MCP 도구 title,
    읽기 kind(read·search·fetch·think), 나머지는 Unknown(무인 턴 deny).
    - edit·delete·move인데 경로(locations[].path ∪ rawInput path|file_path)가 없으면 unresolved_paths
      (Codex apply_patch 경로 추출 실패와 동일 — 특권이어도 deny).
    - fetch인데 rawInput에 url 없이 query만 있으면 WebSearch(웹 검색), 그 외 fetch는 WebFetch.
    """
    tool_call = tool_call if isinstance(tool_call, dict) else {}
    kind = tool_call.get("kind")
    raw = tool_call.get("rawInput") if isinstance(tool_call.get("rawInput"), dict) else {}
    if kind == "execute":
        command = raw.get("command")
        return CanonicalToolCall("Bash", command=command if isinstance(command, str) else None)
    if kind in _GROK_WRITE_KINDS:
        paths = []
        for location in tool_call.get("locations") or []:
            path = location.get("path") if isinstance(location, dict) else None
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
        for key in ("path", "file_path"):
            path = raw.get(key)
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
        return CanonicalToolCall("Write", file_paths=paths, unresolved_paths=not paths)
    match = _GROK_MCP_TITLE_RE.match(tool_call.get("title") or "")
    if match:
        return CanonicalToolCall(f"mcp__{match.group(1)}__{match.group(2)}")
    if kind == "fetch" and isinstance(raw.get("query"), str) and not raw.get("url"):
        return CanonicalToolCall("WebSearch")
    if kind in _GROK_READ_KINDS:
        return CanonicalToolCall(_GROK_READ_KINDS[kind])
    return CanonicalToolCall(UNKNOWN_TOOL)


_MCP_REF_RE = re.compile(r"(?<![A-Za-z0-9_])mcp__([A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*)__([A-Za-z0-9_-]+)")


def render_tool_refs(text: str, backend: str) -> str:
    """런타임에 넘기는 텍스트의 도구 표기 렌더 — Grok만 mcp__<s>__<t> → <s>__<t> (저장 데이터는 불변)."""
    if backend != "grok" or not text:
        return text
    return _MCP_REF_RE.sub(r"\1__\2", text)


# ── 능력 매트릭스 · 도구 부록 · allowed_tools 번역 ─────────────────────

SUPPORTED = "supported"      # 사용 가능, 게이트 허용
GATED = "gated"              # 런타임에 있으나 게이트가 차단
UNSUPPORTED = "unsupported"  # 이 백엔드·권한에서 수단 없음


def _row(priv: str, ro: str) -> dict:
    return {"priv": priv, "ro": ro}


_WRITE_ROW = {"claude": _row(SUPPORTED, GATED), "codex": _row(SUPPORTED, GATED), "grok": _row(SUPPORTED, GATED)}
_READ_ROW = {"claude": _row(SUPPORTED, SUPPORTED), "codex": _row(SUPPORTED, SUPPORTED), "grok": _row(SUPPORTED, SUPPORTED)}
# Codex는 Read/Glob/Grep을 셸로 수행 → 무인(ro) 턴은 Bash 게이트 deny로 사용 불가.
_CODEX_SHELL_READ_ROW = {"claude": _row(SUPPORTED, SUPPORTED), "codex": _row(SUPPORTED, UNSUPPORTED), "grok": _row(SUPPORTED, SUPPORTED)}

# 정규 도구 × 백엔드 × priv/ro. mcp__* = 조회용 MCP 도구 전부, mutation MCP 7종은 개별 행.
CAPABILITY_MATRIX = {
    "Bash": _WRITE_ROW,
    "Read": _CODEX_SHELL_READ_ROW,
    "Write": _WRITE_ROW,
    "Edit": _WRITE_ROW,
    "MultiEdit": _WRITE_ROW,
    "NotebookEdit": _WRITE_ROW,
    "Glob": _CODEX_SHELL_READ_ROW,
    "Grep": _CODEX_SHELL_READ_ROW,
    "Skill": _READ_ROW,
    "WebSearch": _READ_ROW,
    "WebFetch": {"claude": _row(SUPPORTED, SUPPORTED), "codex": _row(UNSUPPORTED, UNSUPPORTED), "grok": _row(SUPPORTED, SUPPORTED)},
    "mcp__*": _READ_ROW,
    "mcp__memory__add_memory": _WRITE_ROW,
    "mcp__memory__remove_memory": _WRITE_ROW,
    "mcp__memory__replace_memory": _WRITE_ROW,
    "mcp__schedule__schedule_create": _WRITE_ROW,
    "mcp__schedule__schedule_pause": _WRITE_ROW,
    "mcp__schedule__schedule_remove": _WRITE_ROW,
    "mcp__schedule__schedule_resume": _WRITE_ROW,
}


def backend_tool_note(backend: str) -> str:
    """codex/grok system prompt 말미 부록 — 미지원 도구 안내(헛시도 방지). Claude는 "".

    어댑터가 `system + "\\n\\n" + note` 형태로 붙인다(빈 문자열이면 생략).
    """
    if backend == "claude":
        return ""
    missing = [t for t, row in CAPABILITY_MATRIX.items() if row[backend]["priv"] == UNSUPPORTED]
    ro_missing = [
        t for t, row in CAPABILITY_MATRIX.items()
        if row[backend]["priv"] != UNSUPPORTED and row[backend]["ro"] == UNSUPPORTED
    ]
    sentences = []
    if missing:
        sentences.append(f"이 환경에는 {'/'.join(missing)}가 없습니다.")
    if ro_missing:
        sentences.append(f"{'/'.join(ro_missing)}은 셸로 수행되며 읽기 전용 턴에서는 사용할 수 없습니다.")
    return " ".join(sentences)


def translate_allowed_tools(backend: str, canonical: list[str] | None, skills_registry: bool = False) -> list[str] | None:
    """정규 allowed_tools → 백엔드 표기. allowed_tools는 스티어링 전용(F10) — 강제는 wiring+capability+샌드박스.

    claude: 입력 그대로(registry 모드면 Skill → mcp__skills). codex/grok: 적용하지 않음(None).
    """
    if backend != "claude":
        return None
    if not skills_registry or canonical is None:
        return canonical
    return [SKILLS_ALLOWED_TOOL if t == SKILL_TOOL else t for t in canonical]
