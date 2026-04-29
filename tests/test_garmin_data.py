"""GarminConnectClient 테스트 — python-garminconnect API 기반."""
import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.garmin_data import GarminConnectClient


@pytest.fixture
def mock_garmin():
    """garminconnect.Garmin 인스턴스를 mock한 GarminConnectClient."""
    with patch("core.garmin_data.Garmin") as MockGarmin:
        mock_api = MagicMock()
        MockGarmin.return_value = mock_api
        client = GarminConnectClient(email="test@test.com", password="pass")
        yield client, mock_api


# ── Sleep ──────────────────────────────────────────────

class TestGetSleep:
    def test_returns_mapped_fields(self, mock_garmin):
        client, api = mock_garmin
        # Garmin's *Local timestamps are GMT-formatted but represent local wall clock,
        # so reading them as UTC yields the local HH:MM directly.
        # 1579129200000 ms = 2020-01-15 23:00:00 UTC → bedtime "23:00"
        # 1579158000000 ms = 2020-01-16 07:00:00 UTC → wake_time "07:00" (8h later)
        bedtime_ms = 1579129200000
        wake_ms = 1579158000000
        api.get_sleep_data.return_value = {
            "dailySleepDTO": {
                "calendarDate": "2026-04-20",
                "sleepTimeSeconds": 28800,  # 8h
                "deepSleepSeconds": 5400,   # 1h30m
                "lightSleepSeconds": 12600, # 3h30m
                "remSleepSeconds": 7200,    # 2h
                "awakeSleepSeconds": 3600,  # 1h
                "averageSpO2Value": 95.0,
                "averageRespirationValue": 15.0,
                "sleepStartTimestampLocal": bedtime_ms,
                "sleepEndTimestampLocal": wake_ms,
                "awakeCount": 4,
            },
            "sleepScores": {
                "overall": {"value": 82},
                "deepPercentage": {"qualifierKey": "GOOD", "value": 19},
            },
            "sleepScoreInsight": "POSITIVE_DURATION",
        }

        data = client.get_sleep(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 20),
        )

        assert len(data) == 1
        row = data[0]
        assert row["day"] == "2026-04-20"
        assert row["total_sleep"] == "08:00:00"
        assert row["deep_sleep"] == "01:30:00"
        assert row["light_sleep"] == "03:30:00"
        assert row["rem_sleep"] == "02:00:00"
        assert row["awake"] == "01:00:00"
        assert row["score"] == 82
        # New raw fields for last_night derived calculations
        assert row["total_seconds"] == 28800
        assert row["awake_seconds"] == 3600
        assert row["deep_pct"] == 19
        assert row["awake_count"] == 4
        assert row["sleep_insight"] == "POSITIVE_DURATION"
        # bedtime/wake_time as "HH:MM" strings derived from local timestamp
        assert row["bedtime"] == "23:00"
        assert row["wake_time"] == "07:00"

    def test_handles_missing_optional_fields(self, mock_garmin):
        """누락된 신규 필드는 None으로 채워져야 한다."""
        client, api = mock_garmin
        api.get_sleep_data.return_value = {
            "dailySleepDTO": {
                "calendarDate": "2026-04-20",
                "sleepTimeSeconds": 25200,
                "deepSleepSeconds": 3600,
                "lightSleepSeconds": 10800,
                "remSleepSeconds": 7200,
                "awakeSleepSeconds": 3600,
            },
            "sleepScores": {"overall": {"value": 75}},
        }
        data = client.get_sleep(
            datetime.date(2026, 4, 20), datetime.date(2026, 4, 20)
        )
        row = data[0]
        assert row["bedtime"] is None
        assert row["wake_time"] is None
        assert row["deep_pct"] is None
        assert row["awake_count"] is None
        assert row["sleep_insight"] is None
        assert row["total_seconds"] == 25200
        assert row["awake_seconds"] == 3600

    def test_deep_pct_accepts_plain_number(self, mock_garmin):
        """sleepScores.deepPercentage가 dict가 아닌 숫자로 와도 허용."""
        client, api = mock_garmin
        api.get_sleep_data.return_value = {
            "dailySleepDTO": {
                "calendarDate": "2026-04-20",
                "sleepTimeSeconds": 25200,
                "deepSleepSeconds": 3600,
                "awakeSleepSeconds": 1800,
            },
            "sleepScores": {
                "overall": {"value": 75},
                "deepPercentage": 22,
            },
        }
        data = client.get_sleep(
            datetime.date(2026, 4, 20), datetime.date(2026, 4, 20)
        )
        assert data[0]["deep_pct"] == 22

    def test_empty_response(self, mock_garmin):
        client, api = mock_garmin
        api.get_sleep_data.return_value = {}

        data = client.get_sleep(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 20),
        )
        assert len(data) == 0

    def test_date_range_calls_each_day(self, mock_garmin):
        client, api = mock_garmin
        api.get_sleep_data.return_value = {}

        client.get_sleep(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 22),
        )
        assert api.get_sleep_data.call_count == 3


