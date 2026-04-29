"""Health Manager Discord 봇 — 메인 엔트리포인트."""
import asyncio
import datetime
import os
import sys
import tempfile

import discord
from discord.ext import tasks
from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import create_llm_adapter
from core.garmin_data import GarminConnectClient
from core.garmin_tools import create_garmin_mcp_server
from core.body_metrics import BodyMetricsManager
from core.body_metrics_tools import create_body_metrics_mcp_server
from core.memory_tools import create_memory_mcp_server
from core.preprocessor import HealthPreprocessor
from core.report import ReportGenerator
from core.channel import DiscordChannel
from core.memory import MemoryManager
from core.context_compressor import ContextCompressor
from core.session_manager import SessionManager
from core.apple_health_reader import sync_from_icloud


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
MEMORY_MODE = os.getenv("MEMORY_MODE", "auto")  # auto | manual
SESSION_IDLE_TIMEOUT = int(os.getenv("SESSION_IDLE_TIMEOUT", "1440"))  # 분 (기본 24시간)
NOTIFY_CHANNEL_ID = os.getenv("NOTIFY_CHANNEL_ID")  # 자동 분석 결과 전송 채널
APPLE_HEALTH_EXPORT_DIR = os.getenv(
    "APPLE_HEALTH_EXPORT_DIR",
    os.path.expanduser("~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/daily inbody"),
)


def parse_allowed_users(raw: str) -> set[int]:
    """쉼표로 구분된 유저 ID 문자열을 정수 집합으로 파싱."""
    result = set()
    for uid in raw.split(","):
        uid = uid.strip()
        if uid:
            try:
                result.add(int(uid))
            except ValueError:
                print(f"⚠️ ALLOWED_USERS에 유효하지 않은 값: {uid!r}")
    return result


ALLOWED_USERS: set[int] = parse_allowed_users(os.getenv("ALLOWED_USERS", ""))


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
mcp_servers = {}
if garmin:
    garmin_mcp = create_garmin_mcp_server(garmin)
    mcp_servers["garmin"] = garmin_mcp
    print("✅ Garmin MCP 도구 등록 완료")

body_metrics_mcp = create_body_metrics_mcp_server(body_metrics_mgr)
mcp_servers["body_metrics"] = body_metrics_mcp

# 메모리 MCP (LLM 어댑터 생성 전에 등록)
memory_mgr = MemoryManager(PROMPTS_DIR)
memory_mcp = create_memory_mcp_server(memory_mgr)
mcp_servers["memory"] = memory_mcp

# LLM 어댑터
llm = create_llm_adapter(LLM_ADAPTER_TYPE, model=LLM_MODEL, mcp_servers=mcp_servers or None, cwd=PROJECT_ROOT)

# 컨텍스트 압축, 세션 관리
context_compressor = ContextCompressor()
session_mgr = SessionManager(idle_timeout_minutes=SESSION_IDLE_TIMEOUT)

# 채널 추상화를 통한 Discord 봇
channel = DiscordChannel(token=DISCORD_TOKEN or "")


MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_IMAGES = 5


def extract_image_attachments(attachments: list[discord.Attachment]) -> list[discord.Attachment]:
    """image/* content_type인 첨부파일만 필터링. 크기/개수 제한 적용."""
    images = [
        a for a in attachments
        if a.content_type and a.content_type.startswith("image/")
        and getattr(a, "size", 0) <= MAX_IMAGE_SIZE
    ]
    return images[:MAX_IMAGES]


async def save_images_to_temp(attachments: list[discord.Attachment]) -> list[str]:
    """이미지 첨부파일을 임시파일로 저장하고 경로 리스트 반환."""
    paths = []
    try:
        for att in attachments:
            ext = os.path.splitext(att.filename)[1] or ".png"
            fd, path = tempfile.mkstemp(suffix=ext, prefix="health_img_")
            try:
                data = await att.read()
                os.write(fd, data)
            finally:
                os.close(fd)
            paths.append(path)
    except Exception:
        cleanup_temp_images(paths)
        raise
    return paths


def cleanup_temp_images(paths: list[str]):
    """임시 이미지 파일 삭제. 에러 시 무시."""
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


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


