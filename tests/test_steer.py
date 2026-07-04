"""Phase D 조종성 테스트 — steer(interrupt-then-restart) + 스레드별 persistent 클라이언트.

동시성 harness는 ClaudeSDKClient를 fake로 대체한다(실 API 호출 없음). interrupt()는 호출을
기록하고 진행 중인 receive_response를 조기 종료시켜, 이전 턴의 남은 블록이 발화되지 않고 새
프롬프트로 재시작(재개 아님)되는지 검증한다.

수용 기준(계획 D1, 폴백=수용 바닥):
  (a) 스레드별 클라이언트 생성/재사용/정리 (누수 없음).
  (b) interrupt-then-restart: interrupt() 정확히 1회 + 1번째 프롬프트 남은 on_text 미발화 +
      2번째 프롬프트 on_text 수신.
  (c) 세션 만료(is_expired) 시 클라이언트 disconnect + 제거.
"""
import asyncio
import os
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from claude_agent_sdk.types import AssistantMessage, TextBlock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# bot.main 임포트를 위한 최소 환경(모듈 레벨 부작용 방어). .env가 있으면 그대로 로드된다.
os.environ.setdefault("DISCORD_BOT_TOKEN", "fake-token-test")
os.environ.setdefault("ALLOWED_USERS", "123456")

from core.llm import ClaudeSDKAdapter, _CLAUDE_FALLBACK_MESSAGE


# ─────────────────────────────────────────────────────────────────────
# Fake ClaudeSDKClient — 동시성/interrupt 관측용
# ─────────────────────────────────────────────────────────────────────


class FakeClient:
    """ClaudeSDKClient 대역. connect/query/interrupt/disconnect 호출을 기록한다.

    receive_response는 self.script[turn]의 AssistantMessage들을 순차 yield하되, interrupt
    발생 시 남은 블록을 더 이상 yield하지 않는다(재시작 시맨틱). gate가 설정되면 turn 0의 첫
    블록 뒤에서 멈춰 테스트가 그 사이에 interrupt를 주입할 수 있게 한다.
    """

    def __init__(self, options=None, transport=None):
        self.options = options
        self.connect_count = 0
        self.disconnect_count = 0
        self.interrupt_count = 0
        self.queries = []
        self.script = None          # list[list[AssistantMessage]] | None (None=제네릭 응답)
        self.gate = None            # asyncio.Event | None (turn 0 첫 블록 뒤 대기)
        self._turn_index = -1
        self._interrupted = False

    async def connect(self, prompt=None):
        self.connect_count += 1

    async def query(self, prompt, session_id="default"):
        self.queries.append(prompt)
        self._turn_index += 1
        self._interrupted = False

    async def interrupt(self):
        self.interrupt_count += 1
        self._interrupted = True
        if self.gate is not None:
            self.gate.set()

    async def receive_response(self):
        turn = self._turn_index
        if self.script is not None and turn < len(self.script):
            msgs = self.script[turn]
        else:
            msgs = [AssistantMessage(content=[TextBlock(text=f"resp-{turn}")], model="m")]
        for i, msg in enumerate(msgs):
            if self._interrupted:
                return
            yield msg
            if self.gate is not None and turn == 0 and i == 0:
                await self.gate.wait()  # 첫 블록 뒤 정지 → 테스트가 interrupt로 깨움

    async def disconnect(self):
        self.disconnect_count += 1


def _text_msg(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="m")


# ─────────────────────────────────────────────────────────────────────
# (a) 스레드별 클라이언트 라이프사이클 — 생성/재사용
# ─────────────────────────────────────────────────────────────────────


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_created_on_first_turn_reused_on_followup(self, monkeypatch):
        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        T = 4242

        r1 = await adapter.ask_with_context("SYS", "q1", {}, thread_id=T)
        r2 = await adapter.ask_with_context("SYS", "q2", {}, thread_id=T)

        assert list(adapter._clients.keys()) == [T]
        client = adapter._clients[T]
        assert isinstance(client, FakeClient)
        assert client.connect_count == 1      # 최초 1회만 connect
        assert len(client.queries) == 2       # 후속 턴은 같은 클라이언트 재사용
        assert r1 == "resp-0"
        assert r2 == "resp-1"
        assert adapter.has_session(T) is True

    @pytest.mark.asyncio
    async def test_one_shot_path_untouched_without_thread_id(self, monkeypatch):
        """thread_id 미전달(무인/유틸) → persistent 클라이언트 미생성, one-shot query() 유지."""
        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")

        async def fake_query(**kwargs):
            yield _text_msg("one-shot")

        with patch("core.llm.query", side_effect=fake_query):
            result = await adapter.ask_with_context("SYS", "q", {})

        assert result == "one-shot"
        assert adapter._clients == {}          # persistent 클라이언트 미생성

    @pytest.mark.asyncio
    async def test_separate_threads_get_separate_clients(self, monkeypatch):
        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")

        await adapter.ask_with_context("SYS", "q", {}, thread_id=1)
        await adapter.ask_with_context("SYS", "q", {}, thread_id=2)

        assert set(adapter._clients.keys()) == {1, 2}
        assert adapter._clients[1] is not adapter._clients[2]


