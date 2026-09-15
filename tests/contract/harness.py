"""계약 스위트 하니스 — 백엔드 파라미터(BackendHarness) + 공유 tmp 환경(ContractEnv) + 관측 헬퍼.

새 백엔드를 계약에 붙이려면(P5 codex, P6 grok) `tests/contract/fakes/<backend>_fake.py`에
`make_harness(name, env, **options) -> BackendHarness` 하나와 아래 FakeRuntime 표면을 구현하면 된다.
테스트 본문은 백엔드를 모른다: 같은 시나리오(scenario.py)를 스크립트하고 같은 관측값을 단언한다.
"""
import asyncio
import contextlib
import io
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from core.body_metrics import BodyMetricsManager
from core.body_metrics_tools import create_body_metrics_mcp_server
from core.garmin_tools import create_garmin_mcp_server
from core.memory import MemoryManager
from core.memory_tools import create_memory_mcp_server
from core.schedule_tools import create_schedule_mcp_server
from core.scheduler import CronStore
from core.session_index import SessionIndex
from core.session_search_tools import create_session_search_mcp_server
from core.skill_registry import SkillRegistry, create_skills_mcp_server

SYS = "계약 시스템 프롬프트"
NOTIFY_CHANNEL_ID = "999"

# ── 스킬 fixture (registry 모드) ─────────────────────────────────────

SLEEP_SKILL_BODY = (
    "# 수면 분석\n"
    "1. mcp__garmin__get_sleep 도구로 7일 수면을 조회한다.\n"
    "2. 과거 기록은 mcp__session_search__search로 찾는다.\n"
)
BODY_SKILL_BODY = "# 체성분 분석\n참조: references/cutoffs.md\n"
CUTOFFS_TEXT = "체지방률 기준표\n"
EXPECTED_CATALOG = (
    "[전문 분석 스킬]\n"
    "- body-composition: 체성분 분석\n"
    "- sleep-analysis: 수면 분석\n"
    "필요한 스킬은 load_skill 도구로 본문을 불러와 그 절차를 따르세요. 참조 파일은 read_skill_file로 읽으세요."
)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class StubGarmin:
    """GarminConnectClient 대역 — 결정적 데이터 + 호출 기록(핸들러 호출 여부 관측)."""

    def __init__(self):
        self.calls: list[str] = []

    def _record(self, name, data):
        self.calls.append(name)
        return data

    def get_sleep(self, start, end):
        return self._record("get_sleep", [{"date": "2026-09-14", "total_hours": 7.2, "score": 81}])

    def get_daily_summary(self, start, end):
        return self._record("get_daily_summary", [{"date": "2026-09-14", "resting_hr": 52}])

    def get_hrv(self, start, end):
        return self._record("get_hrv", [{"date": "2026-09-14", "weekly_avg": 61}])

    def get_activities(self, start, end):
        return self._record("get_activities", [{"activity_id": "a1", "sport": "running", "distance_km": 5.0}])

    def get_stress(self, start, end):
        return self._record("get_stress", [{"date": "2026-09-14", "avg_stress": 28}])

    def get_activity_detail(self, activity_id):
        return self._record("get_activity_detail", {"activity_id": activity_id})

    def get_activity_splits(self, activity_id):
        return self._record("get_activity_splits", {"activity_id": activity_id, "splits": []})

    def get_activity_hr_zones(self, activity_id):
        return self._record("get_activity_hr_zones", {"activity_id": activity_id, "zones": []})

    def get_last_activity(self, count=1):
        return self._record("get_last_activity", [{"activity_id": "a1"}][:count])


