"""SharedToolServer — 실제 루프백 uvicorn + 공식 mcp streamable HTTP 클라이언트 (AC-15 b/c, capability 토큰).

외부 네트워크 없음: 127.0.0.1 임시 포트만 사용한다.
"""
import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from core.body_metrics_tools import create_body_metrics_mcp_server
from core.garmin_tools import create_garmin_mcp_server
from core.llm import evaluate_tool_gate
from core.llm_errors import StartupError
from core.memory import MemoryManager
from core.memory_tools import create_memory_mcp_server
from core.schedule_tools import create_schedule_mcp_server
from core.scheduler import CronStore
from core.session_search_tools import create_session_search_mcp_server
from core.tool_server import http_server
from core.tool_server.capability import CallerCapability
from core.tool_server.http_server import SharedToolServer
from core.tool_server.schema import to_strict_json_schema

URL_RE = r"^http://127\.0\.0\.1:(\d+)/t/([A-Za-z0-9_-]+)/mcp/([a-z_]+)$"


def _deps(tmp_path):
    garmin = MagicMock()
    garmin.get_sleep.return_value = [{"day": "2026-04-20", "total_sleep": "08:00:00", "score": 82}]
    metrics = MagicMock()
    metrics.read_all.return_value = [
        {"date": "2026-04-20", "weight_kg": 75.0, "body_fat_pct": 18.0, "source": "manual"},
        {"date": "2026-04-13", "weight_kg": 75.5, "body_fat_pct": 18.5, "source": "manual"},
    ]
    memory = MemoryManager(str(tmp_path))
    memory.write_memory("기억 A")
    store = CronStore(str(tmp_path / "cron_jobs.json"))
    index = MagicMock()
    index.search.return_value = [{"thread_id": "1", "content": "러닝 페이스", "rank": -1.5}]
    return {"garmin": garmin, "metrics": metrics, "memory": memory, "store": store, "index": index}


def _specs(deps):
    return [
        create_garmin_mcp_server(deps["garmin"]),
        create_body_metrics_mcp_server(deps["metrics"]),
        create_memory_mcp_server(deps["memory"]),
        create_schedule_mcp_server(deps["store"]),
        create_session_search_mcp_server(deps["index"]),
    ]


@contextlib.asynccontextmanager
async def _running(specs, backend="codex"):
    server = SharedToolServer(specs, backend=backend)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@contextlib.asynccontextmanager
