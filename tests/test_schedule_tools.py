"""스케줄 MCP tool 테스트 — 구조화 입력 저장, list/pause/resume/remove, 상한 강제."""
import datetime
import json

import pytest

from core.scheduler import CronStore
from core.schedule_tools import create_schedule_mcp_server, TOOL_REGISTRY


KST = datetime.timezone(datetime.timedelta(hours=9))
FROZEN_NOW = datetime.datetime(2026, 7, 4, 10, 0, tzinfo=KST)


def _extract(result: dict):
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def store(tmp_path):
    return CronStore(str(tmp_path / "cron_jobs.json"))


@pytest.fixture
def tools(store):
    """프리즈드 클록 + 기본 채널 + 낮은 상한으로 서버 생성 후 TOOL_REGISTRY 반환."""
    create_schedule_mcp_server(
        store, default_channel_id="777", max_jobs=3, now_fn=lambda: FROZEN_NOW
    )
    return TOOL_REGISTRY


class TestScheduleCreate:
    @pytest.mark.asyncio
    async def test_create_cron_stored(self, tools, store):
        result = await tools["schedule_create"].handler(
            {"prompt": "일요일 저녁 리포트", "schedule": "0 20 * * 0"}
        )
        payload = _extract(result)
        assert payload["success"] is True
        assert payload["job"]["schedule"] == "0 20 * * 0"
        # 기본 채널로 폴백.
        assert payload["job"]["deliver_channel_id"] == "777"
        assert payload["job"]["next_run_iso"].startswith("2026-07-05T20:00")
        assert store.count() == 1

    @pytest.mark.asyncio
    async def test_create_relative_stored(self, tools, store):
        result = await tools["schedule_create"].handler(
            {"prompt": "30분 뒤 알림", "schedule": "30m", "deliver_channel_id": "555"}
        )
        payload = _extract(result)
        assert payload["success"] is True
        assert payload["job"]["deliver_channel_id"] == "555"
        assert payload["job"]["next_run_iso"].startswith("2026-07-04T10:30")

    @pytest.mark.asyncio
    async def test_create_invalid_schedule_rejected(self, tools, store):
        result = await tools["schedule_create"].handler(
            {"prompt": "x", "schedule": "매일 아침"}
        )
        payload = _extract(result)
        assert payload["success"] is False
        assert "형식" in payload["error"]
        assert store.count() == 0

    @pytest.mark.asyncio
    async def test_create_empty_prompt_rejected(self, tools, store):
        result = await tools["schedule_create"].handler(
            {"prompt": "  ", "schedule": "30m"}
        )
        assert _extract(result)["success"] is False
        assert store.count() == 0

    @pytest.mark.asyncio
    async def test_max_jobs_cap_enforced(self, tools, store):
        for i in range(3):  # max_jobs=3
            r = await tools["schedule_create"].handler(
                {"prompt": f"job{i}", "schedule": "0 20 * * 0"}
            )
            assert _extract(r)["success"] is True
        # 4번째는 거부.
        r = await tools["schedule_create"].handler(
            {"prompt": "overflow", "schedule": "0 20 * * 0"}
        )
        payload = _extract(r)
        assert payload["success"] is False
        assert "상한" in payload["error"]
        assert store.count() == 3


class TestScheduleListPauseResumeRemove:
    @pytest.mark.asyncio
    async def test_list_returns_jobs(self, tools, store):
        await tools["schedule_create"].handler({"prompt": "a", "schedule": "0 20 * * 0"})
        result = await tools["schedule_list"].handler({})
        payload = _extract(result)
        assert len(payload["jobs"]) == 1
        assert payload["jobs"][0]["prompt"] == "a"

    @pytest.mark.asyncio
    async def test_pause_and_resume(self, tools, store):
        created = _extract(
            await tools["schedule_create"].handler({"prompt": "a", "schedule": "0 20 * * 0"})
        )
        jid = created["job"]["id"]
        paused = _extract(await tools["schedule_pause"].handler({"id": jid}))
        assert paused["success"] is True
        assert paused["job"]["paused"] is True
        resumed = _extract(await tools["schedule_resume"].handler({"id": jid}))
        assert resumed["job"]["paused"] is False

    @pytest.mark.asyncio
    async def test_remove(self, tools, store):
        created = _extract(
            await tools["schedule_create"].handler({"prompt": "a", "schedule": "0 20 * * 0"})
        )
        jid = created["job"]["id"]
        removed = _extract(await tools["schedule_remove"].handler({"id": jid}))
        assert removed["success"] is True
        assert removed["removed"] == jid
        assert store.count() == 0

    @pytest.mark.asyncio
    async def test_pause_unknown_id_errors(self, tools, store):
        payload = _extract(await tools["schedule_pause"].handler({"id": "nope"}))
        assert payload["success"] is False


class TestToolRegistration:
    def test_all_tools_registered(self, tools):
        assert set(tools) == {
            "schedule_create",
            "schedule_list",
            "schedule_pause",
            "schedule_resume",
            "schedule_remove",
        }
