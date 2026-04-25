"""로컬 전처리 모듈 테스트."""
import pytest

from core.preprocessor import HealthPreprocessor


class TestHealthPreprocessor:
    def test_summarize_sleep(self):
        sleep_data = [
            {"day": "2026-04-20", "total_sleep": "08:00:00", "deep_sleep": "01:30:00", "rem_sleep": "02:00:00", "score": 82},
            {"day": "2026-04-21", "total_sleep": "07:00:00", "deep_sleep": "01:00:00", "rem_sleep": "02:00:00", "score": 75},
            {"day": "2026-04-22", "total_sleep": "07:30:00", "deep_sleep": "02:00:00", "rem_sleep": "01:30:00", "score": 88},
        ]
        summary = HealthPreprocessor.summarize_sleep(sleep_data)
        assert "avg_total_hours" in summary
        assert summary["avg_total_hours"] == pytest.approx(7.5, abs=0.1)
        assert "avg_score" in summary
        assert summary["avg_score"] == pytest.approx(81.7, abs=0.1)
        assert "trend" in summary

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
