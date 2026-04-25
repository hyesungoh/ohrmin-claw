"""리포트 템플릿 테스트."""
import pytest

from core.report import ReportGenerator


@pytest.fixture
def weekly_summary():
    return {
        "period": "2026-04-14 ~ 2026-04-20",
        "sleep": {"avg_total_hours": 7.2, "avg_score": 80, "trend": "stable"},
        "heart_rate": {"avg_rhr": 57, "trend": "improving"},
        "activities": {
            "total_count": 4,
            "total_calories": 1800,
            "total_distance": 20.5,
            "total_time_hours": 3.5,
            "by_sport": {"running": 2, "training": 2},
        },
        "hrv": {"avg_weekly": 45.0, "trend": "improving", "status_distribution": {"BALANCED": 5, "LOW": 2}},
        "stress": {"avg_stress": 32},
        "body_metrics": {"body_fat_pct": 15.2, "muscle_mass_kg": 34.5, "weight_kg": 71.5, "bmi": 22.0},
    }


class TestReportGenerator:
    def test_weekly_report_has_sections(self, weekly_summary):
        report = ReportGenerator.weekly_report(weekly_summary)
        assert "주간 건강 리포트" in report
        assert "수면" in report
        assert "심박수" in report
        assert "운동" in report
        assert "HRV" in report
        assert "스트레스" in report

    def test_weekly_report_has_data(self, weekly_summary):
        report = ReportGenerator.weekly_report(weekly_summary)
        assert "7.2" in report
        assert "57" in report
        assert "15.2" in report

    def test_weekly_report_is_markdown(self, weekly_summary):
        report = ReportGenerator.weekly_report(weekly_summary)
        assert report.startswith("#")
        assert "**" in report or "|" in report

    def test_weekly_report_without_body_metrics(self, weekly_summary):
        weekly_summary["body_metrics"] = None
        report = ReportGenerator.weekly_report(weekly_summary)
        assert "주간 건강 리포트" in report

    def test_monthly_report(self, weekly_summary):
        report = ReportGenerator.monthly_report(weekly_summary)
        assert "월간 건강 리포트" in report
