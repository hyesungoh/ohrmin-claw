"""Body Metrics CSV 데이터 접근 레이어 테스트."""
import csv
import datetime
import os

import pytest

from core.body_metrics import BodyMetricsManager


@pytest.fixture
def body_metrics_csv(tmp_path):
    """테스트용 Body Metrics CSV 생성."""
    csv_path = tmp_path / "body_metrics.csv"
    csv_path.write_text(
        "date,weight_kg,body_fat_pct,muscle_mass_kg,bmi,source\n"
        "2026-03-01,73.0,16.5,34.0,22.5,manual\n"
        "2026-04-01,72.0,15.8,34.3,22.2,manual\n"
        "2026-04-15,71.5,15.2,34.5,22.0,inbody\n"
    )
    return str(csv_path)


@pytest.fixture
def empty_csv(tmp_path):
    csv_path = tmp_path / "body_metrics.csv"
    csv_path.write_text("date,weight_kg,body_fat_pct,muscle_mass_kg,bmi,source\n")
    return str(csv_path)


class TestBodyMetricsManager:
    def test_read_all(self, body_metrics_csv):
        mgr = BodyMetricsManager(body_metrics_csv)
        data = mgr.read_all()
        assert len(data) == 3
        assert data[0]["weight_kg"] == 73.0

    def test_read_latest(self, body_metrics_csv):
        mgr = BodyMetricsManager(body_metrics_csv)
        latest = mgr.read_latest()
        assert latest["date"] == "2026-04-15"
        assert latest["body_fat_pct"] == 15.2

    def test_read_latest_empty(self, empty_csv):
        mgr = BodyMetricsManager(empty_csv)
        assert mgr.read_latest() is None

    def test_add_entry(self, body_metrics_csv):
        mgr = BodyMetricsManager(body_metrics_csv)
        mgr.add_entry(
            date="2026-04-25",
            weight_kg=71.0,
            body_fat_pct=14.8,
            muscle_mass_kg=34.8,
            bmi=21.8,
        )
        data = mgr.read_all()
        assert len(data) == 4
        assert data[-1]["date"] == "2026-04-25"

    def test_add_entry_creates_file(self, tmp_path):
        csv_path = str(tmp_path / "new_body_metrics.csv")
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(
            date="2026-04-25",
            weight_kg=71.0,
            body_fat_pct=14.8,
            muscle_mass_kg=34.8,
            bmi=21.8,
        )
        assert os.path.exists(csv_path)
        data = mgr.read_all()
        assert len(data) == 1

    def test_get_trend(self, body_metrics_csv):
        mgr = BodyMetricsManager(body_metrics_csv)
        trend = mgr.get_trend("body_fat_pct")
        assert trend["direction"] == "decreasing"
        assert trend["values"] == [16.5, 15.8, 15.2]

    def test_add_entry_with_source(self, tmp_path):
        csv_path = str(tmp_path / "test.csv")
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-20", weight_kg=75.0, body_fat_pct=18.0,
                       muscle_mass_kg=33.0, bmi=24.5, source="manual")
        rows = mgr.read_all()
        assert rows[0]["source"] == "manual"

    def test_add_entry_default_source(self, tmp_path):
        csv_path = str(tmp_path / "test.csv")
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-20", weight_kg=75.0)
        rows = mgr.read_all()
        assert rows[0]["source"] == "manual"

    def test_add_entry_weight_only(self, tmp_path):
        csv_path = str(tmp_path / "test.csv")
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-20", weight_kg=75.0)
        rows = mgr.read_all()
        assert rows[0]["weight_kg"] == 75.0
        assert rows[0]["body_fat_pct"] is None

    def test_backward_compat_old_csv(self, tmp_path):
        """source 컬럼 없는 기존 CSV도 읽을 수 있어야 함."""
        csv_path = str(tmp_path / "old.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "weight_kg", "body_fat_pct", "muscle_mass_kg", "bmi"])
            writer.writeheader()
            writer.writerow({"date": "2026-04-01", "weight_kg": "74.0",
                             "body_fat_pct": "19.0", "muscle_mass_kg": "32.5", "bmi": "24.0"})
        mgr = BodyMetricsManager(csv_path)
        rows = mgr.read_all()
        assert rows[0]["source"] == "unknown"

    def test_upsert_entry_insert_new(self, tmp_path):
        """새 (date, source) 조합이면 추가."""
        csv_path = str(tmp_path / "test.csv")
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-20", weight_kg=75.0, source="manual")
        mgr.upsert_entry(date="2026-04-20", weight_kg=74.0, source="apple_health")
        rows = mgr.read_all()
        assert len(rows) == 2
        assert rows[0]["source"] == "manual"
        assert rows[1]["source"] == "apple_health"

    def test_upsert_entry_update_existing(self, tmp_path):
        """동일 (date, source) 조합이면 덮어쓰기."""
        csv_path = str(tmp_path / "test.csv")
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-20", weight_kg=75.0, body_fat_pct=20.0, source="apple_health")
        mgr.upsert_entry(date="2026-04-20", weight_kg=74.5, body_fat_pct=19.8, source="apple_health")
        rows = mgr.read_all()
        assert len(rows) == 1
        assert rows[0]["weight_kg"] == 74.5
        assert rows[0]["body_fat_pct"] == 19.8

    def test_upsert_entry_creates_file(self, tmp_path):
        """파일이 없으면 새로 생성."""
        csv_path = str(tmp_path / "new.csv")
        mgr = BodyMetricsManager(csv_path)
        mgr.upsert_entry(date="2026-04-20", weight_kg=75.0, source="apple_health")
        rows = mgr.read_all()
        assert len(rows) == 1

    def test_upsert_entry_preserves_other_rows(self, body_metrics_csv):
        """다른 행은 건드리지 않음."""
        mgr = BodyMetricsManager(body_metrics_csv)
        mgr.upsert_entry(date="2026-04-01", weight_kg=71.0, source="apple_health")
        rows = mgr.read_all()
        # 기존 3행 + 새 apple_health 1행
        assert len(rows) == 4
        manual_rows = [r for r in rows if r["source"] == "manual"]
        assert len(manual_rows) == 2
