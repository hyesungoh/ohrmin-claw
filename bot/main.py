"""ohrmin-claw Discord 봇 — 메인 엔트리포인트."""
import asyncio
import datetime
import os
import sys
import tempfile
import time

import discord
from discord.ext import tasks
from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import create_llm_adapter, _CLAUDE_FALLBACK_MESSAGE
from core.learning import (
    should_propose_skill,
    is_explicit_skill_request,
    snapshot_skill_mtimes,
    detect_skill_writes,
    SKILL_RESTART_NOTICE,
)
from core.skill_sync import sync_agent_made_symlinks
from core.garmin_data import GarminConnectClient, GarminDataError
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
from core.session_index import SessionIndex
from core.session_search_tools import create_session_search_mcp_server
from core.scheduler import CronStore
from core.schedule_tools import create_schedule_mcp_server


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
# 학습 루프(축4) — off|manual|auto, 기본 off (MEMORY_MODE 미러). 인터랙티브 오너 턴에서만 동작.
LEARNING_MODE = os.getenv("LEARNING_MODE", "off")
LEARNING_TOOL_THRESHOLD = int(os.getenv("LEARNING_TOOL_THRESHOLD", "5"))
SESSION_IDLE_TIMEOUT = int(os.getenv("SESSION_IDLE_TIMEOUT", "1440"))  # 분 (기본 24시간)
NOTIFY_CHANNEL_ID = os.getenv("NOTIFY_CHANNEL_ID")  # 자동 분석 결과 전송 채널
APPLE_HEALTH_EXPORT_DIR = os.getenv(
    "APPLE_HEALTH_EXPORT_DIR",
    os.path.expanduser("~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/daily inbody"),
)
SESSION_INDEX_DB_PATH = os.path.join(PROJECT_ROOT, "data", "session_index.db")
CRON_JOBS_PATH = os.path.join(PROJECT_ROOT, "data", "cron_jobs.json")
# 학습 루프 hot-load 폴백 — 이번 턴에 스킬이 저장/수정됐는지 감지할 대상 디렉토리.
SKILLS_DIR = os.path.join(PROJECT_ROOT, ".claude", "skills")


