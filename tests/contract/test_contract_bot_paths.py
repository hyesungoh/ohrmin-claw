"""봇 초기자 경로 계약 (AC-4·AC-18·AC-19) — bot.main의 실제 오케스트레이션 + 하니스 어댑터 + fake Discord.

- session/steer: 인터랙티브 새 스레드·후속 턴 세션 재사용·만료 정리, 생성 중 새 메시지 = interrupt-then-restart,
  interrupt unwind 타임아웃 = end_session 후 재시작(`bot/main.py` _steer_and_run).
- unattended: cron 잡·자동 분석 = 먼저 만든 스레드에 게시, ro 라우팅 + UNATTENDED_ALLOWED_TOOLS 번역, 프롬프트 도구 표기 렌더.
- weekly: 주간 리포트 one-shot. memory: 인터랙티브 턴 후 auto 추출 결과가 참조 구현과 동일.
- error: 초기자별 오류 안내 게시(인터랙티브 1회·cron/자동분석 생성 스레드·주간 리포트 반환), 압축 실패 = 원본 이력.
"""
import asyncio
import datetime
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core.context_compressor import ContextCompressor
from core.llm_errors import LLMError
from core.memory import MemoryManager
from core.runtimes.tool_names import render_tool_refs, translate_allowed_tools
from core.session_manager import SessionManager
from tests.contract.harness import EXPECTED_CATALOG, NOTIFY_CHANNEL_ID, eventually, gate_lines
from tests.contract.scenario import (
    RAW_PROVIDER_MARKER,
    end,
    error,
    gate_probe,
    text,
    tool,
    wait_interrupt,
)

SCHEDULE_ARGS = {"prompt": "주입된 예약", "schedule": "30m", "deliver_channel_id": None, "max_turns": None}
BOT_ERRORS = [
    ("usage_limit", lambda: error("usage_limit", resets_at=1760000000), lambda h: LLMError.usage_limit(h.backend_id, 1760000000)),
    ("auth_expired", lambda: error("auth_expired"), lambda h: LLMError.auth_expired(h.backend_id, h.auth_fix)),
    ("generic", lambda: error("generic"), lambda h: LLMError.generic()),
]
BOT_ERROR_PARAMS = pytest.mark.parametrize(
    "step, expected", [(s, e) for _, s, e in BOT_ERRORS], ids=[i for i, _, _ in BOT_ERRORS]
)


# ── fake Discord ─────────────────────────────────────────────────────


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class SentMessage:
    def __init__(self, content):
        self.content = content
        self.deleted = False

    async def edit(self, content=None):
        self.content = content

    async def delete(self):
        self.deleted = True


def make_thread(thread_id):
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.sent = []
    thread.history_messages = []

    async def send(content):
        message = SentMessage(content)
        thread.sent.append(message)
        return message

    async def history(limit=None, oldest_first=True):
        for message in thread.history_messages:
            yield message

    thread.send = send
    thread.history = history
    thread.typing = lambda: _Typing()
    return thread


def texts(thread):
    """스레드에 남은 메시지 (정리된 도구 상태 줄 제외) = 사용자에게 보인 on_text 순서."""
    return [m.content for m in thread.sent if not m.deleted]


def history_message(content, bot):
    return SimpleNamespace(content=content, author=SimpleNamespace(bot=bot))


def make_message(content, channel, msg_id=1, new_thread=None):
    message = MagicMock(spec=discord.Message)
    message.content = content
    message.id = msg_id
    message.created_at = None
    message.author = MagicMock()
    message.author.bot = False
    message.channel = channel
    message.create_thread = AsyncMock(return_value=new_thread)
    return message


class NotifyChannel:
    def __init__(self):
        self.threads = []

    async def create_thread(self, name, type=None):
        thread = make_thread(6000 + len(self.threads))
        thread.name = name
        self.threads.append(thread)
        return thread