class TestPersistentSafetyGate:
    """persistent 클라이언트 옵션에 PreToolUse 안전 게이트 훅이 그대로 실려야 한다(불변식)."""

    @pytest.mark.asyncio
    async def test_persistent_options_carry_pretooluse_gate(self, monkeypatch):
        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        T = 77

        # 무인 승인 없음(approve_skill_writes=False) → skill-write 하드 차단 훅이 옵션에 배선돼야.
        await adapter.ask_with_context(
            "SYS", "q", {}, thread_id=T, approve_skill_writes=False
        )

        opts = adapter._clients[T].options  # ClaudeSDKClient 생성자에 넘어간 옵션
        matchers = opts.hooks["PreToolUse"]
        assert matchers[0].matcher == "Write|Edit|MultiEdit|NotebookEdit"
        assert matchers[1].matcher  # schedule/memory mutation matcher 배선

        guard = matchers[0].hooks[0]
        out = await guard(
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/skills/x/SKILL.md"}},
            None,
            {},
        )
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ─────────────────────────────────────────────────────────────────────
# (c) 세션 정리 — end_session disconnect + 제거, 멱등, 에러 폐기
# ─────────────────────────────────────────────────────────────────────


class TestSessionCleanup:
    @pytest.mark.asyncio
    async def test_end_session_disconnects_and_removes(self, monkeypatch):
        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        T = 7
        await adapter.ask_with_context("SYS", "q", {}, thread_id=T)
        client = adapter._clients[T]
        assert adapter.has_session(T)

        await adapter.end_session(T)

        assert client.disconnect_count == 1
        assert not adapter.has_session(T)

    @pytest.mark.asyncio
    async def test_end_session_idempotent(self, monkeypatch):
        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        T = 8
        await adapter.ask_with_context("SYS", "q", {}, thread_id=T)
        client = adapter._clients[T]
        await adapter.end_session(T)
        await adapter.end_session(T)  # 두 번째는 no-op (예외 없음)
        assert client.disconnect_count == 1

    @pytest.mark.asyncio
    async def test_interrupt_without_client_is_noop(self, monkeypatch):
        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        await adapter.interrupt_session(999)  # 클라이언트 없음 → 조용히 통과

    @pytest.mark.asyncio
    async def test_generation_error_drops_client_and_falls_back(self, monkeypatch):
        """생성 예외 시 깨진 클라이언트를 폐기하고 한국어 폴백을 전달한다."""

        class BoomClient(FakeClient):
            async def query(self, prompt, session_id="default"):
                raise RuntimeError("boom")

        monkeypatch.setattr("core.llm.ClaudeSDKClient", BoomClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        T = 11
        texts = []

        async def on_text(t):
            texts.append(t)

        result = await adapter.ask_with_context("SYS", "q", {}, on_text=on_text, thread_id=T)

        assert result == _CLAUDE_FALLBACK_MESSAGE
        assert texts == [_CLAUDE_FALLBACK_MESSAGE]
        assert not adapter.has_session(T)  # 깨진 클라이언트 폐기 → 다음 턴 재생성


# ─────────────────────────────────────────────────────────────────────
# (b) interrupt-then-restart — bot._steer_and_run 동시성 조율
# ─────────────────────────────────────────────────────────────────────


class TestInterruptThenRestart:
    @pytest.mark.asyncio
    async def test_new_message_interrupts_and_restarts(self, monkeypatch):
        import bot.main as main

        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        monkeypatch.setattr(main, "llm", adapter)
        main._inflight_turns.clear()

        T = 12345
        gate = asyncio.Event()
        fc = FakeClient()
        fc.gate = gate
        fc.script = [
            [_text_msg("turn1-block0"), _text_msg("turn1-block1")],  # turn 0: 두 블록
            [_text_msg("turn2-response")],                            # turn 1: 재시작 응답
        ]
        adapter._clients[T] = fc  # 사전 주입 → 재사용(누수 없이 같은 클라이언트)

        collect1, collect2 = [], []

        async def on1(t):
            collect1.append(t)

        async def on2(t):
            collect2.append(t)

        def gen1():
            return adapter.ask_with_context(
                "SYS", "q1", {}, on_text=on1, thread_id=T, approve_skill_writes=True
            )

        def gen2():
            return adapter.ask_with_context(
                "SYS", "q2", {}, on_text=on2, thread_id=T, approve_skill_writes=True
            )

        # 턴 1 시작 → 첫 블록 발화 후 gate에서 정지(진행 중 상태).
        task1 = asyncio.create_task(main._steer_and_run(T, gen1))
        for _ in range(200):
            if collect1:
                break
            await asyncio.sleep(0.005)
        assert collect1 == ["turn1-block0"]  # 첫 블록만 발화, 아직 진행 중

        # 턴 2 도착 → interrupt-then-restart.
        result2 = await main._steer_and_run(T, gen2)
        r1 = await task1

        assert fc.interrupt_count == 1              # interrupt 정확히 1회
        assert collect1 == ["turn1-block0"]         # 1번째 프롬프트 남은 블록(block1) 미발화
        assert r1 == "turn1-block0"                 # 턴1 반환은 interrupt 시점까지의 부분 텍스트
        assert collect2 == ["turn2-response"]       # 2번째 프롬프트 on_text 수신
        assert result2 == "turn2-response"
        assert len(fc.queries) == 2                 # 같은 클라이언트 재사용(재접속 아님)
        assert fc.connect_count == 0                # 사전 주입 → 재접속 없음
        assert main._inflight_turns.get(T) is None  # 진행 태스크 정리됨

    @pytest.mark.asyncio
    async def test_no_interrupt_when_previous_turn_done(self, monkeypatch):
        """이전 턴이 이미 끝났으면 interrupt 없이 순차 실행(정상 후속 질문)."""
        import bot.main as main

        monkeypatch.setattr("core.llm.ClaudeSDKClient", FakeClient)
        adapter = ClaudeSDKAdapter(cwd="/proj")
        monkeypatch.setattr(main, "llm", adapter)
        main._inflight_turns.clear()

        T = 5555

        def gen(tag):
            return lambda: adapter.ask_with_context("SYS", tag, {}, thread_id=T)

        r1 = await main._steer_and_run(T, gen("q1"))
        r2 = await main._steer_and_run(T, gen("q2"))

        client = adapter._clients[T]
        assert client.interrupt_count == 0   # 진행 중 턴 없음 → interrupt 미발생
        assert len(client.queries) == 2
        assert r1 == "resp-0"
        assert r2 == "resp-1"
        assert main._inflight_turns.get(T) is None


# ─────────────────────────────────────────────────────────────────────
# (c-wiring) handle_health_query가 is_expired 시 end_session을 호출
# ─────────────────────────────────────────────────────────────────────


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_mock_thread():
    thread = MagicMock(spec=discord.Thread)
    thread.id = 987654

    async def fake_history(limit=None, oldest_first=True):
        for m in []:
            yield m

    thread.history = fake_history
    thread.send = AsyncMock()
    thread.typing = MagicMock(return_value=_FakeTyping())
    return thread


def _make_mock_message(content, thread):
    msg = MagicMock(spec=discord.Message)
    msg.content = content
    msg.id = 111
    msg.created_at = None
    msg.author = MagicMock()
    msg.author.bot = False
    msg.channel = thread
    return msg


class TestExpiredThreadCleanup:
    @pytest.mark.asyncio
    async def test_expired_thread_calls_end_session(self, monkeypatch):
        import bot.main as main

        main._inflight_turns.clear()
        thread = _make_mock_thread()
        message = _make_mock_message("후속 질문", thread=thread)

        mock_llm = MagicMock()
        mock_llm.ask_with_context = AsyncMock(return_value="응답")
        mock_llm.end_session = AsyncMock()
        mock_llm.interrupt_session = AsyncMock()

        with patch("bot.main.llm", mock_llm), \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_body, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor"), \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_body.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_sess.is_expired.return_value = True

            await main.handle_health_query(message, "후속 질문")

        # 만료된 스레드의 stateful 클라이언트가 정리되어야 한다.
        mock_llm.end_session.assert_awaited_once_with(thread.id)