@dataclass
class ContractEnv:
    """테스트별 tmp 공유 상태 — 도구 핸들러가 실제로 읽고 쓰는 대상(메모리·크론·체성분·세션 인덱스·스킬)."""

    root: str
    prompts_dir: str
    skills_dir: str
    memory_mgr: MemoryManager
    cron_store: CronStore
    body_metrics_mgr: BodyMetricsManager
    session_index: SessionIndex
    garmin: StubGarmin

    def specs(self, registry: bool, backend_id: str) -> list:
        """봇과 같은 순서의 ServerSpec 목록 (bot/main.py: garmin·body_metrics·memory·session_search·schedule [+skills])."""
        specs = [
            create_garmin_mcp_server(self.garmin),
            create_body_metrics_mcp_server(self.body_metrics_mgr),
            create_memory_mcp_server(self.memory_mgr),
            create_session_search_mcp_server(self.session_index),
            create_schedule_mcp_server(self.cron_store, default_channel_id=NOTIFY_CHANNEL_ID),
        ]
        if registry:
            specs.append(create_skills_mcp_server(SkillRegistry(self.skills_dir, backend=backend_id)))
        return specs

    def state_snapshot(self) -> tuple:
        """mutation 도구가 바꿀 수 있는 공유 상태 전체 (핸들러 미호출 단언용)."""
        memory = self.memory_mgr._read_raw("memory").encode()
        user = self.memory_mgr._read_raw("user").encode()
        return json.dumps(self.cron_store.list(), sort_keys=True), memory, user


def build_env(tmp_path) -> ContractEnv:
    root = str(tmp_path / "project")
    prompts_dir = os.path.join(root, "prompts")
    skills_dir = os.path.join(root, ".claude", "skills")
    os.makedirs(prompts_dir, exist_ok=True)
    _write(
        os.path.join(skills_dir, "sleep-analysis", "SKILL.md"),
        "---\nname: sleep-analysis\ndescription: 수면 분석\n---\n" + SLEEP_SKILL_BODY,
    )
    _write(
        os.path.join(skills_dir, "body-composition", "SKILL.md"),
        "---\nname: body-composition\ndescription: 체성분 분석\n---\n" + BODY_SKILL_BODY,
    )
    _write(os.path.join(skills_dir, "body-composition", "references", "cutoffs.md"), CUTOFFS_TEXT)
    return ContractEnv(
        root=root,
        prompts_dir=prompts_dir,
        skills_dir=skills_dir,
        memory_mgr=MemoryManager(prompts_dir),
        cron_store=CronStore(os.path.join(root, "data", "cron_jobs.json")),
        body_metrics_mgr=BodyMetricsManager(os.path.join(root, "data", "inbody.csv")),
        session_index=SessionIndex(os.path.join(root, "data", "session_index.db")),
        garmin=StubGarmin(),
    )


# ── 런타임 fake 표면 ─────────────────────────────────────────────────


@dataclass
class Execution:
    """런타임이 시작한 도구 호출 1건의 결과.

    executed = 도구가 실제로 실행됐는가(MCP면 서버 핸들러 경로 도달). blocked_by = hook|approval|runtime_guard|
    no_such_tool|… (fake 진단용, 계약 단언에는 쓰지 않는다).
    """

    name: str
    input: dict
    executed: bool
    blocked_by: str | None = None
    result_text: str | None = None
    is_error: bool = False


@dataclass
class ToolResult:
    text: str
    is_error: bool


class FakeRuntime(Protocol):
    """P5/P6 fake가 구현할 표면 (claude_fake.ClaudeFake 참고).

    스크립트·장애 주입: script(*steps), fail_starts(다음 N회 세션 기동 실패), ignore_interrupt(interrupt 무시).
    관측(턴 순서대로 누적): prompts(런타임이 받은 사용자 메시지), system_prompts(턴에 적용된 시스템 지시),
    allowed_tools(런타임에 넘긴 allowed_tools — 미적용 백엔드는 None), executions(Execution), session_starts
    (one-shot 호출·스레드 세션 기동 수), session_closes(스레드 세션 종료 수), interrupts, unscripted_turns, pending_turns.
    도구 transport: list_exposed_tools() — 최근 턴에 런타임이 받은 도구 서버의 {server: {tool: (description, inputSchema)}},
    call_tool_endpoint(canonical, args) — 게이트 신호 없이 그 transport로 직접 호출(서버측 capability 관측).
    """

    fail_starts: int
    ignore_interrupt: bool
    prompts: list
    system_prompts: list
    allowed_tools: list
    executions: list
    session_starts: int
    session_closes: int
    interrupts: int
    unscripted_turns: int

    def script(self, *steps) -> None: ...

    @property
    def pending_turns(self) -> int: ...

    async def list_exposed_tools(self) -> dict: ...

    async def call_tool_endpoint(self, canonical: str, args: dict) -> ToolResult: ...


