"""LLM 백엔드 설정 — config.json 로드·검증(stdlib) + 기존 LLM_ADAPTER/LLM_MODEL env 이관 규칙.

config.json은 LLM 설정만 담는다(비밀값 없음). 백엔드 전환은 재시작 단위. 검증 실패는
ConfigError(cause, fix) — bot/main.py의 main()이 Discord 토큰 검사 전에 출력 후 exit 1.
"""
import copy
import json
import os
from dataclasses import dataclass

from core.llm_errors import StartupError

BACKENDS = ("claude", "codex", "grok")
CLAUDE_SKILL_MODES = ("native", "registry")
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"

DEFAULT_CONFIG = {
    "llm": {
        "backend": "claude",
        "claude": {"model": None, "skills": "native"},
        "codex": {"model": None, "bin": "codex"},
        "grok": {"model": None, "bin": "~/.grok/bin/grok"},
    }
}

_CONFIG_FIX = "config.json의 llm 설정을 수정하세요 (backend: claude | codex | grok)"


class ConfigError(StartupError):
    """config.json 파싱/스키마 위반 또는 env와의 충돌."""

    def __init__(self, cause: str, fix: str = _CONFIG_FIX):
        super().__init__(cause, fix)


@dataclass(frozen=True)
class LLMConfig:
    backend: str
    claude: dict
    codex: dict
    grok: dict
    # env 이관 규칙까지 적용한 최종 Claude 모델 (항상 non-null).
    claude_model: str
    warnings: tuple = ()

    @property
    def options(self) -> dict:
        """선택 백엔드 섹션."""
        return getattr(self, self.backend)

    @property
    def model(self) -> str | None:
        """선택 백엔드의 최종 모델 (codex는 null이면 런타임 기본값)."""
        if self.backend == "claude":
            return self.claude_model
        return self.options["model"]

    @property
    def skills_mode(self) -> str:
        """스킬 로딩 모드 — codex/grok은 항상 registry(봇 SkillRegistry), claude는 llm.claude.skills."""
        if self.backend == "claude":
            return self.claude["skills"]
        return "registry"


def _env_value(env, name: str) -> str | None:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip()


def _validate_str_or_null(section: str, key: str, value, nullable: bool) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip():
        kind = "문자열 또는 null" if nullable else "비어있지 않은 문자열"
        raise ConfigError(f"config.json llm.{section}.{key}는 {kind}이어야 합니다 (현재: {value!r})")


def _validate(raw) -> dict:
    """원본 JSON → 기본값이 채워진 llm 섹션 dict. 위반 시 ConfigError."""
    if not isinstance(raw, dict):
        raise ConfigError("config.json 최상위는 객체여야 합니다")
    unknown = sorted(set(raw) - {"llm"})
    if unknown:
        raise ConfigError(f"config.json 최상위에 허용되지 않는 키: {', '.join(unknown)} (llm만 허용)")
    llm = raw.get("llm")
    if not isinstance(llm, dict):
        raise ConfigError("config.json에 llm 객체가 필요합니다")
    unknown = sorted(set(llm) - {"backend", *BACKENDS})
    if unknown:
        raise ConfigError(f"config.json llm에 허용되지 않는 키: {', '.join(unknown)}")
    backend = llm.get("backend")
    if backend not in BACKENDS:
        raise ConfigError(f"config.json llm.backend 값이 잘못됐습니다: {backend!r} (claude | codex | grok)")

    merged = copy.deepcopy(DEFAULT_CONFIG["llm"])
    merged["backend"] = backend
    for section in BACKENDS:
        if section not in llm:
            continue
        value = llm[section]
        if not isinstance(value, dict):
            raise ConfigError(f"config.json llm.{section}는 객체여야 합니다")
        unknown = sorted(set(value) - set(DEFAULT_CONFIG["llm"][section]))
        if unknown:
            raise ConfigError(f"config.json llm.{section}에 허용되지 않는 키: {', '.join(unknown)}")
        merged[section].update(value)

    for section in BACKENDS:
        _validate_str_or_null(section, "model", merged[section]["model"], nullable=True)
    for section in ("codex", "grok"):
        _validate_str_or_null(section, "bin", merged[section]["bin"], nullable=False)
    if merged["claude"]["skills"] not in CLAUDE_SKILL_MODES:
        raise ConfigError(
            f"config.json llm.claude.skills 값이 잘못됐습니다: {merged['claude']['skills']!r} (native | registry)"
        )
    if backend == "grok" and merged["grok"]["model"] is None:
        raise ConfigError(
            "backend=grok에는 config.json llm.grok.model이 필요합니다",
            fix="config.json llm.grok.model에 사용할 Grok 모델명을 지정하세요",
        )
    return merged