# ── Daily Summary (심박수 포함) ────────────────────────

class TestGetDailySummary:
    def test_returns_mapped_fields(self, mock_garmin):
        client, api = mock_garmin
        api.get_stats.return_value = {
            "calendarDate": "2026-04-20",
            "minHeartRate": 52,
            "maxHeartRate": 145,
            "restingHeartRate": 58,
            "averageStressLevel": 30,
            "dailyStepGoal": 8000,
            "totalSteps": 9200,
            "moderateIntensityMinutes": 30,
            "vigorousIntensityMinutes": 15,
            "totalDistanceMeters": 7200,
            "totalKilocalories": 2200,
            "activeKilocalories": 500,
        }

        data = client.get_daily_summary(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 20),
        )

        assert len(data) == 1
        row = data[0]
        assert row["day"] == "2026-04-20"
        assert row["rhr"] == 58
        assert row["steps"] == 9200
        assert row["hr_min"] == 52
        assert row["hr_max"] == 145


# ── HRV ────────────────────────────────────────────────

class TestGetHrv:
    def test_returns_mapped_fields(self, mock_garmin):
        client, api = mock_garmin
        api.get_hrv_data.return_value = {
            "calendarDate": "2026-04-20",
            "weeklyAvg": 45.0,
            "lastNightAvg": 48.0,
            "lastNight5MinHigh": 65.0,
            "baseline": {"lowUpper": 35.0, "balancedUpper": 55.0},
            "status": "BALANCED",
        }

        data = client.get_hrv(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 20),
        )

        assert len(data) == 1
        row = data[0]
        assert row["day"] == "2026-04-20"
        assert row["weekly_avg"] == 45.0
        assert row["last_night_avg"] == 48.0
        assert row["status"] == "BALANCED"

    def test_none_response(self, mock_garmin):
        client, api = mock_garmin
        api.get_hrv_data.return_value = None

        data = client.get_hrv(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 20),
        )
        assert len(data) == 0


# ── Activities ─────────────────────────────────────────

class TestGetActivities:
    def test_returns_mapped_fields(self, mock_garmin):
        client, api = mock_garmin
        api.get_activities_by_date.return_value = [
            {
                "activityId": "act1",
                "activityName": "Morning Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": "2026-04-20 07:00:00",
                "endTimeLocal": "2026-04-20 07:45:00",
                "elapsedDuration": 2700.0,   # 45min in seconds
                "movingDuration": 2580.0,     # 43min
                "distance": 6500.0,           # meters
                "averageHR": 145,
                "maxHR": 170,
                "calories": 450,
                "averageSpeed": 2.42,
                "maxSpeed": 3.33,
            },
        ]

        data = client.get_activities(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 22),
        )

        assert len(data) == 1
        row = data[0]
        assert row["sport"] == "running"
        assert row["calories"] == 450
        assert row["distance"] == 6.5  # km로 변환
        assert row["elapsed_time"] == "00:45:00"
        assert row["start_time"] == "2026-04-20 07:00:00"
        assert row["name"] == "Morning Run"

    def test_empty_activities(self, mock_garmin):
        client, api = mock_garmin
        api.get_activities_by_date.return_value = []

        data = client.get_activities(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 22),
        )
        assert len(data) == 0


