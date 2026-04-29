"""로컬 전처리 모듈 — 원시 데이터를 통계 요약으로 변환."""
import datetime
import statistics
from collections import Counter


def _parse_duration_hours(duration_str: str) -> float:
    """'HH:MM:SS' 형식을 시간(float)으로 변환."""
    if not duration_str:
        return 0.0
    parts = duration_str.split(":")
    return int(parts[0]) + int(parts[1]) / 60 + int(parts[2]) / 3600


def _bedtime_to_minutes(bedtime: str | None) -> int | None:
    """'HH:MM' 문자열을 minute-of-day 정수로 변환."""
    if not bedtime:
        return None
    try:
        h, m = map(int, bedtime.split(":"))
        return h * 60 + m
    except (ValueError, AttributeError):
        return None


def _median_bedtime(bedtimes: list[str]) -> str | None:
    """bedtime 리스트의 minute-of-day median을 'HH:MM'로 반환.

    단일 median 값이라 wrap-around 처리 불필요한 가정 (사용자 패턴이 정오를 가로지르지 않을 때 정확).
    """
    minutes = [_bedtime_to_minutes(b) for b in bedtimes]
    minutes = [m for m in minutes if m is not None]
    if not minutes:
        return None
    median_min = int(statistics.median(minutes))
    return f"{median_min // 60:02d}:{median_min % 60:02d}"


def _compute_hrv_z(hrv_data: list[dict], today_date: str) -> float | None:
    """(last_night_avg − weekly_avg) / (baseline_upper − baseline_low) z-score.

    baseline span ≤ 0이면 z 자체가 무의미하므로 None을 반환한다(분모 clamp 대신 명시적 None).

    Args:
        hrv_data: HRV records (각 항목 dict, day/last_night_avg/weekly_avg/baseline_low/baseline_upper).
        today_date: today_date에 매칭되는 entry 우선, 없으면 가장 마지막 entry 사용.

    Returns:
        round 2 z-score, 또는 데이터/baseline range가 없으면 None.
    """
    if not hrv_data:
        return None
    today_hrv = next((h for h in hrv_data if h.get("day") == today_date), None)
    if today_hrv is None:
        today_hrv = hrv_data[-1]
    last_night_avg = today_hrv.get("last_night_avg")
    weekly_avg = today_hrv.get("weekly_avg")
    baseline_low = today_hrv.get("baseline_low")
    baseline_upper = today_hrv.get("baseline_upper")
    if any(v is None for v in (last_night_avg, weekly_avg, baseline_low, baseline_upper)):
        return None
    span = baseline_upper - baseline_low
    if span <= 0:
        return None
    return round((last_night_avg - weekly_avg) / span, 2)


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
            return {
                "avg_total_hours": 0,
                "avg_score": 0,
                "min_hours": 0,
                "max_hours": 0,
                "avg_bedtime": None,
                "trend": "insufficient_data",
            }
        hours = [_parse_duration_hours(d["total_sleep"]) for d in sleep_data]
        scores = [d["score"] for d in sleep_data if d.get("score")]
        bedtimes = [d.get("bedtime") for d in sleep_data]
        return {
            "avg_total_hours": round(sum(hours) / len(hours), 1),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "min_hours": round(min(hours), 1),
            "max_hours": round(max(hours), 1),
            "avg_bedtime": _median_bedtime(bedtimes),
            "trend": _trend_higher_is_better(scores) if scores else "insufficient_data",
        }

    @staticmethod
    def summarize_last_night_sleep(
        sleep_data: list[dict],
        hrv_data: list[dict],
        today_date: str,
    ) -> dict | None:
        """어젯밤 단일 수면 + HRV로부터 derived 필드를 산출.

        Args:
            sleep_data: 7일 윈도우의 sleep records (chronological).
            hrv_data: 7일 윈도우의 HRV records.
            today_date: ISO 'YYYY-MM-DD' — last_night으로 인정할 날짜.

        Returns:
            derived 필드 dict, 또는 today_date 데이터가 없으면 None.
            계산 불가 필드는 None으로 채움 (LLM 스키마 일관성).
        """
        if not sleep_data:
            return None

        today_record = None
        prior_records = []
        for r in sleep_data:
            if r.get("day") == today_date:
                today_record = r
            else:
                prior_records.append(r)

        if today_record is None:
            return None

        total_seconds = today_record.get("total_seconds") or 0
        awake_seconds = today_record.get("awake_seconds") or 0

        hours = round(total_seconds / 3600, 1) if total_seconds > 0 else None

        score_raw = today_record.get("score")
        score = score_raw if score_raw else None

        if total_seconds > 0:
            efficiency_pct = round(
                (total_seconds - awake_seconds) / total_seconds * 100, 1
            )
        else:
            efficiency_pct = None

        today_deep_pct = today_record.get("deep_pct")
        prior_deep_pcts = [
            r.get("deep_pct") for r in prior_records if r.get("deep_pct") is not None
        ]
        if today_deep_pct is not None and prior_deep_pcts and total_seconds > 0:
            median_deep_pct = statistics.median(prior_deep_pcts)
            deep_pct_delta = round(today_deep_pct - median_deep_pct, 1)
        else:
            deep_pct_delta = None

        hrv_z = _compute_hrv_z(hrv_data, today_date)

        return {
            "hours": hours,
            "score": score,
            "efficiency_pct": efficiency_pct,
            "deep_pct_delta": deep_pct_delta,
            "hrv_z": hrv_z,
            "avg_rr": today_record.get("avg_rr"),
            "awake_count": today_record.get("awake_count"),
            "bedtime": today_record.get("bedtime"),
            "sleep_insight": today_record.get("sleep_insight"),
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
