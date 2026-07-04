"""스케줄 MCP tool 정의 — Claude Agent SDK 인프로세스 서버.

CronStore를 감싸 예약/반복 작업을 CRUD한다 → mcp__schedule__schedule_create 등.
입력은 구조화(5필드 cron 문자열 또는 30m/2h/1d 상대) — 자연어→cron 변환은 LLM이 담당한다.
무인 초기자(cron tick/자동 분석)에는 schedule_list만 노출한다(권한 매트릭스, allowed_tools).
"""
import datetime
from typing import Annotated

from claude_agent_sdk import tool, create_sdk_mcp_server

from core.garmin_tools import _json_response
from core.scheduler import validate_schedule


TOOL_REGISTRY: dict = {}

DEFAULT_MAX_JOBS = 50


def _local_now() -> datetime.datetime:
    """스케줄 계산 기준 현재 시각 — 로컬 타임존 aware (매처의 tz 가정)."""
    return datetime.datetime.now().astimezone()


def create_schedule_mcp_server(
    store,
    default_channel_id=None,
    max_jobs: int = DEFAULT_MAX_JOBS,
    now_fn=_local_now,
):
    """CronStore를 감싸는 인프로세스 MCP 서버 생성.

    default_channel_id: schedule_create에서 deliver_channel_id 미지정 시 기본 전송 채널.
    max_jobs: 생성 상한(초과 시 거부). now_fn: 테스트 주입용 클록.
    """
    TOOL_REGISTRY.clear()

    CREATE_SCHEMA = {
        "prompt": Annotated[str, "발화 시 실행할 지시(프롬프트). 이 내용으로 에이전트 턴이 실행됨"],
        "schedule": Annotated[
            str,
            "5필드 cron('0 20 * * 0' = 매주 일 20시) 또는 상대 one-shot('30m'/'2h'/'1d'). "
            "자연어는 호출 전에 cron으로 변환할 것",
        ],
        "deliver_channel_id": Annotated[str, "결과 전송 Discord 채널 ID (생략 시 기본 알림 채널)"],
        "max_turns": Annotated[int, "잡 실행 시 최대 턴 수 (기본 15)"],
    }

    @tool(
        "schedule_create",
        "예약/반복 작업 생성 (cron 또는 상대 시간). 자연어는 호출 전 cron으로 변환할 것",
        CREATE_SCHEMA,
    )
    async def schedule_create(args):
        prompt = (args.get("prompt") or "").strip()
        schedule = (args.get("schedule") or "").strip()
        if not prompt:
            return _json_response({"success": False, "error": "prompt가 비어 있습니다."})
        try:
            validate_schedule(schedule)
        except ValueError as e:
            return _json_response({"success": False, "error": f"schedule 형식 오류: {e}"})
        if store.count() >= max_jobs:
            return _json_response(
                {"success": False, "error": f"스케줄 수 상한({max_jobs}) 초과 — 기존 잡을 삭제하세요."}
            )
        channel = args.get("deliver_channel_id") or default_channel_id
        try:
            max_turns = int(args.get("max_turns") or 15)
        except (TypeError, ValueError):
            max_turns = 15
        job = store.create(
            prompt, schedule, now_fn(), deliver_channel_id=channel, max_turns=max_turns
        )
        return _json_response({"success": True, "job": job})

    @tool("schedule_list", "등록된 예약/반복 작업 목록 조회", {})
    async def schedule_list(args):
        return _json_response({"jobs": store.list()})

    ID_SCHEMA = {"id": Annotated[str, "대상 스케줄 ID (schedule_list로 조회)"]}

    @tool("schedule_pause", "스케줄 일시정지 (발화 중단, 삭제 아님)", ID_SCHEMA)
    async def schedule_pause(args):
        job = store.pause(args.get("id", ""))
        if job is None:
            return _json_response({"success": False, "error": "해당 ID의 스케줄이 없습니다."})
        return _json_response({"success": True, "job": job})

    @tool("schedule_resume", "일시정지된 스케줄 재개", ID_SCHEMA)
    async def schedule_resume(args):
        job = store.resume(args.get("id", ""))
        if job is None:
            return _json_response({"success": False, "error": "해당 ID의 스케줄이 없습니다."})
        return _json_response({"success": True, "job": job})

    @tool("schedule_remove", "스케줄 삭제", ID_SCHEMA)
    async def schedule_remove(args):
        job = store.remove(args.get("id", ""))
        if job is None:
            return _json_response({"success": False, "error": "해당 ID의 스케줄이 없습니다."})
        return _json_response({"success": True, "removed": job["id"]})

    all_tools = [
        schedule_create,
        schedule_list,
        schedule_pause,
        schedule_resume,
        schedule_remove,
    ]
    TOOL_REGISTRY.update({t.name: t for t in all_tools})

    return create_sdk_mcp_server(name="schedule", tools=all_tools)