# ── Stress ─────────────────────────────────────────────

class TestGetStress:
    def test_returns_mapped_fields(self, mock_garmin):
        client, api = mock_garmin
        api.get_stress_data.return_value = {
            "calendarDate": "2026-04-20",
            "avgStressLevel": 30,
            "maxStressLevel": 65,
        }

        data = client.get_stress(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 20),
        )

        assert len(data) == 1
        row = data[0]
        assert row["stress"] == 30
        assert row["timestamp"] == "2026-04-20 00:00:00"

    def test_date_range(self, mock_garmin):
        client, api = mock_garmin
        api.get_stress_data.return_value = {
            "calendarDate": "2026-04-20",
            "avgStressLevel": 30,
            "maxStressLevel": 50,
        }

        data = client.get_stress(
            datetime.date(2026, 4, 20),
            datetime.date(2026, 4, 22),
        )
        assert len(data) == 3


# ── Authentication ─────────────────────────────────────

class TestAuthentication:
    def test_login_called_on_init(self, mock_garmin):
        client, api = mock_garmin
        api.login.assert_called_once()

    def test_token_dir_used(self):
        with patch("core.garmin_data.Garmin") as MockGarmin:
            mock_api = MagicMock()
            MockGarmin.return_value = mock_api
            client = GarminConnectClient(
                email="test@test.com",
                password="pass",
                token_dir="/tmp/test_tokens",
            )
            # login 시 tokenstore 경로가 전달되는지 확인
            mock_api.login.assert_called_once_with(tokenstore="/tmp/test_tokens")


# ── Preprocessor 호환성 ────────────────────────────────

class TestPreprocessorCompatibility:
    """preprocessor.py가 사용하는 필드가 모두 존재하는지 확인."""

    def test_sleep_fields_for_preprocessor(self, mock_garmin):
        """summarize_sleep이 사용하는 필드: total_sleep, score"""
        client, api = mock_garmin
        api.get_sleep_data.return_value = {
            "dailySleepDTO": {
                "calendarDate": "2026-04-20",
                "sleepTimeSeconds": 25200,
                "deepSleepSeconds": 3600,
                "lightSleepSeconds": 10800,
                "remSleepSeconds": 7200,
                "awakeSleepSeconds": 3600,
            },
            "sleepScores": {"overall": {"value": 75}},
        }
        data = client.get_sleep(datetime.date(2026, 4, 20), datetime.date(2026, 4, 20))
        row = data[0]
        # preprocessor uses these exact keys
        assert "total_sleep" in row
        assert "score" in row

    def test_daily_fields_for_preprocessor(self, mock_garmin):
        """summarize_heart_rate가 사용하는 필드: rhr"""
        client, api = mock_garmin
        api.get_stats.return_value = {
            "calendarDate": "2026-04-20",
            "restingHeartRate": 58,
            "totalSteps": 9000,
            "minHeartRate": 50,
            "maxHeartRate": 140,
        }
        data = client.get_daily_summary(datetime.date(2026, 4, 20), datetime.date(2026, 4, 20))
        row = data[0]
        assert "rhr" in row

    def test_activity_fields_for_preprocessor(self, mock_garmin):
        """summarize_activities가 사용하는 필드: calories, distance, elapsed_time, sport"""
        client, api = mock_garmin
        api.get_activities_by_date.return_value = [{
            "activityId": "1",
            "activityName": "Run",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-20 07:00:00",
            "elapsedDuration": 1800.0,
            "distance": 5000.0,
            "calories": 300,
        }]
        data = client.get_activities(datetime.date(2026, 4, 20), datetime.date(2026, 4, 20))
        row = data[0]
        assert "calories" in row
        assert "distance" in row
        assert "elapsed_time" in row
        assert "sport" in row

    def test_hrv_fields_for_preprocessor(self, mock_garmin):
        """summarize_hrv가 사용하는 필드: weekly_avg, status"""
        client, api = mock_garmin
        api.get_hrv_data.return_value = {
            "calendarDate": "2026-04-20",
            "weeklyAvg": 45.0,
            "lastNightAvg": 48.0,
            "lastNight5MinHigh": 65.0,
            "status": "BALANCED",
        }
        data = client.get_hrv(datetime.date(2026, 4, 20), datetime.date(2026, 4, 20))
        row = data[0]
        assert "weekly_avg" in row
        assert "status" in row

    def test_stress_fields_for_preprocessor(self, mock_garmin):
        """summarize_stress가 사용하는 필드: stress"""
        client, api = mock_garmin
        api.get_stress_data.return_value = {
            "calendarDate": "2026-04-20",
            "avgStressLevel": 35,
            "maxStressLevel": 60,
        }
        data = client.get_stress(datetime.date(2026, 4, 20), datetime.date(2026, 4, 20))
        row = data[0]
        assert "stress" in row


