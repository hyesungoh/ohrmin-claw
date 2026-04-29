"""Garmin Connect 데이터 접근 레이어 — python-garminconnect 기반."""
import datetime

from garminconnect import Garmin


def _speed_to_pace(speed_mps: float | None) -> str | None:
    """m/s → "M:SS" /km 페이스 변환."""
    if not speed_mps:
        return None
    secs_per_km = 1000.0 / speed_mps
    minutes = int(secs_per_km // 60)
    seconds = int(secs_per_km % 60)
    return f"{minutes}:{seconds:02d}"


def _seconds_to_hms(seconds: int | float | None) -> str:
    """초를 'HH:MM:SS' 형식으로 변환."""
    if not seconds:
        return "00:00:00"
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ms_to_local_hhmm(ms: int | float | None) -> str | None:
    """Garmin *Local timestamp(ms)을 'HH:MM' 문자열로 변환.

    Garmin의 Local timestamp는 epoch ms이지만 timezone offset이 이미 더해져 있어
    UTC로 읽으면 사용자의 wall-clock time이 그대로 나온다.
    """
    if ms is None:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(int(ms) / 1000, tz=datetime.timezone.utc)
        return dt.strftime("%H:%M")
    except (ValueError, TypeError, OSError):
        return None


def _extract_pct(field) -> float | int | None:
    """sleepScores의 percentage 필드 — dict({qualifierKey, value}) 또는 숫자 모두 허용."""
    if field is None:
        return None
    if isinstance(field, dict):
        return field.get("value")
    return field


def _date_range(start: datetime.date, end: datetime.date):
    """start부터 end까지의 날짜를 순회."""
    current = start
    while current <= end:
        yield current
        current += datetime.timedelta(days=1)


class GarminConnectClient:
    """python-garminconnect API를 통해 건강 데이터를 조회한다."""

    def __init__(
        self,
        email: str,
        password: str,
        is_cn: bool = False,
        token_dir: str | None = None,
    ):
        self.api = Garmin(email=email, password=password, is_cn=is_cn)
        self.api.login(tokenstore=token_dir)

    def get_sleep(self, start: datetime.date, end: datetime.date) -> list[dict]:
        results = []
        for day in _date_range(start, end):
            raw = self.api.get_sleep_data(day.isoformat())
            dto = raw.get("dailySleepDTO")
            if not dto:
                continue
            scores = raw.get("sleepScores", {})
            overall = scores.get("overall", {})
            results.append({
                "day": day.isoformat(),
                "total_sleep": _seconds_to_hms(dto.get("sleepTimeSeconds")),
                "deep_sleep": _seconds_to_hms(dto.get("deepSleepSeconds")),
                "light_sleep": _seconds_to_hms(dto.get("lightSleepSeconds")),
                "rem_sleep": _seconds_to_hms(dto.get("remSleepSeconds")),
                "awake": _seconds_to_hms(dto.get("awakeSleepSeconds")),
                "avg_spo2": dto.get("averageSpO2Value"),
                "avg_rr": dto.get("averageRespirationValue"),
                "score": overall.get("value", 0),
                # 어젯밤 derived 필드 계산용 raw + Garmin 자체 산출값
                "total_seconds": dto.get("sleepTimeSeconds"),
                "awake_seconds": dto.get("awakeSleepSeconds"),
                "bedtime": _ms_to_local_hhmm(dto.get("sleepStartTimestampLocal")),
                "wake_time": _ms_to_local_hhmm(dto.get("sleepEndTimestampLocal")),
                "deep_pct": _extract_pct(scores.get("deepPercentage")),
                "awake_count": dto.get("awakeCount"),
                "sleep_insight": raw.get("sleepScoreInsight"),
            })
        return results

    def get_daily_summary(self, start: datetime.date, end: datetime.date) -> list[dict]:
        results = []
        for day in _date_range(start, end):
            raw = self.api.get_stats(day.isoformat())
            if not raw:
                continue
            results.append({
                "day": day.isoformat(),
                "hr_min": raw.get("minHeartRate", 0),
                "hr_max": raw.get("maxHeartRate", 0),
                "rhr": raw.get("restingHeartRate", 0),
                "stress_avg": raw.get("averageStressLevel", 0),
                "steps": raw.get("totalSteps", 0),
                "distance": (raw.get("totalDistanceMeters", 0) or 0) / 1000,
                "calories_total": raw.get("totalKilocalories", 0),
                "calories_active": raw.get("activeKilocalories", 0),
            })
        return results

    def get_hrv(self, start: datetime.date, end: datetime.date) -> list[dict]:
        results = []
        for day in _date_range(start, end):
            raw = self.api.get_hrv_data(day.isoformat())
            if not raw:
                continue
            baseline = raw.get("baseline", {})
            results.append({
                "day": day.isoformat(),
                "weekly_avg": raw.get("weeklyAvg"),
                "last_night_avg": raw.get("lastNightAvg"),
                "last_night_5min_high": raw.get("lastNight5MinHigh"),
                "baseline_low": baseline.get("lowUpper"),
                "baseline_upper": baseline.get("balancedUpper"),
                "status": raw.get("status"),
            })
        return results

    def get_activities(self, start: datetime.date, end: datetime.date) -> list[dict]:
        raw_list = self.api.get_activities_by_date(
            start.isoformat(), end.isoformat(),
        )
        if not raw_list:
            return []
        results = []
        for a in raw_list:
            activity_type = a.get("activityType", {})
            results.append({
                "activity_id": str(a.get("activityId", "")),
                "name": a.get("activityName", ""),
                "sport": activity_type.get("typeKey", "unknown"),
                "start_time": a.get("startTimeLocal", ""),
                "elapsed_time": _seconds_to_hms(a.get("elapsedDuration")),
                "moving_time": _seconds_to_hms(a.get("movingDuration")),
                "distance": round((a.get("distance", 0) or 0) / 1000, 1),
                "avg_hr": a.get("averageHR"),
                "max_hr": a.get("maxHR"),
                "calories": a.get("calories", 0),
                "avg_speed": a.get("averageSpeed"),
                "max_speed": a.get("maxSpeed"),
                "elevation_gain": a.get("elevationGain"),
                "cadence": a.get("averageRunningCadenceInStepsPerMinute"),
                "avg_power": a.get("avgPower"),
                "training_effect_aerobic": a.get("aerobicTrainingEffect"),
                "training_effect_anaerobic": a.get("anaerobicTrainingEffect"),
                "vo2_max": a.get("vO2MaxValue"),
            })
        return results

    def get_stress(self, start: datetime.date, end: datetime.date) -> list[dict]:
        results = []
        for day in _date_range(start, end):
            raw = self.api.get_stress_data(day.isoformat())
            if not raw or raw.get("avgStressLevel") is None:
                continue
            results.append({
                "timestamp": f"{day.isoformat()} 00:00:00",
                "stress": raw.get("avgStressLevel", 0),
            })
        return results

    # ── 상세 활동 메서드 ──────────────────────────────

    def get_activity_splits(self, activity_id: str) -> list[dict]:
        raw = self.api.get_activity_splits(activity_id)
        laps = raw.get("lapDTOs", [])
        return [
            {
                "lap": i + 1,
                "distance_km": round((lap.get("distance", 0) or 0) / 1000, 1),
                "duration": _seconds_to_hms(lap.get("duration")),
                "avg_hr": lap.get("averageHR"),
                "max_hr": lap.get("maxHR"),
                "avg_speed": lap.get("averageSpeed"),
                "pace_min_km": _speed_to_pace(lap.get("averageSpeed")),
                "elevation_gain": lap.get("elevationGain"),
                "calories": lap.get("calories"),
            }
            for i, lap in enumerate(laps)
        ]

    def get_activity_hr_zones(self, activity_id: str) -> list[dict]:
        raw = self.api.get_activity_hr_in_timezones(activity_id)
        if not raw:
            return []
        total_secs = sum(z.get("secsInZone", 0) or 0 for z in raw)
        return [
            {
                "zone": z.get("zoneNumber"),
                "minutes": round((z.get("secsInZone", 0) or 0) / 60, 1),
                "zone_floor_bpm": z.get("zoneLowBoundary"),
                "zone_pct": round(((z.get("secsInZone", 0) or 0) / total_secs) * 100, 1) if total_secs > 0 else 0,
            }
            for z in raw
        ]

    def get_exercise_sets(self, activity_id: str) -> list[dict]:
        """웨이트 트레이닝 세트 조회 (REST 세트 제외)."""
        raw = self.api.get_activity_exercise_sets(activity_id)
        sets = raw.get("exerciseSets", [])
        return [
            {
                "exercise": s.get("exerciseName", "UNKNOWN"),
                "weight": s.get("weight"),
                "reps": s.get("repetitionCount"),
                "start_time": s.get("startTime"),
            }
            for s in sets
            if s.get("setType") == "ACTIVE"
        ]

    # 종목 분류 상수
    RUNNING_SPORTS = {"running", "trail_running", "treadmill_running", "track_running"}
    STRENGTH_SPORTS = {"strength_training", "indoor_cardio"}
    SWIMMING_SPORTS = {"lap_swimming", "open_water_swimming"}
    HIKING_CYCLING_SPORTS = {"hiking", "cycling", "mountain_biking", "road_biking"}

    def get_activity_detail(self, activity_id: str) -> dict:
        """종목 자동 감지 후 종목별 상세 데이터 통합 조회."""
        act = self.api.get_activity(activity_id)
        if not act:
            return {}

        activity_type = act.get("activityType", {})
        sport = activity_type.get("typeKey", "unknown")

        result = {
            "activity_id": activity_id,
            "name": act.get("activityName", ""),
            "sport": sport,
            "start_time": act.get("startTimeLocal", ""),
            "distance_km": round((act.get("distance", 0) or 0) / 1000, 1),
            "duration": _seconds_to_hms(act.get("duration")),
            "avg_hr": act.get("averageHR"),
            "max_hr": act.get("maxHR"),
            "calories": act.get("calories", 0),
        }

        # 종목별 추가 필드
        if sport in self.RUNNING_SPORTS:
            result["vo2_max"] = act.get("vO2MaxValue")
            result["cadence"] = act.get("averageRunningCadenceInStepsPerMinute")
            result["avg_pace"] = _speed_to_pace(act.get("averageSpeed"))
        elif sport in self.STRENGTH_SPORTS:
            result["exercise_sets"] = self.get_exercise_sets(activity_id)
        elif sport in self.SWIMMING_SPORTS:
            result["swolf"] = act.get("averageSwolf")
            result["avg_strokes"] = act.get("averageStrokes")
        elif sport in self.HIKING_CYCLING_SPORTS:
            result["elevation_gain"] = act.get("elevationGain")
            result["elevation_loss"] = act.get("elevationLoss")
            result["avg_power"] = act.get("avgPower")

        # splits/hr_zones는 웨이트 제외 모든 종목에서 조회
        if sport not in self.STRENGTH_SPORTS:
            result["splits"] = self.get_activity_splits(activity_id)
            result["hr_zones"] = self.get_activity_hr_zones(activity_id)

        return result

    # ── get_last_activity ─────────────────────────────

    def _format_activity_summary(self, raw: dict) -> dict:
        """API 응답을 정규화된 활동 요약으로 변환."""
        activity_type = raw.get("activityType", {})
        return {
            "activity_id": str(raw.get("activityId", "")),
            "name": raw.get("activityName", ""),
            "sport": activity_type.get("typeKey", "unknown"),
            "start_time": raw.get("startTimeLocal", ""),
            "distance_km": round((raw.get("distance", 0) or 0) / 1000, 1),
            "duration": _seconds_to_hms(raw.get("duration")),
            "avg_hr": raw.get("averageHR"),
            "max_hr": raw.get("maxHR"),
            "calories": raw.get("calories", 0),
        }

    def get_last_activity(self, count: int = 1) -> dict | list[dict]:
        """최근 활동 조회. count=1이면 단일 dict, count>1이면 list."""
        if count == 1:
            raw = self.api.get_last_activity()
            return self._format_activity_summary(raw)
        raw_list = self.api.get_activities(0, count)
        return [self._format_activity_summary(a) for a in raw_list[:count]]
