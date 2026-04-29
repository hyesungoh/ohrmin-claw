"""Regression tests: _collect_health_context_async does not block the event loop.

Phase 1 (RED): These tests must FAIL until Phase 2 wraps the sync Garmin I/O
with asyncio.to_thread inside _collect_health_context_async().

Failure expected:
  AttributeError — _collect_health_context_async does not exist yet on bot.main
"""
import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Module-level import guard: patch env before bot/main.py module-level code runs.
# We do this once at collection time via a module-scoped fixture approach, but
# because bot/main.py executes side-effects at import time (GarminConnectClient,
# BodyMetricsManager, DiscordChannel), we monkeypatch os.environ before importing.
# ---------------------------------------------------------------------------

os.environ.setdefault("DISCORD_BOT_TOKEN", "fake-token-test")
os.environ.setdefault("ALLOWED_USERS", "123456")
os.environ.setdefault("GARMIN_USERNAME", "")
os.environ.setdefault("GARMIN_PASSWORD", "")
os.environ.setdefault("APPLE_HEALTH_EXPORT_DIR", "/tmp/fake_hae_test")


TICKER_INTERVAL = 0.05   # seconds between ticker ticks
SLOW_CALL_SLEEP = 0.3    # seconds each fake Garmin call blocks
MIN_TICKS_REQUIRED = 4   # must see at least this many ticks to prove loop was free


def _make_slow_garmin_mock() -> MagicMock:
    """Return a MagicMock whose Garmin methods each block for SLOW_CALL_SLEEP."""
    client = MagicMock()

    def _slow(*_args, **_kwargs):
        time.sleep(SLOW_CALL_SLEEP)
        return []

    client.get_sleep.side_effect = _slow
    client.get_daily_summary.side_effect = _slow
    client.get_hrv.side_effect = _slow
    client.get_activities.side_effect = _slow
    client.get_stress.side_effect = _slow
    return client


@pytest.mark.asyncio
class TestCollectHealthContextAsyncDoesNotBlockLoop:
    """_collect_health_context_async() must run sync I/O in a thread pool."""

    async def test_async_helper_exists_on_module(self):
        """bot.main must expose _collect_health_context_async (AttributeError if missing)."""
        import bot.main as main_mod
        assert hasattr(main_mod, "_collect_health_context_async"), (
            "_collect_health_context_async not found on bot.main — "
            "Phase 2 has not been implemented yet."
        )

    async def test_event_loop_not_blocked_during_garmin_io(self, monkeypatch):
        """Ticker coroutine must fire >= MIN_TICKS_REQUIRED times while context is collected.

        If _collect_health_context_async blocks the event loop (by calling the
        sync garmin methods directly), the ticker cannot run and tick_count stays 0.
        The test therefore fails until asyncio.to_thread wrapping is in place.
        """
        import bot.main as main_mod

        # Replace the module-level garmin client with a slow mock.
        slow_client = _make_slow_garmin_mock()
        monkeypatch.setattr(main_mod, "garmin", slow_client)

        # Stub out HealthPreprocessor summarizers so they return {} immediately.
        mock_preprocessor = MagicMock()
        mock_preprocessor.summarize_sleep.return_value = {}
        mock_preprocessor.summarize_heart_rate.return_value = {}
        mock_preprocessor.summarize_hrv.return_value = {}
        mock_preprocessor.summarize_activities.return_value = {}
        mock_preprocessor.summarize_stress.return_value = {}
        monkeypatch.setattr(main_mod, "HealthPreprocessor", mock_preprocessor)

        # Stub out body_metrics_mgr.read_latest to avoid CSV I/O.
        mock_body_mgr = MagicMock()
        mock_body_mgr.read_latest.return_value = None
        monkeypatch.setattr(main_mod, "body_metrics_mgr", mock_body_mgr)

        tick_count = 0

        async def ticker():
            nonlocal tick_count
            # Run until cancelled by the gather timeout/cancel.
            while True:
                await asyncio.sleep(TICKER_INTERVAL)
                tick_count += 1

        async def run_context():
            # This will raise AttributeError if the function doesn't exist yet,
            # which is the expected RED failure for test_async_helper_exists_on_module.
            return await main_mod._collect_health_context_async()

        ticker_task = asyncio.create_task(ticker())
        try:
            await asyncio.gather(run_context(), return_exceptions=False)
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

        assert tick_count >= MIN_TICKS_REQUIRED, (
            f"Event loop was blocked: ticker fired only {tick_count} times "
            f"(need >= {MIN_TICKS_REQUIRED}). "
            f"_collect_health_context_async() is still calling sync garmin methods "
            f"directly on the event loop."
        )


