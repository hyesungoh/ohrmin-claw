"""기동 preflight(AC-3) + main() 설정 오류 exit 1(AC-2) + setup_hook 핸드셰이크 실패 테스트.

실제 claude/codex/grok 바이너리는 절대 실행하지 않는다 — 러너·파일시스템·which는 전부 fake.
"""
import json
import os

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.llm_config import DEFAULT_CONFIG, ConfigError, load_llm_config, load_llm_config_safe
from core.llm_errors import RealRuntimeForbidden, StartupError
from core import preflight
from core.preflight import (
    CODEX_INSTALL_FIX,
    GROK_INSTALL_FIX,
    default_runner,
    resolve_claude_cli,
    run_preflight,
)

_LOGGED_IN = {
    "apiProvider": "firstParty",
    "authMethod": "claude.ai",
    "email": "owner@example.com",
    "loggedIn": True,
    "orgId": "org",
    "orgName": "org",
    "subscriptionType": "max",
}


class FakeRunner:
    def __init__(self, returncode=0, stdout=None, exc=None):
        self.returncode = returncode
        self.stdout = json.dumps(_LOGGED_IN) if stdout is None else stdout
        self.exc = exc
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.exc is not None:
            raise self.exc
        return self.returncode, self.stdout


class FakeFS:
    """os.path 대역 — files에 있는 경로만 존재. `~`는 home으로 치환."""

    def __init__(self, files=(), home="/home/owner"):
        self.files = set(files)
        self.home = home

    def expanduser(self, path):
        return self.home + path[1:] if path.startswith("~") else path

    def isfile(self, path):
        return path in self.files or (path.endswith("/_bundled/claude") and "BUNDLED" in self.files)

    def exists(self, path):
        return self.isfile(path)


def _no_which(name):
    return None


def _config(tmp_path, backend="claude", **sections):
    llm = json.loads(json.dumps(DEFAULT_CONFIG["llm"]))
    llm["backend"] = backend
    for key, value in sections.items():
        llm[key].update(value)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": llm}), encoding="utf-8")
    return load_llm_config(str(path), {})


# ── claude ────────────────────────────────────────────────────────────