# ── get_last_activity ─────────────────────────────────

class TestGetLastActivity:
    def test_returns_single_activity(self, mock_garmin):
        client, api = mock_garmin
        api.get_last_activity.return_value = {
            "activityId": "999", "activityName": "오후 러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-24 18:00:00",
            "distance": 5000.0, "duration": 1800.0,
            "averageHR": 150, "maxHR": 170, "calories": 400,
        }
        data = client.get_last_activity()
        assert data["activity_id"] == "999"
        assert data["sport"] == "running"

    def test_returns_multiple_activities(self, mock_garmin):
        client, api = mock_garmin
        api.get_last_activity.return_value = {
            "activityId": "999", "activityName": "러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-24 18:00:00",
            "distance": 5000.0, "duration": 1800.0,
            "averageHR": 150, "maxHR": 170, "calories": 400,
        }
        api.get_activities.return_value = [
            {"activityId": "999", "activityName": "러닝",
             "activityType": {"typeKey": "running"},
             "startTimeLocal": "2026-04-24 18:00:00",
             "distance": 5000.0, "duration": 1800.0,
             "averageHR": 150, "maxHR": 170, "calories": 400},
            {"activityId": "998", "activityName": "웨이트",
             "activityType": {"typeKey": "strength_training"},
             "startTimeLocal": "2026-04-23 10:00:00",
             "distance": 0, "duration": 3600.0,
             "averageHR": 120, "maxHR": 155, "calories": 300},
        ]
        data = client.get_last_activity(count=2)
        assert len(data) == 2


# ── get_exercise_sets ─────────────────────────────────

