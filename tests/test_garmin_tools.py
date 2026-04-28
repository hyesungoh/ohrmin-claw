"""Garmin MCP tool 테스트."""
import asyncio
import datetime
import json
import time
from unittest.mock import MagicMock

import pytest

from core.garmin_tools import create_garmin_mcp_server, TOOL_REGISTRY


@pytest.fixture
def mock_garmin_client():
    client = MagicMock()
    client.get_sleep.return_value = [
        {"day": "2026-04-20", "total_sleep": "08:00:00", "score": 82},
    ]
    client.get_daily_summary.return_value = [
        {"day": "2026-04-20", "rhr": 58, "steps": 9200},
    ]
    client.get_hrv.return_value = [
        {"day": "2026-04-20", "weekly_avg": 45.0, "status": "BALANCED"},
    ]
    client.get_activities.return_value = [
        {"activity_id": "123", "name": "러닝", "sport": "running",
         "distance": 6.1, "calories": 450, "elapsed_time": "00:45:00",
         "avg_hr": 154, "max_hr": 172, "start_time": "2026-04-20 07:00:00"},
    ]
    client.get_stress.return_value = [
        {"timestamp": "2026-04-20 00:00:00", "stress": 30},
    ]
    client.get_last_activity.return_value = {
        "activity_id": "999", "name": "러닝", "sport": "running",
    }
    return client


@pytest.fixture
def garmin_server(mock_garmin_client):
    return create_garmin_mcp_server(mock_garmin_client)


@pytest.fixture
def garmin_tools(mock_garmin_client, garmin_server):
    """Tool registry dict를 반환 (garmin_server 생성 후)."""
    return TOOL_REGISTRY.copy()


class TestGarminMcpServer:
    def test_server_created(self, garmin_server):
        assert garmin_server is not None
        assert garmin_server["name"] == "garmin"

    def test_server_has_type_sdk(self, garmin_server):
        assert garmin_server["type"] == "sdk"

    def test_all_basic_tools_registered(self, garmin_tools):
        assert "get_sleep" in garmin_tools
        assert "get_daily_summary" in garmin_tools
        assert "get_hrv" in garmin_tools
        assert "get_activities" in garmin_tools
        assert "get_stress" in garmin_tools


