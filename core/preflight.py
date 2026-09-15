"""기동 전 LLM 백엔드 인증/설치 검사 — 실패 시 StartupError(cause, fix).

러너(외부 명령 실행)·파일시스템·which는 주입 가능하다(테스트는 fake). 구독 정책: Claude·Codex는
구독 로그인만 허용(API 키 env 거부), Grok만 XAI_API_KEY.
"""
import importlib.util
import json
import os
import shutil
import subprocess

from core.llm_errors import StartupError, forbid_real_runtime

CLAUDE_INSTALL_FIX = "npm install -g @anthropic-ai/claude-code"
CODEX_INSTALL_FIX = "npm install -g @openai/codex"
GROK_INSTALL_FIX = "curl -fsSL https://x.ai/cli/install.sh | bash"

_RUNNER_TIMEOUT = 20


def default_runner(argv: list[str]) -> tuple[int, str]:
    """외부 명령 실행 → (returncode, stdout). 테스트(OHRMIN_FORBID_REAL_RUNTIMES=1)에서는 금지."""
    forbid_real_runtime()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=_RUNNER_TIMEOUT)
    return proc.returncode, proc.stdout


def _is_set(env, name: str) -> bool:
    return bool(str(env.get(name) or "").strip())


def bundled_claude_cli(fs=os.path) -> str | None:
    """claude_agent_sdk 번들 CLI 경로 (SDK와 동일: <패키지>/_bundled/claude). import 없이 탐색."""
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec is None or not spec.submodule_search_locations:
        return None
    path = os.path.join(list(spec.submodule_search_locations)[0], "_bundled", "claude")
    return path if fs.isfile(path) else None


def resolve_claude_cli(fs=os.path, which=shutil.which) -> str | None:
    """SDK와 같은 순서: 번들 CLI → PATH의 claude."""
    return bundled_claude_cli(fs) or which("claude")


def resolve_bin(bin_value: str, fs=os.path, which=shutil.which) -> str | None:
    """경로형(`/`·`~` 포함)은 expanduser 후 파일 존재 확인, 이름형은 PATH 탐색."""
    if "/" in bin_value or bin_value.startswith("~"):
        path = fs.expanduser(bin_value)
        return path if fs.isfile(path) else None
    return which(bin_value)


def _check_claude(env, runner, fs, which) -> None:
    if _is_set(env, "ANTHROPIC_API_KEY"):
        raise StartupError(
            "ANTHROPIC_API_KEY가 설정되어 있습니다 — Claude는 구독 로그인만 허용",
            ".env에서 ANTHROPIC_API_KEY 제거",
        )
    cli = resolve_claude_cli(fs, which)
    if cli is None:
        raise StartupError("Claude CLI를 찾을 수 없습니다", CLAUDE_INSTALL_FIX)
    try:
        returncode, stdout = runner([cli, "auth", "status", "--json"])
    except (OSError, subprocess.SubprocessError) as e:
        raise StartupError(f"claude auth status 실행 실패: {type(e).__name__}", "claude login") from e
    if returncode != 0:
        raise StartupError(f"Claude 로그인 상태를 확인하지 못했습니다 (auth status exit={returncode})", "claude login")
    try:
        status = json.loads(stdout)
    except (TypeError, ValueError) as e:
        raise StartupError("claude auth status 출력(JSON)을 해석하지 못했습니다", "claude login") from e
    if not isinstance(status, dict) or status.get("loggedIn") is not True:
        raise StartupError("Claude에 로그인되어 있지 않습니다", "claude login")
    if status.get("authMethod") != "claude.ai" or status.get("apiProvider") != "firstParty":
        raise StartupError(
            f"구독 로그인 아님: authMethod={status.get('authMethod')} apiProvider={status.get('apiProvider')}",
            "claude login",
        )


def _check_codex(config, env, fs, which) -> None:
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        if _is_set(env, key):
            raise StartupError(
                f"{key}가 설정되어 있습니다 — Codex는 ChatGPT 구독 로그인만 허용",
                f".env에서 {key} 제거",
            )
    if resolve_bin(config.codex["bin"], fs, which) is None:
        raise StartupError(f"codex CLI 설치 필요 (bin={config.codex['bin']})", CODEX_INSTALL_FIX)
    codex_home = str(env.get("CODEX_HOME") or "").strip() or fs.expanduser("~/.codex")
    auth_path = os.path.join(codex_home, "auth.json")
    if not fs.exists(auth_path):
        raise StartupError(f"Codex 로그인 정보가 없습니다 ({auth_path})", "codex login")


def _check_grok(config, env, fs, which) -> None:
    if not _is_set(env, "XAI_API_KEY"):
        raise StartupError("XAI_API_KEY가 설정되지 않았습니다", ".env에 XAI_API_KEY=... 추가")
    if resolve_bin(config.grok["bin"], fs, which) is None:
        raise StartupError(f"Grok CLI를 찾을 수 없습니다 (bin={config.grok['bin']})", GROK_INSTALL_FIX)


def run_preflight(config, env, runner=None, fs=os.path, which=shutil.which) -> None:
    """선택 백엔드의 인증/설치 상태를 검사한다. 실패 시 StartupError."""
    runner = runner or default_runner
    if config.backend == "claude":
        _check_claude(env, runner, fs, which)
    elif config.backend == "codex":
        _check_codex(config, env, fs, which)
    elif config.backend == "grok":
        _check_grok(config, env, fs, which)
    else:
        raise StartupError(f"알 수 없는 backend: {config.backend}", "config.json llm.backend 수정")
