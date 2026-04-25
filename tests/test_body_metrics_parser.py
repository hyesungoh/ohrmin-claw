"""체성분 자연어 파싱 테스트."""
import pytest

from core.body_metrics_parser import BodyMetricsParser


class TestBodyMetricsParser:
    def test_parse_full_message(self):
        msg = "인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1"
        result = BodyMetricsParser.parse(msg)
        assert result["weight_kg"] == 72.0
        assert result["body_fat_pct"] == 15.2
        assert result["muscle_mass_kg"] == 34.5
        assert result["bmi"] == 22.1
        assert result["date"] is not None

    def test_parse_with_date(self):
        msg = "인바디 결과 2026-04-01 체중 73kg 체지방률 16.0% 골격근량 34.0kg BMI 22.5"
        result = BodyMetricsParser.parse(msg)
        assert result["date"] == "2026-04-01"
        assert result["weight_kg"] == 73.0

    def test_parse_partial_data(self):
        msg = "인바디 체중 72kg 체지방률 15.2%"
        result = BodyMetricsParser.parse(msg)
        assert result["weight_kg"] == 72.0
        assert result["body_fat_pct"] == 15.2
        assert result["muscle_mass_kg"] is None
        assert result["bmi"] is None

    def test_parse_decimal_values(self):
        msg = "인바디 결과 체중 72.3kg 체지방률 15.25% 골격근량 34.55kg"
        result = BodyMetricsParser.parse(msg)
        assert result["weight_kg"] == 72.3
        assert result["body_fat_pct"] == 15.25
        assert result["muscle_mass_kg"] == 34.55

    def test_is_body_metrics_message_true(self):
        assert BodyMetricsParser.is_body_metrics_message("인바디 결과 체중 72kg") is True
        assert BodyMetricsParser.is_body_metrics_message("InBody 결과 체중 72kg") is True

    def test_is_body_metrics_message_false(self):
        assert BodyMetricsParser.is_body_metrics_message("오늘 수면 어때?") is False
        assert BodyMetricsParser.is_body_metrics_message("운동 기록 보여줘") is False

    def test_parse_no_data_returns_none(self):
        result = BodyMetricsParser.parse("인���디")
        assert result is None

    def test_parse_various_formats(self):
        msg = "인바디결과 체중72kg 체지방률15.2% 골격근량34.5kg"
        result = BodyMetricsParser.parse(msg)
        assert result["weight_kg"] == 72.0