async def _session(url):
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _tool(spec, name):
    return next(t for t in spec.tools if t.name == name)


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_binds_loopback_and_builds_token_endpoints(self, tmp_path):
        import re

        specs = _specs(_deps(tmp_path))
        server = SharedToolServer(specs, backend="grok")
        with pytest.raises(RuntimeError):
            server.endpoint("garmin", CallerCapability.READ_ONLY)
        await server.start()
        try:
            assert isinstance(server.port, int) and server.port > 0
            priv = re.match(URL_RE, server.endpoint("memory", CallerCapability.PRIVILEGED))
            ro = re.match(URL_RE, server.endpoint("memory", CallerCapability.READ_ONLY))
            assert priv and ro
            assert int(priv.group(1)) == int(ro.group(1)) == server.port
            assert priv.group(2) != ro.group(2)
            assert len(priv.group(2)) >= 32 and len(ro.group(2)) >= 32
            assert priv.group(3) == ro.group(3) == "memory"
            with pytest.raises(KeyError):
                server.endpoint("skills", CallerCapability.PRIVILEGED)
            with pytest.raises(RuntimeError):
                await server.start()
        finally:
            await server.stop()
        await server.stop()  # 멱등

    @pytest.mark.asyncio
    async def test_tokens_differ_per_instance(self, tmp_path):
        specs = _specs(_deps(tmp_path))
        a, b = SharedToolServer(specs, backend="codex"), SharedToolServer(specs, backend="codex")
        assert a._tokens != b._tokens
        assert set(a._tokens) == set(CallerCapability)

    @pytest.mark.asyncio
    async def test_start_timeout_is_startup_error(self, tmp_path, monkeypatch):
        async def hang(self, sockets=None):
            await asyncio.sleep(3600)

        monkeypatch.setattr(http_server._NoSignalServer, "startup", hang)
        server = SharedToolServer(_specs(_deps(tmp_path)), backend="codex")
        with pytest.raises(StartupError) as exc:
            await server.start(timeout=0.2)
        assert "기동하지 않았습니다" in exc.value.cause
        assert server._task is None

    def test_default_start_timeout_is_10s(self):
        assert http_server.START_TIMEOUT == 10.0

    @pytest.mark.asyncio
    async def test_lifespan_failure_is_startup_error(self, tmp_path, monkeypatch):
        @contextlib.asynccontextmanager
        async def boom(self):
            raise RuntimeError("manager down")
            yield

        monkeypatch.setattr(StreamableHTTPSessionManager, "run", boom)
        server = SharedToolServer(_specs(_deps(tmp_path)), backend="codex")
        with pytest.raises(StartupError) as exc:
            await server.start(timeout=5)
        assert "공유 도구 서버 기동 실패" in exc.value.cause

    def test_signal_capture_disabled(self):
        import uvicorn

        assert issubclass(http_server._NoSignalServer, uvicorn.Server)
        assert http_server._NoSignalServer.capture_signals is not uvicorn.Server.capture_signals
        assert http_server._SECURITY_SETTINGS.allowed_hosts == ["127.0.0.1:*"]

    def test_invalid_tool_name_rejected_at_construction(self):
        from core.tool_server.spec import ServerSpec, tool

        @tool("bad name", "d", {})
        async def bad(args):
            return {}

        with pytest.raises(ValueError):
            SharedToolServer([ServerSpec("s", [bad])], backend="codex")


class TestHttpRouting:
    @pytest.mark.asyncio
    async def test_unknown_token_and_unknown_server_are_404(self, tmp_path):
        async with _running(_specs(_deps(tmp_path))) as server:
            base = f"http://127.0.0.1:{server.port}"
            valid = server.endpoint("garmin", CallerCapability.READ_ONLY)
            token = valid.split("/t/")[1].split("/")[0]
            body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            headers = {"Accept": "application/json, text/event-stream"}
            async with httpx.AsyncClient() as client:
                bogus = await client.post(f"{base}/t/not-a-token/mcp/garmin", json=body, headers=headers)
                assert bogus.status_code == 404
                bogus_get = await client.get(f"{base}/t/not-a-token/mcp/garmin", headers=headers)
                assert bogus_get.status_code == 404
                no_server = await client.post(f"{base}/t/{token}/mcp/skills", json=body, headers=headers)
                assert no_server.status_code == 404
                gate = await client.post(f"{base}/t/not-a-token/gate", json={})
                assert gate.status_code == 404  # 게이트 엔드포인트(P3)도 미등록 토큰은 404

    @pytest.mark.asyncio
    async def test_foreign_host_header_rejected(self, tmp_path):
        async with _running(_specs(_deps(tmp_path))) as server:
            url = server.endpoint("garmin", CallerCapability.PRIVILEGED)
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Host": "evil.example:80", "Accept": "application/json, text/event-stream"},
                )
            assert resp.status_code == 421


