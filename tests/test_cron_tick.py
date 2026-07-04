"""cron tick 배선 테스트 — due 실행/paused 스킵, per-job 실패 격리, 무인 도구셋, one-shot 삭제."""
import datetime
from unittest.mock import AsyncMock, patch

import pytest

from core.scheduler import CronStore


KST = datetime.timezone(datetime.timedelta(hours=9))


def _at(y, mo, d, h, mi):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=KST)


@pytest.fixture
def store(tmp_path):
    return CronStore(str(tmp_path / "cron_jobs.json"))


class TestCronTickOnce:
    @pytest.mark.asyncio
    async def test_runs_due_skips_paused_and_future(self, store):
        import bot.main as main

        create_now = _at(2026, 7, 4, 10, 0)
        due_job = store.create("due", "0 20 * * 0", create_now, deliver_channel_id="111")
        future_job = store.create("future", "0 6 * * 1-5", _at(2026, 7, 5, 20, 0))
        paused = store.create("paused", "0 20 * * 0", create_now)
        store.pause(paused["id"])

        tick_now = _at(2026, 7, 5, 20, 0)  # 일요일 20:00 — due_job만 due
        calls = []

        async def fake_run(prompt, channel_id, thread_name, **kwargs):
            calls.append({"prompt": prompt, "channel_id": channel_id, "kwargs": kwargs})

        with patch.object(main, "cron_store", store), \
             patch.object(main, "run_agent_to_channel", new=fake_run):
            await main._cron_tick_once(tick_now)

        assert len(calls) == 1
        assert calls[0]["prompt"] == "due"
        assert calls[0]["channel_id"] == "111"
        # 무인 초기자 도구셋(schedule mutation 제외)이 전달됨.
        assert calls[0]["kwargs"]["allowed_tools"] == main.UNATTENDED_ALLOWED_TOOLS
        assert "mcp__schedule__schedule_create" not in calls[0]["kwargs"]["allowed_tools"]
        # due_job next_run이 다음 주로 전진.
        assert store.get(due_job["id"])["next_run_iso"] == _at(2026, 7, 12, 20, 0).isoformat()
        assert store.get(future_job["id"])["last_run_iso"] is None

    @pytest.mark.asyncio
    async def test_per_job_failure_isolation(self, store):
        """한 잡이 예외를 던져도 나머지 due 잡이 계속 처리되고 루프가 죽지 않는다."""
        import bot.main as main

        create_now = _at(2026, 7, 4, 10, 0)
        job_a = store.create("boom", "0 20 * * 0", create_now)
        job_b = store.create("ok", "0 20 * * 0", create_now)

        tick_now = _at(2026, 7, 5, 20, 0)
        ran = []

        async def fake_run(prompt, channel_id, thread_name, **kwargs):
            ran.append(prompt)
            if prompt == "boom":
                raise RuntimeError("intentional failure")

        with patch.object(main, "cron_store", store), \
             patch.object(main, "run_agent_to_channel", new=fake_run):
            # tick 자체는 예외를 전파하지 않아야 한다.
            await main._cron_tick_once(tick_now)

        # 두 잡 모두 실행 시도됨(하나 실패해도 나머지 진행).
        assert set(ran) == {"boom", "ok"}
        # 실패한 잡도 next_run 전진(매 분 폭주 방지).
        assert store.get(job_a["id"])["next_run_iso"] == _at(2026, 7, 12, 20, 0).isoformat()
        assert store.get(job_b["id"])["next_run_iso"] == _at(2026, 7, 12, 20, 0).isoformat()

    @pytest.mark.asyncio
    async def test_relative_one_shot_self_deletes_after_fire(self, store):
        import bot.main as main

        create_now = _at(2026, 7, 4, 10, 0)
        one_shot = store.create("30분 뒤", "30m", create_now)
        assert store.count() == 1

        tick_now = _at(2026, 7, 4, 10, 30)  # next_run 도달
        with patch.object(main, "cron_store", store), \
             patch.object(main, "run_agent_to_channel", new=AsyncMock()):
            await main._cron_tick_once(tick_now)

        # 발화 후 자기 삭제.
        assert store.get(one_shot["id"]) is None
        assert store.count() == 0

    @pytest.mark.asyncio
    async def test_no_due_jobs_no_calls(self, store):
        import bot.main as main

        store.create("future", "0 6 * * 1-5", _at(2026, 7, 5, 20, 0))
        run_mock = AsyncMock()
        with patch.object(main, "cron_store", store), \
             patch.object(main, "run_agent_to_channel", new=run_mock):
            await main._cron_tick_once(_at(2026, 7, 5, 20, 1))
        run_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_deliver_channel_falls_back_to_notify(self, store):
        import bot.main as main

        job = store.create("no channel", "0 20 * * 0", _at(2026, 7, 4, 10, 0))
        assert job["deliver_channel_id"] is None
        captured = {}

        async def fake_run(prompt, channel_id, thread_name, **kwargs):
            captured["channel_id"] = channel_id

        with patch.object(main, "cron_store", store), \
             patch.object(main, "NOTIFY_CHANNEL_ID", "999"), \
             patch.object(main, "run_agent_to_channel", new=fake_run):
            await main._cron_tick_once(_at(2026, 7, 5, 20, 0))

        assert captured["channel_id"] == "999"


class TestSchedulerGating:
    def test_scheduler_disabled_by_default(self):
        import bot.main as main
        # 기본 kill-switch off (SCHEDULER_ENABLED 미설정).
        assert main.SCHEDULER_ENABLED is False
