"""로컬 전처리 모듈 테스트."""
import pytest

from core.preprocessor import HealthPreprocessor


def _make_sleep_record(
    day: str,
    total_sec: int = 25200,
    awake_sec: int = 1800,
    deep_pct: float | None = 18.0,
    score: int = 80,
    bedtime: str | None = "23:30",
    avg_rr: float | None = 14.0,
    awake_count: int | None = 2,
    sleep_insight: str | None = None,
) -> dict:
    """summarize_last_night_sleep 테스트용 sleep record fixture."""
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return {
        "day": day,
        "total_sleep": f"{h:02d}:{m:02d}:{s:02d}",
        "deep_sleep": "01:30:00",
        "rem_sleep": "02:00:00",
        "awake": "00:30:00",
        "score": score,
        "total_seconds": total_sec,
        "awake_seconds": awake_sec,
        "deep_pct": deep_pct,
        "bedtime": bedtime,
        "avg_rr": avg_rr,
        "awake_count": awake_count,
        "sleep_insight": sleep_insight,
    }


class TestHealthPreprocessor:
    def test_summarize_sleep(self):
        sleep_data = [
            {"day": "2026-04-20", "total_sleep": "08:00:00", "deep_sleep": "01:30:00", "rem_sleep": "02:00:00", "score": 82, "bedtime": "23:30"},
            {"day": "2026-04-21", "total_sleep": "07:00:00", "deep_sleep": "01:00:00", "rem_sleep": "02:00:00", "score": 75, "bedtime": "23:50"},
            {"day": "2026-04-22", "total_sleep": "07:30:00", "deep_sleep": "02:00:00", "rem_sleep": "01:30:00", "score": 88, "bedtime": "23:45"},
        ]
        summary = HealthPreprocessor.summarize_sleep(sleep_data)
        assert "avg_total_hours" in summary
        assert summary["avg_total_hours"] == pytest.approx(7.5, abs=0.1)
        assert "avg_score" in summary
        assert summary["avg_score"] == pytest.approx(81.7, abs=0.1)
        assert "trend" in summary
        assert "avg_bedtime" in summary
        # minute-of-day median of [1410, 1430, 1425] = 1425 = "23:45"
        assert summary["avg_bedtime"] == "23:45"

    def test_summarize_heart_rate(self):
        daily_data = [
            {"day": "2026-04-20", "rhr": 58},
            {"day": "2026-04-21", "rhr": 57},
            {"day": "2026-04-22", "rhr": 56},
        ]
        summary = HealthPreprocessor.summarize_heart_rate(daily_data)
        assert "avg_rhr" in summary
        assert summary["avg_rhr"] == pytest.approx(57.0, abs=0.1)
        assert summary["trend"] == "improving"

    def test_summarize_activities(self):
        activities = [
            {"name": "Morning Run", "sport": "running", "elapsed_time": "00:45:00", "distance": 6.5, "calories": 450},
            {"name": "Weight Training", "sport": "training", "elapsed_time": "01:00:00", "distance": 0.0, "calories": 350},
        ]
        summary = HealthPreprocessor.summarize_activities(activities)
        assert summary["total_count"] == 2
        assert summary["total_calories"] == 800
        assert "by_sport" in summary

    def test_summarize_hrv(self):
        hrv_data = [
            {"day": "2026-04-20", "weekly_avg": 45.0, "last_night_avg": 48.0, "status": "BALANCED"},
            {"day": "2026-04-21", "weekly_avg": 44.0, "last_night_avg": 42.0, "status": "LOW"},
            {"day": "2026-04-22", "weekly_avg": 46.0, "last_night_avg": 50.0, "status": "BALANCED"},
        ]
        summary = HealthPreprocessor.summarize_hrv(hrv_data)
        assert "avg_weekly" in summary
        assert "trend" in summary
        assert "status_distribution" in summary

    def test_summarize_stress(self):
        stress_data = [
            {"timestamp": "2026-04-20 10:00:00", "stress": 25},
            {"timestamp": "2026-04-20 14:00:00", "stress": 40},
            {"timestamp": "2026-04-21 10:00:00", "stress": 30},
        ]
        summary = HealthPreprocessor.summarize_stress(stress_data)
        assert "avg_stress" in summary
        assert summary["avg_stress"] == pytest.approx(31.7, abs=0.1)

    def test_create_weekly_summary(self):
        summary = HealthPreprocessor.create_weekly_summary(
            sleep={"avg_total_hours": 7.2, "avg_score": 80, "trend": "stable"},
            heart_rate={"avg_rhr": 57, "trend": "improving"},
            activities={"total_count": 4, "total_calories": 1800, "by_sport": {"running": 2, "training": 2}},
            hrv={"avg_weekly": 45.0, "trend": "improving", "status_distribution": {"BALANCED": 5, "LOW": 2}},
            stress={"avg_stress": 32},
            body_metrics={"body_fat_pct": 15.2, "muscle_mass_kg": 34.5},
        )
        assert "period" in summary
        assert "sleep" in summary
        assert "heart_rate" in summary

    def test_empty_data(self):
        summary = HealthPreprocessor.summarize_sleep([])
        assert summary["avg_total_hours"] == 0
        assert summary["avg_score"] == 0


