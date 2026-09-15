"""config.json 로드·검증(AC-1) + LLM_ADAPTER/LLM_MODEL env 이관 규칙(AC-5) + 팩토리 테스트."""
import json
import os

import pytest

from core.llm import ClaudeSDKAdapter, create_llm_adapter_from_config
from core.llm_config import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CONFIG,
    ConfigError,
    load_llm_config,
    load_llm_config_safe,
)
from core.llm_errors import StartupError

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(tmp_path, data) -> str:
    path = tmp_path / "config.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def _llm(**overrides) -> dict:
    llm = json.loads(json.dumps(DEFAULT_CONFIG["llm"]))
    llm.update(overrides)
    return {"llm": llm}


# ── AC-1: 기본/부재/정상 ─────────────────────────────────────────────


class TestLoadConfig:
    def test_committed_config_json_shape(self):
        path = os.path.join(PROJECT_ROOT, "config.json")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        assert set(raw["llm"]) == set(DEFAULT_CONFIG["llm"])
        assert raw["llm"]["backend"] == "claude"
        assert raw["llm"]["claude"]["model"] is None or isinstance(raw["llm"]["claude"]["model"], str)
        assert raw["llm"]["claude"]["skills"] == "native"

    def test_committed_config_loads_as_claude(self):
        path = os.path.join(PROJECT_ROOT, "config.json")
        with open(path, encoding="utf-8") as f:
            committed_model = json.load(f)["llm"]["claude"]["model"]
        config = load_llm_config(path, {})
        assert config.backend == "claude"
        assert config.model == (committed_model or DEFAULT_CLAUDE_MODEL)
        assert config.options == {"model": committed_model, "skills": "native"}
        assert config.warnings == ()

    def test_missing_file_uses_defaults_with_warning(self, tmp_path):
        config = load_llm_config(str(tmp_path / "nope.json"), {})
        assert config.backend == "claude"
        assert config.model == DEFAULT_CLAUDE_MODEL
        assert len(config.warnings) == 1
        assert "config.json 없음" in config.warnings[0]

    def test_partial_sections_filled_with_defaults(self, tmp_path):
        config = load_llm_config(_write(tmp_path, {"llm": {"backend": "codex"}}), {})
        assert config.backend == "codex"
        assert config.model is None
        assert config.codex == {"model": None, "bin": "codex"}
        assert config.grok == {"model": None, "bin": "~/.grok/bin/grok"}

    def test_codex_backend_with_model(self, tmp_path):
        data = _llm(backend="codex", codex={"model": "gpt-5-codex", "bin": "/opt/codex"})
        config = load_llm_config(_write(tmp_path, data), {})
        assert (config.backend, config.model, config.options["bin"]) == ("codex", "gpt-5-codex", "/opt/codex")

    def test_grok_backend_with_model(self, tmp_path):
        data = _llm(backend="grok", grok={"model": "grok-code-fast-1", "bin": "~/.grok/bin/grok"})
        config = load_llm_config(_write(tmp_path, data), {})
        assert (config.backend, config.model) == ("grok", "grok-code-fast-1")

    def test_claude_model_and_registry_mode(self, tmp_path):
        data = _llm(claude={"model": "claude-opus-4-8", "skills": "registry"})
        config = load_llm_config(_write(tmp_path, data), {})
        assert config.model == "claude-opus-4-8"
        assert config.claude["skills"] == "registry"


