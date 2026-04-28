"""Apple Health (Health Auto Export) iCloud JSON → inbody.csv 동기화."""
import json
from pathlib import Path

from core.body_metrics import BodyMetricsManager

METRIC_MAP = {
    "weight_body_mass": "weight_kg",
    "body_fat_percentage": "body_fat_pct",
    "lean_body_mass": "muscle_mass_kg",   # LBM, not SMM
    "body_mass_index": "bmi",
}


def sync_from_icloud(hae_dir: str, body_metrics_mgr: BodyMetricsManager) -> list[dict]:
    """iCloud Drive의 Health Auto Export JSON을 읽어 inbody.csv에 upsert.

    Returns:
        새로 추가된 행 목록 (기존 행 업데이트는 포함하지 않음).
    """
    hae_path = Path(hae_dir)
    if not hae_path.exists():
        return []

    existing = {(r["date"], r.get("source", "")) for r in body_metrics_mgr.read_all()}
    new_rows = []

    for f in sorted(hae_path.glob("HealthAutoExport-*.json")):
        try:
            with open(f) as fp:
                raw = json.load(fp)
            metrics = raw["data"]["metrics"]
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            continue  # 부분 쓰기, .icloud placeholder, 또는 예상 외 JSON 구조

        row = {}
        for metric in metrics:
            key = METRIC_MAP.get(metric["name"])
            if not key:
                continue
            valid = [d for d in metric["data"] if d.get("source") == "InBody" and d["qty"] > 0]
            if valid:
                row[key] = round(valid[-1]["qty"], 2)
                row["date"] = valid[-1]["date"][:10]

        if row.get("date"):
            row["source"] = "apple_health"
            is_new = (row["date"], row["source"]) not in existing
            body_metrics_mgr.upsert_entry(**row)
            if is_new:
                new_rows.append(row)

    return new_rows
