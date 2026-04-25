"""Body Metrics CSV 데이터 관리."""
import csv
import os


class BodyMetricsManager:
    """체성분 측정 데이터를 CSV로 관리한다."""

    HEADER = ["date", "weight_kg", "body_fat_pct", "muscle_mass_kg", "bmi", "source"]

    def __init__(self, csv_path: str):
        self.csv_path = csv_path

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.csv_path):
            return []
        with open(self.csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                for key in ["weight_kg", "body_fat_pct", "muscle_mass_kg", "bmi"]:
                    val = row.get(key)
                    if val is not None and val != "":
                        row[key] = float(val)
                    else:
                        row[key] = None
                # 하위 호환: source 컬럼 없는 기존 CSV
                if "source" not in row or not row.get("source"):
                    row["source"] = "unknown"
                rows.append(row)
            return rows

    def read_latest(self) -> dict | None:
        data = self.read_all()
        return data[-1] if data else None

    def add_entry(
        self,
        date: str,
        weight_kg: float | None = None,
        body_fat_pct: float | None = None,
        muscle_mass_kg: float | None = None,
        bmi: float | None = None,
        source: str = "manual",
    ):
        file_exists = os.path.exists(self.csv_path)
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADER)
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "date": date,
                "weight_kg": "" if weight_kg is None else weight_kg,
                "body_fat_pct": "" if body_fat_pct is None else body_fat_pct,
                "muscle_mass_kg": "" if muscle_mass_kg is None else muscle_mass_kg,
                "bmi": "" if bmi is None else bmi,
                "source": source,
            })

    def get_trend(self, field: str) -> dict:
        data = self.read_all()
        values = [row[field] for row in data if row.get(field)]
        if len(values) < 2:
            return {"direction": "insufficient_data", "values": values}
        if values[-1] < values[0]:
            direction = "decreasing"
        elif values[-1] > values[0]:
            direction = "increasing"
        else:
            direction = "stable"
        return {"direction": direction, "values": values}