def _env_bool(name: str, default: bool = False) -> bool:
    """환경변수를 불리언으로 파싱 (1/true/yes/on = True)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# cron 스케줄러 — kill-switch(기본 off, NOTIFY_CHANNEL_ID 게이팅 미러), 잡 수 상한.
SCHEDULER_ENABLED = _env_bool("SCHEDULER_ENABLED", False)
MAX_CRON_JOBS = int(os.getenv("MAX_CRON_JOBS", "50"))
# Garmin 컨텍스트 캐시 TTL(초) — 겹치는 초기자의 429 완화용.
HEALTH_CONTEXT_TTL = float(os.getenv("HEALTH_CONTEXT_TTL", "90"))
# steer(interrupt-then-restart) — 이전 턴 interrupt 후 unwind를 기다릴 최대 시간(초).
# 이 안에 unwind 못하면 클라이언트를 강제 정리하고 새 클라이언트로 재시작한다(무한 대기 방지).
STEER_INTERRUPT_TIMEOUT = float(os.getenv("STEER_INTERRUPT_TIMEOUT", "20"))


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

# 대화 장기기억 인덱스 (FTS5)
session_index = SessionIndex(SESSION_INDEX_DB_PATH)

# cron 스케줄러 스토어 (원자적 JSON 영속, 재시작 생존)
cron_store = CronStore(CRON_JOBS_PATH)

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

# 세션 검색 MCP (과거 대화 FTS5 전문 검색 → mcp__session_search__search)
session_search_mcp = create_session_search_mcp_server(session_index)
mcp_servers["session_search"] = session_search_mcp

# 스케줄 MCP (NL cron 스케줄러 CRUD → mcp__schedule__schedule_create 등)
# deliver 기본 채널 = NOTIFY_CHANNEL_ID. 무인 초기자엔 schedule_list만 노출(allowed_tools 매트릭스).
schedule_mcp = create_schedule_mcp_server(
    cron_store, default_channel_id=NOTIFY_CHANNEL_ID, max_jobs=MAX_CRON_JOBS
)
mcp_servers["schedule"] = schedule_mcp

# LLM 어댑터
llm = create_llm_adapter(LLM_ADAPTER_TYPE, model=LLM_MODEL, mcp_servers=mcp_servers or None, cwd=PROJECT_ROOT)

# add_memory MCP 툴이 용량 초과 시 LLM 통합기를 호출할 수 있도록 사후 주입.
memory_mgr.llm = llm

# 컨텍스트 압축, 세션 관리
context_compressor = ContextCompressor()
session_mgr = SessionManager(idle_timeout_minutes=SESSION_IDLE_TIMEOUT)

# 채널 추상화를 통한 Discord 봇
channel = DiscordChannel(token=DISCORD_TOKEN or "")


MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_IMAGES = 5

# 무인 초기자(cron tick·자동 분석) 도구셋 — 권한 매트릭스: 읽기 전용 도구 + schedule_list만.
# 무인 = 읽기 전용 불변식: Bash·파일-쓰기(Write/Edit)·schedule mutation·memory-write를 전부 제외한다.
# 주의: permission_mode="bypassPermissions" 하에서 allowed_tools는 하드 샌드박스가 아니라 모델
# 스티어링 신호에 가깝다(--allowedTools). 구조적 강제선은 PreToolUse 게이트(evaluate_tool_gate)로,
# Bash + 파일-쓰기 + schedule/memory mutation을 무인 턴에 하드 차단한다. 이 목록은 그 위의
# 스티어링 계층 — 무인 턴이 애초에 쓰기/셸 도구를 시도하지 않도록 읽기 전용 도구만 노출한다.
UNATTENDED_ALLOWED_TOOLS = [
    "Read", "Glob", "Grep", "Skill", "WebSearch", "WebFetch",
    "mcp__garmin", "mcp__body_metrics", "mcp__session_search",
    "mcp__schedule__schedule_list",  # 무인: 조회만 (mutation 제외)
]

# Garmin 429 완화 — 겹치는 초기자(on_message·cron_tick·2분 루프)가 동시에 5콜 Garmin 버스트를
# 내지 않도록 컨텍스트 수집을 세마포어(동시 1)로 직렬화 + 단기 TTL 캐시로 중복 제거한다.
_garmin_context_semaphore = asyncio.Semaphore(1)
_context_cache_lock = asyncio.Lock()
_context_cache: dict = {"ts": 0.0, "data": None}


def _invalidate_context_cache():
    """건강 컨텍스트 캐시 무효화 — 새 데이터 도착(2분 루프) 시 강제 재수집용."""
    _context_cache["data"] = None
    _context_cache["ts"] = 0.0


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


def _iso_ts(dt) -> str:
    """datetime → ISO 문자열. None/변환 실패 시 현재 UTC."""
    try:
        if dt is not None:
            return dt.isoformat()
    except Exception:
        pass
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def _index_turn_message(thread_id, ts, role, content, turn_id=None):
    """세션 인덱스 write를 스레드로 오프로드 (on_message 지연 방지). best-effort.

    봇 답변은 청크 send_reply가 아니라 턴 반환값에서 1회만 색인해 파편화를 막는다.
    """
    try:
        await asyncio.to_thread(
            session_index.index_message, str(thread_id), str(ts), role, content, turn_id
        )
    except Exception as e:
        print(f"⚠️ 세션 인덱스 색인 실패: {e}")


def map_tool_status(name: str) -> str:
    """도구 이름을 사용자용 한국어 상태 문구로 매핑."""
    if name.startswith("mcp__garmin__"):
        return "💻 Garmin 조회 중…"
    if name in ("WebSearch", "WebFetch"):
        return "🔍 검색 중…"
    if name.startswith("mcp__body_metrics__"):
        return "📊 체성분 확인 중…"
    if name.startswith("mcp__session_search__"):
        return "🔎 과거 기록 검색 중…"
    if name == "Skill":
        return "🧠 분석 중…"
    return "⚙️ 작업 중…"


class ToolStatusLine:
    """단일 transient 상태 메시지를 생성/편집/정리하는 도구 상태 피드.

    on_tool 호출마다 상태를 갱신(첫 도구=생성, 이후=편집)하고 턴 종료 시 정리한다.
    멀티툴 턴은 text↔tool을 오가므로 '첫 텍스트에 제거'하지 않고 전이별 편집 + 종료 정리한다.
    """

    def __init__(self, target):
        self._target = target
        self._msg = None

    async def update(self, name: str):
        label = map_tool_status(name)
        if self._msg is None:
            self._msg = await self._target.send(label)
        else:
            try:
                await self._msg.edit(content=label)
            except discord.HTTPException:
                pass

    async def clear(self):
        if self._msg is not None:
            try:
                await self._msg.delete()
            except discord.HTTPException:
                pass
            self._msg = None


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

        baseline_7d = HealthPreprocessor.summarize_sleep(sleep)
        last_night = HealthPreprocessor.summarize_last_night_sleep(
            sleep, hrv, today.isoformat()
        )
        context["sleep"] = {"baseline_7d": baseline_7d, "last_night": last_night}
        context["heart_rate"] = HealthPreprocessor.summarize_heart_rate(daily)
        context["hrv"] = HealthPreprocessor.summarize_hrv(hrv)
        context["activities"] = HealthPreprocessor.summarize_activities(activities)
        context["stress"] = HealthPreprocessor.summarize_stress(stress)

    latest_body_metrics = body_metrics_mgr.read_latest()
    if latest_body_metrics:
        context["body_metrics"] = latest_body_metrics

    return context


async def _collect_health_context_async() -> dict:
    """건강 컨텍스트를 수집 — 단기 TTL 캐시 + 세마포어로 Garmin 429 완화.

    캐시가 신선하면 재사용(중복 5콜 버스트 제거), 미스 시 세마포어로 직렬화해 한 번에 한
    초기자만 Garmin을 호출한다. 동기 수집은 스레드로 오프로드해 이벤트 루프를 막지 않는다.
    """
    now = time.monotonic()
    async with _context_cache_lock:
        if _context_cache["data"] is not None and (now - _context_cache["ts"]) < HEALTH_CONTEXT_TTL:
            return _context_cache["data"]

    async with _garmin_context_semaphore:
        # 세마포어 대기 중 다른 태스크가 이미 갱신했을 수 있으니 재확인(double-checked).
        async with _context_cache_lock:
            fresh = time.monotonic()
            if _context_cache["data"] is not None and (fresh - _context_cache["ts"]) < HEALTH_CONTEXT_TTL:
                return _context_cache["data"]
        data = await asyncio.to_thread(_collect_health_context)
        async with _context_cache_lock:
            _context_cache["data"] = data
            _context_cache["ts"] = time.monotonic()
        return data


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


# 인터랙티브 스레드별 진행 중 생성 태스크 (thread_id → asyncio.Task) — steer 조율용.
# 새 메시지가 오면 이 태스크를 interrupt한 뒤 새 생성으로 재시작(재개 아님)한다.
_inflight_turns: dict = {}
# 스레드별 현재 턴의 상태 홀더 (thread_id → {"superseded": bool}). steer로 밀려난 턴이
# 자신의 후처리(색인/메모리/제안)를 건너뛰도록 하는 신호. 각 턴은 자신의 홀더 참조를 잡고,
# 후행 턴이 그 홀더의 superseded를 동기적으로 True로 표시한다(dict 정체성 경합 없음).
_turn_state: dict = {}


async def _steer_and_run(thread_id, gen_factory):
    """steer — 같은 스레드에 진행 중 생성이 있으면 interrupt 후 unwind를 기다린 뒤 새 생성을 시작.

    한 스레드의 stateful 클라이언트 스트림은 항상 한 생성만 읽게 직렬화해 인터리브/중복 스트림을
    막는다. interrupt는 "재개"가 아니라 "재시작": 이전 턴의 남은 on_text는 더 이상 발화되지 않고
    새 프롬프트로 다시 시작한다. 이전 턴이 제때 unwind하지 못하면 클라이언트를 강제 정리한다.

    반환: (result, superseded). superseded=True면 이 턴은 후행 턴에 의해 밀려났으므로(부분 응답)
    호출자는 후처리(부분 봇 답변 색인·메모리 추출·스킬 제안·재시작 안내)를 건너뛰어야 한다 —
    살아남은 턴만 후처리해 memory.md 동시 read-modify-write 경합과 파편 색인을 피한다(F2).
    """
    existing = _inflight_turns.get(thread_id)
    if existing is not None and not existing.done():
        # 이전 턴을 superseded로 표시(동기적) → 그 턴이 자기 후처리를 건너뛰게 한다.
        prev_state = _turn_state.get(thread_id)
        if prev_state is not None:
            prev_state["superseded"] = True
        try:
            await llm.interrupt_session(thread_id)
        except Exception as e:
            print(f"⚠️ interrupt 실패(thread={thread_id}): {type(e).__name__}: {e}")
        # 이전 턴의 receive_response가 종료(ResultMessage)되길 기다린다. wait는 timeout에도
        # 태스크를 취소하지 않으므로(동시 awaiter 안전), 지연 시 클라이언트만 강제 정리한다.
        _done, pending = await asyncio.wait({existing}, timeout=STEER_INTERRUPT_TIMEOUT)
        if pending:
            print(f"⚠️ interrupt unwind 지연(thread={thread_id}) — 클라이언트 강제 정리 후 재시작")
            await llm.end_session(thread_id)
    state = {"superseded": False}
    _turn_state[thread_id] = state
    task = asyncio.create_task(gen_factory())
    _inflight_turns[thread_id] = task
    try:
        result = await task
    finally:
        if _inflight_turns.get(thread_id) is task:
            _inflight_turns.pop(thread_id, None)
            _turn_state.pop(thread_id, None)
    return result, state["superseded"]


async def handle_health_query(message: discord.Message, content: str, image_paths: list[str] | None = None):
    """스레드 기반 자연어 건강 질의 처리. TextBlock마다 즉시 전송."""
    full_system = _build_system_prompt()

    # 항상-켜진 7일 기본 컨텍스트 수집 실패(Garmin 401/429 등)가 인터랙티브 사용자에게 침묵으로
    # 이어지지 않도록 방어한다(F5). 실패 시 빈 컨텍스트로 degrade해 턴을 계속 진행하고, Claude는
    # 필요 시 MCP 도구로 재조회하거나 일반 답변을 이어간다.
    try:
        context = await _collect_health_context_async()
    except GarminDataError as e:
        print(f"⚠️ 기본 컨텍스트 수집 실패(Garmin) — 빈 컨텍스트로 진행: {type(e).__name__}: {e}")
        context = {}
    except Exception as e:
        print(f"⚠️ 기본 컨텍스트 수집 실패 — 빈 컨텍스트로 진행: {type(e).__name__}: {e}")
        context = {}

    is_thread = isinstance(message.channel, discord.Thread)

    if is_thread:
        target = message.channel
        thread_id = target.id

        # 세션 타임아웃 확인
        if session_mgr.is_expired(thread_id):
            session_mgr.clear(thread_id)
            # 만료된 스레드의 stateful 클라이언트를 정리(누수 방지) + 진행 태스크/상태 참조 제거.
            await llm.end_session(thread_id)
            _inflight_turns.pop(thread_id, None)
            _turn_state.pop(thread_id, None)
            history = None
        else:
            history = await build_history_from_thread(message.channel, exclude_last=True)
            # 컨텍스트 압축
            if history:
                history = await context_compressor.compress(history, llm)

        session_mgr.update_activity(thread_id)
    else:
        target = await message.create_thread(name=content[:100])
        thread_id = target.id
        session_mgr.update_activity(thread_id)
        history = None

    # 유저 메시지 색인 (화이트리스트는 on_message에서 이미 통과).
    # 첫 채널 메시지도 생성된 thread.id로 키잉해 부모 채널 고아화를 막는다.
    # 이미지-only(빈 텍스트)는 index_message가 스킵.
    turn_id = str(getattr(message, "id", "") or "")
    user_text = (getattr(message, "content", "") or "").strip()
    if user_text:
        await _index_turn_message(
            thread_id, _iso_ts(getattr(message, "created_at", None)), "user", user_text, turn_id
        )

    # 이미지 경로가 있으면 프롬프트에 포함
    if image_paths:
        img_lines = "\n".join(f"- {p}" for p in image_paths)
        content = f"[첨부 이미지]\n{img_lines}\n위 이미지 파일을 Read 도구로 읽어서 참고하세요.\n\n{content}"

    async def on_text(text: str):
        await send_reply(target, text)

    status = ToolStatusLine(target)
    # 학습 루프: 턴의 tool_use 횟수 카운트(counter) + 스킬 쓰기 감지용 사전 스냅샷.
    counter = [0]
    skills_before = snapshot_skill_mtimes(SKILLS_DIR)

    async with target.typing():
        # 인터랙티브 오너 턴 = 특권 턴. approve_skill_writes=True로 skill-write + schedule/memory
        # mutation을 허용한다(무인 턴은 미전달 → PreToolUse 게이트가 하드 차단). 화이트리스트를
        # 통과한 오너만 이 경로에 도달한다(on_message).
        # thread_id를 넘겨 스레드별 stateful 클라이언트(persistent)로 라우팅하고, _steer_and_run으로
        # 감싸 진행 중 턴이 있으면 interrupt-then-restart 한다(인터리브/중복 스트림 방지).
        def _generate():
            return llm.ask_with_context(
                full_system, content, context,
                history=history,
                on_text=on_text,
                on_tool=status.update,
                counter=counter,
                approve_skill_writes=True,
                thread_id=thread_id,
            )
        reply_text, superseded = await _steer_and_run(thread_id, _generate)
    await status.clear()

    # steer로 밀려난(superseded) 턴은 부분 응답 상태 — 후처리를 전부 건너뛴다(F2). 살아남은
    # 턴만 색인/메모리 추출/스킬 제안/재시작 안내를 수행해, 동시 read-modify-write 경합(memory.md)과
    # 부분 답변의 파편 색인을 피한다.
    if superseded:
        return

    # 봇 답변은 턴 반환값에서 1회 색인 (청크 send_reply 아님 → 파편화 방지).
    if reply_text:
        await _index_turn_message(
            thread_id,
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "assistant",
            reply_text,
            turn_id,
        )

    # 이번 턴에 에이전트가 `.agent-made/<name>/`에 스킬을 썼으면(CLI가 `.claude/` 직접 쓰기를
    # 하드 차단하므로 이 경로로 우회) `.claude/skills/<name>` 심링크로 노출 — 그래야 아래 detect가 잡는다.
    sync_agent_made_symlinks(PROJECT_ROOT)
    # hot-load 폴백: 이번 턴에 오너 승인으로 스킬이 저장/수정됐으면 재시작 안내.
    # (SDK 스킬 hot-load를 라이브 확인할 수 없어 재시작 후 적용을 보장하는 폴백 경로를 택했다.)
    new_skills = detect_skill_writes(SKILLS_DIR, skills_before)
    if new_skills:
        await send_reply(target, SKILL_RESTART_NOTICE.format(names=", ".join(new_skills)))

    # 학습 루프 제안 판정 — 인터랙티브 + 성공 + (auto: 도구 5+ / manual: 명시 요청).
    turn_ok = bool(reply_text) and reply_text != _CLAUDE_FALLBACK_MESSAGE
    propose = should_propose_skill(
        LEARNING_MODE,
        interactive=True,
        tool_count=counter[0],
        success=turn_ok,
        explicit_request=is_explicit_skill_request(content),
        threshold=LEARNING_TOOL_THRESHOLD,
    )

    # auto 메모리 추출과 스킬 캡처 제안을 단일 extract_and_save 패스로 coalesce(M4, 별도 왕복 금지).
    if MEMORY_MODE == "auto" or propose:
        conversation = [{"role": "user", "content": content}]
        if history:
            conversation = history + conversation
        proposal = await memory_mgr.extract_and_save(
            llm,
            conversation,
            propose_skill=propose,
            save_memory=(MEMORY_MODE == "auto"),
        )
        if propose and proposal:
            await send_reply(
                target,
                f"💡 이 분석 절차를 재사용 스킬로 저장할까요? — {proposal}\n"
                f'원하시면 "스킬로 저장해"라고 말씀해 주세요.',
            )


async def generate_weekly_report() -> str:
    """주간 리포트 생성."""
    context = await _collect_health_context_async()

    sleep_summary = context.get("sleep", {})
    hr_summary = context.get("heart_rate", {})
    hrv_summary = context.get("hrv", {})
    activity_summary = context.get("activities", {})
    stress_summary = context.get("stress", {})
    body_metrics_data = context.get("body_metrics")

    fallback_sleep = {
        "baseline_7d": HealthPreprocessor.summarize_sleep([]),
        "last_night": None,
    }
    weekly = HealthPreprocessor.create_weekly_summary(
        sleep=sleep_summary or fallback_sleep,
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
    """어젯밤 수면을 간단히 브리핑. baseline_7d 평균은 보조로만 표시."""
    if not sleep_data:
        return "데이터 없음"

    last_night = sleep_data.get("last_night")
    baseline = sleep_data.get("baseline_7d") or {}

    if not last_night:
        avg_hours = baseline.get("avg_total_hours", 0) or 0
        if avg_hours == 0:
            return "데이터 없음"
        return f"어젯밤 데이터 없음 (7일 평균 {avg_hours:.1f}시간)"

    hours = last_night.get("hours")
    if hours is None:
        return "데이터 없음"

    if hours >= 7:
        quality = "양호"
    elif hours >= 6:
        quality = "부족"
    else:
        quality = "매우 부족"

    parts = [f"{hours:.1f}시간 ({quality})"]
    score = last_night.get("score")
    if score:
        parts.append(f"점수 {score}")
    efficiency = last_night.get("efficiency_pct")
    if efficiency is not None:
        parts.append(f"효율 {efficiency:.0f}%")
    bedtime = last_night.get("bedtime")
    if bedtime:
        parts.append(f"취침 {bedtime}")

    return ", ".join(parts)


async def run_agent_turn(
    system: str,
    message: str,
    context: dict,
    on_text,
    on_tool=None,
    history: list[dict] | None = None,
    max_turns: int = 15,
    approve_skill_writes: bool | None = None,
    allowed_tools: list[str] | None = None,
) -> str:
    """순수 생성 primitive — 이미 수집된 context를 인자로 받아 LLM 생성만 수행.

    context를 호출자에게서 수령하므로 Garmin/체성분을 재수집하지 않는다(이중 조회 방지).
    스레드/채널 오케스트레이션은 run_agent_to_channel 또는 handle_health_query가 담당.
    """
    return await llm.ask_with_context(
        system,
        message,
        context,
        history=history,
        on_text=on_text,
        on_tool=on_tool,
        max_turns=max_turns,
        approve_skill_writes=approve_skill_writes,
        allowed_tools=allowed_tools,
    )


async def run_agent_to_channel(
    prompt: str,
    channel_id,
    thread_name: str,
    *,
    system: str | None = None,
    context: dict | None = None,
    on_tool=None,
    max_turns: int = 15,
    approve_skill_writes: bool | None = None,
    allowed_tools: list[str] | None = None,
):
    """지정 채널에 스레드를 만들고 run_agent_turn 응답을 스트리밍 게시하는 오케스트레이션.

    system/context를 넘기지 않으면 여기서 조립·수집한다. 이미 수집한 호출자(자동 분석)는
    context를 넘겨 이중 조회를 막는다. 무인 초기자(cron/자동 분석)는 approve_skill_writes를
    넘기지 않으므로 skill-write 안전 게이트가 유지된다.
    """
    if not channel_id:
        print("⚠️ channel_id 미설정 — run_agent_to_channel 건너뜀")
        return None

    try:
        notify_channel = await channel._client.fetch_channel(int(channel_id))
    except Exception as e:
        print(f"⚠️ 알림 채널 조회 실패: {e}")
        return None

    thread = await notify_channel.create_thread(
        name=thread_name,
        type=discord.ChannelType.public_thread,
    )
    session_mgr.update_activity(thread.id)

    if system is None:
        system = _build_system_prompt()
    if context is None:
        context = await _collect_health_context_async()

    async def on_text(text: str):
        await send_reply(thread, text)

    async with thread.typing():
        reply_text = await run_agent_turn(
            system,
            prompt,
            context,
            on_text,
            on_tool=on_tool,
            max_turns=max_turns,
            approve_skill_writes=approve_skill_writes,
            allowed_tools=allowed_tools,
        )

    # 봇 답변을 생성된 thread.id로 색인 (턴 반환값에서 1회).
    if reply_text:
        await _index_turn_message(
            thread.id,
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "assistant",
            reply_text,
        )
    return thread


async def _run_auto_analysis(new_rows: list[dict]):
    """새 체성분 데이터 감지 시 채널에 스레드 생성 + Claude 분석 전송."""
    if not NOTIFY_CHANNEL_ID:
        print("⚠️ NOTIFY_CHANNEL_ID 미설정 — 자동 분석 건너뜀")
        return

    latest_date = new_rows[-1]["date"]
    summary = _format_new_data_summary(new_rows)
    context = await _collect_health_context_async()

    # 어젯밤 수면 브리핑 추가 (context는 run_agent_to_channel에 재전달 → 이중 조회 방지)
    sleep_briefing = _format_sleep_briefing(context.get("sleep", {}))

    user_message = (
        f"새로운 체성분 데이터가 Apple Health에서 자동 수집됨.\n\n"
        f"**📊 체성분 데이터:**\n{summary}\n\n"
        f"**😴 어젯밤 수면:**\n{sleep_briefing}\n\n"
        f"체성분 트렌드와 수면을 데이터 기반으로 서술.\n\n"
        f"[지시]\n"
        f"1. **라벨 규칙**: `muscle_mass_kg` 컬럼은 `source`에 따라 해석이 다름. "
        f"`source=apple_health`면 **제지방(FFM)** 으로 표기(Apple Health가 lean body mass로 보내기 때문). "
        f"`source=inbody`(가정용·전문가 장비 모두 여기로 들어옴) / `source=manual`이면 **골격근**으로 표기. "
        f"자동 수집 데이터(이 트리거)는 apple_health 소스이므로 제지방으로 다룰 것.\n"
        f"2. **밴드 비교 필수 — source 필터 강제**: 최근 60일 체성분 히스토리를 "
        f"`mcp__body_metrics__get_body_metrics_history`로 실측 조회한 뒤, "
        f"**반드시 `source=apple_health` 행만 필터링**해서 밴드(min/max)를 산출할 것. "
        f"이유: 같은 `muscle_mass_kg` 컬럼이라도 apple_health는 제지방(~70kg), inbody는 골격근(~41kg) 스케일이라 "
        f"섞으면 밴드가 30kg 폭으로 벌어져 무의미해짐. 밴드 산출 후 현재값(apple_health)과 비교. "
        f"밴드 내면 **'60일 정상 변동 범위 내 — 다이어트 궤도 유지'** 로 프레이밍(단순 '정체' 라벨 반복 금지 — 학습된 무기력 리스크). "
        f"밴드 이탈 시에만 원인 논의. **BIA 노이즈 상수: 가정용 발전극형 BIA는 ±2-3%p 체지방률, ±1-2kg 제지방 변동이 정상"
        f"(Nickerson 2016, Aandstad 2014). 하루 2-3kg 제지방 스윙은 수분·글리코겐 변동으로 흔함.**\n"
        f"3. **운동 볼륨 + 러닝 질적 진단 필수**: 최근 30일 활동을 `mcp__garmin__get_activities`로 실측 조회한 뒤:\n"
        f"   - **볼륨**: 러닝 세션수·거리·주 평균, 웨이트 세션수·주요 리프트\n"
        f"   - **러닝 강도 분포 (10K sub-65 목표 정합성 진단)**: 각 러닝 세션의 심박존을 "
        f"`mcp__garmin__get_activity_hr_zones`로 조회해서 **Z1+Z2 시간 비율**을 산출. "
        f"**Z1+Z2 < 75%면 '그레이존(Z3) 편중 — VO2max 정체 원인 후보' 플래그**(Seiler polarized 80/20 기준). "
        f"LT2 자극(템포/인터벌) 월 횟수, 롱런(75분+) 월 횟수도 카운트.\n"
        f"   - 이전 대화·메모리의 낡은 요약(§11 등) 재사용 금지.\n"
        f"4. **톤 + 프레이밍**: '경보/위험/⚠️' 헤더 금지. 침착·근거 기반 서술. "
        f"밴드 내 변동은 '정체'가 아니라 **'다이어트 5주차 -2.5kg 궤도 유지 중'** 같이 누적 진전 프레이밍 우선. "
        f"짧게 마무리해도 됨.\n"
        f"5. **액션**: 별도 섹션으로 강제 서술하지 말 것. 문맥상 필요한 만큼만 자연스럽게 언급. 개수 제한 없음, 필요 없으면 생략."
    )

    # 무인 초기자 — approve_skill_writes 미전달(= skill-write 차단 유지) + 축소 도구셋
    # (매트릭스: schedule mutation·skill-write 제외, schedule_list만).
    await run_agent_to_channel(
        user_message,
        NOTIFY_CHANNEL_ID,
        f"체성분 자동 분석 — {latest_date}",
        context=context,
        allowed_tools=UNATTENDED_ALLOWED_TOOLS,
    )


async def _sweep_expired_sessions():
    """만료된 스레드의 persistent 클라이언트를 기회적으로 정리 (버려진 스레드 서브프로세스 누수 방지).

    idle 타임아웃(session_mgr.is_expired)을 재사용한다. 진행 중 턴이 있는 스레드는 건드리지
    않는다(_inflight_turns). best-effort — 한 스레드 정리 실패가 나머지를 막지 않는다.
    """
    for thread_id in llm.session_ids():
        try:
            if thread_id in _inflight_turns:
                continue  # 생성 진행 중 — 스윕 대상 아님
            if session_mgr.is_expired(thread_id):
                await llm.end_session(thread_id)
                session_mgr.clear(thread_id)
                _turn_state.pop(thread_id, None)
        except Exception as e:
            print(f"⚠️ 세션 스윕 실패(thread={thread_id}): {type(e).__name__}: {e}")


@tasks.loop(minutes=2)
async def health_sync_loop():
    """매 2분마다 iCloud에서 Apple Health 데이터 동기화 + 만료 세션 스윕(기회적)."""
    try:
        new_rows = await asyncio.to_thread(sync_from_icloud, APPLE_HEALTH_EXPORT_DIR, body_metrics_mgr)
        if new_rows:
            print(f"📊 새 체성분 데이터 {len(new_rows)}건 동기화됨")
            _invalidate_context_cache()  # 새 데이터 반영 위해 캐시 무효화(신선 컨텍스트 재수집)
            await _run_auto_analysis(new_rows)
    except Exception as e:
        print(f"⚠️ 자동 동기화 오류: {e}")
    # 동기화와 독립적으로 만료 세션을 스윕(별도 try — 스윕 실패가 동기화 루프를 죽이지 않음).
    await _sweep_expired_sessions()


@health_sync_loop.before_loop
async def before_health_sync():
    await channel._client.wait_until_ready()


def _scheduler_now() -> datetime.datetime:
    """스케줄러 기준 현재 시각 — 로컬 타임존 aware (scheduler 매처의 tz 가정)."""
    return datetime.datetime.now().astimezone()


async def _run_cron_job(job: dict, now: datetime.datetime):
    """단일 cron 잡 실행 — 예외를 삼켜 한 잡 실패가 나머지 due 잡·루프를 죽이지 않게 격리한다.

    무인 초기자이므로 축소 도구셋(UNATTENDED_ALLOWED_TOOLS)을 넘기고 approve_skill_writes는
    전달하지 않는다(skill-write 차단 유지). full memory/user 정책은 run_agent_to_channel의
    _build_system_prompt 조립으로 자동 적용된다(자동 분석과 동일, M6).
    """
    job_id = job.get("id")
    try:
        channel_id = job.get("deliver_channel_id") or NOTIFY_CHANNEL_ID
        thread_name = f"⏰ 예약 실행 — {now.strftime('%m-%d %H:%M')}"
        await run_agent_to_channel(
            job.get("prompt", ""),
            channel_id,
            thread_name,
            max_turns=int(job.get("max_turns") or 15),
            allowed_tools=UNATTENDED_ALLOWED_TOOLS,
        )
    except Exception as e:
        print(f"⚠️ cron 잡 실행 실패(id={job_id}): {type(e).__name__}: {e}")
    finally:
        # 발화 후 상태 갱신 — 실패해도 next_run을 전진시켜 매 분 재시도 폭주를 막는다.
        # 상대 one-shot은 여기서 자기 삭제된다.
        try:
            cron_store.mark_fired(job_id, now)
        except Exception as e:
            print(f"⚠️ cron 상태 갱신 실패(id={job_id}): {e}")


async def _cron_tick_once(now: datetime.datetime):
    """한 번의 tick — due·비-paused 잡을 순회 실행. per-job try/except로 실패를 격리한다.

    tasks.loop 래퍼와 분리해 단위 테스트에서 직접 호출 가능하게 둔다.
    """
    try:
        due_jobs = cron_store.due_jobs(now)
    except Exception as e:
        print(f"⚠️ cron due 조회 실패: {e}")
        return
    for job in due_jobs:
        await _run_cron_job(job, now)


@tasks.loop(minutes=1)
async def cron_tick_loop():
    """매 1분: due·비-paused cron 잡을 실행."""
    await _cron_tick_once(_scheduler_now())


@cron_tick_loop.before_loop
async def before_cron_tick():
    await channel._client.wait_until_ready()


@cron_tick_loop.error
async def cron_tick_error(exc: Exception):
    """루프 자체가 죽으면 로깅 후 재시작(loop-death 방지)."""
    print(f"⚠️ cron_tick_loop 오류 — 재시작: {type(exc).__name__}: {exc}")
    if not cron_tick_loop.is_running():
        cron_tick_loop.restart()


_session_backfill_done = False


async def _backfill_session_index():
    """봇 시작 시 Discord 스레드 히스토리를 세션 인덱스에 1회 백필 (best-effort, 멱등)."""
    try:
        rows = []
        for guild in channel._client.guilds:
            for ch in getattr(guild, "text_channels", []):
                for th in getattr(ch, "threads", []):
                    try:
                        async for msg in th.history(limit=50, oldest_first=True):
                            text = (msg.content or "").strip()
                            if not text:
                                continue
                            rows.append({
                                "thread_id": str(th.id),
                                "ts": _iso_ts(getattr(msg, "created_at", None)),
                                "role": "assistant" if msg.author.bot else "user",
                                "content": text,
                                "turn_id": str(getattr(msg, "id", "") or ""),
                            })
                    except Exception:
                        continue  # 스레드 단위 실패는 나머지 백필을 막지 않음
        if rows:
            added = await asyncio.to_thread(session_index.backfill, rows)
            print(f"🔎 세션 인덱스 백필: {added}건 색인 (총 {len(rows)}건 스캔)")
    except Exception as e:
        print(f"⚠️ 세션 인덱스 백필 실패: {e}")


@channel._client.event
async def on_ready():
    global _session_backfill_done
    print(f"✅ {channel._client.user} 로그인 완료!")
    # `.agent-made/<name>/` 에이전트 생성 스킬을 `.claude/skills/<name>` 심링크로 노출
    # (CLI가 `.claude/` 직접 쓰기를 차단하므로 에이전트는 `.agent-made/`에 쓰고 여기서 링크한다).
    linked = sync_agent_made_symlinks(PROJECT_ROOT)
    if linked:
        print(f"🔗 .agent-made 스킬 심링크 동기화: {', '.join(linked)}")
    if not health_sync_loop.is_running():
        health_sync_loop.start()
        print(f"📊 Apple Health 자동 동기화 시작 (2분 주기, 경로: {APPLE_HEALTH_EXPORT_DIR})")
    # cron 스케줄러 — kill-switch(SCHEDULER_ENABLED)로 게이팅. 미설정 시 비활성.
    if SCHEDULER_ENABLED:
        if not cron_tick_loop.is_running():
            cron_tick_loop.start()
            print(f"⏰ cron 스케줄러 시작 (1분 주기, 등록 잡 {cron_store.count()}개)")
    else:
        print("⏰ cron 스케줄러 비활성 (SCHEDULER_ENABLED 미설정)")
    if not _session_backfill_done:
        _session_backfill_done = True
        await _backfill_session_index()


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


# 봇 종료 시 모든 persistent 클라이언트를 정리(서브프로세스 누수 방지). discord.py의
# Client.close()는 종료 시 이벤트 루프가 살아있는 동안 호출되므로, 이를 감싸 기본 종료 전에
# close_all을 실행한다. on_disconnect(매 재접속마다 발화)가 아니라 실제 종료 경로만 탄다.
_original_client_close = channel._client.close


async def _close_with_cleanup():
    """기본 종료 전에 llm.close_all()로 스레드 클라이언트를 모두 disconnect."""
    try:
        await llm.close_all()
    except Exception as e:
        print(f"⚠️ 종료 시 세션 정리 실패: {type(e).__name__}: {e}")
    await _original_client_close()


channel._client.close = _close_with_cleanup


def main():
    if not DISCORD_TOKEN:
        print("❌ DISCORD_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
        sys.exit(1)
    print("🚀 ohrmin-claw 봇 시작...")
    if ALLOWED_USERS:
        print(f"🔒 허용된 유저: {len(ALLOWED_USERS)}명")
    else:
        print("⚠️ ALLOWED_USERS가 비어있습니다. 모든 메시지가 무시됩니다.")
    channel.run()


if __name__ == "__main__":
    main()