class TestFactory:
    def test_factory_returns_claude_adapter_with_resolved_model(self, tmp_path):
        config = load_llm_config(_write(tmp_path, _llm()), {"LLM_MODEL": "claude-opus-4-8"})
        servers = {"garmin": {"type": "sdk", "name": "garmin", "instance": None}}
        adapter = create_llm_adapter_from_config(config, mcp_servers=servers, cwd="/proj")
        assert isinstance(adapter, ClaudeSDKAdapter)
        assert adapter.model == "claude-opus-4-8"
        assert adapter.mcp_servers is servers
        assert adapter.cwd == "/proj"

    def test_factory_default_model(self, tmp_path):
        adapter = create_llm_adapter_from_config(load_llm_config(_write(tmp_path, _llm()), {}))
        assert isinstance(adapter, ClaudeSDKAdapter)
        assert adapter.model == DEFAULT_CLAUDE_MODEL

    def test_factory_codex_returns_codex_adapter(self, tmp_path):
        from core.runtimes.codex_adapter import CodexAdapter

        data = _llm(backend="codex", codex={"model": "gpt-5-codex", "bin": "/opt/codex"})
        config = load_llm_config(_write(tmp_path, data), {})
        shared, specs = object(), []
        adapter = create_llm_adapter_from_config(config, server_specs=specs, cwd="/proj", tool_server=shared)
        assert isinstance(adapter, CodexAdapter) and adapter.backend_id == "codex"
        assert (adapter.model, adapter.bin, adapter.cwd) == ("gpt-5-codex", "/opt/codex", "/proj")
        assert adapter.tool_server is shared and adapter.server_specs is specs

    def test_factory_codex_without_tool_server_is_startup_error(self, tmp_path):
        config = load_llm_config(_write(tmp_path, _llm(backend="codex")), {})
        with pytest.raises(StartupError) as exc:
            create_llm_adapter_from_config(config)
        assert "codex" in exc.value.cause

    def test_factory_grok_returns_grok_adapter(self, tmp_path):
        """P6 — grok 분기(지연 import): 공유 도구 서버·ServerSpec·cwd·model·bin을 그대로 배선."""
        from core.runtimes.grok_adapter import GrokAdapter

        data = _llm(backend="grok", grok={"model": "grok-x", "bin": "~/.grok/bin/grok"})
        config = load_llm_config(_write(tmp_path, data), {})
        shared, specs = object(), []
        adapter = create_llm_adapter_from_config(config, server_specs=specs, cwd="/proj", tool_server=shared)
        assert isinstance(adapter, GrokAdapter) and adapter.backend_id == "grok"
        assert (adapter.model, adapter.bin, adapter.cwd) == ("grok-x", "~/.grok/bin/grok", "/proj")
        assert adapter.tool_server is shared and adapter.server_specs is specs

    @pytest.mark.parametrize("backend", ["grok"])
    def test_factory_grok_without_tool_server_is_startup_error(self, tmp_path, backend):
        data = _llm(backend=backend, grok={"model": "grok-x", "bin": "~/.grok/bin/grok"})
        config = load_llm_config(_write(tmp_path, data), {})
        with pytest.raises(StartupError) as exc:
            create_llm_adapter_from_config(config)
        assert backend in exc.value.cause

    def test_factory_unknown_backend_is_startup_error(self):
        from types import SimpleNamespace

        with pytest.raises(StartupError) as exc:
            create_llm_adapter_from_config(SimpleNamespace(backend="gemini"))
        assert "gemini" in exc.value.cause
        assert "claude" in exc.value.fix


