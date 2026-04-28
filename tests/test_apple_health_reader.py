"""Apple Health iCloud JSON 리더 테스트."""
import json
import os

import pytest

from core.body_metrics import BodyMetricsManager
from core.apple_health_reader import sync_from_icloud


def _make_hae_json(metrics: list[dict]) -> dict:
    """Health Auto Export JSON 구조 생성 헬퍼."""
    return {"data": {"metrics": metrics}}


def _write_json(directory, filename: str, data: dict):
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        json.dump(data, f)
    return path


@pytest.fixture
def hae_dir(tmp_path):
    """iCloud 폴더를 시뮬레이션하는 임시 디렉토리."""
    d = tmp_path / "hae"
    d.mkdir()
    return str(d)


@pytest.fixture
def csv_path(tmp_path):
    return str(tmp_path / "inbody.csv")


class TestSyncFromIcloud:
    def test_parses_valid_json(self, hae_dir, csv_path):
        """정상 JSON에서 체성분 데이터를 파싱한다."""
        data = _make_hae_json([
            {"name": "weight_body_mass", "units": "kg",
             "data": [{"qty": 104.6, "date": "2026-04-26 12:47:00 +0900", "source": "InBody"}]},
            {"name": "body_fat_percentage", "units": "%",
             "data": [{"qty": 32.7, "date": "2026-04-26 12:47:00 +0900", "source": "InBody"}]},
            {"name": "lean_body_mass", "units": "kg",
             "data": [{"qty": 70.399993896484375, "date": "2026-04-26 12:47:00 +0900", "source": "InBody"}]},
            {"name": "body_mass_index", "units": "count",
             "data": [{"qty": 30.199999999999999, "date": "2026-04-26 12:47:00 +0900", "source": "InBody"}]},
        ])
        _write_json(hae_dir, "HealthAutoExport-2026-04-26.json", data)

        mgr = BodyMetricsManager(csv_path)
        new_rows = sync_from_icloud(hae_dir, mgr)

        assert len(new_rows) == 1
        row = new_rows[0]
        assert row["date"] == "2026-04-26"
        assert row["weight_kg"] == 104.6
        assert row["body_fat_pct"] == 32.7
        assert row["muscle_mass_kg"] == 70.4  # rounded
        assert row["bmi"] == 30.2  # rounded
        assert row["source"] == "apple_health"

    def test_filters_non_inbody_source(self, hae_dir, csv_path):
        """source가 InBody가 아닌 항목은 무시한다."""
        data = _make_hae_json([
            {"name": "weight_body_mass", "units": "kg",
             "data": [
                 {"qty": 104.6, "date": "2026-04-26 12:47:00 +0900", "source": "InBody"},
                 {"qty": 0, "date": "2026-04-26 22:30:00 +0900", "source": "단축어"},
             ]},
        ])
        _write_json(hae_dir, "HealthAutoExport-2026-04-26.json", data)

        mgr = BodyMetricsManager(csv_path)
        new_rows = sync_from_icloud(hae_dir, mgr)

        assert len(new_rows) == 1
        assert new_rows[0]["weight_kg"] == 104.6

    def test_filters_zero_qty(self, hae_dir, csv_path):
        """qty가 0인 항목은 무시한다."""
        data = _make_hae_json([
            {"name": "weight_body_mass", "units": "kg",
             "data": [{"qty": 0, "date": "2026-04-26 12:47:00 +0900", "source": "InBody"}]},
        ])
        _write_json(hae_dir, "HealthAutoExport-2026-04-26.json", data)

        mgr = BodyMetricsManager(csv_path)
        new_rows = sync_from_icloud(hae_dir, mgr)

        assert len(new_rows) == 0

    def test_skips_already_synced(self, hae_dir, csv_path):
        """이미 CSV에 있는 (date, source) 조합은 새 행으로 카운트하지 않는다."""
        data = _make_hae_json([
            {"name": "weight_body_mass", "units": "kg",
             "data": [{"qty": 104.6, "date": "2026-04-26 12:47:00 +0900", "source": "InBody"}]},
        ])
        _write_json(hae_dir, "HealthAutoExport-2026-04-26.json", data)

        mgr = BodyMetricsManager(csv_path)
        # 첫 번째 동기화
        first = sync_from_icloud(hae_dir, mgr)
        assert len(first) == 1
        # 두 번째 동기화 — 새 행 없음
        second = sync_from_icloud(hae_dir, mgr)
        assert len(second) == 0

    def test_skips_invalid_json(self, hae_dir, csv_path):
        """깨진 JSON 파일은 건너뛴다."""
        path = os.path.join(hae_dir, "HealthAutoExport-2026-04-25.json")
        with open(path, "w") as f:
            f.write("{invalid json")

        mgr = BodyMetricsManager(csv_path)
        new_rows = sync_from_icloud(hae_dir, mgr)
        assert len(new_rows) == 0

    def test_multiple_files(self, hae_dir, csv_path):
        """여러 날짜의 JSON 파일을 처리한다."""
        for day in [25, 26, 27]:
            data = _make_hae_json([
                {"name": "weight_body_mass", "units": "kg",
                 "data": [{"qty": 100.0 + day, "date": f"2026-04-{day} 08:00:00 +0900", "source": "InBody"}]},
            ])
            _write_json(hae_dir, f"HealthAutoExport-2026-04-{day}.json", data)

        mgr = BodyMetricsManager(csv_path)
        new_rows = sync_from_icloud(hae_dir, mgr)
        assert len(new_rows) == 3

    def test_unknown_metric_ignored(self, hae_dir, csv_path):
        """알 수 없는 메트릭은 무시한다."""
        data = _make_hae_json([
            {"name": "unknown_metric", "units": "kg",
             "data": [{"qty": 50.0, "date": "2026-04-26 12:00:00 +0900", "source": "InBody"}]},
        ])
        _write_json(hae_dir, "HealthAutoExport-2026-04-26.json", data)

        mgr = BodyMetricsManager(csv_path)
        new_rows = sync_from_icloud(hae_dir, mgr)
        assert len(new_rows) == 0

    def test_empty_directory(self, hae_dir, csv_path):
        """빈 디렉토리면 빈 리스트 반환."""
        mgr = BodyMetricsManager(csv_path)
        new_rows = sync_from_icloud(hae_dir, mgr)
        assert len(new_rows) == 0

    def test_nonexistent_directory(self, tmp_path, csv_path):
        """존재하지 않는 디렉토리면 빈 리스트 반환."""
        mgr = BodyMetricsManager(csv_path)
        new_rows = sync_from_icloud(str(tmp_path / "nonexistent"), mgr)
        assert len(new_rows) == 0

    def test_coexists_with_manual_entries(self, hae_dir, csv_path):
        """수동 입력(manual)과 자동 수집(apple_health)이 별도 행으로 공존한다."""
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-26", weight_kg=105.0, source="manual")

        data = _make_hae_json([
            {"name": "weight_body_mass", "units": "kg",
             "data": [{"qty": 104.6, "date": "2026-04-26 12:47:00 +0900", "source": "InBody"}]},
        ])
        _write_json(hae_dir, "HealthAutoExport-2026-04-26.json", data)

        new_rows = sync_from_icloud(hae_dir, mgr)
        assert len(new_rows) == 1

        all_rows = mgr.read_all()
        assert len(all_rows) == 2
        sources = {r["source"] for r in all_rows}
        assert sources == {"manual", "apple_health"}