class TestClaudePreflight:
    def test_logged_in_subscription_passes_with_bundled_cli_argv(self, tmp_path):
        runner = FakeRunner()
        fs = FakeFS(files={"BUNDLED"})
        run_preflight(_config(tmp_path), {}, runner=runner, fs=fs, which=lambda n: "/usr/local/bin/claude")
        assert len(runner.calls) == 1
        argv = runner.calls[0]
        assert argv[0].endswith("/_bundled/claude")  # 번들 CLI 우선 (SDK와 동일 순서)
        assert argv[1:] == ["auth", "status", "--json"]

    def test_falls_back_to_path_claude(self, tmp_path):
        runner = FakeRunner()
        run_preflight(_config(tmp_path), {}, runner=runner, fs=FakeFS(), which=lambda n: "/usr/local/bin/claude")
        assert runner.calls == [["/usr/local/bin/claude", "auth", "status", "--json"]]

    def test_cli_resolution_order(self):
        assert resolve_claude_cli(FakeFS(files={"BUNDLED"}), lambda n: "/x/claude").endswith("/_bundled/claude")
        assert resolve_claude_cli(FakeFS(), lambda n: "/x/claude") == "/x/claude"
        assert resolve_claude_cli(FakeFS(), _no_which) is None

    @pytest.mark.parametrize("runner, needle", [
        (FakeRunner(stdout=json.dumps({**_LOGGED_IN, "loggedIn": False})), "로그인되어 있지 않습니다"),
        (FakeRunner(stdout=json.dumps({"loggedIn": "true"})), "로그인되어 있지 않습니다"),
        (FakeRunner(returncode=1, stdout=""), "exit=1"),
        (FakeRunner(stdout="not json"), "JSON"),
        (FakeRunner(exc=FileNotFoundError("claude")), "실행 실패"),
        (FakeRunner(stdout=json.dumps({**_LOGGED_IN, "authMethod": "api_key"})), "authMethod=api_key"),
        (FakeRunner(stdout=json.dumps({**_LOGGED_IN, "apiProvider": "bedrock"})), "apiProvider=bedrock"),
    ])
    def test_not_logged_in_or_not_subscription_fails_with_claude_login(self, tmp_path, runner, needle):
        with pytest.raises(StartupError) as exc:
            run_preflight(_config(tmp_path), {}, runner=runner, fs=FakeFS(files={"BUNDLED"}), which=_no_which)
        assert needle in exc.value.cause
        assert exc.value.fix == "claude login"

    def test_anthropic_api_key_rejected_before_running_cli(self, tmp_path):
        runner = FakeRunner()
        with pytest.raises(StartupError) as exc:
            run_preflight(_config(tmp_path), {"ANTHROPIC_API_KEY": "sk-ant-x"}, runner=runner,
                          fs=FakeFS(files={"BUNDLED"}), which=_no_which)
        assert "구독 로그인만 허용" in exc.value.cause
        assert exc.value.fix == ".env에서 ANTHROPIC_API_KEY 제거"
        assert runner.calls == []

    def test_cli_missing(self, tmp_path):
        runner = FakeRunner()
        with pytest.raises(StartupError) as exc:
            run_preflight(_config(tmp_path), {}, runner=runner, fs=FakeFS(), which=_no_which)
        assert "Claude CLI" in exc.value.cause
        assert runner.calls == []

    def test_default_runner_forbidden_in_tests(self, monkeypatch):
        monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")
        with pytest.raises(RealRuntimeForbidden):
            default_runner(["claude", "auth", "status", "--json"])

    def test_default_runner_guard_propagates_through_preflight(self, tmp_path, monkeypatch):
        """가드는 '실행 실패' StartupError로 삼켜지지 않고 그대로 드러나야 한다."""
        monkeypatch.setenv("OHRMIN_FORBID_REAL_RUNTIMES", "1")
        with pytest.raises(RealRuntimeForbidden):
            run_preflight(_config(tmp_path), {}, fs=FakeFS(files={"BUNDLED"}), which=_no_which)


# ── codex ─────────────────────────────────────────────────────────────


class TestCodexPreflight:
    def _fs(self, *extra):
        return FakeFS(files={"/opt/bin/codex", "/home/owner/.codex/auth.json", *extra})

    def test_logged_in_passes(self, tmp_path):
        config = _config(tmp_path, "codex", codex={"bin": "/opt/bin/codex"})
        run_preflight(config, {}, runner=FakeRunner(exc=AssertionError("no cli run")), fs=self._fs(), which=_no_which)

    def test_bin_resolved_from_path(self, tmp_path):
        config = _config(tmp_path, "codex")
        run_preflight(config, {}, fs=self._fs(), which=lambda n: "/usr/local/bin/codex" if n == "codex" else None)

    def test_auth_json_missing(self, tmp_path):
        config = _config(tmp_path, "codex", codex={"bin": "/opt/bin/codex"})
        with pytest.raises(StartupError) as exc:
            run_preflight(config, {}, fs=FakeFS(files={"/opt/bin/codex"}), which=_no_which)
        assert "/home/owner/.codex/auth.json" in exc.value.cause
        assert exc.value.fix == "codex login"

    def test_codex_home_respected(self, tmp_path):
        config = _config(tmp_path, "codex", codex={"bin": "/opt/bin/codex"})
        with pytest.raises(StartupError) as exc:
            run_preflight(config, {"CODEX_HOME": "/srv/codex"}, fs=self._fs(), which=_no_which)
        assert "/srv/codex/auth.json" in exc.value.cause
        run_preflight(config, {"CODEX_HOME": "/srv/codex"}, fs=self._fs("/srv/codex/auth.json"), which=_no_which)

    @pytest.mark.parametrize("key", ["OPENAI_API_KEY", "CODEX_API_KEY"])
    def test_api_key_env_rejected(self, tmp_path, key):
        config = _config(tmp_path, "codex", codex={"bin": "/opt/bin/codex"})
        with pytest.raises(StartupError) as exc:
            run_preflight(config, {key: "sk-x"}, fs=self._fs(), which=_no_which)
        assert key in exc.value.cause
        assert exc.value.fix == f".env에서 {key} 제거"

    def test_bin_missing(self, tmp_path):
        config = _config(tmp_path, "codex")
        with pytest.raises(StartupError) as exc:
            run_preflight(config, {}, fs=self._fs(), which=_no_which)
        assert "codex CLI 설치 필요" in exc.value.cause
        assert exc.value.fix == CODEX_INSTALL_FIX


