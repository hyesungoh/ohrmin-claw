"""Garmin 데이터 MCP tool 정의 — Claude Agent SDK 인프로세스 서버."""
import datetime
import json
from typing import Annotated

from claude_agent_sdk import tool, create_sdk_mcp_server


MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 7

# Tool 레지스트리 — 테스트에서 tool 객체에 직접 접근할 수 있도록 보관
TOOL_REGISTRY: dict = {}


def _parse_dates(args: dict) -> tuple[datetime.date, datetime.date]:
    """start/end 문자열을 date로 변환. 기본값: 최근 7일, 최대 90일."""
    today = datetime.date.today()
    end = datetime.date.fromisoformat(args["end"]) if args.get("end") else today
    start = datetime.date.fromisoformat(args["start"]) if args.get("start") else end - datetime.timedelta(days=DEFAULT_RANGE_DAYS)
    if (end - start).days > MAX_RANGE_DAYS:
        start = end - datetime.timedelta(days=MAX_RANGE_DAYS)
    return start, end


def _json_response(data) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}


def create_garmin_mcp_server(garmin_client):
    """GarminConnectClient를 감싸는 인프로세스 MCP 서버 생성."""
    TOOL_REGISTRY.clear()

    DATE_SCHEMA = {
        "start": Annotated[str, "시작 날짜 (YYYY-MM-DD). 생략 시 7일 전"],
        "end": Annotated[str, "종료 날짜 (YYYY-MM-DD). 생략 시 오늘"],
    }

    @tool("get_sleep", "수면 데이터 조회 (일별 총수면, 깊은수면, REM, 점수)", DATE_SCHEMA)
    async def get_sleep(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_sleep(start, end))

    @tool("get_daily_summary", "일별 건강 요약 (안정시 심박수, 걸음수, 칼로리, 스트레스)", DATE_SCHEMA)
    async def get_daily_summary(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_daily_summary(start, end))

    @tool("get_hrv", "심박변이도(HRV) 데이터 조회 (주간평균, 상태)", DATE_SCHEMA)
    async def get_hrv(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_hrv(start, end))

    @tool("get_activities", "운동 활동 목록 조회 (종목, 거리, 시간, 심박수, 칼로리)", DATE_SCHEMA)
    async def get_activities(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_activities(start, end))

    @tool("get_stress", "일별 스트레스 수준 조회", DATE_SCHEMA)
    async def get_stress(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_stress(start, end))

    # --- 상세 활동 tool ---

    ACTIVITY_ID_SCHEMA = {
        "activity_id": Annotated[str, "활동 ID (get_activities로 먼저 조회)"],
    }

    @tool("get_activity_detail", "특정 활동의 상세 분석 (스플릿, HR 존, VO2 Max, 케이던스)", ACTIVITY_ID_SCHEMA)
    async def get_activity_detail(args):
        return _json_response(garmin_client.get_activity_detail(args["activity_id"]))

    @tool("get_activity_splits", "특정 활동의 구간별(랩) 데이터 (페이스, 심박수, 고도)", ACTIVITY_ID_SCHEMA)
    async def get_activity_splits(args):
        return _json_response(garmin_client.get_activity_splits(args["activity_id"]))

    @tool("get_activity_hr_zones", "특정 활동의 심박수 존별 시간 분포", ACTIVITY_ID_SCHEMA)
    async def get_activity_hr_zones(args):
        return _json_response(garmin_client.get_activity_hr_zones(args["activity_id"]))

    # --- get_last_activity tool ---

    LAST_ACTIVITY_SCHEMA = {
        "count": Annotated[int, "조회할 활동 수 (기본 1, 최대 10)"],
    }

    @tool("get_last_activity", "최근 활동 조회 (가장 마지막 운동부터). count로 개수 지정", LAST_ACTIVITY_SCHEMA)
    async def get_last_activity(args):
        count = min(args.get("count", 1), 10)
        return _json_response(garmin_client.get_last_activity(count=count))

    all_tools = [
        get_sleep, get_daily_summary, get_hrv, get_activities, get_stress,
        get_activity_detail, get_activity_splits, get_activity_hr_zones,
        get_last_activity,
    ]

    TOOL_REGISTRY.update({t.name: t for t in all_tools})

    return create_sdk_mcp_server(
        name="garmin",
        tools=all_tools,
    )
