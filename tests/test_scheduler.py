"""cron 스케줄러 테스트 — 프리즈드 클록 매처, CronStore CRUD/영속, one-shot 자기삭제."""
import datetime
import json

import pytest

from core.scheduler import (
    CronStore,
    SCHEMA_VERSION,
    compute_next_run,
    due,
    is_relative,
    validate_schedule,
    _cron_matches,
    _parse_cron,
)


# 로컬 aware 프리즈드 클록(KST, DST 없음)으로 결정적 테스트.
KST = datetime.timezone(datetime.timedelta(hours=9))


def _at(y, mo, d, h, mi):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=KST)


class TestMatcherFields:
    def test_every_15_minutes(self):
        parsed = _parse_cron("*/15 * * * *")
        assert _cron_matches(parsed, _at(2026, 7, 4, 10, 0))
        assert _cron_matches(parsed, _at(2026, 7, 4, 10, 15))
        assert _cron_matches(parsed, _at(2026, 7, 4, 10, 30))
        assert _cron_matches(parsed, _at(2026, 7, 4, 10, 45))
        assert not _cron_matches(parsed, _at(2026, 7, 4, 10, 7))

    def test_sunday_8pm(self):
        # 0 20 * * 0 — 2026-07-05는 일요일.
        parsed = _parse_cron("0 20 * * 0")
        assert _at(2026, 7, 5, 20, 0).isoweekday() == 7  # 일요일
        assert _cron_matches(parsed, _at(2026, 7, 5, 20, 0))
        assert not _cron_matches(parsed, _at(2026, 7, 4, 20, 0))  # 토요일
        assert not _cron_matches(parsed, _at(2026, 7, 5, 21, 0))  # 시간 불일치

    def test_weekday_range_6am(self):
        # 0 6 * * 1-5 — 월~금 06:00.
        parsed = _parse_cron("0 6 * * 1-5")
        assert _cron_matches(parsed, _at(2026, 7, 6, 6, 0))  # 월
        assert _cron_matches(parsed, _at(2026, 7, 10, 6, 0))  # 금
        assert not _cron_matches(parsed, _at(2026, 7, 11, 6, 0))  # 토
        assert not _cron_matches(parsed, _at(2026, 7, 5, 6, 0))  # 일

    def test_dom_dow_both_restricted_is_or(self):
        # 둘 다 제한 시 OR: 15일 '또는' 월요일.
        parsed = _parse_cron("0 0 15 * 1")
        assert _cron_matches(parsed, _at(2026, 7, 15, 0, 0))  # 15일(수요일)
        assert _cron_matches(parsed, _at(2026, 7, 6, 0, 0))  # 월요일(6일)
        assert not _cron_matches(parsed, _at(2026, 7, 7, 0, 0))  # 화, 7일

    def test_dow_7_is_sunday(self):
        # dow 7도 일요일로 정규화.
        parsed = _parse_cron("0 20 * * 7")
        assert _cron_matches(parsed, _at(2026, 7, 5, 20, 0))  # 일요일

    def test_list_and_range_and_step(self):
        parsed = _parse_cron("0,30 9-17/4 * * *")
        # 분 0,30 · 시 9,13,17.
        assert _cron_matches(parsed, _at(2026, 7, 4, 9, 0))
        assert _cron_matches(parsed, _at(2026, 7, 4, 13, 30))
        assert _cron_matches(parsed, _at(2026, 7, 4, 17, 0))
        assert not _cron_matches(parsed, _at(2026, 7, 4, 11, 0))


class TestComputeNextRun:
    def test_sunday_8pm_next(self):
        now = _at(2026, 7, 4, 10, 0)  # 토요일 오전
        nr = compute_next_run({"schedule": "0 20 * * 0"}, now)
        assert nr == _at(2026, 7, 5, 20, 0)  # 다음날(일) 20시

    def test_every_15_next_is_strictly_after(self):
        now = _at(2026, 7, 4, 10, 15)  # 정확히 매치되는 순간
        nr = compute_next_run({"schedule": "*/15 * * * *"}, now)
        # 엄격히 이후여야 하므로 10:30(현재 10:15은 스킵).
        assert nr == _at(2026, 7, 4, 10, 30)

    def test_weekday_6am_from_friday_evening(self):
        now = _at(2026, 7, 10, 20, 0)  # 금요일 저녁
        nr = compute_next_run({"schedule": "0 6 * * 1-5"}, now)
        assert nr == _at(2026, 7, 13, 6, 0)  # 다음 월요일 06:00

    def test_relative_minutes(self):
        now = _at(2026, 7, 4, 10, 0)
        nr = compute_next_run({"schedule": "30m"}, now)
        assert nr == _at(2026, 7, 4, 10, 30)

    def test_relative_hours_and_days(self):
        now = _at(2026, 7, 4, 10, 0)
        assert compute_next_run({"schedule": "2h"}, now) == _at(2026, 7, 4, 12, 0)
        assert compute_next_run({"schedule": "1d"}, now) == _at(2026, 7, 5, 10, 0)

    def test_seconds_truncated_to_minute_boundary(self):
        now = _at(2026, 7, 4, 10, 15).replace(second=42)
        nr = compute_next_run({"schedule": "*/15 * * * *"}, now)
        assert nr == _at(2026, 7, 4, 10, 30)
        assert nr.second == 0