class TestSummarizeLastNightSleep:
    """어젯밤 단일 수면 derived 필드 요약."""

    def test_normal_case_all_derived_fields(self):
        sleep_data = [
            _make_sleep_record("2026-04-24", deep_pct=15.0, score=78),
            _make_sleep_record("2026-04-25", deep_pct=17.0, score=80),
            _make_sleep_record("2026-04-26", deep_pct=16.0, score=82),
            _make_sleep_record("2026-04-27", deep_pct=18.0, score=79),
            _make_sleep_record("2026-04-28", deep_pct=14.0, score=77),
            _make_sleep_record("2026-04-29", deep_pct=19.0, score=85),
            _make_sleep_record(
                "2026-04-30",
                total_sec=23040,
                awake_sec=3000,
                deep_pct=13.0,
                score=75,
                bedtime="04:12",
                avg_rr=14.2,
                awake_count=3,
                sleep_insight="NEGATIVE_LATE_BED_TIME",
            ),
        ]
        hrv_data = [
            {"day": "2026-04-30", "weekly_avg": 50.0, "last_night_avg": 42.0,
             "baseline_low": 45.0, "baseline_upper": 55.0, "status": "LOW"},
        ]
        result = HealthPreprocessor.summarize_last_night_sleep(
            sleep_data, hrv_data, "2026-04-30"
        )
        assert result is not None
        assert result["hours"] == pytest.approx(6.4, abs=0.05)
        assert result["score"] == 75
        # efficiency: (23040 - 3000) / 23040 * 100 = 86.97
        assert result["efficiency_pct"] == pytest.approx(87.0, abs=0.2)
        # deep_pct_delta: 13.0 - median(15,17,16,18,14,19) = 13.0 - 16.5 = -3.5
        assert result["deep_pct_delta"] == pytest.approx(-3.5, abs=0.05)
        # hrv_z: (42 - 50) / max(55 - 45, 1) = -0.8
        assert result["hrv_z"] == pytest.approx(-0.8, abs=0.05)
        assert result["avg_rr"] == 14.2
        assert result["awake_count"] == 3
        assert result["bedtime"] == "04:12"
        assert result["sleep_insight"] == "NEGATIVE_LATE_BED_TIME"

    def test_no_sleep_data_returns_none(self):
        result = HealthPreprocessor.summarize_last_night_sleep([], [], "2026-04-30")
        assert result is None

    def test_only_one_day_no_prior_baseline(self):
        sleep_data = [_make_sleep_record("2026-04-30", deep_pct=18.0)]
        result = HealthPreprocessor.summarize_last_night_sleep(
            sleep_data, [], "2026-04-30"
        )
        # last_night exists, but no prior days for delta
        assert result is not None
        assert result["deep_pct_delta"] is None

    def test_last_night_missing_returns_none(self):
        # sleep_data has past 6 days but not "today" (2026-04-30)
        sleep_data = [
            _make_sleep_record(f"2026-04-{d}") for d in (24, 25, 26, 27, 28, 29)
        ]
        result = HealthPreprocessor.summarize_last_night_sleep(
            sleep_data, [], "2026-04-30"
        )
        assert result is None

    def test_no_hrv_data_z_is_none(self):
        sleep_data = [
            _make_sleep_record(f"2026-04-{d}", deep_pct=16.0) for d in (24, 25, 26, 27, 28, 29)
        ] + [_make_sleep_record("2026-04-30", deep_pct=15.0)]
        result = HealthPreprocessor.summarize_last_night_sleep(
            sleep_data, [], "2026-04-30"
        )
        assert result is not None
        assert result["hrv_z"] is None
        assert result["efficiency_pct"] is not None  # other fields still computed

    def test_awake_zero_efficiency_100(self):
        sleep_data = [
            _make_sleep_record("2026-04-30", total_sec=28800, awake_sec=0)
        ]
        result = HealthPreprocessor.summarize_last_night_sleep(
            sleep_data, [], "2026-04-30"
        )
        assert result is not None
        assert result["efficiency_pct"] == 100.0

    def test_total_zero_no_zero_division(self):
        sleep_data = [
            _make_sleep_record("2026-04-30", total_sec=0, awake_sec=0, deep_pct=10.0)
        ]
        result = HealthPreprocessor.summarize_last_night_sleep(
            sleep_data, [], "2026-04-30"
        )
        assert result is not None
        assert result["efficiency_pct"] is None
        assert result["hours"] is None

    def test_baseline_range_zero_z_is_none(self):
        sleep_data = [_make_sleep_record("2026-04-30")]
        hrv_data = [
            {"day": "2026-04-30", "weekly_avg": 50.0, "last_night_avg": 48.0,
             "baseline_low": 50.0, "baseline_upper": 50.0, "status": "BALANCED"},
        ]
        result = HealthPreprocessor.summarize_last_night_sleep(
            sleep_data, hrv_data, "2026-04-30"
        )
        assert result is not None
        assert result["hrv_z"] is None  # baseline_upper - baseline_low = 0 → guarded
