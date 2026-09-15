"""AC-4 (어댑터 표면) — provider 오류 신호 → 타입드 사용자 메시지. 스트리밍 턴은 on_text 1회 + 반환,
유틸 ask는 LLMError raise(on_text 미발화), 압축·추출·통합은 memory.md 바이트 불변·원본 이력 반환.
원시 provider 문자열 미포함, 다른 어댑터로의 대체(팩토리 호출) 0회.

초기자별(인터랙티브·cron·자동분석·주간 리포트) 게시 경로는 test_contract_bot_paths.py `-k error`.
"""
import pytest

from core.context_compressor import ContextCompressor
from core.llm_errors import LLMError, is_llm_error_reply
from core.memory import ENTRY_DELIMITER, MAX_MEMORY_CHARS
from tests.contract.harness import SYS, Recorder, turn_lines
from tests.contract.scenario import RAW_PROVIDER_MARKER, end, error, text

RESETS_AT = 1760000000

# (id, 스크립트 스텝, 기대 LLMError 생성자)
ERRORS = [
    ("usage_limit_with_reset", lambda: error("usage_limit", resets_at=RESETS_AT),
     lambda h: LLMError.usage_limit(h.backend_id, RESETS_AT)),
    ("usage_limit", lambda: error("usage_limit"), lambda h: LLMError.usage_limit(h.backend_id)),
    ("auth_expired", lambda: error("auth_expired"), lambda h: LLMError.auth_expired(h.backend_id, h.auth_fix)),
    ("generic", lambda: error("generic"), lambda h: LLMError.generic()),
]
ERROR_PARAMS = pytest.mark.parametrize("step, expected", [(s, e) for _, s, e in ERRORS], ids=[i for i, _, _ in ERRORS])


@pytest.fixture
def factory_spy(monkeypatch):
    """자동 대체 금지 — 오류 처리 중 어댑터 팩토리가 호출되면 기록된다."""
    import bot.main as main
    import core.llm as llm_module

    calls = []

    def spy(name):
        def record(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"adapter factory called during error handling: {name}")
        return record

    for module, attr in (
        (llm_module, "create_llm_adapter"),
        (llm_module, "create_llm_adapter_from_config"),
        (main, "create_llm_adapter_from_config"),
        (main, "build_llm_and_tool_server"),
    ):
        monkeypatch.setattr(module, attr, spy(f"{module.__name__}.{attr}"))
    return calls


@pytest.mark.asyncio
@ERROR_PARAMS
@pytest.mark.parametrize("thread_id", [None, 8101], ids=["one_shot", "thread"])
async def test_streaming_turn_delivers_typed_message_once(harness, capsys, factory_spy, step, expected, thread_id):
    want = expected(harness)
    harness.fake.script(step())
    rec = Recorder()
    capsys.readouterr()

    result = await harness.adapter.ask_with_context(
        SYS, "q", {}, on_text=rec.on_text, thread_id=thread_id, approve_skill_writes=True,
    )

    assert result == want.user_message
    assert rec.texts == [want.user_message]
    assert is_llm_error_reply(result)
    assert RAW_PROVIDER_MARKER not in result
    (turn,) = turn_lines(capsys.readouterr().out)
    assert turn["outcome"] == want.kind.value
    assert factory_spy == []


@pytest.mark.asyncio
async def test_partial_text_then_error_appends_message_once(harness):
    want = LLMError.usage_limit(harness.backend_id)
    harness.fake.script(text("부분 응답"), error("usage_limit"))
    rec = Recorder()

    result = await harness.adapter.ask_with_context(SYS, "q", {}, on_text=rec.on_text)

    assert rec.texts == ["부분 응답", want.user_message]
    assert result == want.user_message


@pytest.mark.asyncio
@ERROR_PARAMS
async def test_ask_raises_typed_error_without_on_text(harness, capsys, factory_spy, step, expected):
    want = expected(harness)
    harness.fake.script(step())
    rec = Recorder()
    capsys.readouterr()

    with pytest.raises(LLMError) as exc:
        await harness.adapter.ask("메모리 추출기", "대화", on_text=rec.on_text)

    assert exc.value.kind is want.kind
    assert exc.value.user_message == want.user_message
    assert RAW_PROVIDER_MARKER not in exc.value.user_message
    assert rec.texts == []
    (turn,) = turn_lines(capsys.readouterr().out)
    assert turn["outcome"] == want.kind.value
    assert factory_spy == []


@pytest.mark.asyncio
async def test_compression_returns_original_history_on_error(harness):
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"메시지 {i}"} for i in range(25)]
    harness.fake.script(error("usage_limit"))

    result = await ContextCompressor().compress(history, harness.adapter)

    assert result == history
    assert harness.fake.pending_turns == 0


def _fill_memory(mgr):
    mgr.write_memory(f"기억 A{ENTRY_DELIMITER}기억 B{ENTRY_DELIMITER}" + "M" * (MAX_MEMORY_CHARS - 20))
    return mgr._read_raw("memory").encode(), mgr._read_raw("user").encode()


@pytest.mark.asyncio
@ERROR_PARAMS
async def test_extraction_error_keeps_memory_bytes(harness, step, expected):
    mgr = harness.env.memory_mgr
    before = _fill_memory(mgr)
    harness.fake.script(step())

    proposal = await mgr.extract_and_save(harness.adapter, [{"role": "user", "content": "매일 5km 뛰어"}])

    assert proposal is None
    assert (mgr._read_raw("memory").encode(), mgr._read_raw("user").encode()) == before


@pytest.mark.asyncio
async def test_consolidation_error_keeps_memory_bytes(harness):
    mgr = harness.env.memory_mgr
    before_memory, _ = _fill_memory(mgr)
    harness.fake.script(text("MEMORY: 매일 5km 러닝"), end(), error("generic"))

    await mgr.extract_and_save(harness.adapter, [{"role": "user", "content": "매일 5km 뛰어"}])

    assert harness.fake.pending_turns == 0  # 추출 1회 + 통합 1회(실패)
    after = mgr._read_raw("memory").encode()
    assert after == before_memory
    assert LLMError.generic().user_message.encode() not in after