async def _collect_health_context_async() -> dict:
    """Run the sync context collector in a thread to avoid blocking the asyncio event loop."""
    return await asyncio.to_thread(_collect_health_context)


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


def _build_system_prompt() -> str:
    """시스템 프롬프트 + 목표 + 메모리를 조립."""
    parts = [load_prompt("system.md"), load_prompt("goals.md")]
    mem = memory_mgr.read_memory()
    usr = memory_mgr.read_user()
    if mem:
        parts.append(f"[기억]\n{mem}")
    if usr:
        parts.append(f"[사용자 프로필]\n{usr}")
    return "\n\n".join(p for p in parts if p)


async def handle_health_query(message: discord.Message, content: str, image_paths: list[str] | None = None):
    """스레드 기반 자연어 건강 질의 처리. TextBlock마다 즉시 전송."""
    full_system = _build_system_prompt()

    context = await _collect_health_context_async()

    is_thread = isinstance(message.channel, discord.Thread)

    if is_thread:
        target = message.channel
        thread_id = target.id

        # 세션 타임아웃 확인
        if session_mgr.is_expired(thread_id):
            session_mgr.clear(thread_id)
            history = None
        else:
            history = await build_history_from_thread(message.channel, exclude_last=True)
            # 컨텍스트 압축
            if history:
                history = await context_compressor.compress(history, llm)

        session_mgr.update_activity(thread_id)
    else:
        target = await message.create_thread(name=content[:100])
        session_mgr.update_activity(target.id)
        history = None

    # 이미지 경로가 있으면 프롬프트에 포함
    if image_paths:
        img_lines = "\n".join(f"- {p}" for p in image_paths)
        content = f"[첨부 이미지]\n{img_lines}\n위 이미지 파일을 Read 도구로 읽어서 참고하세요.\n\n{content}"

    async def on_text(text: str):
        await send_reply(target, text)

    async with target.typing():
        await llm.ask_with_context(
            full_system, content, context,
            history=history,
            on_text=on_text,
        )

    # auto 모드: 대화에서 메모리 추출
    if MEMORY_MODE == "auto":
        conversation = [{"role": "user", "content": content}]
        if history:
            conversation = history + conversation
        await memory_mgr.extract_and_save(llm, conversation)


async def generate_weekly_report() -> str:
    """주간 리포트 생성."""
    context = await _collect_health_context_async()

    sleep_summary = context.get("sleep", {})
    hr_summary = context.get("heart_rate", {})
    hrv_summary = context.get("hrv", {})
    activity_summary = context.get("activities", {})
    stress_summary = context.get("stress", {})
    body_metrics_data = context.get("body_metrics")

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



def _format_new_data_summary(rows: list[dict]) -> str:
    """새 체성분 데이터를 한 줄 요약."""
    parts = []
    for row in rows:
        items = [f"날짜: {row['date']}"]
        if row.get("weight_kg") is not None:
            items.append(f"체중: {row['weight_kg']}kg")
        if row.get("body_fat_pct") is not None:
            items.append(f"체지방률: {row['body_fat_pct']}%")
        if row.get("muscle_mass_kg") is not None:
            items.append(f"제지방량: {row['muscle_mass_kg']}kg")
        if row.get("bmi") is not None:
            items.append(f"BMI: {row['bmi']}")
        parts.append(", ".join(items))
    return "\n".join(parts)


def _format_sleep_briefing(sleep_data: dict) -> str:
    """수면 데이터를 간단히 브리핑."""
    if not sleep_data:
        return "데이터 없음"

    avg_hours = sleep_data.get("avg_total_hours", 0)
    min_hours = sleep_data.get("min_hours", 0)
    max_hours = sleep_data.get("max_hours", 0)
    trend = sleep_data.get("trend", "no_data")

    if avg_hours == 0:
        return "데이터 없음"

    # 수면 품질 판정
    if avg_hours >= 7:
        quality = "양호"
    elif avg_hours >= 6:
        quality = "부족"
    else:
        quality = "매우 부족"

    # 변동성 계산
    if max_hours > 0 and min_hours > 0:
        variation = max_hours - min_hours
        variation_desc = f" (변동: {variation:.1f}시간)" if variation > 2 else ""
    else:
        variation_desc = ""

    trend_emoji = "📈" if trend == "improving" else "📉" if trend == "declining" else "➡️"

    return f"평균 {avg_hours:.1f}시간 ({quality}){variation_desc} {trend_emoji}"