class TestGetExerciseSets:
    def test_returns_active_sets_only(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_exercise_sets.return_value = {
            "exerciseSets": [
                {"setType": "ACTIVE", "exerciseName": "SQUAT",
                 "weight": 80.0, "repetitionCount": 8},
                {"setType": "REST"},
                {"setType": "ACTIVE", "exerciseName": "SQUAT",
                 "weight": 90.0, "repetitionCount": 6},
            ],
        }
        data = client.get_exercise_sets("456")
        assert len(data) == 2
        assert data[0]["exercise"] == "SQUAT"
        assert data[1]["weight"] == 90.0

    def test_empty_sets(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_exercise_sets.return_value = {"exerciseSets": []}
        data = client.get_exercise_sets("456")
        assert data == []


# ── get_activity_detail (종목별 자동 감지) ────────────

class TestGetActivityDetails:
    def test_returns_splits(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_splits.return_value = {
            "activityId": 123,
            "lapDTOs": [
                {"distance": 1000.0, "duration": 494.0, "averageHR": 145, "maxHR": 164,
                 "averageSpeed": 2.02, "elevationGain": 8.0, "calories": 93},
            ],
        }
        data = client.get_activity_splits("123")
        assert len(data) == 1
        assert data[0]["distance_km"] == 1.0
        assert data[0]["avg_hr"] == 145

    def test_returns_hr_zones(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_hr_in_timezones.return_value = [
            {"zoneNumber": 1, "secsInZone": 619.0, "zoneLowBoundary": 131},
            {"zoneNumber": 2, "secsInZone": 2040.0, "zoneLowBoundary": 149},
        ]
        data = client.get_activity_hr_zones("123")
        assert len(data) == 2
        assert data[0]["zone"] == 1
        assert data[0]["minutes"] == 10.3


class TestGetActivityDetailBySport:
    """종목별 자동 감지 상세 조회."""

    def test_running_includes_splits_cadence_vo2(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "123", "activityName": "러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-22 07:00:00",
            "distance": 6000.0, "duration": 2700.0,
            "averageHR": 154, "maxHR": 172, "calories": 500,
            "vO2MaxValue": 37.0,
            "averageRunningCadenceInStepsPerMinute": 160.0,
            "averageSpeed": 2.22,
        }
        api.get_activity_splits.return_value = {"lapDTOs": [
            {"distance": 1000.0, "duration": 450.0, "averageHR": 145,
             "maxHR": 164, "averageSpeed": 2.22, "elevationGain": 5.0, "calories": 90},
        ]}
        api.get_activity_hr_in_timezones.return_value = [
            {"zoneNumber": 1, "secsInZone": 600.0, "zoneLowBoundary": 131},
        ]
        data = client.get_activity_detail("123")
        assert data["vo2_max"] == 37.0
        assert data["cadence"] == 160.0
        assert "splits" in data
        assert "avg_speed" in data["splits"][0] or "pace_min_km" in data["splits"][0]

    def test_strength_includes_exercise_sets(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "456", "activityName": "웨이트 트레이닝",
            "activityType": {"typeKey": "strength_training"},
            "startTimeLocal": "2026-04-22 10:00:00",
            "distance": 0, "duration": 3600.0,
            "averageHR": 120, "maxHR": 155, "calories": 300,
        }
        api.get_activity_exercise_sets.return_value = {
            "exerciseSets": [
                {"setType": "ACTIVE", "exerciseName": "BENCH_PRESS",
                 "weight": 60.0, "repetitionCount": 10, "startTime": "2026-04-22T10:05:00"},
                {"setType": "ACTIVE", "exerciseName": "BENCH_PRESS",
                 "weight": 70.0, "repetitionCount": 8, "startTime": "2026-04-22T10:08:00"},
                {"setType": "REST"},
            ],
        }
        data = client.get_activity_detail("456")
        assert data["sport"] == "strength_training"
        assert "exercise_sets" in data
        assert len(data["exercise_sets"]) == 2
        assert data["exercise_sets"][0]["weight"] == 60.0

    def test_swimming_includes_swolf(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "789", "activityName": "수영",
            "activityType": {"typeKey": "lap_swimming"},
            "startTimeLocal": "2026-04-22 08:00:00",
            "distance": 1500.0, "duration": 2400.0,
            "averageHR": 135, "maxHR": 160, "calories": 350,
            "averageSwolf": 42, "averageStrokes": 18,
        }
        api.get_activity_splits.return_value = {"lapDTOs": []}
        api.get_activity_hr_in_timezones.return_value = []
        data = client.get_activity_detail("789")
        assert data["swolf"] == 42
        assert data["avg_strokes"] == 18

    def test_hiking_includes_elevation(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "321", "activityName": "하이킹",
            "activityType": {"typeKey": "hiking"},
            "startTimeLocal": "2026-04-22 09:00:00",
            "distance": 8000.0, "duration": 7200.0,
            "averageHR": 125, "maxHR": 150, "calories": 600,
            "elevationGain": 450.0, "elevationLoss": 430.0,
        }
        api.get_activity_splits.return_value = {"lapDTOs": []}
        api.get_activity_hr_in_timezones.return_value = []
        data = client.get_activity_detail("321")
        assert data["elevation_gain"] == 450.0
        assert data["elevation_loss"] == 430.0


# ── _speed_to_pace ────────────────────────────────────

class TestSpeedToPace:
    def test_normal_pace(self):
        from core.garmin_data import _speed_to_pace
        pace = _speed_to_pace(2.78)
        assert pace == "5:59"

    def test_slow_pace(self):
        from core.garmin_data import _speed_to_pace
        pace = _speed_to_pace(1.67)
        # 1.67 m/s ≈ 598.8 s/km ≈ 9:58
        assert pace in ("9:58", "9:59", "10:00")

    def test_zero_speed(self):
        from core.garmin_data import _speed_to_pace
        assert _speed_to_pace(0) is None

    def test_none_speed(self):
        from core.garmin_data import _speed_to_pace
        assert _speed_to_pace(None) is None


# ── get_activities 확장 필드 ──────────────────────────

class TestGetActivitiesExtendedFields:
    def test_includes_extended_fields(self, mock_garmin):
        client, api = mock_garmin
        api.get_activities_by_date.return_value = [{
            "activityId": "123", "activityName": "러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-22 07:00:00",
            "distance": 6000.0, "duration": 2700.0,
            "averageHR": 154, "maxHR": 172, "calories": 500,
            "elevationGain": 35.0,
            "averageRunningCadenceInStepsPerMinute": 162.0,
            "avgPower": None,
            "aerobicTrainingEffect": 3.2,
            "anaerobicTrainingEffect": 1.5,
            "vO2MaxValue": 37.0,
        }]
        data = client.get_activities(datetime.date(2026, 4, 22), datetime.date(2026, 4, 22))
        act = data[0]
        assert act["elevation_gain"] == 35.0
        assert act["cadence"] == 162.0
        assert act["avg_power"] is None
        assert act["training_effect_aerobic"] == 3.2
        assert act["training_effect_anaerobic"] == 1.5
        assert act["vo2_max"] == 37.0

    def test_missing_extended_fields_are_none(self, mock_garmin):
        client, api = mock_garmin
        api.get_activities_by_date.return_value = [{
            "activityId": "456", "activityName": "걷기",
            "activityType": {"typeKey": "walking"},
            "startTimeLocal": "2026-04-22 12:00:00",
            "distance": 3000.0, "duration": 2400.0,
            "averageHR": 95, "maxHR": 110, "calories": 150,
        }]
        data = client.get_activities(datetime.date(2026, 4, 22), datetime.date(2026, 4, 22))
        act = data[0]
        assert act["elevation_gain"] is None
        assert act["cadence"] is None
        assert act["vo2_max"] is None


# ── splits pace + HR zones percent ───────────────────

class TestActivitySplitsPace:
    def test_splits_include_pace(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_splits.return_value = {"lapDTOs": [
            {"distance": 1000.0, "duration": 360.0, "averageHR": 145,
             "maxHR": 164, "averageSpeed": 2.78, "elevationGain": 5.0, "calories": 90},
        ]}
        data = client.get_activity_splits("123")
        assert "pace_min_km" in data[0]
        assert data[0]["pace_min_km"] is not None


class TestHrZonesPercent:
    def test_hr_zones_include_pct(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_hr_in_timezones.return_value = [
            {"zoneNumber": 1, "secsInZone": 600.0, "zoneLowBoundary": 100},
            {"zoneNumber": 2, "secsInZone": 1200.0, "zoneLowBoundary": 130},
            {"zoneNumber": 3, "secsInZone": 600.0, "zoneLowBoundary": 150},
        ]
        data = client.get_activity_hr_zones("123")
        total_pct = sum(z["zone_pct"] for z in data)
        assert abs(total_pct - 100.0) < 0.1
        assert data[1]["zone_pct"] == 50.0