class TestMcpOverHttp:
    @pytest.mark.asyncio
    async def test_list_tools_per_server_matches_strict_schema(self, tmp_path):
        specs = _specs(_deps(tmp_path))
        total = 0
        async with _running(specs) as server:
            for capability in CallerCapability:
                for spec in specs:
                    async with _session(server.endpoint(spec.name, capability)) as session:
                        listed = (await session.list_tools()).tools
                    assert [t.name for t in listed] == [t.name for t in spec.tools]
                    for remote, local in zip(listed, spec.tools):
                        assert remote.description == local.description
                        assert remote.inputSchema == to_strict_json_schema(local.params)
                        total += 1
        assert total == 44  # 22 도구 × priv/ro

    @pytest.mark.asyncio
    async def test_call_results_byte_identical_to_direct_handler(self, tmp_path):
        deps = _deps(tmp_path)
        specs = _specs(deps)
        garmin, metrics, memory, schedule, search = specs
        cases = [
            (garmin, "get_sleep", {"start": "2026-04-20", "end": "2026-04-20"}, {"start": "2026-04-20", "end": "2026-04-20"}),
            (metrics, "get_body_metrics_history", {"count": 1, "days": None}, {"count": 1}),
            (memory, "list_memory", {"target": "memory"}, {"target": "memory"}),
            (schedule, "schedule_list", {}, {}),
            (search, "search", {"query": "러닝", "limit": 5}, {"query": "러닝", "limit": 5}),
        ]
        async with _running(specs) as server:
            for spec, name, remote_args, direct_args in cases:
                async with _session(server.endpoint(spec.name, CallerCapability.READ_ONLY)) as session:
                    result = await session.call_tool(name, remote_args)
                direct = await _tool(spec, name).handler(direct_args)
                assert result.isError is False, (spec.name, name, result)
                assert len(result.content) == len(direct["content"]) == 1
                assert result.content[0].text.encode("utf-8") == direct["content"][0]["text"].encode("utf-8"), name

    @pytest.mark.asyncio
    async def test_ro_token_denies_mutation_priv_token_allows(self, tmp_path, capsys):
        deps = _deps(tmp_path)
        specs = _specs(deps)
        args = {"prompt": "매일 요약", "schedule": "30m", "deliver_channel_id": None, "max_turns": None}
        _, reason = evaluate_tool_gate("mcp__schedule__schedule_create", {}, False)
        async with _running(specs, backend="grok") as server:
            async with _session(server.endpoint("schedule", CallerCapability.READ_ONLY)) as session:
                denied = await session.call_tool("schedule_create", args)
            assert json.loads(denied.content[0].text) == {"success": False, "error": reason, "denied_by": "capability"}
            assert deps["store"].count() == 0
            assert (
                f"[gate] backend=grok cap=ro tool=mcp__schedule__schedule_create decision=deny via=capability reason={reason}"
                in capsys.readouterr().out.splitlines()
            )

            async with _session(server.endpoint("schedule", CallerCapability.PRIVILEGED)) as session:
                allowed = await session.call_tool("schedule_create", args)
            assert json.loads(allowed.content[0].text)["success"] is True
            assert deps["store"].count() == 1

    @pytest.mark.asyncio
    async def test_ro_token_denies_all_seven_mutations_without_handler_calls(self, tmp_path):
        deps = _deps(tmp_path)
        memory_before = (tmp_path / "memory.md").read_bytes()
        specs = _specs(deps)
        mutations = {
            "schedule": ["schedule_create", "schedule_pause", "schedule_resume", "schedule_remove"],
            "memory": ["add_memory", "replace_memory", "remove_memory"],
        }
        async with _running(specs) as server:
            for server_name, names in mutations.items():
                spec = next(s for s in specs if s.name == server_name)
                async with _session(server.endpoint(server_name, CallerCapability.READ_ONLY)) as session:
                    for name in names:
                        args = {k: None for k in _tool(spec, name).params}
                        result = await session.call_tool(name, args)
                        assert json.loads(result.content[0].text)["denied_by"] == "capability", name
        assert deps["store"].count() == 0
        assert (tmp_path / "memory.md").read_bytes() == memory_before

    @pytest.mark.asyncio
    async def test_required_null_or_omitted_is_validation_error_and_optional_key_may_be_omitted(self, tmp_path):
        """HTTP 경로는 strict schema 검증을 끄고(선택 키 생략 허용) 필수 키 확인은 wrap_handler가 한다."""
        deps = _deps(tmp_path)
        deps["garmin"].get_activity_detail.return_value = {"activityId": 1}
        specs = _specs(deps)
        async with _running(specs) as server:
            async with _session(server.endpoint("garmin", CallerCapability.PRIVILEGED)) as session:
                null_required = await session.call_tool("get_activity_detail", {"activity_id": None})
                omitted_required = await session.call_tool("get_activity_detail", {})
                omitted_optional = await session.call_tool("get_sleep", {"start": "2026-04-20"})
        assert null_required.isError is True
        assert "Input validation error" in null_required.content[0].text
        assert omitted_required.isError is True
        assert omitted_required.content[0].text == "Input validation error: required parameter(s) missing or null: activity_id"
        deps["garmin"].get_activity_detail.assert_not_called()
        # Codex/Grok은 선택 키를 생략할 수 있다 → 핸들러가 기본값으로 처리한다.
        assert omitted_optional.isError is False
        deps["garmin"].get_sleep.assert_called_once()
        direct = await _tool(specs[0], "get_sleep").handler({"start": "2026-04-20"})
        assert omitted_optional.content[0].text == direct["content"][0]["text"]