class TestDue:
    def test_due_when_now_past_next_run(self):
        job = {"next_run_iso": _at(2026, 7, 4, 10, 0).isoformat(), "paused": False}
        assert due(job, _at(2026, 7, 4, 10, 0)) is True
        assert due(job, _at(2026, 7, 4, 10, 1)) is True
        assert due(job, _at(2026, 7, 4, 9, 59)) is False

    def test_paused_never_due(self):
        job = {"next_run_iso": _at(2026, 7, 4, 10, 0).isoformat(), "paused": True}
        assert due(job, _at(2026, 7, 4, 11, 0)) is False

    def test_missing_next_run_not_due(self):
        assert due({"paused": False}, _at(2026, 7, 4, 10, 0)) is False


class TestValidateSchedule:
    def test_valid_cron_and_relative(self):
        validate_schedule("0 20 * * 0")
        validate_schedule("*/15 * * * *")
        validate_schedule("30m")
        validate_schedule("2h")
        validate_schedule("1d")

    def test_is_relative(self):
        assert is_relative("30m")
        assert is_relative("2h")
        assert not is_relative("0 20 * * 0")

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "0 20 * *", "0 20 * * * *", "99 * * * *", "0 20 * * 9", "abc", "0d"],
    )
    def test_invalid_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_schedule(bad)


class TestCronStoreCRUD:
    @pytest.fixture
    def store(self, tmp_path):
        return CronStore(str(tmp_path / "cron_jobs.json"))

    def test_create_and_get(self, store):
        now = _at(2026, 7, 4, 10, 0)
        job = store.create("수면 브리핑", "0 20 * * 0", now, deliver_channel_id="123", max_turns=8)
        assert job["schedule"] == "0 20 * * 0"
        assert job["deliver_channel_id"] == "123"
        assert job["max_turns"] == 8
        assert job["paused"] is False
        assert job["next_run_iso"] == _at(2026, 7, 5, 20, 0).isoformat()
        assert store.get(job["id"]) == job
        assert store.count() == 1

    def test_pause_resume_remove(self, store):
        now = _at(2026, 7, 4, 10, 0)
        job = store.create("x", "0 6 * * 1-5", now)
        jid = job["id"]
        assert store.pause(jid)["paused"] is True
        assert store.resume(jid)["paused"] is False
        assert store.remove(jid)["id"] == jid
        assert store.get(jid) is None
        assert store.remove("nonexistent") is None

    def test_create_invalid_schedule_raises(self, store):
        with pytest.raises(ValueError):
            store.create("x", "not a cron", _at(2026, 7, 4, 10, 0))


class TestCronStorePersistence:
    def test_atomic_write_and_restart_reload(self, tmp_path):
        path = str(tmp_path / "sub" / "cron_jobs.json")
        now = _at(2026, 7, 4, 10, 0)
        store = CronStore(path)
        job = store.create("재시작 테스트", "0 20 * * 0", now, deliver_channel_id="999")
        # 파일이 실제로 원자적으로 기록됐는지 + schema_version 포함.
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["schema_version"] == SCHEMA_VERSION
        assert len(data["jobs"]) == 1
        # 새 인스턴스로 재오픈해도 잡 생존(재시작 시뮬).
        store2 = CronStore(path)
        assert store2.count() == 1
        assert store2.get(job["id"])["prompt"] == "재시작 테스트"

    def test_corrupt_file_does_not_crash(self, tmp_path):
        path = str(tmp_path / "cron_jobs.json")
        with open(path, "w") as f:
            f.write("{ not valid json")
        store = CronStore(path)  # 로드 실패해도 예외 없이 빈 스토어.
        assert store.count() == 0


class TestDueJobsAndMarkFired:
    @pytest.fixture
    def store(self, tmp_path):
        return CronStore(str(tmp_path / "cron_jobs.json"))

    def test_due_jobs_filters_paused_and_future(self, store):
        now = _at(2026, 7, 5, 20, 0)
        due_job = store.create("due", "0 20 * * 0", _at(2026, 7, 4, 10, 0))  # next=일 20:00
        future_job = store.create("future", "0 6 * * 1-5", now)  # next=월 06:00(미래)
        paused_job = store.create("paused", "0 20 * * 0", _at(2026, 7, 4, 10, 0))
        store.pause(paused_job["id"])

        ready = store.due_jobs(now)
        ids = {j["id"] for j in ready}
        assert due_job["id"] in ids
        assert future_job["id"] not in ids
        assert paused_job["id"] not in ids

    def test_mark_fired_cron_recomputes_next_run(self, store):
        create_now = _at(2026, 7, 4, 10, 0)
        job = store.create("weekly", "0 20 * * 0", create_now)
        assert job["next_run_iso"] == _at(2026, 7, 5, 20, 0).isoformat()
        fire_now = _at(2026, 7, 5, 20, 0)
        updated = store.mark_fired(job["id"], fire_now)
        assert updated is not None
        assert updated["last_run_iso"] == fire_now.isoformat()
        # 다음 주 일요일로 갱신.
        assert updated["next_run_iso"] == _at(2026, 7, 12, 20, 0).isoformat()

    def test_mark_fired_relative_self_deletes(self, store):
        now = _at(2026, 7, 4, 10, 0)
        job = store.create("one-shot", "30m", now)
        assert store.count() == 1
        result = store.mark_fired(job["id"], _at(2026, 7, 4, 10, 30))
        assert result is None  # 상대 one-shot은 발화 후 자기 삭제.
        assert store.count() == 0
        assert store.get(job["id"]) is None


class TestMaxJobsBoundaryHelper:
    def test_count_reflects_creations(self, tmp_path):
        store = CronStore(str(tmp_path / "cron_jobs.json"))
        now = _at(2026, 7, 4, 10, 0)
        for i in range(3):
            store.create(f"job{i}", "0 20 * * 0", now)
        assert store.count() == 3