# ── grok ──────────────────────────────────────────────────────────────


class TestGrokPreflight:
    def _config(self, tmp_path, **grok):
        return _config(tmp_path, "grok", grok={"model": "grok-code-fast-1", **grok})

    def test_api_key_and_bin_pass(self, tmp_path):
        run_preflight(self._config(tmp_path), {"XAI_API_KEY": "xai-x"},
                      fs=FakeFS(files={"/home/owner/.grok/bin/grok"}), which=_no_which)

    @pytest.mark.parametrize("env", [{}, {"XAI_API_KEY": ""}, {"XAI_API_KEY": "   "}])
    def test_xai_api_key_missing(self, tmp_path, env):
        with pytest.raises(StartupError) as exc:
            run_preflight(self._config(tmp_path), env, fs=FakeFS(files={"/home/owner/.grok/bin/grok"}), which=_no_which)
        assert "XAI_API_KEY" in exc.value.cause
        assert exc.value.fix == ".env에 XAI_API_KEY=... 추가"

    def test_bin_missing(self, tmp_path):
        with pytest.raises(StartupError) as exc:
            run_preflight(self._config(tmp_path), {"XAI_API_KEY": "xai-x"}, fs=FakeFS(), which=_no_which)
        assert "Grok CLI" in exc.value.cause
        assert exc.value.fix == GROK_INSTALL_FIX

    def test_model_null_is_config_error(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"llm": {"backend": "grok"}}), encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            load_llm_config(str(path), {"XAI_API_KEY": "xai-x"})
        assert "llm.grok.model" in exc.value.cause


# ── main(): 설정/인증 실패 → exit 1, Discord 토큰 검사보다 먼저 ─────────────


def _patch_main_config(monkeypatch, main, tmp_path, raw, env=None):
    path = tmp_path / "config.json"
    path.write_text(raw if isinstance(raw, str) else json.dumps(raw), encoding="utf-8")
    config, error = load_llm_config_safe(str(path), env or {})
    monkeypatch.setattr(main, "LLM_CONFIG", config)
    monkeypatch.setattr(main, "LLM_CONFIG_ERROR", error)


