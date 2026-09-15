"""AC-17 비관적 fake (codex·grok 전용) — 런타임이 게이트 신호를 보내지 않는 최악의 경우.

- Codex: PreToolUse 훅 미발화 / Grok: session/request_permission 미요청 (`gate_signals=False`).
- mutation MCP는 ro 토큰의 서버측 CallerCapability가 `via=capability` deny(핸들러 미호출).
- Grok 빌트인은 실행 전 강제 수단이 없어 사후 봉쇄: `session/cancel` + `via=tripwire ... executed=likely` + GENERIC 반환.
- Codex ro 프로세스는 `sandbox_mode="read-only"`로 기동된다.
런타임이 실제로 훅/요청을 발생시키는지는 증명하지 않는다(아침 live 확인).
Claude는 인프로세스 훅이 bypass에서도 발화하므로(검증된 표면) 이 모듈의 파라미터가 없다.
"""
import pytest
import pytest_asyncio

from core.llm_errors import GENERIC_MESSAGE
from core.safety_gate import CanonicalToolCall, decide
from tests.contract.harness import SYS, Recorder, gate_lines
from tests.contract.scenario import gate_probe, text

pytestmark = pytest.mark.backends("codex", "grok")

MUTATIONS = [
    ("mcp__schedule__schedule_create", {"prompt": "p", "schedule": "30m", "deliver_channel_id": None, "max_turns": None}),
    ("mcp__schedule__schedule_pause", {"id": "missing-job"}),
    ("mcp__schedule__schedule_resume", {"id": "missing-job"}),
    ("mcp__schedule__schedule_remove", {"id": "missing-job"}),
    ("mcp__memory__add_memory", {"target": "memory", "content": "주입"}),
    ("mcp__memory__replace_memory", {"target": "memory", "index": 0, "content": "주입"}),
    ("mcp__memory__remove_memory", {"target": "memory", "index": 0}),
]


@pytest_asyncio.fixture
async def pessimistic(harness_factory):
    h = harness_factory(gate_signals=False)
    await h.start()
    try:
        yield h
    finally:
        await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("name, args", MUTATIONS, ids=[m[0] for m in MUTATIONS])
async def test_mutation_mcp_denied_by_capability_without_gate_signal(pessimistic, capsys, name, args):
    before = pessimistic.env.state_snapshot()
    capsys.readouterr()
    pessimistic.fake.script(gate_probe(name, args), text("끝"))

    await pessimistic.adapter.ask_with_context(SYS, "cron", {})

    _, reason = decide(CanonicalToolCall(name), privileged=False, runtime_guard=True)
    lines = [(l["cap"], l["decision"], l["via"], l["reason"]) for l in gate_lines(capsys.readouterr().out, tool=name)]
    assert lines == [("ro", "deny", "capability", reason)]
    assert '"denied_by": "capability"' in pessimistic.fake.executions[0].result_text
    assert pessimistic.env.state_snapshot() == before


@pytest.mark.asyncio
@pytest.mark.backends("grok")
async def test_builtin_without_permission_request_trips_cancel(pessimistic, capsys):
    rec = Recorder()
    capsys.readouterr()
    pessimistic.fake.script(gate_probe("Bash", {"command": "touch data/x"}), text("never"))

    result = await pessimistic.adapter.ask_with_context(SYS, "cron", {}, on_text=rec.on_text)

    _, reason = decide(CanonicalToolCall("Bash"), privileged=False, runtime_guard=True)
    lines = [(l["cap"], l["decision"], l["via"], l["reason"], l["executed"]) for l in gate_lines(capsys.readouterr().out, tool="Bash")]
    assert lines == [("ro", "deny", "tripwire", reason, " executed=likely")]
    assert pessimistic.fake.cancels == 1
    assert result == GENERIC_MESSAGE and "never" not in rec.texts


@pytest.mark.asyncio
@pytest.mark.backends("codex")
async def test_readonly_process_runs_read_only_sandbox(pessimistic):
    assert 'sandbox_mode="read-only"' in pessimistic.fake.process_argv("ro")
    assert 'sandbox_mode="danger-full-access"' in pessimistic.fake.process_argv("priv")