def _config(tmp_path, backend):
    from core.llm_config import load_llm_config

    data = {"llm": {"backend": backend}}
    if backend == "grok":
        data["llm"]["grok"] = {"model": "grok-x", "bin": "~/.grok/bin/grok"}
    path = tmp_path / f"config-{backend}.json"
    path.write_text(json.dumps(data))
    return load_llm_config(str(path), {})


class TestBotWiring:
    @pytest.mark.parametrize("backend", ["codex", "grok"])
    def test_codex_grok_build_shared_tool_server(self, tmp_path, monkeypatch, backend):
        import bot.main as main

        specs = _specs(_deps(tmp_path))
        sentinel = object()
        factory = MagicMock(return_value=sentinel)
        monkeypatch.setattr(main, "create_llm_adapter_from_config", factory)

        adapter, shared = main.build_llm_and_tool_server(_config(tmp_path, backend), specs)

        assert adapter is sentinel
        assert isinstance(shared, SharedToolServer)
        assert shared.backend == backend
        assert shared.port is None  # 생성만 — 기동은 setup_hook
        assert factory.call_args.kwargs["server_specs"] is specs
        assert "mcp_servers" not in factory.call_args.kwargs

    def test_codex_builds_codex_adapter_wired_to_shared_tool_server(self, tmp_path):
        import bot.main as main
        from core.runtimes.codex_adapter import CodexAdapter

        specs = _specs(_deps(tmp_path))
        adapter, shared = main.build_llm_and_tool_server(_config(tmp_path, "codex"), specs)

        assert isinstance(adapter, CodexAdapter)
        assert isinstance(shared, SharedToolServer) and shared.port is None
        assert adapter.tool_server is shared
        assert adapter.server_specs is specs
        assert adapter.cwd == main.PROJECT_ROOT

    def test_grok_builds_grok_adapter_wired_to_shared_tool_server(self, tmp_path):
        """P6 — grok도 공유 도구 서버(생성만, 기동은 setup_hook)에 배선된 GrokAdapter를 만든다."""
        import bot.main as main
        from core.runtimes.grok_adapter import GrokAdapter

        specs = _specs(_deps(tmp_path))
        adapter, shared = main.build_llm_and_tool_server(_config(tmp_path, "grok"), specs)

        assert isinstance(adapter, GrokAdapter)
        assert isinstance(shared, SharedToolServer) and shared.port is None
        assert adapter.tool_server is shared
        assert adapter.server_specs is specs
        assert adapter.cwd == main.PROJECT_ROOT
        assert (adapter.model, adapter.bin) == ("grok-x", "~/.grok/bin/grok")

    @pytest.mark.parametrize("backend", ["grok"])
    def test_codex_grok_adapter_without_tool_server_is_startup_error(self, tmp_path, monkeypatch, backend):
        import bot.main as main

        real_factory = main.create_llm_adapter_from_config
        monkeypatch.setattr(
            main, "create_llm_adapter_from_config",
            lambda config, **kwargs: real_factory(config, **{**kwargs, "tool_server": None}),
        )
        with pytest.raises(StartupError) as exc:
            main.build_llm_and_tool_server(_config(tmp_path, backend), _specs(_deps(tmp_path)))
        assert backend in exc.value.cause

    def test_claude_builds_sdk_sets_without_tool_server(self, tmp_path):
        import bot.main as main
        from core.llm import ClaudeSDKAdapter

        specs = _specs(_deps(tmp_path))
        adapter, shared = main.build_llm_and_tool_server(_config(tmp_path, "claude"), specs)

        assert shared is None
        assert isinstance(adapter, ClaudeSDKAdapter)
        assert list(adapter.mcp_servers) == list(adapter.readonly_mcp_servers) == [s.name for s in specs]
        assert adapter.server_specs is specs
        assert adapter.cwd == main.PROJECT_ROOT

    @pytest.mark.asyncio
    async def test_setup_hook_starts_tool_server_before_llm(self, monkeypatch):
        import bot.main as main

        order = []
        fake_server = MagicMock()
        fake_server.start = AsyncMock(side_effect=lambda: order.append("tool_server.start"))
        fake_llm = MagicMock()
        fake_llm.start = AsyncMock(side_effect=lambda: order.append("llm.start"))
        monkeypatch.setattr(main, "tool_server", fake_server)
        monkeypatch.setattr(main, "llm", fake_llm)
        monkeypatch.setattr(main.channel._client, "close", AsyncMock())
        monkeypatch.setattr(main, "_STARTUP_FAILED", False)

        await main._llm_setup_hook()

        assert order == ["tool_server.start", "llm.start"]
        assert main._STARTUP_FAILED is False

    @pytest.mark.asyncio
    async def test_tool_server_start_failure_fails_startup_without_llm_start(self, monkeypatch, capsys):
        import bot.main as main

        fake_server = MagicMock()
        fake_server.start = AsyncMock(side_effect=StartupError("공유 도구 서버 기동 실패: x", "봇 로그를 확인하세요"))
        fake_llm = MagicMock()
        fake_llm.start = AsyncMock()
        close = AsyncMock()
        monkeypatch.setattr(main, "tool_server", fake_server)
        monkeypatch.setattr(main, "llm", fake_llm)
        monkeypatch.setattr(main.channel._client, "close", close)
        monkeypatch.setattr(main, "_STARTUP_FAILED", False)

        await main._llm_setup_hook()

        assert main._STARTUP_FAILED is True
        fake_llm.start.assert_not_awaited()
        close.assert_awaited_once()
        assert "❌ [LLM] 공유 도구 서버 기동 실패: x" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_close_stops_tool_server_after_close_all(self, monkeypatch):
        import bot.main as main

        order = []
        fake_server = MagicMock()
        fake_server.stop = AsyncMock(side_effect=lambda: order.append("tool_server.stop"))
        fake_llm = MagicMock()
        fake_llm.close_all = AsyncMock(side_effect=lambda: order.append("llm.close_all"))
        original = AsyncMock(side_effect=lambda: order.append("client.close"))
        monkeypatch.setattr(main, "tool_server", fake_server)
        monkeypatch.setattr(main, "llm", fake_llm)
        monkeypatch.setattr(main, "_original_client_close", original)

        await main._close_with_cleanup()

        assert order == ["llm.close_all", "tool_server.stop", "client.close"]

    @pytest.mark.asyncio
    async def test_real_server_start_and_stop_through_bot_hooks(self, tmp_path, monkeypatch):
        import bot.main as main

        server = SharedToolServer(_specs(_deps(tmp_path)), backend="codex")
        fake_llm = MagicMock()
        fake_llm.start = AsyncMock()
        fake_llm.close_all = AsyncMock()
        monkeypatch.setattr(main, "tool_server", server)
        monkeypatch.setattr(main, "llm", fake_llm)
        monkeypatch.setattr(main.channel._client, "close", AsyncMock())
        monkeypatch.setattr(main, "_original_client_close", AsyncMock())
        monkeypatch.setattr(main, "_STARTUP_FAILED", False)

        await main._llm_setup_hook()
        assert main._STARTUP_FAILED is False
        async with _session(server.endpoint("memory", CallerCapability.READ_ONLY)) as session:
            assert len((await session.list_tools()).tools) == 4

        await main._close_with_cleanup()
        assert server._task is None
        with pytest.raises(httpx.ConnectError):
            async with httpx.AsyncClient() as client:
                await client.get(f"http://127.0.0.1:{server.port}/")