@pytest.fixture
def rig(harness, monkeypatch):
    import bot.main as main

    notify = NotifyChannel()
    bot_channel = SimpleNamespace(_client=MagicMock(), _split_message=lambda text: [text])
    bot_channel._client.fetch_channel = AsyncMock(return_value=notify)
    env = harness.env
    for name, value in {
        "llm": harness.adapter,
        "LLM_CONFIG": harness.config,
        "channel": bot_channel,
        "memory_mgr": env.memory_mgr,
        "body_metrics_mgr": env.body_metrics_mgr,
        "cron_store": env.cron_store,
        "garmin": None,
        "session_mgr": SessionManager(),
        "context_compressor": ContextCompressor(),
        "SKILLS_DIR": env.skills_dir,
        "PROJECT_ROOT": env.root,
        "NOTIFY_CHANNEL_ID": NOTIFY_CHANNEL_ID,
        "MEMORY_MODE": "manual",
        "LEARNING_MODE": "off",
        "load_prompt": lambda filename: f"<{filename}>",
    }.items():
        monkeypatch.setattr(main, name, value)
    main._inflight_turns.clear()
    main._turn_state.clear()
    yield SimpleNamespace(main=main, notify=notify)
    main._inflight_turns.clear()
    main._turn_state.clear()


def _unattended_tools(harness, main):
    return translate_allowed_tools(harness.backend_id, main.UNATTENDED_ALLOWED_TOOLS, skills_registry=harness.registry)


# ── session / steer ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interactive_session_new_thread_streams_owner_turn(rig, harness, capsys):
    thread = make_thread(4001)
    message = make_message("오늘 수면 어때?", channel=MagicMock(), new_thread=thread)
    harness.fake.script(
        text("첫"),
        gate_probe("Bash", {"command": "ls"}),
        tool("mcp__garmin__get_sleep", {"start": None, "end": None}),
        text("둘"),
    )
    capsys.readouterr()

    await rig.main.handle_health_query(message, "오늘 수면 어때?")

    assert texts(thread) == ["첫", "둘"]
    statuses = [m for m in thread.sent if m.deleted]
    assert len(statuses) == 1 and statuses[0].content == rig.main.map_tool_status("mcp__garmin__get_sleep")
    assert "[질문]\n오늘 수면 어때?" in harness.fake.prompts[0]
    assert (EXPECTED_CATALOG in harness.fake.system_prompts[0]) is harness.registry
    lines = [(l["cap"], l["decision"]) for l in gate_lines(capsys.readouterr().out, tool="Bash")]
    assert lines == [("priv", "allow")]
    assert harness.adapter.has_session(4001)


@pytest.mark.asyncio
async def test_interactive_session_followup_reuses_live_session(rig, harness):
    thread = make_thread(4002)
    harness.fake.script(text("a1"), end(), text("a2"))
    await rig.main.handle_health_query(make_message("q1", channel=MagicMock(), new_thread=thread), "q1")
    thread.history_messages = [history_message("q1", False), history_message("a1", True), history_message("q2", False)]

    await rig.main.handle_health_query(make_message("q2", channel=thread, msg_id=2), "q2")

    assert texts(thread) == ["a1", "a2"]
    assert harness.fake.session_starts == 1
    assert "[대화 이력]" not in harness.fake.prompts[1] and "[질문]\nq2" in harness.fake.prompts[1]


@pytest.mark.asyncio
async def test_interactive_session_expired_thread_ends_session_and_starts_fresh(rig, harness, monkeypatch):
    thread = make_thread(4003)
    harness.fake.script(text("a1"), end(), text("a2"))
    await rig.main.handle_health_query(make_message("q1", channel=MagicMock(), new_thread=thread), "q1")
    rig.main.session_mgr._last_activity[4003] = time.time() - 10**9
    thread.history_messages = [history_message("q1", False), history_message("a1", True), history_message("q2", False)]

    await rig.main.handle_health_query(make_message("q2", channel=thread, msg_id=2), "q2")

    assert harness.fake.session_closes == 1 and harness.fake.session_starts == 2
    assert "[대화 이력]" not in harness.fake.prompts[1]
    assert texts(thread) == ["a1", "a2"]