async def _run_auto_analysis(new_rows: list[dict]):
    """새 체성분 데이터 감지 시 채널에 스레드 생성 + Claude 분석 전송."""
    if not NOTIFY_CHANNEL_ID:
        print("⚠️ NOTIFY_CHANNEL_ID 미설정 — 자동 분석 건너뜀")
        return

    try:
        notify_channel = await channel._client.fetch_channel(int(NOTIFY_CHANNEL_ID))
    except Exception as e:
        print(f"⚠️ 알림 채널 조회 실패: {e}")
        return

    latest_date = new_rows[-1]["date"]
    thread = await notify_channel.create_thread(
        name=f"체성분 자동 분석 — {latest_date}",
        type=discord.ChannelType.public_thread,
    )
    session_mgr.update_activity(thread.id)

    summary = _format_new_data_summary(new_rows)
    full_system = _build_system_prompt()
    context = await _collect_health_context_async()

    # 어젯밤 수면 브리핑 추가
    sleep_briefing = _format_sleep_briefing(context.get("sleep", {}))

    user_message = (
        f"새로운 체성분 데이터가 Apple Health에서 자동 수집되었습니다.\n\n"
        f"**📊 체성분 데이터:**\n{summary}\n\n"
        f"**😴 어젯밤 수면:**\n{sleep_briefing}\n\n"
        f"체성분 트렌드와 수면 효율을 종합적으로 분석해주세요."
    )

    async def on_text(text: str):
        await send_reply(thread, text)

    async with thread.typing():
        await llm.ask_with_context(full_system, user_message, context, on_text=on_text)


@tasks.loop(minutes=2)
async def health_sync_loop():
    """매 2분마다 iCloud에서 Apple Health 데이터 동기화."""
    try:
        new_rows = await asyncio.to_thread(sync_from_icloud, APPLE_HEALTH_EXPORT_DIR, body_metrics_mgr)
        if new_rows:
            print(f"📊 새 체성분 데이터 {len(new_rows)}건 동기화됨")
            await _run_auto_analysis(new_rows)
        else:
            print("📊 새 체성분 데이터 없음")
    except Exception as e:
        print(f"⚠️ 자동 동기화 오류: {e}")


@health_sync_loop.before_loop
async def before_health_sync():
    await channel._client.wait_until_ready()


@channel._client.event
async def on_ready():
    print(f"✅ {channel._client.user} 로그인 완료!")
    if not health_sync_loop.is_running():
        health_sync_loop.start()
        print(f"📊 Apple Health 자동 동기화 시작 (2분 주기, 경로: {APPLE_HEALTH_EXPORT_DIR})")


@channel._client.event
async def on_message(message: discord.Message):
    if message.author == channel._client.user:
        return

    # 허용된 유저만 응답 (화이트리스트)
    if message.author.id not in ALLOWED_USERS:
        return

    content = message.content.strip()

    # 이미지 첨부파일 확인
    image_attachments = extract_image_attachments(message.attachments)

    if not content and not image_attachments:
        return

    # 주간 리포트 요청
    if content and ("주간 리포트" in content or "weekly report" in content.lower()):
        async with message.channel.typing():
            report = await generate_weekly_report()
        await send_reply(message.channel, report)
        return

    # 이미지 저장 → 질의 → 정리
    image_paths = await save_images_to_temp(image_attachments)
    try:
        await handle_health_query(
            message,
            content or "이 이미지를 분석해주세요.",
            image_paths=image_paths or None,
        )
    finally:
        cleanup_temp_images(image_paths)


def main():
    if not DISCORD_TOKEN:
        print("❌ DISCORD_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
        sys.exit(1)
    print("🚀 Health Manager 봇 시작...")
    if ALLOWED_USERS:
        print(f"🔒 허용된 유저: {len(ALLOWED_USERS)}명")
    else:
        print("⚠️ ALLOWED_USERS가 비어있습니다. 모든 메시지가 무시됩니다.")
    channel.run()


if __name__ == "__main__":
    main()