# ── 스키마 위반 ──────────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize("data, needle", [
        ({"llm": {"backend": "gemini"}}, "llm.backend"),
        ({"llm": {}}, "llm.backend"),
        ({"llm": {"backend": "claude"}, "discord": {}}, "최상위"),
        ({"llm": {"backend": "claude", "fallback": "codex"}}, "fallback"),
        ({"llm": {"backend": "claude", "claude": {"model": None, "base_url": "x"}}}, "base_url"),
        ({"llm": {"backend": "claude", "claude": {"model": 3}}}, "llm.claude.model"),
        ({"llm": {"backend": "claude", "claude": {"model": ""}}}, "llm.claude.model"),
        ({"llm": {"backend": "claude", "claude": {"skills": "auto"}}}, "llm.claude.skills"),
        ({"llm": {"backend": "codex", "codex": {"bin": None}}}, "llm.codex.bin"),
        ({"llm": {"backend": "claude", "grok": "grok-4"}}, "llm.grok"),
        ({"llm": "claude"}, "llm 객체"),
        (["llm"], "최상위"),
    ])
    def test_schema_violations(self, tmp_path, data, needle):
        with pytest.raises(ConfigError) as exc:
            load_llm_config(_write(tmp_path, data), {})
        assert needle in exc.value.cause
        assert exc.value.fix

    def test_grok_requires_model(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            load_llm_config(_write(tmp_path, {"llm": {"backend": "grok"}}), {})
        assert "llm.grok.model" in exc.value.cause

    def test_json_parse_error(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            load_llm_config(_write(tmp_path, '{"llm": {"backend": "claude",}}'), {})
        assert "JSON 파싱 실패" in exc.value.cause
        assert "config.json" in exc.value.fix

    def test_config_error_is_startup_error(self):
        assert issubclass(ConfigError, StartupError)


# ── AC-5: env 이관 규칙 ───────────────────────────────────────────────


class TestEnvPrecedence:
    def test_env_absent_uses_default_model(self, tmp_path):
        config = load_llm_config(_write(tmp_path, _llm()), {})
        assert config.model == DEFAULT_CLAUDE_MODEL
        assert config.warnings == ()

    def test_env_llm_model_applies_when_config_model_null(self, tmp_path):
        """현행 .env LLM_MODEL=claude-opus-4-8 + claude.model null → opus 유지 + deprecated WARN."""
        config = load_llm_config(_write(tmp_path, _llm()), {"LLM_MODEL": "claude-opus-4-8"})
        assert config.model == "claude-opus-4-8"
        assert len(config.warnings) == 1
        assert "LLM_MODEL은 deprecated" in config.warnings[0]

    def test_env_config_model_wins_when_equal(self, tmp_path):
        data = _llm(claude={"model": "claude-opus-4-8", "skills": "native"})
        config = load_llm_config(_write(tmp_path, data), {"LLM_MODEL": "claude-opus-4-8"})
        assert config.model == "claude-opus-4-8"
        assert any("deprecated" in w for w in config.warnings)

    def test_env_model_conflict_is_config_error(self, tmp_path):
        data = _llm(claude={"model": "claude-sonnet-4-5", "skills": "native"})
        with pytest.raises(ConfigError) as exc:
            load_llm_config(_write(tmp_path, data), {"LLM_MODEL": "claude-opus-4-8"})
        assert "claude-sonnet-4-5" in exc.value.cause and "claude-opus-4-8" in exc.value.cause
        assert "LLM_MODEL" in exc.value.fix

    def test_env_llm_model_ignored_for_non_claude_backend(self, tmp_path):
        data = _llm(backend="codex", codex={"model": "gpt-5-codex", "bin": "codex"},
                    claude={"model": "claude-sonnet-4-5", "skills": "native"})
        config = load_llm_config(_write(tmp_path, data), {"LLM_MODEL": "claude-opus-4-8"})
        assert config.model == "gpt-5-codex"
        assert config.claude_model == "claude-sonnet-4-5"
        assert len(config.warnings) == 1
        assert "backend=codex에서 무시" in config.warnings[0]

    def test_env_llm_adapter_mismatch_is_config_error(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            load_llm_config(_write(tmp_path, _llm()), {"LLM_ADAPTER": "codex"})
        assert "LLM_ADAPTER(codex)" in exc.value.cause
        assert "llm.backend(claude)" in exc.value.cause

    def test_env_llm_adapter_equal_warns(self, tmp_path):
        config = load_llm_config(_write(tmp_path, _llm()), {"LLM_ADAPTER": "claude"})
        assert config.backend == "claude"
        assert len(config.warnings) == 1
        assert "LLM_ADAPTER는 deprecated" in config.warnings[0]

    def test_env_blank_values_treated_as_unset(self, tmp_path):
        config = load_llm_config(_write(tmp_path, _llm()), {"LLM_ADAPTER": "", "LLM_MODEL": "  "})
        assert config.model == DEFAULT_CLAUDE_MODEL
        assert config.warnings == ()

    def test_env_safe_loader_prints_warnings(self, tmp_path, capsys):
        config, error = load_llm_config_safe(_write(tmp_path, _llm()), {"LLM_MODEL": "claude-opus-4-8"})
        assert error is None and config.model == "claude-opus-4-8"
        out = capsys.readouterr().out.splitlines()
        assert out == ["[llm] WARN LLM_MODEL은 deprecated — config.json llm.claude.model로 옮기세요"]

    def test_env_safe_loader_returns_error_without_exit(self, tmp_path, capsys):
        config, error = load_llm_config_safe(_write(tmp_path, _llm()), {"LLM_ADAPTER": "grok"})
        assert config is None
        assert isinstance(error, ConfigError)
        assert capsys.readouterr().out == ""