@pytest.mark.asyncio
async def test_steer_new_message_interrupts_and_restarts(rig, harness, monkeypatch):
    monkeypatch.setattr(rig.main, "MEMORY_MODE", "auto")
    thread = make_thread(4004)
    harness.fake.script(
        text("turn1-a"), wait_interrupt(), text("turn1-b"), end(),
        text("turn2"), end(),
        text("NONE"),  # 살아남은 턴만의 메모리 추출
    )

    first = asyncio.create_task(rig.main.handle_health_query(make_message("q1", channel=thread, msg_id=1), "q1"))
    await eventually(lambda: texts(thread) == ["turn1-a"])
    await rig.main.handle_health_query(make_message("q2", channel=thread, msg_id=2), "q2")
    await first

    assert texts(thread) == ["turn1-a", "turn2"]
    assert harness.fake.interrupts == 1
    assert harness.fake.session_starts == 2 and harness.fake.session_closes == 0  # 스레드 세션 1(재사용) + 추출 one-shot 1
    assert harness.fake.pending_turns == 0 and harness.fake.unscripted_turns == 0
    assert sum("메모리 추출기" in s for s in harness.fake.system_prompts) == 1  # superseded 턴은 후처리 생략
    assert rig.main._inflight_turns.get(4004) is None


@pytest.mark.asyncio
async def test_steer_interrupt_timeout_forces_end_session_then_restarts(rig, harness, monkeypatch):
    monkeypatch.setattr(rig.main, "STEER_INTERRUPT_TIMEOUT", 0.05)
    harness.fake.ignore_interrupt = True
    thread = make_thread(4005)
    harness.fake.script(text("turn1-a"), wait_interrupt(), text("turn1-b"), end(), text("turn2"))

    first = asyncio.create_task(rig.main.handle_health_query(make_message("q1", channel=thread, msg_id=1), "q1"))
    await eventually(lambda: texts(thread) == ["turn1-a"])
    await rig.main.handle_health_query(make_message("q2", channel=thread, msg_id=2), "q2")
    await first

    assert texts(thread) == ["turn1-a", "turn2"]
    assert harness.fake.interrupts == 1
    assert harness.fake.session_closes == 1 and harness.fake.session_starts == 2
    assert harness.adapter.has_session(4005)


# ── unattended / weekly / memory ─────────────────────────────────────


@pytest.mark.asyncio
async def test_unattended_cron_job_posts_to_created_thread_readonly(rig, harness, capsys):
    now = datetime.datetime.now().astimezone()
    job = harness.env.cron_store.create(
        "mcp__garmin__get_sleep로 어젯밤 수면 요약", "0 9 * * *", now, deliver_channel_id=NOTIFY_CHANNEL_ID,
    )
    harness.fake.script(text("요약"), gate_probe("mcp__schedule__schedule_create", SCHEDULE_ARGS), text("끝"))
    capsys.readouterr()

    await rig.main._run_cron_job(job, now)

    (thread,) = rig.notify.threads
    assert texts(thread) == ["요약", "끝"]
    assert render_tool_refs(job["prompt"], harness.backend_id) in harness.fake.prompts[-1]
    assert harness.fake.allowed_tools[-1] == _unattended_tools(harness, rig.main)
    lines = [(l["cap"], l["decision"]) for l in gate_lines(capsys.readouterr().out, tool="mcp__schedule__schedule_create")]
    assert lines == [("ro", "deny")]
    assert harness.env.cron_store.count() == 1
    assert harness.env.cron_store.get(job["id"])["last_run_iso"] == now.isoformat()
    assert harness.adapter.session_ids() == []


@pytest.mark.asyncio
async def test_unattended_auto_analysis_readonly_with_rendered_tool_refs(rig, harness):
    rows = [{"date": "2026-09-15", "weight_kg": 79.9, "body_fat_pct": 20.1, "muscle_mass_kg": 60.0, "bmi": 24.1}]
    harness.fake.script(text("분석"))

    await rig.main._run_auto_analysis(rows)

    (thread,) = rig.notify.threads
    assert texts(thread) == ["분석"]
    prompt = harness.fake.prompts[-1]
    assert "📍 2026-09-15" in prompt
    assert render_tool_refs("mcp__body_metrics__get_body_metrics_history", harness.backend_id) in prompt
    assert harness.fake.allowed_tools[-1] == _unattended_tools(harness, rig.main)
    assert harness.adapter.session_ids() == []


@pytest.mark.asyncio
async def test_weekly_report_is_one_shot(rig, harness):
    harness.fake.script(text("다음 주 권장"))

    report = await rig.main.generate_weekly_report()

    assert report.endswith("## 🤖 AI 인사이트\n다음 주 권장")
    assert harness.fake.session_starts == 1 and harness.adapter.session_ids() == []
    assert "<system.md>\n\n<goals.md>" in harness.fake.system_prompts[-1]