@pytest.mark.asyncio
class TestGetSleepTool:
    async def test_returns_json(self, mock_garmin_client, garmin_tools):
        tool_fn = garmin_tools["get_sleep"]
        result = await tool_fn.handler({"start": "2026-04-20", "end": "2026-04-20"})
        data = json.loads(result["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["score"] == 82

    async def test_default_date_range(self, mock_garmin_client, garmin_tools):
        tool_fn = garmin_tools["get_sleep"]
        await tool_fn.handler({})
        call_args = mock_garmin_client.get_sleep.call_args
        start, end = call_args[0]
        assert (end - start).days == 7

    async def test_max_date_range_90_days(self, mock_garmin_client, garmin_tools):
        tool_fn = garmin_tools["get_sleep"]
        await tool_fn.handler({"start": "2025-01-01", "end": "2026-04-20"})
        call_args = mock_garmin_client.get_sleep.call_args
        start, end = call_args[0]
        assert (end - start).days <= 90


@pytest.mark.asyncio
class TestGetDailySummaryTool:
    async def test_returns_json(self, mock_garmin_client, garmin_tools):
        tool_fn = garmin_tools["get_daily_summary"]
        result = await tool_fn.handler({"start": "2026-04-20", "end": "2026-04-20"})
        data = json.loads(result["content"][0]["text"])
        assert data[0]["rhr"] == 58


@pytest.mark.asyncio
class TestGetHrvTool:
    async def test_returns_json(self, mock_garmin_client, garmin_tools):
        tool_fn = garmin_tools["get_hrv"]
        result = await tool_fn.handler({})
        data = json.loads(result["content"][0]["text"])
        assert data[0]["weekly_avg"] == 45.0


@pytest.mark.asyncio
class TestGetActivitiesTool:
    async def test_returns_json(self, mock_garmin_client, garmin_tools):
        tool_fn = garmin_tools["get_activities"]
        result = await tool_fn.handler({})
        data = json.loads(result["content"][0]["text"])
        assert data[0]["sport"] == "running"


@pytest.mark.asyncio
class TestGetStressTool:
    async def test_returns_json(self, mock_garmin_client, garmin_tools):
        tool_fn = garmin_tools["get_stress"]
        result = await tool_fn.handler({})
        data = json.loads(result["content"][0]["text"])
        assert data[0]["stress"] == 30


# --- Task 2: Detail tools ---

class TestDetailToolsRegistered:
    def test_server_has_detail_tools(self, garmin_tools):
        assert "get_activity_detail" in garmin_tools
        assert "get_activity_splits" in garmin_tools
        assert "get_activity_hr_zones" in garmin_tools


@pytest.mark.asyncio
class TestGetActivityDetailTool:
    async def test_calls_client(self, mock_garmin_client, garmin_tools):
        mock_garmin_client.get_activity_detail.return_value = {
            "activity_id": "123", "sport": "running", "vo2_max": 37.0,
        }
        tool_fn = garmin_tools["get_activity_detail"]
        result = await tool_fn.handler({"activity_id": "123"})
        data = json.loads(result["content"][0]["text"])
        assert data["vo2_max"] == 37.0
        mock_garmin_client.get_activity_detail.assert_called_with("123")


@pytest.mark.asyncio
class TestGetActivitySplitsTool:
    async def test_calls_client(self, mock_garmin_client, garmin_tools):
        mock_garmin_client.get_activity_splits.return_value = [
            {"lap": 1, "distance_km": 1.0, "avg_hr": 145},
        ]
        tool_fn = garmin_tools["get_activity_splits"]
        result = await tool_fn.handler({"activity_id": "123"})
        data = json.loads(result["content"][0]["text"])
        assert data[0]["lap"] == 1


@pytest.mark.asyncio
class TestGetActivityHrZonesTool:
    async def test_calls_client(self, mock_garmin_client, garmin_tools):
        mock_garmin_client.get_activity_hr_zones.return_value = [
            {"zone": 1, "minutes": 10.3},
        ]
        tool_fn = garmin_tools["get_activity_hr_zones"]
        result = await tool_fn.handler({"activity_id": "123"})
        data = json.loads(result["content"][0]["text"])
        assert data[0]["zone"] == 1


# --- Task 8: get_last_activity tool ---

class TestGetLastActivityTool:
    def test_tool_registered(self, garmin_tools):
        assert "get_last_activity" in garmin_tools

    @pytest.mark.asyncio
    async def test_default_count_1(self, mock_garmin_client, garmin_tools):
        tool_fn = garmin_tools["get_last_activity"]
        await tool_fn.handler({})
        mock_garmin_client.get_last_activity.assert_called_with(count=1)

    @pytest.mark.asyncio
    async def test_count_capped_at_10(self, mock_garmin_client, garmin_tools):
        mock_garmin_client.get_last_activity.return_value = []
        tool_fn = garmin_tools["get_last_activity"]
        await tool_fn.handler({"count": 50})
        mock_garmin_client.get_last_activity.assert_called_with(count=10)


# ---------------------------------------------------------------------------
# Regression test: @tool handlers must not block the event loop
# ---------------------------------------------------------------------------

TICKER_INTERVAL = 0.05   # seconds between ticker ticks
SLOW_CALL_SLEEP = 0.3    # seconds the fake sync Garmin call blocks
MIN_TICKS_REQUIRED = 4   # ticker must fire at least this many times


@pytest.mark.asyncio
class TestGetSleepToolDoesNotBlockEventLoop:
    """Regression: get_sleep @tool handler must offload sync I/O to a thread.

    If garmin_client.get_sleep is called directly on the event loop, a concurrent
    ticker coroutine cannot run while the blocking call is executing. The handler
    must wrap the call with await asyncio.to_thread(...) so the loop stays free.
    """

    @pytest.fixture
    def slow_garmin_client(self):
        """MagicMock whose get_sleep blocks for SLOW_CALL_SLEEP seconds."""
        client = MagicMock()

        def _blocking_sleep(*_args, **_kwargs):
            time.sleep(SLOW_CALL_SLEEP)
            return [{"day": "2026-04-20", "total_sleep": "08:00:00", "score": 82}]

        client.get_sleep.side_effect = _blocking_sleep
        return client

    @pytest.fixture
    def slow_garmin_tools(self, slow_garmin_client):
        """TOOL_REGISTRY populated with the slow client."""
        create_garmin_mcp_server(slow_garmin_client)
        return TOOL_REGISTRY.copy()

    async def test_event_loop_not_blocked_during_get_sleep(
        self, slow_garmin_client, slow_garmin_tools
    ):
        """Ticker must fire >= MIN_TICKS_REQUIRED times while get_sleep handler runs."""
        tick_count = 0

        async def ticker():
            nonlocal tick_count
            while True:
                await asyncio.sleep(TICKER_INTERVAL)
                tick_count += 1

        tool_fn = slow_garmin_tools["get_sleep"]

        async def invoke_tool():
            return await tool_fn.handler({"start": "2026-04-20", "end": "2026-04-20"})

        ticker_task = asyncio.create_task(ticker())
        try:
            await asyncio.gather(invoke_tool(), return_exceptions=False)
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

        assert tick_count >= MIN_TICKS_REQUIRED, (
            f"Event loop was blocked: ticker fired only {tick_count} times "
            f"(need >= {MIN_TICKS_REQUIRED}). "
            f"get_sleep handler calls garmin_client.get_sleep() synchronously, "
            f"blocking the asyncio event loop. Wrap with await asyncio.to_thread(...)."
        )
