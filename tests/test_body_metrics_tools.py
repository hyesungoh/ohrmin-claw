"""Body Metrics MCP tool 테스트."""
import json
from unittest.mock import MagicMock

import pytest

from core.body_metrics_tools import create_body_metrics_mcp_server, TOOL_REGISTRY as BM_TOOL_REGISTRY


@pytest.fixture
def mock_metrics_manager():
    mgr = MagicMock()
    mgr.read_all.return_value = [
        {"date": "2026-04-20", "weight_kg": 75.0, "body_fat_pct": 18.0,
         "muscle_mass_kg": 33.0, "bmi": 24.5, "source": "manual"},
        {"date": "2026-04-13", "weight_kg": 75.5, "body_fat_pct": 18.5,
         "muscle_mass_kg": 32.8, "bmi": 24.7, "source": "manual"},
    ]
    return mgr


@pytest.fixture
def metrics_server(mock_metrics_manager):
    return create_body_metrics_mcp_server(mock_metrics_manager)


@pytest.fixture
def metrics_tools(mock_metrics_manager, metrics_server):
    return BM_TOOL_REGISTRY.copy()


class TestBodyMetricsMcpServer:
    def test_server_created(self, metrics_server):
        assert metrics_server is not None
        assert metrics_server["name"] == "body_metrics"

    def test_has_required_tools(self, metrics_tools):
        assert "add_body_measurement" in metrics_tools
        assert "get_body_metrics_trend" in metrics_tools
        assert "get_body_metrics_history" in metrics_tools


@pytest.mark.asyncio
class TestAddBodyMeasurementTool:
    async def test_add_weight_only(self, mock_metrics_manager, metrics_tools):
        tool_fn = metrics_tools["add_body_measurement"]
        result = await tool_fn.handler({"weight_kg": 74.5})
        mock_metrics_manager.add_entry.assert_called_once()
        call_kwargs = mock_metrics_manager.add_entry.call_args[1]
        assert call_kwargs["weight_kg"] == 74.5

    async def test_requires_at_least_one_field(self, mock_metrics_manager, metrics_tools):
        tool_fn = metrics_tools["add_body_measurement"]
        result = await tool_fn.handler({})
        data = json.loads(result["content"][0]["text"])
        assert "error" in data


@pytest.mark.asyncio
class TestGetBodyMetricsHistoryTool:
    async def test_returns_recent_entries(self, mock_metrics_manager, metrics_tools):
        tool_fn = metrics_tools["get_body_metrics_history"]
        result = await tool_fn.handler({"count": 5})
        data = json.loads(result["content"][0]["text"])
        assert len(data) == 2


@pytest.mark.asyncio
class TestGetBodyMetricsTrendTool:
    async def test_returns_trend_for_field(self, mock_metrics_manager, metrics_tools):
        tool_fn = metrics_tools["get_body_metrics_trend"]
        result = await tool_fn.handler({"field": "weight_kg", "days": 30})
        data = json.loads(result["content"][0]["text"])
        assert "values" in data