class TestMainExit:
    @pytest.fixture
    def main(self, monkeypatch):
        import bot.main as main_module

        monkeypatch.setattr(main_module, "DISCORD_TOKEN", None)
        run = MagicMock()
        monkeypatch.setattr(main_module.channel, "run", run)
        monkeypatch.setattr(main_module, "_test_run", run, raising=False)
        return main_module

    @pytest.mark.parametrize("raw, env, needle", [
        ({"llm": {"backend": "openai"}}, {}, "llm.backend"),
        ({"llm": {"backend": "claude", "proxy": "http://x"}}, {}, "proxy"),
        ({"llm": {"backend": "claude", "claude": {"model": 42}}}, {}, "llm.claude.model"),
        ('{"llm": {"backend": ', {}, "JSON 파싱 실패"),
        ({"llm": {"backend": "claude"}}, {"LLM_ADAPTER": "codex"}, "LLM_ADAPTER(codex)"),
    ], ids=["bad-backend", "unknown-key", "type-violation", "json-error", "env-conflict"])
    def test_config_error_exits_1_before_discord_token_check(self, main, monkeypatch, tmp_path, capsys, raw, env, needle):
        _patch_main_config(monkeypatch, main, tmp_path, raw, env)
        runner = FakeRunner()
        monkeypatch.setattr(main, "PREFLIGHT_RUNNER", runner)

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert out.startswith("❌ [LLM] ")
        assert needle in out
        assert "\n   해결: " in out
        assert "DISCORD_BOT_TOKEN" not in out  # 토큰 검사 전에 종료
        assert runner.calls == []  # 설정 오류면 preflight도 실행하지 않음
        main._test_run.assert_not_called()

    def test_config_grok_model_null_exits_1(self, main, monkeypatch, tmp_path, capsys):
        _patch_main_config(monkeypatch, main, tmp_path, {"llm": {"backend": "grok"}})
        with pytest.raises(SystemExit) as exc:
            main.main()
        assert exc.value.code == 1
        assert "llm.grok.model" in capsys.readouterr().out

    @pytest.mark.parametrize("runner, fix", [
        (FakeRunner(stdout=json.dumps({**_LOGGED_IN, "loggedIn": False})), "claude login"),
        (FakeRunner(stdout=json.dumps({**_LOGGED_IN, "authMethod": "console"})), "claude login"),
        (FakeRunner(returncode=2, stdout=""), "claude login"),
    ])
    def test_claude_preflight_failure_exits_1(self, main, monkeypatch, tmp_path, capsys, runner, fix):
        _patch_main_config(monkeypatch, main, tmp_path, {"llm": {"backend": "claude"}})
        monkeypatch.setattr(preflight, "resolve_claude_cli", lambda fs=None, which=None: "/fake/claude")
        monkeypatch.setattr(main, "PREFLIGHT_RUNNER", runner)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert out.startswith("❌ [LLM] ")
        assert f"   해결: {fix}" in out
        assert "DISCORD_BOT_TOKEN" not in out
        assert runner.calls == [["/fake/claude", "auth", "status", "--json"]]
        main._test_run.assert_not_called()

    def test_anthropic_api_key_env_exits_1(self, main, monkeypatch, tmp_path, capsys):
        _patch_main_config(monkeypatch, main, tmp_path, {"llm": {"backend": "claude"}})
        monkeypatch.setattr(main, "PREFLIGHT_RUNNER", FakeRunner())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
        with pytest.raises(SystemExit) as exc:
            main.main()
        assert exc.value.code == 1
        assert "   해결: .env에서 ANTHROPIC_API_KEY 제거" in capsys.readouterr().out

    @pytest.mark.parametrize("env, section, fix", [
        ({}, {"codex": {"bin": "/nonexistent/ohrmin-test/codex"}}, CODEX_INSTALL_FIX),
        ({"XAI_API_KEY": ""}, {"grok": {"model": "grok-x"}}, ".env에 XAI_API_KEY=... 추가"),
    ])
    def test_non_claude_preflight_failure_exits_1(self, main, monkeypatch, tmp_path, capsys, env, section, fix):
        backend = next(iter(section))
        llm = json.loads(json.dumps(DEFAULT_CONFIG["llm"]))
        llm["backend"] = backend
        llm[backend].update(section[backend])
        config = load_llm_config(str(_write_json(tmp_path, {"llm": llm})), {})
        monkeypatch.setattr(main, "LLM_CONFIG", config)
        monkeypatch.setattr(main, "LLM_CONFIG_ERROR", None)
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "XAI_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert f"   해결: {fix}" in out
        main._test_run.assert_not_called()

    def test_preflight_ok_then_discord_token_check(self, main, monkeypatch, tmp_path, capsys):
        _patch_main_config(monkeypatch, main, tmp_path, {"llm": {"backend": "claude"}})
        monkeypatch.setattr(preflight, "resolve_claude_cli", lambda fs=None, which=None: "/fake/claude")
        monkeypatch.setattr(main, "PREFLIGHT_RUNNER", FakeRunner())
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "[LLM]" not in out
        assert "DISCORD_BOT_TOKEN" in out

    def test_preflight_ok_runs_bot(self, main, monkeypatch, tmp_path):
        _patch_main_config(monkeypatch, main, tmp_path, {"llm": {"backend": "claude"}})
        monkeypatch.setattr(preflight, "resolve_claude_cli", lambda fs=None, which=None: "/fake/claude")
        monkeypatch.setattr(main, "PREFLIGHT_RUNNER", FakeRunner())
        monkeypatch.setattr(main, "DISCORD_TOKEN", "token")
        monkeypatch.setattr(main, "_STARTUP_FAILED", False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        main.main()  # SystemExit 없음

        main._test_run.assert_called_once()


def _write_json(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── setup_hook: 런타임 핸드셰이크 실패 → exit 1 ─────────────────────────


class TestSetupHookStartup:
    @pytest.mark.asyncio
    async def test_start_failure_marks_failed_and_closes_client(self, monkeypatch, capsys):
        import bot.main as main

        fake_llm = MagicMock()
        fake_llm.start = AsyncMock(side_effect=StartupError("게이트 probe 실패", "봇 로그 확인"))
        close = AsyncMock()
        monkeypatch.setattr(main, "llm", fake_llm)
        monkeypatch.setattr(main.channel._client, "close", close)
        monkeypatch.setattr(main, "_STARTUP_FAILED", False)

        await main._llm_setup_hook()

        assert main._STARTUP_FAILED is True
        close.assert_awaited_once()
        out = capsys.readouterr().out
        assert "❌ [LLM] 게이트 probe 실패" in out
        assert "   해결: 봇 로그 확인" in out

    @pytest.mark.asyncio
    async def test_unexpected_start_exception_also_fails(self, monkeypatch, capsys):
        import bot.main as main

        fake_llm = MagicMock()
        fake_llm.start = AsyncMock(side_effect=RuntimeError("boom"))
        close = AsyncMock()
        monkeypatch.setattr(main, "llm", fake_llm)
        monkeypatch.setattr(main.channel._client, "close", close)
        monkeypatch.setattr(main, "_STARTUP_FAILED", False)

        await main._llm_setup_hook()

        assert main._STARTUP_FAILED is True
        close.assert_awaited_once()
        assert "❌ [LLM] LLM 런타임 기동 실패: RuntimeError: boom" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_start_success_keeps_running(self, monkeypatch):
        import bot.main as main

        fake_llm = MagicMock()
        fake_llm.start = AsyncMock()
        close = AsyncMock()
        monkeypatch.setattr(main, "llm", fake_llm)
        monkeypatch.setattr(main.channel._client, "close", close)
        monkeypatch.setattr(main, "_STARTUP_FAILED", False)

        await main._llm_setup_hook()

        fake_llm.start.assert_awaited_once()
        assert main._STARTUP_FAILED is False
        close.assert_not_awaited()

    def test_setup_hook_wired_on_client(self):
        import bot.main as main

        assert main.channel._client.setup_hook is main._llm_setup_hook

    def test_main_exits_1_when_handshake_failed(self, monkeypatch, tmp_path):
        import bot.main as main

        _patch_main_config(monkeypatch, main, tmp_path, {"llm": {"backend": "claude"}})
        monkeypatch.setattr(preflight, "resolve_claude_cli", lambda fs=None, which=None: "/fake/claude")
        monkeypatch.setattr(main, "PREFLIGHT_RUNNER", FakeRunner())
        monkeypatch.setattr(main, "DISCORD_TOKEN", "token")
        monkeypatch.setattr(main, "_STARTUP_FAILED", False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        def run():
            main._STARTUP_FAILED = True  # setup_hook이 핸드셰이크 실패를 기록하고 클라이언트를 닫은 상황

        monkeypatch.setattr(main.channel, "run", run)

        with pytest.raises(SystemExit) as exc:
            main.main()
        assert exc.value.code == 1

    def test_import_loads_config_without_exit(self):
        """bot.main import 시점엔 exit하지 않는다 — 기본 config.json이면 오류 없음 + 어댑터 생성."""
        import bot.main as main

        assert main.LLM_CONFIG_ERROR is None
        assert main.LLM_CONFIG.backend == "claude"
        assert main.llm is not None
        assert os.path.basename(main.CONFIG_PATH) == "config.json"