def load_llm_config(path: str, env) -> LLMConfig:
    """config.json 로드 + 검증 + env 이관 규칙 적용. 실패 시 ConfigError.

    env 이관(OQ-2): Claude 모델 = claude.model(non-null) > LLM_MODEL(deprecated WARN) > 기본값.
    claude.model·LLM_MODEL이 둘 다 있고 다르면 ConfigError. LLM_ADAPTER가 backend와 다르면
    ConfigError, 같으면 deprecated WARN. backend≠claude에서 LLM_MODEL은 무시 WARN.
    """
    warnings = []
    if not os.path.exists(path):
        llm = copy.deepcopy(DEFAULT_CONFIG["llm"])
        warnings.append(f"config.json 없음({path}) — 기본값(backend=claude) 사용")
    else:
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(
                f"config.json JSON 파싱 실패: {e.msg} (line {e.lineno}, col {e.colno})",
                fix="config.json 문법(JSON)을 수정하세요",
            ) from e
        except OSError as e:
            raise ConfigError(f"config.json 읽기 실패: {e}", fix="config.json 파일 권한/경로를 확인하세요") from e
        llm = _validate(raw)

    backend = llm["backend"]

    env_adapter = _env_value(env, "LLM_ADAPTER")
    if env_adapter is not None:
        if env_adapter != backend:
            raise ConfigError(
                f".env LLM_ADAPTER({env_adapter})가 config.json llm.backend({backend})와 다릅니다",
                fix=".env에서 LLM_ADAPTER를 제거하세요 (백엔드 선택은 config.json llm.backend)",
            )
        warnings.append("LLM_ADAPTER는 deprecated — .env에서 제거하세요 (백엔드 선택은 config.json llm.backend)")

    env_model = _env_value(env, "LLM_MODEL")
    config_model = llm["claude"]["model"]
    claude_model = config_model or DEFAULT_CLAUDE_MODEL
    if env_model is not None:
        if backend != "claude":
            warnings.append(f"LLM_MODEL은 backend={backend}에서 무시됩니다 — .env에서 제거하세요")
        elif config_model is not None and config_model != env_model:
            raise ConfigError(
                f"config.json llm.claude.model({config_model})과 .env LLM_MODEL({env_model})이 다릅니다",
                fix=".env에서 LLM_MODEL을 제거하세요 (모델은 config.json llm.claude.model)",
            )
        else:
            claude_model = config_model or env_model
            warnings.append("LLM_MODEL은 deprecated — config.json llm.claude.model로 옮기세요")

    return LLMConfig(
        backend=backend,
        claude=llm["claude"],
        codex=llm["codex"],
        grok=llm["grok"],
        claude_model=claude_model,
        warnings=tuple(warnings),
    )


def load_llm_config_safe(path: str, env) -> tuple[LLMConfig | None, ConfigError | None]:
    """import 시점용 — 예외 대신 (config, error)를 반환하고 WARN을 출력한다 (exit 안 함)."""
    try:
        config = load_llm_config(path, env)
    except ConfigError as e:
        return None, e
    for w in config.warnings:
        print(f"[llm] WARN {w}")
    return config, None
