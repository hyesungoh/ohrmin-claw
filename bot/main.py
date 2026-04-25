"""Health Manager Discord 봇 — 메인 엔트리포인트."""
import datetime
import os
import sys

import discord
from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import create_llm_adapter
from core.garmin_data import GarminConnectClient
from core.garmin_tools import create_garmin_mcp_server
from core.body_metrics import BodyMetricsManager
from core.body_metrics_tools import create_body_metrics_mcp_server
from core.preprocessor import HealthPreprocessor
from core.report import ReportGenerator
from core.channel import DiscordChannel


load_dotenv()

# 설정
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
LLM_ADAPTER_TYPE = os.getenv("LLM_ADAPTER", "claude")
LLM_MODEL = os.getenv("LLM_MODEL")  # 예: claude-sonnet-4-20250514
GARMIN_EMAIL = os.getenv("GARMIN_USERNAME")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GARMIN_TOKEN_DIR = os.path.expanduser("~/.garminconnect")
BODY_METRICS_CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "inbody.csv")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def load_prompt(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""


# 데이터 소스
garmin = None
if GARMIN_EMAIL and GARMIN_PASSWORD:
    try:
        garmin = GarminConnectClient(
            email=GARMIN_EMAIL,
            password=GARMIN_PASSWORD,
            token_dir=GARMIN_TOKEN_DIR,
        )
        print("✅ Garmin Connect 로그인 성공")
    except Exception as e:
        print(f"⚠️ Garmin Connect 로그인 실패: {e}")
        garmin = None
body_metrics_mgr = BodyMetricsManager(BODY_METRICS_CSV_PATH)

# MCP 서버 생성
mcp_servers = []
if garmin:
    garmin_mcp = create_garmin_mcp_server(garmin)
    mcp_servers.append(garmin_mcp)
    print("✅ Garmin MCP 도구 등록 완료")

body_metrics_mcp = create_body_metrics_mcp_server(body_metrics_mgr)
mcp_servers.append(body_metrics_mcp)

# LLM 어댑터
llm = create_llm_adapter(LLM_ADAPTER_TYPE, model=LLM_MODEL, mcp_servers=mcp_servers or None, cwd=PROJECT_ROOT)

# 채널 추상화를 통한 Discord 봇
channel = DiscordChannel(token=DISCORD_TOKEN or "")


async def send_reply(target: discord.abc.Messageable, text: str):
    """채널 추상화의 _split_message를 사용하여 메시지를 전송."""
    for chunk in channel._split_message(text):
        await target.send(chunk)


def _collect_health_context() -> dict:
    """건강 데이터 컨텍스트를 수집."""
    context = {}
    today = datetime.date.today()
    week_ago = today - datetime.timedelta(days=7)

    if garmin:
        sleep = garmin.get_sleep(week_ago, today)
        daily = garmin.get_daily_summary(week_ago, today)
        hrv = garmin.get_hrv(week_ago, today)
        activities = garmin.get_activities(week_ago, today)
        stress = garmin.get_stress(week_ago, today)

        context["sleep"] = HealthPreprocessor.summarize_sleep(sleep)
        context["heart_rate"] = HealthPreprocessor.summarize_heart_rate(daily)
        context["hrv"] = HealthPreprocessor.summarize_hrv(hrv)
        context["activities"] = HealthPreprocessor.summarize_activities(activities)
        context["stress"] = HealthPreprocessor.summarize_stress(stress)

    latest_body_metrics = body_metrics_mgr.read_latest()
    if latest_body_metrics:
        context["body_metrics"] = latest_body_metrics

    return context


async def build_history_from_thread(
    thread: discord.Thread,
    exclude_last: bool = False,
) -> list[dict]:
    """스레드의 메시지들을 대화 이력 형태로 변환."""
    messages = []
    async for msg in thread.history(limit=50, oldest_first=True):
        role = "assistant" if msg.author.bot else "user"
        messages.append({"role": role, "content": msg.content})
    if exclude_last and messages:
        messages = messages[:-1]
    return messages


async def handle_health_query(message: discord.Message, content: str):
    """스레드 기반 자연어 건강 질의 처리. TextBlock마다 즉시 전송."""
    system_prompt = load_prompt("system.md")
    goals = load_prompt("goals.md")
    full_system = f"{system_prompt}\n\n{goals}"

    context = _collect_health_context()

    is_thread = isinstance(message.channel, discord.Thread)

    if is_thread:
        target = message.channel
        history = await build_history_from_thread(message.channel, exclude_last=True)
    else:
        target = await message.create_thread(name=content[:100])
        history = None

    async def on_text(text: str):
        await send_reply(target, text)

    async with target.typing():
        await llm.ask_with_context(
            full_system, content, context,
            history=history,
            on_text=on_text,
        )


async def generate_weekly_report() -> str:
    """주간 리포트 생성."""
    today = datetime.date.today()
    week_ago = today - datetime.timedelta(days=7)

    sleep_summary = {}
    hr_summary = {}
    hrv_summary = {}
    activity_summary = {}
    stress_summary = {}

    if garmin:
        sleep_summary = HealthPreprocessor.summarize_sleep(garmin.get_sleep(week_ago, today))
        hr_summary = HealthPreprocessor.summarize_heart_rate(garmin.get_daily_summary(week_ago, today))
        hrv_summary = HealthPreprocessor.summarize_hrv(garmin.get_hrv(week_ago, today))
        activity_summary = HealthPreprocessor.summarize_activities(garmin.get_activities(week_ago, today))
        stress_summary = HealthPreprocessor.summarize_stress(garmin.get_stress(week_ago, today))

    body_metrics_data = body_metrics_mgr.read_latest()

    weekly = HealthPreprocessor.create_weekly_summary(
        sleep=sleep_summary or {"avg_total_hours": 0, "avg_score": 0, "trend": "no_data"},
        heart_rate=hr_summary or {"avg_rhr": 0, "trend": "no_data"},
        activities=activity_summary or {"total_count": 0, "total_calories": 0, "total_distance": 0, "total_time_hours": 0, "by_sport": {}},
        hrv=hrv_summary or {"avg_weekly": 0, "trend": "no_data", "status_distribution": {}},
        stress=stress_summary or {"avg_stress": 0},
        body_metrics=body_metrics_data,
    )

    report = ReportGenerator.weekly_report(weekly)

    # Claude로 인사이트 추가
    system_prompt = load_prompt("system.md")
    goals = load_prompt("goals.md")
    insight_prompt = f"{system_prompt}\n\n{goals}"

    insight = await llm.ask_with_context(
        insight_prompt,
        "위 주간 데이터를 분석하고, 개선 포인트와 다음 주 권장사항을 간결하게 제시해줘.",
        weekly,
    )

    return f"{report}\n\n---\n\n## 🤖 AI 인사이트\n{insight}"



@channel._client.event
async def on_ready():
    print(f"✅ {channel._client.user} 로그인 완료!")


@channel._client.event
async def on_message(message: discord.Message):
    if message.author == channel._client.user:
        return

    content = message.content.strip()
    if not content:
        return

    # 주간 리포트 요청
    if "주간 리포트" in content or "weekly report" in content.lower():
        async with message.channel.typing():
            report = await generate_weekly_report()
        await send_reply(message.channel, report)
        return

    # 일반 건강 질의
    await handle_health_query(message, content)


def main():
    if not DISCORD_TOKEN:
        print("❌ DISCORD_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
        sys.exit(1)
    print("🚀 Health Manager 봇 시작...")
    channel.run()


if __name__ == "__main__":
    main()