@dataclass
class BackendHarness:
    """계약 파라미터 1개 = 배선된 실제 어댑터 + 런타임 fake + 기대 표기값.

    - name: claude_native | claude_registry | codex | grok (pytest id)
    - backend_id: 로그·사용자 메시지 표기(claude | codex | grok), skills_mode: native | registry
    - config/specs: 어댑터를 만든 LLMConfig·ServerSpec 목록 (봇 배선과 동일)
    - gate_via: 이 백엔드 게이트 wiring의 [gate] via 값 (hook | approval), auth_fix: AUTH_EXPIRED 해결 명령
    - check_native_skills_suppressed: registry 모드에서 런타임 네이티브 스킬 억제 설정 단언(백엔드별 수단)
    - tool_server: codex/grok의 SharedToolServer (claude는 None) — start()가 봇 setup_hook 순서로 기동
    - normalize_tool: 이 백엔드 정규화가 만드는 게이트 도구명(§3.5 — Codex apply_patch·Grok edit 계열은 전부 Write).
      Claude는 항등.
    """

    name: str
    backend_id: str
    skills_mode: str
    config: object
    specs: list
    adapter: object
    fake: FakeRuntime
    env: ContractEnv
    gate_via: str
    auth_fix: str
    check_native_skills_suppressed: Callable[[], None]
    tool_server: object = None
    normalize_tool: Callable[[str], str] = lambda name: name
    startup_lines: list = field(default_factory=list)

    @property
    def registry(self) -> bool:
        return self.skills_mode == "registry"

    async def start(self) -> None:
        """봇 setup_hook과 같은 순서(tool_server → llm.start). 기동 로그는 startup_lines로 분리 수집."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            if self.tool_server is not None:
                await self.tool_server.start()
            await self.adapter.start()
        self.startup_lines = buffer.getvalue().splitlines()

    async def close(self) -> None:
        await self.adapter.close_all()
        if self.tool_server is not None:
            await self.tool_server.stop()


# ── 관측 헬퍼 ────────────────────────────────────────────────────────


class Recorder:
    """on_text / on_tool / counter 수집기."""

    def __init__(self):
        self.texts: list[str] = []
        self.tools: list[str] = []
        self.counter = [0]

    async def on_text(self, text: str) -> None:
        self.texts.append(text)

    async def on_tool(self, name: str) -> None:
        self.tools.append(name)


async def eventually(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


_GATE_RE = re.compile(
    r"^\[gate\] backend=(?P<backend>\S+) cap=(?P<cap>\S+) tool=(?P<tool>\S+) decision=(?P<decision>\S+) "
    r"via=(?P<via>\S+) reason=(?P<reason>.*?)(?P<executed> executed=likely)?$"
)
_TURN_RE = re.compile(
    r"^\[llm\] turn backend=(?P<backend>\S+) thread=(?P<thread>\S+) cap=(?P<cap>\S+) tools=(?P<tools>\d+) "
    r"skills_loaded=(?P<skills_loaded>\d+) outcome=(?P<outcome>\S+)$"
)


def gate_lines(out: str, tool: str | None = None) -> list[dict]:
    """[gate] 로그 → 필드 dict 목록 (tool 지정 시 해당 도구만)."""
    lines = []
    for line in out.splitlines():
        match = _GATE_RE.match(line)
        if match and (tool is None or match["tool"] == tool):
            lines.append(match.groupdict())
    return lines


def turn_lines(out: str) -> list[dict]:
    return [m.groupdict() for m in map(_TURN_RE.match, out.splitlines()) if m]