@pytest.mark.asyncio
async def test_memory_auto_extraction_after_interactive_turn_matches_reference(rig, harness, monkeypatch, tmp_path):
    monkeypatch.setattr(rig.main, "MEMORY_MODE", "auto")
    reply = "MEMORY: 매일 5km 러닝"
    (tmp_path / "reference").mkdir()
    reference = MemoryManager(str(tmp_path / "reference"))
    stub = MagicMock()
    stub.ask = AsyncMock(return_value=reply)
    await reference.extract_and_save(stub, [{"role": "user", "content": "나 매일 5km 뛰어"}])
    thread = make_thread(4006)
    harness.fake.script(text("좋아요"), end(), text(reply))

    await rig.main.handle_health_query(make_message("나 매일 5km 뛰어", channel=MagicMock(), new_thread=thread), "나 매일 5km 뛰어")

    assert texts(thread) == ["좋아요"]
    assert harness.env.memory_mgr.read_memory().encode() == reference.read_memory().encode() == "매일 5km 러닝".encode()
    assert "메모리 추출기" in harness.fake.system_prompts[1]


# ── error (AC-4 초기자별) ────────────────────────────────────────────


@pytest.mark.asyncio
@BOT_ERROR_PARAMS
async def test_error_interactive_turn_posts_message_once_without_followups(rig, harness, monkeypatch, step, expected):
    monkeypatch.setattr(rig.main, "LEARNING_MODE", "manual")
    want = expected(harness)
    thread = make_thread(4101)
    harness.fake.script(step())
    content = "이 분석 절차를 스킬로 저장해"

    await rig.main.handle_health_query(make_message(content, channel=MagicMock(), new_thread=thread), content)

    assert texts(thread) == [want.user_message]
    assert RAW_PROVIDER_MARKER not in want.user_message
    assert len(harness.fake.prompts) == 1 and harness.fake.unscripted_turns == 0  # 실패 턴 = 스킬 제안·추출 없음


@pytest.mark.asyncio
@BOT_ERROR_PARAMS
async def test_error_cron_job_posts_message_to_created_thread(rig, harness, step, expected):
    want = expected(harness)
    now = datetime.datetime.now().astimezone()
    job = harness.env.cron_store.create("수면 요약", "0 9 * * *", now, deliver_channel_id=NOTIFY_CHANNEL_ID)
    harness.fake.script(step())

    await rig.main._run_cron_job(job, now)

    (thread,) = rig.notify.threads
    assert texts(thread) == [want.user_message]
    assert harness.env.cron_store.get(job["id"])["last_run_iso"] == now.isoformat()


@pytest.mark.asyncio
@BOT_ERROR_PARAMS
async def test_error_auto_analysis_posts_message_to_created_thread(rig, harness, step, expected):
    want = expected(harness)
    harness.fake.script(step())

    await rig.main._run_auto_analysis([{"date": "2026-09-15", "weight_kg": 79.9}])

    (thread,) = rig.notify.threads
    assert texts(thread) == [want.user_message]


@pytest.mark.asyncio
@BOT_ERROR_PARAMS
async def test_error_weekly_report_returns_message(rig, harness, step, expected):
    want = expected(harness)
    harness.fake.script(step())

    report = await rig.main.generate_weekly_report()

    assert report.endswith(f"## 🤖 AI 인사이트\n{want.user_message}")
    assert RAW_PROVIDER_MARKER not in report


@pytest.mark.asyncio
async def test_error_compression_in_followup_keeps_original_history(rig, harness):
    thread = make_thread(4102)
    thread.history_messages = [history_message(f"메시지 {i}", bool(i % 2)) for i in range(25)] + [
        history_message("q", False)
    ]
    harness.fake.script(error("usage_limit"), end(), text("답"))

    await rig.main.handle_health_query(make_message("q", channel=thread), "q")

    assert texts(thread) == ["답"]
    answer_prompt = harness.fake.prompts[-1]
    assert "메시지 0" in answer_prompt and "메시지 24" in answer_prompt
    assert "[이전 대화 요약]" not in answer_prompt