@pytest.mark.asyncio
class TestCollectHealthContextSleepStructure:
    """sleep 키가 {baseline_7d, last_night} 2단 구조로 반환되어야 한다."""

    async def test_sleep_is_two_tier(self, monkeypatch):
        import bot.main as main_mod

        client = MagicMock()
        client.get_sleep.return_value = []
        client.get_daily_summary.return_value = []
        client.get_hrv.return_value = []
        client.get_activities.return_value = []
        client.get_stress.return_value = []
        monkeypatch.setattr(main_mod, "garmin", client)

        mock_body_mgr = MagicMock()
        mock_body_mgr.read_latest.return_value = None
        monkeypatch.setattr(main_mod, "body_metrics_mgr", mock_body_mgr)

        context = await main_mod._collect_health_context_async()

        assert "sleep" in context
        assert "baseline_7d" in context["sleep"]
        assert "last_night" in context["sleep"]


@pytest.mark.asyncio
class TestGenerateWeeklyReportDoesNotBlockLoop:
    """generate_weekly_report() must not block the event loop with sync Garmin I/O."""

    async def test_weekly_report_event_loop_not_blocked(self, monkeypatch):
        """Ticker coroutine must fire >= MIN_TICKS_REQUIRED times while weekly report is built."""
        import bot.main as main_mod

        slow_client = _make_slow_garmin_mock()
        monkeypatch.setattr(main_mod, "garmin", slow_client)

        mock_preprocessor = MagicMock()
        mock_preprocessor.summarize_sleep.return_value = {}
        mock_preprocessor.summarize_heart_rate.return_value = {}
        mock_preprocessor.summarize_hrv.return_value = {}
        mock_preprocessor.summarize_activities.return_value = {}
        mock_preprocessor.summarize_stress.return_value = {}
        mock_preprocessor.create_weekly_summary.return_value = {}
        monkeypatch.setattr(main_mod, "HealthPreprocessor", mock_preprocessor)

        mock_body_mgr = MagicMock()
        mock_body_mgr.read_latest.return_value = None
        monkeypatch.setattr(main_mod, "body_metrics_mgr", mock_body_mgr)

        mock_report_gen = MagicMock()
        mock_report_gen.weekly_report.return_value = "## Weekly Report"
        monkeypatch.setattr(main_mod, "ReportGenerator", mock_report_gen)

        mock_llm = MagicMock()
        mock_llm.ask_with_context = AsyncMock(return_value="insight text")
        monkeypatch.setattr(main_mod, "llm", mock_llm)

        tick_count = 0

        async def ticker():
            nonlocal tick_count
            while True:
                await asyncio.sleep(TICKER_INTERVAL)
                tick_count += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            await asyncio.gather(main_mod.generate_weekly_report(), return_exceptions=False)
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

        assert tick_count >= MIN_TICKS_REQUIRED, (
            f"Event loop was blocked: ticker fired only {tick_count} times "
            f"(need >= {MIN_TICKS_REQUIRED}). "
            f"generate_weekly_report() is still calling sync garmin methods "
            f"directly on the event loop."
        )
