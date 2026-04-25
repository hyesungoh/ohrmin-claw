"""로컬 전처리 모듈 — 원시 데이터를 통계 요약으로 변환."""
import datetime
from collections import Counter


def _parse_duration_hours(duration_str: str) -> float:
    """'HH:MM:SS' 형식을 시간(float)으로 변환."""
    if not duration_str:
        return 0.0
    parts = duration_str.split(":")
    return int(parts[0]) + int(parts[1]) / 60 + int(parts[2]) / 3600


def _trend(values: list[float], higher_is_better: bool = True) -> str:
    """값 리스트의 추세를 판단. higher_is_better=False면 낮을수록 좋은 지표."""
    if len(values) < 2:
        return "insufficient_data"
    first_half = sum(values[: len(values) // 2]) / max(len(values) // 2, 1)
    second_half = sum(values[len(values) // 2 :]) / max(len(values) - len(values) // 2, 1)
    diff = second_half - first_half
    if abs(diff) < 0.5:
        return "stable"
    if higher_is_better:
        return "improving" if diff > 0 else "worsening"
    return "improving" if diff < 0 else "worsening"


def _trend_lower_is_better(values: list[float]) -> str:
    return _trend(values, higher_is_better=False)


def _trend_higher_is_better(values: list[float]) -> str:
    return _trend(values, higher_is_better=True)


class HealthPreprocessor:
    """건강 데이터를 통계 요약으로 변환한다."""

    @staticmethod
    def summarize_sleep(sleep_data: list[dict]) -> dict:
        if not sleep_data:
            return {"avg_total_hours": 0, "avg_score": 0, "trend": "no_data"}
        hours = [_parse_duration_hours(d["total_sleep"]) for d in sleep_data]
        scores = [d["score"] for d in sleep_data if d.get("score")]
        return {
            "avg_total_hours": round(sum(hours) / len(hours), 1),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "min_hours": round(min(hours), 1),
            "max_hours": round(max(hours), 1),
            "trend": _trend_higher_is_better(scores) if scores else "no_data",
        }

    @staticmethod
    def summarize_heart_rate(daily_data: list[dict]) -> dict:
        if not daily_data:
            return {"avg_rhr": 0, "trend": "no_data"}
        rhrs = [d["rhr"] for d in daily_data if d.get("rhr")]
        return {
            "avg_rhr": round(sum(rhrs) / len(rhrs), 1) if rhrs else 0,
            "min_rhr": min(rhrs) if rhrs else 0,
            "max_rhr": max(rhrs) if rhrs else 0,
            "trend": _trend_lower_is_better(rhrs),
        }

    @staticmethod
    def summarize_activities(activities: list[dict]) -> dict:
        if not activities:
            return {"total_count": 0, "total_calories": 0, "total_distance": 0, "total_time_hours": 0, "by_sport": {}}
        total_cal = sum(a.get("calories", 0) or 0 for a in activities)
        total_dist = sum(a.get("distance", 0) or 0 for a in activities)
        total_time = sum(_parse_duration_hours(a.get("elapsed_time", "00:00:00")) for a in activities)
        sports = Counter(a.get("sport", "unknown") for a in activities)
        return {
            "total_count": len(activities),
            "total_calories": total_cal,
            "total_distance": round(total_dist, 1),
            "total_time_hours": round(total_time, 1),
            "by_sport": dict(sports),
        }

    @staticmethod
    def summarize_hrv(hrv_data: list[dict]) -> dict:
        if not hrv_data:
            return {"avg_weekly": 0, "trend": "no_data", "status_distribution": {}}
        weekly_avgs = [d["weekly_avg"] for d in hrv_data if d.get("weekly_avg")]
        statuses = Counter(d.get("status", "UNKNOWN") for d in hrv_data)
        return {
            "avg_weekly": round(sum(weekly_avgs) / len(weekly_avgs), 1) if weekly_avgs else 0,
            "trend": _trend_higher_is_better(weekly_avgs),
            "status_distribution": dict(statuses),
        }

    @staticmethod
    def summarize_stress(stress_data: list[dict]) -> dict:
        if not stress_data:
            return {"avg_stress": 0, "trend": "no_data"}
        values = [d["stress"] for d in stress_data if d.get("stress") is not None]
        return {
            "avg_stress": round(sum(values) / len(values), 1) if values else 0,
            "max_stress": max(values) if values else 0,
            "min_stress": min(values) if values else 0,
            "trend": _trend_lower_is_better(values),
        }

    @staticmethod
    def create_weekly_summary(
        sleep: dict,
        heart_rate: dict,
        activities: dict,
        hrv: dict,
        stress: dict,
        body_metrics: dict | None = None,
    ) -> dict:
        today = datetime.date.today()
        week_ago = today - datetime.timedelta(days=7)
        return {
            "period": f"{week_ago.isoformat()} ~ {today.isoformat()}",
            "sleep": sleep,
            "heart_rate": heart_rate,
            "activities": activities,
            "hrv": hrv,
            "stress": stress,
            "body_metrics": body_metrics,
        }
