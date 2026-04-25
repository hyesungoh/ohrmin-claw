"""Body Metrics MCP tool 정의 — Claude Agent SDK 인프로세스 서버."""
import datetime
from typing import Annotated

from claude_agent_sdk import tool, create_sdk_mcp_server

from core.garmin_tools import _json_response


TOOL_REGISTRY: dict = {}


def create_body_metrics_mcp_server(metrics_manager):
    """BodyMetricsManager를 감싸는 인프로세스 MCP 서버 생성."""
    TOOL_REGISTRY.clear()

    ADD_SCHEMA = {
        "date": Annotated[str, "측정 날짜 (YYYY-MM-DD). 생략 시 오늘"],
        "weight_kg": Annotated[float, "체중 (kg)"],
        "body_fat_pct": Annotated[float, "체지방률 (%)"],
        "muscle_mass_kg": Annotated[float, "골격근량 (kg)"],
        "bmi": Annotated[float, "BMI"],
        "source": Annotated[str, "데이터 출처 (manual, inbody 등). 기본 manual"],
    }

    @tool("add_body_measurement", "체성분 측정 기록 추가 (최소 1개 필드 필수)", ADD_SCHEMA)
    async def add_body_measurement(args):
        measurement_fields = {"weight_kg", "body_fat_pct", "muscle_mass_kg", "bmi"}
        provided = {k: v for k, v in args.items() if k in measurement_fields and v is not None}
        if not provided:
            return _json_response({"error": "최소 1개의 측정값(weight_kg, body_fat_pct, muscle_mass_kg, bmi)이 필요합니다."})

        date = args.get("date") or datetime.date.today().isoformat()
        source = args.get("source", "manual")
        metrics_manager.add_entry(
            date=date,
            weight_kg=args.get("weight_kg"),
            body_fat_pct=args.get("body_fat_pct"),
            muscle_mass_kg=args.get("muscle_mass_kg"),
            bmi=args.get("bmi"),
            source=source,
        )
        return _json_response({"status": "ok", "date": date, **provided})

    HISTORY_SCHEMA = {
        "count": Annotated[int, "조회할 최근 기록 수 (기본 10)"],
        "days": Annotated[int, "최근 N일 이내 기록만 조회 (선택)"],
    }

    @tool("get_body_metrics_history", "체성분 측정 이력 조회 (최근 N건 또는 기간)", HISTORY_SCHEMA)
    async def get_body_metrics_history(args):
        all_rows = metrics_manager.read_all()
        count = args.get("count", 10)

        if args.get("days"):
            cutoff = (datetime.date.today() - datetime.timedelta(days=args["days"])).isoformat()
            all_rows = [r for r in all_rows if r.get("date", "") >= cutoff]

        return _json_response(all_rows[:count])

    TREND_SCHEMA = {
        "field": Annotated[str, "분석할 필드명 (weight_kg, body_fat_pct, muscle_mass_kg, bmi)"],
        "days": Annotated[int, "분석 기간 (일, 기본 30)"],
    }

    @tool("get_body_metrics_trend", "특정 체성분 지표의 트렌드 분석 (기간별 변화)", TREND_SCHEMA)
    async def get_body_metrics_trend(args):
        field = args.get("field", "weight_kg")
        days = args.get("days", 30)
        cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

        all_rows = metrics_manager.read_all()
        filtered = [r for r in all_rows if r.get("date", "") >= cutoff]

        values = []
        for r in filtered:
            val = r.get(field)
            if val is not None:
                values.append({"date": r["date"], "value": val})

        return _json_response({"field": field, "days": days, "values": values})

    all_tools = [add_body_measurement, get_body_metrics_history, get_body_metrics_trend]
    TOOL_REGISTRY.update({t.name: t for t in all_tools})

    return create_sdk_mcp_server(
        name="body_metrics",
        tools=all_tools,
    )
