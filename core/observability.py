"""LLM 백엔드 관측 로그 — stdout 한 줄 형식 고정 (기동 요약 · 미검증 표면 WARN · 게이트 결정 · 턴 결과).

형식은 tests/test_observability.py가 capsys로 고정한다. 형식을 바꾸면 운영 grep이 깨지므로 주의.
"""

GATE_WIRING = {"claude": "claude-hook", "codex": "codex-hook", "grok": "grok-approval"}
AUTH_LABEL = {"claude": "claude.ai", "codex": "chatgpt", "grok": "xai-env_key-isolated-home"}

_CODEX_APP_SERVER = "https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md"
_CODEX_HOOKS = "https://learn.chatgpt.com/docs/hooks.md"
_CODEX_APP_SERVER_DOCS = "https://learn.chatgpt.com/docs/app-server"
_ACP_SCHEMA = "https://agentclientprotocol.com/protocol/schema"
_GROK_PERMISSIONS = "https://docs.x.ai/build/features/permissions"

# 문서 기반(live 미확인) 외부 표면 — 선택 백엔드·모드가 사용하는 항목만 기동 시 WARN 1줄씩.
# claude native는 로컬 소스·로컬 실행으로 확인된 표면만 쓰므로 0줄.
UNVERIFIED_SURFACES = {
    "codex": (
        ("codex.thread_start.developerInstructions", _CODEX_APP_SERVER),
        ("codex.hooks.cli_override", _CODEX_HOOKS),
        ("codex.hooks.fires_under_never", _CODEX_HOOKS),
        ("codex.hooks.payload_shape", _CODEX_HOOKS),
        ("codex.item_types", _CODEX_APP_SERVER),
        ("codex.error_info", _CODEX_APP_SERVER),
        ("codex.account_read_shape", _CODEX_APP_SERVER),
        ("codex.mcp.url_override", _CODEX_APP_SERVER_DOCS),
        ("codex.hooks.env_inheritance", _CODEX_HOOKS),
        ("codex.thread_archive", _CODEX_APP_SERVER),
    ),
    "grok": (
        ("grok.acp.mcp_http", _ACP_SCHEMA),
        ("grok.acp.permission_requests", _GROK_PERMISSIONS),
        ("grok.acp.cancel_reprompt", _ACP_SCHEMA),
        (
            "grok.credentials.isolated_home",
            "https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/02-authentication.md",
        ),
        ("grok.tool_call_shape", _ACP_SCHEMA),
        ("grok.error_shape", _ACP_SCHEMA),
        ("grok.image_prompt", _ACP_SCHEMA),
        ("grok.reads_project_claude_files", "https://docs.x.ai/build/features/skills-plugins-marketplaces"),
        ("grok.system_prompt_preamble", "https://docs.x.ai/build/cli/reference"),
        ("grok.acp.session_close", _ACP_SCHEMA),
    ),
    "claude_registry": (
        (
            "claude.skills_filter",
            "https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py",
        ),
    ),
}

# 프로젝트 .claude/settings*.json에 게이트 대상 permissions.allow 규칙이 있을 때만 추가되는 grok 항목.
GROK_PROJECT_CLAUDE_PERMISSIONS = ("grok.project_claude_permissions", _GROK_PERMISSIONS)


def emit(line: str) -> None:
    print(line, flush=True)


def format_startup_summary(backend, model, skills, tools, tool_server=None) -> str:
    """[llm] startup ... — tool_server는 "127.0.0.1:<port>" 또는 None(off)."""
    return (
        f"[llm] startup backend={backend} model={model} skills={skills} "
        f"gate={GATE_WIRING[backend]} tools={tools} tool_server={tool_server or 'off'} "
        f"auth={AUTH_LABEL[backend]}"
    )


def unverified_surfaces(backend, skills="native", project_claude_permissions=False) -> list:
    """선택 백엔드·모드가 사용하는 미검증 표면 (surface_id, source) 목록."""
    if backend == "claude":
        return list(UNVERIFIED_SURFACES["claude_registry"]) if skills == "registry" else []
    surfaces = list(UNVERIFIED_SURFACES.get(backend, ()))
    if backend == "grok" and project_claude_permissions:
        surfaces.append(GROK_PROJECT_CLAUDE_PERMISSIONS)
    return surfaces


def format_unverified_warning(surface_id, source) -> str:
    return f"[llm] WARN UNVERIFIED {surface_id} source={source}"


def emit_unverified_warnings(backend, skills="native", project_claude_permissions=False) -> None:
    for surface_id, source in unverified_surfaces(backend, skills, project_claude_permissions):
        emit(format_unverified_warning(surface_id, source))


def format_gate_decision(backend, cap, tool, allow, via, reason="", executed_likely=False) -> str:
    """[gate] ... — cap = priv|ro, via = hook|approval|capability|tripwire|probe."""
    line = (
        f"[gate] backend={backend} cap={cap} tool={tool} decision={'allow' if allow else 'deny'} "
        f"via={via} reason={reason or '-'}"
    )
    if executed_likely:
        line += " executed=likely"
    return line


def format_turn_result(backend, thread_id, cap, tools, skills_loaded, outcome) -> str:
    """[llm] turn ... — outcome = ok | usage_limit | auth_expired | runtime_unavailable | generic."""
    thread = "-" if thread_id is None else thread_id
    return (
        f"[llm] turn backend={backend} thread={thread} cap={cap} tools={tools} "
        f"skills_loaded={skills_loaded} outcome={outcome}"
    )
