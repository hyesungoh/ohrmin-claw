# Garmin 데이터 Tool 하이브리드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude 에이전트가 Garmin 데이터를 직접 조회할 수 있는 SDK MCP tool을 추가하고, 기본 컨텍스트(preprocessor 요약)와 결합하여 "필요할 때 상세 데이터를 가져오는" 하이브리드 아키텍처 구현

**Architecture:** 기존 preprocessor 요약은 항상 context에 포함하여 토큰 절약. `@tool` + `create_sdk_mcp_server()`로 Garmin 상세 데이터 tool을 봇 프로세스 내 인프로세스로 등록. Claude가 상세 분석이 필요할 때만 tool을 호출. 빌트인 도구(파일시스템, 웹 등)는 유지하여 범용 에이전트 역할 보존.

**Tech Stack:** python-garminconnect, claude_agent_sdk (`@tool`, `create_sdk_mcp_server`), pytest

---

## 파일 구조

| 파일 | 역할 | 변경 유형 |
|------|------|-----------|
| `core/garmin_tools.py` | `@tool` 정의 + MCP 서버 팩토리 | **신규** |
| `tests/test_garmin_tools.py` | garmin_tools 테스트 | **신규** |
| `core/llm.py` | `ClaudeSDKAdapter`에 `mcp_servers` 전달 | **수정** |
| `tests/test_llm.py` | mcp_servers 전달 테스트 | **수정** |
| `bot/main.py` | MCP 서버 생성 + adapter에 전달 | **수정** |
| `prompts/system.md` | 에이전트 역할·도구 안내 강화 | **수정** |
| `CLAUDE.md` | 아키텍처 문서 업데이트 | **수정** |

---

### Task 1: Garmin Tool 정의 (`core/garmin_tools.py`)

**Files:**
- Create: `core/garmin_tools.py`
- Create: `tests/test_garmin_tools.py`

- [ ] **Step 1: 테스트 작성 — 기본 tool 5개 (요약 데이터)**

```python
# tests/test_garmin_tools.py
"""Garmin MCP tool 테스트."""
import datetime
import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from core.garmin_tools import create_garmin_mcp_server


@pytest.fixture
def mock_garmin_client():
    client = MagicMock()
    client.get_sleep.return_value = [
        {"day": "2026-04-20", "total_sleep": "08:00:00", "score": 82},
    ]
    client.get_daily_summary.return_value = [
        {"day": "2026-04-20", "rhr": 58, "steps": 9200},
    ]
    client.get_hrv.return_value = [
        {"day": "2026-04-20", "weekly_avg": 45.0, "status": "BALANCED"},
    ]
    client.get_activities.return_value = [
        {"activity_id": "123", "name": "러닝", "sport": "running",
         "distance": 6.1, "calories": 450, "elapsed_time": "00:45:00",
         "avg_hr": 154, "max_hr": 172, "start_time": "2026-04-20 07:00:00"},
    ]
    client.get_stress.return_value = [
        {"timestamp": "2026-04-20 00:00:00", "stress": 30},
    ]
    return client


@pytest.fixture
def garmin_server(mock_garmin_client):
    return create_garmin_mcp_server(mock_garmin_client)


class TestGarminMcpServer:
    def test_server_created(self, garmin_server):
        """MCP 서버가 생성되는지 확인."""
        assert garmin_server is not None
        assert garmin_server.name == "garmin"

    def test_server_has_tools(self, garmin_server):
        """필수 tool이 모두 등록되었는지 확인."""
        tool_names = [t.name for t in garmin_server.tools]
        assert "get_sleep" in tool_names
        assert "get_daily_summary" in tool_names
        assert "get_hrv" in tool_names
        assert "get_activities" in tool_names
        assert "get_stress" in tool_names


@pytest.mark.asyncio
class TestGetSleepTool:
    async def test_returns_json(self, mock_garmin_client, garmin_server):
        sleep_tool = next(t for t in garmin_server.tools if t.name == "get_sleep")
        result = await sleep_tool.handler({"start": "2026-04-20", "end": "2026-04-20"})
        data = json.loads(result["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["score"] == 82

    async def test_default_date_range(self, mock_garmin_client, garmin_server):
        """start/end 생략 시 최근 7일 기본값."""
        sleep_tool = next(t for t in garmin_server.tools if t.name == "get_sleep")
        await sleep_tool.handler({})
        call_args = mock_garmin_client.get_sleep.call_args
        start, end = call_args[0]
        assert (end - start).days == 7

    async def test_max_date_range_90_days(self, mock_garmin_client, garmin_server):
        """90일 초과 범위 요청 시 90일로 제한."""
        sleep_tool = next(t for t in garmin_server.tools if t.name == "get_sleep")
        await sleep_tool.handler({"start": "2025-01-01", "end": "2026-04-20"})
        call_args = mock_garmin_client.get_sleep.call_args
        start, end = call_args[0]
        assert (end - start).days <= 90
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_garmin_tools.py -x --tb=short`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.garmin_tools'`

- [ ] **Step 3: 기본 tool 5개 구현**

```python
# core/garmin_tools.py
"""Garmin 데이터 MCP tool 정의 — Claude Agent SDK 인프로세스 서버."""
import datetime
import json
from typing import Annotated

from claude_agent_sdk import tool, create_sdk_mcp_server


MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 7


def _parse_dates(args: dict) -> tuple[datetime.date, datetime.date]:
    """start/end 문자열을 date로 변환. 기본값: 최근 7일, 최대 90일."""
    today = datetime.date.today()
    end = datetime.date.fromisoformat(args["end"]) if args.get("end") else today
    start = datetime.date.fromisoformat(args["start"]) if args.get("start") else end - datetime.timedelta(days=DEFAULT_RANGE_DAYS)
    if (end - start).days > MAX_RANGE_DAYS:
        start = end - datetime.timedelta(days=MAX_RANGE_DAYS)
    return start, end


def _json_response(data) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}


DATE_SCHEMA = {
    "start": Annotated[str, "시작 날짜 (YYYY-MM-DD). 생략 시 7일 전"],
    "end": Annotated[str, "종료 날짜 (YYYY-MM-DD). 생략 시 오늘"],
}


def create_garmin_mcp_server(garmin_client):
    """GarminConnectClient를 감싸는 인프로세스 MCP 서버 생성."""

    @tool("get_sleep", "수면 데이터 조회 (일별 총수면, 깊은수면, REM, 점수)", DATE_SCHEMA)
    async def get_sleep(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_sleep(start, end))

    @tool("get_daily_summary", "일별 건강 요약 (안정시 심박수, 걸음수, 칼로리, 스트레스)", DATE_SCHEMA)
    async def get_daily_summary(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_daily_summary(start, end))

    @tool("get_hrv", "심박변이도(HRV) 데이터 조회 (주간평균, 상태)", DATE_SCHEMA)
    async def get_hrv(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_hrv(start, end))

    @tool("get_activities", "운동 활동 목록 조회 (종목, 거리, 시간, 심박수, 칼로리)", DATE_SCHEMA)
    async def get_activities(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_activities(start, end))

    @tool("get_stress", "일별 스트레스 수준 조회", DATE_SCHEMA)
    async def get_stress(args):
        start, end = _parse_dates(args)
        return _json_response(garmin_client.get_stress(start, end))

    return create_sdk_mcp_server(
        name="garmin",
        tools=[get_sleep, get_daily_summary, get_hrv, get_activities, get_stress],
    )
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_garmin_tools.py -v`
Expected: PASS (5+ tests)

- [ ] **Step 5: 커밋**

```bash
git add core/garmin_tools.py tests/test_garmin_tools.py
git commit -m "feat: add Garmin MCP tools (sleep, daily, hrv, activities, stress)"
```

---

### Task 2: 상세 활동 분석 tool 추가

**Files:**
- Modify: `core/garmin_data.py` — 상세 조회 메서드 추가
- Modify: `tests/test_garmin_data.py` — 상세 조회 테스트
- Modify: `core/garmin_tools.py` — 상세 tool 등록
- Modify: `tests/test_garmin_tools.py` — 상세 tool 테스트

- [ ] **Step 1: GarminConnectClient 상세 메서드 테스트 작성**

```python
# tests/test_garmin_data.py에 추가

class TestGetActivityDetails:
    def test_returns_splits(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_splits.return_value = {
            "activityId": 123,
            "lapDTOs": [
                {"distance": 1000.0, "duration": 494.0, "averageHR": 145, "maxHR": 164,
                 "averageSpeed": 2.02, "elevationGain": 8.0, "calories": 93},
            ],
        }
        data = client.get_activity_splits("123")
        assert len(data) == 1
        assert data[0]["distance_km"] == 1.0
        assert data[0]["avg_hr"] == 145

    def test_returns_hr_zones(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_hr_in_timezones.return_value = [
            {"zoneNumber": 1, "secsInZone": 619.0, "zoneLowBoundary": 131},
            {"zoneNumber": 2, "secsInZone": 2040.0, "zoneLowBoundary": 149},
        ]
        data = client.get_activity_hr_zones("123")
        assert len(data) == 2
        assert data[0]["zone"] == 1
        assert data[0]["minutes"] == 10.3

    def test_returns_activity_detail(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "123", "activityName": "러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-22 22:47:02",
            "distance": 6137.0, "duration": 2930.0, "elapsedDuration": 2930.0,
            "averageHR": 154, "maxHR": 172, "calories": 565,
            "averageSpeed": 2.09, "maxSpeed": 3.81,
            "vO2MaxValue": 37.0,
            "averageRunningCadenceInStepsPerMinute": 160.0,
            "elevationGain": 22.0,
        }
        api.get_activity_splits.return_value = {"lapDTOs": [
            {"distance": 1000.0, "duration": 494.0, "averageHR": 145, "maxHR": 164,
             "averageSpeed": 2.02, "elevationGain": 8.0, "calories": 93},
        ]}
        api.get_activity_hr_in_timezones.return_value = [
            {"zoneNumber": 1, "secsInZone": 619.0, "zoneLowBoundary": 131},
        ]
        data = client.get_activity_detail("123")
        assert data["vo2_max"] == 37.0
        assert data["cadence"] == 160.0
        assert len(data["splits"]) == 1
        assert len(data["hr_zones"]) == 1
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_garmin_data.py::TestGetActivityDetails -x --tb=short`
Expected: FAIL — `AttributeError: 'GarminConnectClient' has no attribute 'get_activity_splits'`

- [ ] **Step 3: GarminConnectClient에 상세 메서드 구현**

```python
# core/garmin_data.py에 추가

    def get_activity_splits(self, activity_id: str) -> list[dict]:
        raw = self.api.get_activity_splits(activity_id)
        laps = raw.get("lapDTOs", [])
        return [
            {
                "lap": i + 1,
                "distance_km": round((lap.get("distance", 0) or 0) / 1000, 1),
                "duration": _seconds_to_hms(lap.get("duration")),
                "avg_hr": lap.get("averageHR"),
                "max_hr": lap.get("maxHR"),
                "avg_speed": lap.get("averageSpeed"),
                "elevation_gain": lap.get("elevationGain"),
                "calories": lap.get("calories"),
            }
            for i, lap in enumerate(laps)
        ]

    def get_activity_hr_zones(self, activity_id: str) -> list[dict]:
        raw = self.api.get_activity_hr_in_timezones(activity_id)
        if not raw:
            return []
        return [
            {
                "zone": z.get("zoneNumber"),
                "minutes": round((z.get("secsInZone", 0) or 0) / 60, 1),
                "zone_floor_bpm": z.get("zoneLowBoundary"),
            }
            for z in raw
        ]

    def get_activity_detail(self, activity_id: str) -> dict:
        """활동 요약 + 스플릿 + HR 존을 통합 조회."""
        act = self.api.get_activity(activity_id)
        if not act:
            return {}
        activity_type = act.get("activityType", {})
        return {
            "activity_id": activity_id,
            "name": act.get("activityName", ""),
            "sport": activity_type.get("typeKey", "unknown"),
            "start_time": act.get("startTimeLocal", ""),
            "distance_km": round((act.get("distance", 0) or 0) / 1000, 1),
            "duration": _seconds_to_hms(act.get("duration")),
            "avg_hr": act.get("averageHR"),
            "max_hr": act.get("maxHR"),
            "calories": act.get("calories", 0),
            "vo2_max": act.get("vO2MaxValue"),
            "cadence": act.get("averageRunningCadenceInStepsPerMinute"),
            "elevation_gain": act.get("elevationGain"),
            "splits": self.get_activity_splits(activity_id),
            "hr_zones": self.get_activity_hr_zones(activity_id),
        }
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_garmin_data.py::TestGetActivityDetails -v`
Expected: PASS

- [ ] **Step 5: garmin_tools.py에 상세 tool 추가**

`core/garmin_tools.py`의 `create_garmin_mcp_server` 함수 내부에 추가:

```python
    ACTIVITY_ID_SCHEMA = {
        "activity_id": Annotated[str, "활동 ID (get_activities로 먼저 조회)"],
    }

    @tool("get_activity_detail", "특정 활동의 상세 분석 (스플릿, HR 존, VO2 Max, 케이던스)", ACTIVITY_ID_SCHEMA)
    async def get_activity_detail(args):
        return _json_response(garmin_client.get_activity_detail(args["activity_id"]))

    @tool("get_activity_splits", "특정 활동의 구간별(랩) 데이터 (페이스, 심박수, 고도)", ACTIVITY_ID_SCHEMA)
    async def get_activity_splits(args):
        return _json_response(garmin_client.get_activity_splits(args["activity_id"]))

    @tool("get_activity_hr_zones", "특정 활동의 심박수 존별 시간 분포", ACTIVITY_ID_SCHEMA)
    async def get_activity_hr_zones(args):
        return _json_response(garmin_client.get_activity_hr_zones(args["activity_id"]))
```

`create_sdk_mcp_server` 호출에 새 tool 추가:

```python
    return create_sdk_mcp_server(
        name="garmin",
        tools=[get_sleep, get_daily_summary, get_hrv, get_activities, get_stress,
               get_activity_detail, get_activity_splits, get_activity_hr_zones],
    )
```

- [ ] **Step 6: 상세 tool 테스트 추가 + 실행**

```python
# tests/test_garmin_tools.py에 추가

class TestDetailTools:
    def test_server_has_detail_tools(self, garmin_server):
        tool_names = [t.name for t in garmin_server.tools]
        assert "get_activity_detail" in tool_names
        assert "get_activity_splits" in tool_names
        assert "get_activity_hr_zones" in tool_names
```

Run: `python3 -m pytest tests/test_garmin_tools.py tests/test_garmin_data.py -v`
Expected: ALL PASS

- [ ] **Step 7: 커밋**

```bash
git add core/garmin_data.py core/garmin_tools.py tests/test_garmin_data.py tests/test_garmin_tools.py
git commit -m "feat: add detailed activity tools (splits, HR zones, VO2 Max)"
```

---

### Task 3: LLM 어댑터에 MCP 서버 전달

**Files:**
- Modify: `core/llm.py:28-86`
- Modify: `tests/test_llm.py`

- [ ] **Step 1: mcp_servers 전달 테스트 작성**

```python
# tests/test_llm.py에 추가

class TestMcpServersPassthrough:
    """ClaudeSDKAdapter가 mcp_servers를 ClaudeAgentOptions에 전달하는지 확인."""

    @pytest.mark.asyncio
    async def test_mcp_servers_passed_to_options(self):
        adapter = ClaudeSDKAdapter()
        mock_server = {"name": "garmin", "tools": []}
        adapter.mcp_servers = {"garmin": mock_server}

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter.ask("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert options.mcp_servers == {"garmin": mock_server}

    @pytest.mark.asyncio
    async def test_no_mcp_servers_by_default(self):
        adapter = ClaudeSDKAdapter()

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter.ask("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert not hasattr(options, 'mcp_servers') or not options.mcp_servers
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_llm.py::TestMcpServersPassthrough -x --tb=short`
Expected: FAIL

- [ ] **Step 3: ClaudeSDKAdapter 수정**

`core/llm.py`의 `ClaudeSDKAdapter.__init__`에 `mcp_servers` 필드 추가, `_call_claude`에서 options에 전달:

```python
class ClaudeSDKAdapter(LLMAdapter):
    """Claude Agent SDK — 구독 모델 기반."""

    def __init__(self, model: str = "claude-sonnet-4-20250514", mcp_servers: dict | None = None):
        self.model = model
        self.mcp_servers = mcp_servers or {}

    async def _call_claude(
        self,
        system_prompt: str,
        user_message: str,
        on_text: Callable | None = None,
    ) -> str:
        result_texts = []
        options_kwargs = {
            "system_prompt": system_prompt,
            "model": self.model,
            "max_turns": 15,
        }
        if self.mcp_servers:
            options_kwargs["mcp_servers"] = self.mcp_servers
        async for msg in query(
            prompt=user_message,
            options=ClaudeAgentOptions(**options_kwargs),
        ):
            # ... 기존 로직 유지
```

`create_llm_adapter`에도 `mcp_servers` 파라미터 추가:

```python
def create_llm_adapter(adapter_type: str = "claude", model: str | None = None, mcp_servers: dict | None = None) -> LLMAdapter:
    if adapter_type == "claude":
        kwargs = {}
        if model:
            kwargs["model"] = model
        if mcp_servers:
            kwargs["mcp_servers"] = mcp_servers
        return ClaudeSDKAdapter(**kwargs)
    raise ValueError(f"Unknown adapter type: {adapter_type}")
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_llm.py -v`
Expected: ALL PASS (기존 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
git add core/llm.py tests/test_llm.py
git commit -m "feat: pass mcp_servers through ClaudeSDKAdapter to ClaudeAgentOptions"
```

---

### Task 4: bot/main.py 통합

**Files:**
- Modify: `bot/main.py:12-49`

- [ ] **Step 1: main.py에 MCP 서버 생성 및 전달**

```python
# bot/main.py 상단 import에 추가
from core.garmin_tools import create_garmin_mcp_server

# garmin 클라이언트 생성 직후, MCP 서버 생성
garmin_mcp = None
if garmin:
    garmin_mcp = create_garmin_mcp_server(garmin)
    print("✅ Garmin MCP 도구 등록 완료")

# LLM 어댑터 생성 시 mcp_servers 전달
mcp_servers = {}
if garmin_mcp:
    mcp_servers["garmin"] = garmin_mcp

llm = create_llm_adapter(LLM_ADAPTER_TYPE, model=LLM_MODEL, mcp_servers=mcp_servers)
```

- [ ] **Step 2: `_collect_health_context` 유지 확인**

기존 `_collect_health_context()`는 그대로 유지. 이것이 하이브리드의 "A안" 부분 — 기본 컨텍스트로 preprocessor 요약을 항상 전달.

변경 불필요. 확인만.

- [ ] **Step 3: 구문 검증**

Run: `python3 -c "import bot.main"` (import만으로 구문 에러 체크. 실제 봇 실행은 아님)
Expected: Garmin 로그인 시도 또는 환경변수 미설정 메시지

- [ ] **Step 4: 커밋**

```bash
git add bot/main.py
git commit -m "feat: integrate Garmin MCP server into bot"
```

---

### ~~Task 5: 삭제됨 — Task 13에서 system.md를 최종 작성~~

---

### Task 6: CLAUDE.md 문서 업데이트

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Architecture 섹션에 하이브리드 구조 반영**

CLAUDE.md의 Architecture 섹션을 다음과 같이 업데이트:

```markdown
## Architecture

\```
core/           추상화 레이어 + 데이터 접근
  llm.py          LLMAdapter ABC → ClaudeSDKAdapter (claude-agent-sdk, 구독 모델, MCP 서버 지원)
  channel.py      MessagingChannel ABC → DiscordChannel (discord.py)
  garmin_data.py   GarminConnectClient (python-garminconnect API 기반)
  garmin_tools.py  Garmin MCP tool 정의 (@tool + create_sdk_mcp_server)
  inbody_data.py   InBody CSV CRUD (data/inbody.csv)
  inbody_parser.py 자연어 파싱 → 구조화 데이터 (정규식 기반)
  preprocessor.py  원시 데이터 → 통계 요약 (평균, 트렌드, 이상치)
  report.py        주간/월간 마크다운 리포트 생성

bot/main.py     Discord 봇 엔트리포인트 (스레드 기반 대화 세션)
prompts/        시스템 프롬프트 (system.md) + 개인 목표 (goals.md), 마크다운 분리
\```
```

Key Patterns에 추가:

```markdown
- **하이브리드 데이터 접근**: 기본 컨텍스트(preprocessor 요약 7일치)는 항상 포함. Claude가 상세 분석이 필요할 때만 Garmin MCP tool을 호출하여 추가 데이터 조회. 토큰 절약과 분석 깊이를 동시에 확보.
- **인프로세스 MCP 서버**: `claude_agent_sdk`의 `@tool` + `create_sdk_mcp_server()`로 봇 프로세스 내에서 Garmin 도구를 직접 제공. 별도 프로세스 불필요.
- **범용 에이전트**: `tools=[]`를 사용하지 않아 빌트인 도구(웹 검색 등)도 활용 가능. 건강 질의뿐 아니라 일반 질문에도 응답 가능.
```

Data Sources에 추가:

```markdown
- **Garmin Connect API**: python-garminconnect 패키지를 통해 직접 API 호출. 토큰은 `~/.garminconnect/`에 캐시.
  - 요약 데이터: sleep, daily_summary, hrv, activities, stress
  - 상세 데이터: activity_detail (splits, HR zones, VO2 Max, cadence)
```

Environment에 추가:

```markdown
- `garminconnect` — Garmin Connect API 클라이언트 (python-garminconnect)
- `claude_agent_sdk` — Claude Agent SDK (@tool, create_sdk_mcp_server)
```

- [ ] **Step 2: 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with hybrid architecture and MCP tools"
```

---

### Task 7: 전체 통합 테스트

**Files:**
- 없음 (기존 테스트 전체 실행)

- [ ] **Step 1: 전체 테스트 실행**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS (기존 73+ 신규 테스트 모두)

- [ ] **Step 2: 실제 봇 실행 확인 (수동)**

Run: `python3 bot/main.py`
Expected:
```
✅ Garmin Connect 로그인 성공
✅ Garmin MCP 도구 등록 완료
🚀 Health Manager 봇 시작...
✅ {봇이름} 로그인 완료!
```

Discord에서 테스트:
1. "오늘 컨디션 어때?" → 기본 컨텍스트만으로 응답 (tool 미호출)
2. "최근 러닝 상세하게 분석해봐" → `get_activities` → `get_activity_detail` tool 호출
3. "오늘 서울 날씨 어때?" → 빌트인 도구로 응답 (범용 에이전트)

- [ ] **Step 3: 최종 커밋**

```bash
git add -A
git commit -m "test: verify full integration of Garmin MCP tools"
```

---

### Task 8: `get_last_activity` tool + 종목 자동 감지 상세 조회 (P0)

**Files:**
- Modify: `core/garmin_data.py` — `get_last_activity`, `get_exercise_sets`, `get_activity_detail` 수정
- Modify: `tests/test_garmin_data.py` — 종목별 상세 조회 테스트
- Modify: `core/garmin_tools.py` — `get_last_activity` tool 등록
- Modify: `tests/test_garmin_tools.py` — `get_last_activity` tool 테스트

- [ ] **Step 1: 테스트 작성 — `get_last_activity` + 종목별 `get_activity_detail`**

```python
# tests/test_garmin_data.py에 추가

class TestGetLastActivity:
    def test_returns_single_activity(self, mock_garmin):
        client, api = mock_garmin
        api.get_last_activity.return_value = {
            "activityId": "999", "activityName": "오후 러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-24 18:00:00",
            "distance": 5000.0, "duration": 1800.0,
            "averageHR": 150, "maxHR": 170, "calories": 400,
        }
        data = client.get_last_activity()
        assert data["activity_id"] == "999"
        assert data["sport"] == "running"

    def test_returns_multiple_activities(self, mock_garmin):
        client, api = mock_garmin
        api.get_last_activity.return_value = {
            "activityId": "999", "activityName": "러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-24 18:00:00",
            "distance": 5000.0, "duration": 1800.0,
            "averageHR": 150, "maxHR": 170, "calories": 400,
        }
        api.get_activities.return_value = [
            {"activityId": "999", "activityName": "러닝",
             "activityType": {"typeKey": "running"},
             "startTimeLocal": "2026-04-24 18:00:00",
             "distance": 5000.0, "duration": 1800.0,
             "averageHR": 150, "maxHR": 170, "calories": 400},
            {"activityId": "998", "activityName": "웨이트",
             "activityType": {"typeKey": "strength_training"},
             "startTimeLocal": "2026-04-23 10:00:00",
             "distance": 0, "duration": 3600.0,
             "averageHR": 120, "maxHR": 155, "calories": 300},
        ]
        data = client.get_last_activity(count=2)
        assert len(data) == 2


class TestGetActivityDetailBySport:
    """종목별 자동 감지 상세 조회."""

    def test_running_includes_splits_cadence_vo2(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "123", "activityName": "러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-22 07:00:00",
            "distance": 6000.0, "duration": 2700.0,
            "averageHR": 154, "maxHR": 172, "calories": 500,
            "vO2MaxValue": 37.0,
            "averageRunningCadenceInStepsPerMinute": 160.0,
            "averageSpeed": 2.22,
        }
        api.get_activity_splits.return_value = {"lapDTOs": [
            {"distance": 1000.0, "duration": 450.0, "averageHR": 145,
             "maxHR": 164, "averageSpeed": 2.22, "elevationGain": 5.0, "calories": 90},
        ]}
        api.get_activity_hr_in_timezones.return_value = [
            {"zoneNumber": 1, "secsInZone": 600.0, "zoneLowBoundary": 131},
        ]
        data = client.get_activity_detail("123")
        assert data["vo2_max"] == 37.0
        assert data["cadence"] == 160.0
        assert "splits" in data
        assert "pace" in data["splits"][0] or "avg_speed" in data["splits"][0]

    def test_strength_includes_exercise_sets(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "456", "activityName": "웨이트 트레이닝",
            "activityType": {"typeKey": "strength_training"},
            "startTimeLocal": "2026-04-22 10:00:00",
            "distance": 0, "duration": 3600.0,
            "averageHR": 120, "maxHR": 155, "calories": 300,
        }
        api.get_activity_exercise_sets.return_value = {
            "exerciseSets": [
                {"setType": "ACTIVE", "exerciseName": "BENCH_PRESS",
                 "weight": 60.0, "repetitionCount": 10, "startTime": "2026-04-22T10:05:00"},
                {"setType": "ACTIVE", "exerciseName": "BENCH_PRESS",
                 "weight": 70.0, "repetitionCount": 8, "startTime": "2026-04-22T10:08:00"},
                {"setType": "REST"},
            ],
        }
        data = client.get_activity_detail("456")
        assert data["sport"] == "strength_training"
        assert "exercise_sets" in data
        assert len(data["exercise_sets"]) == 2  # REST 제외
        assert data["exercise_sets"][0]["weight"] == 60.0

    def test_swimming_includes_swolf(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "789", "activityName": "수영",
            "activityType": {"typeKey": "lap_swimming"},
            "startTimeLocal": "2026-04-22 08:00:00",
            "distance": 1500.0, "duration": 2400.0,
            "averageHR": 135, "maxHR": 160, "calories": 350,
            "averageSwolf": 42, "averageStrokes": 18,
        }
        api.get_activity_splits.return_value = {"lapDTOs": []}
        api.get_activity_hr_in_timezones.return_value = []
        data = client.get_activity_detail("789")
        assert data["swolf"] == 42
        assert data["avg_strokes"] == 18

    def test_hiking_includes_elevation(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity.return_value = {
            "activityId": "321", "activityName": "하이킹",
            "activityType": {"typeKey": "hiking"},
            "startTimeLocal": "2026-04-22 09:00:00",
            "distance": 8000.0, "duration": 7200.0,
            "averageHR": 125, "maxHR": 150, "calories": 600,
            "elevationGain": 450.0, "elevationLoss": 430.0,
        }
        api.get_activity_splits.return_value = {"lapDTOs": []}
        api.get_activity_hr_in_timezones.return_value = []
        data = client.get_activity_detail("321")
        assert data["elevation_gain"] == 450.0
        assert data["elevation_loss"] == 430.0


class TestGetExerciseSets:
    def test_returns_active_sets_only(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_exercise_sets.return_value = {
            "exerciseSets": [
                {"setType": "ACTIVE", "exerciseName": "SQUAT",
                 "weight": 80.0, "repetitionCount": 8},
                {"setType": "REST"},
                {"setType": "ACTIVE", "exerciseName": "SQUAT",
                 "weight": 90.0, "repetitionCount": 6},
            ],
        }
        data = client.get_exercise_sets("456")
        assert len(data) == 2
        assert data[0]["exercise"] == "SQUAT"
        assert data[1]["weight"] == 90.0

    def test_empty_sets(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_exercise_sets.return_value = {"exerciseSets": []}
        data = client.get_exercise_sets("456")
        assert data == []
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_garmin_data.py::TestGetLastActivity tests/test_garmin_data.py::TestGetActivityDetailBySport tests/test_garmin_data.py::TestGetExerciseSets -x --tb=short`
Expected: FAIL — `AttributeError: 'GarminConnectClient' has no attribute 'get_last_activity'`

- [ ] **Step 3: GarminConnectClient에 메서드 구현**

`core/garmin_data.py`에 추가:

```python
    # 종목 분류 상수
    RUNNING_SPORTS = {"running", "trail_running", "treadmill_running", "track_running"}
    STRENGTH_SPORTS = {"strength_training", "indoor_cardio"}
    SWIMMING_SPORTS = {"lap_swimming", "open_water_swimming"}
    HIKING_CYCLING_SPORTS = {"hiking", "cycling", "mountain_biking", "road_biking"}

    def get_last_activity(self, count: int = 1) -> dict | list[dict]:
        """최근 활동 조회. count=1이면 단일 dict, count>1이면 list."""
        if count == 1:
            raw = self.api.get_last_activity()
            return self._format_activity_summary(raw)
        # count > 1: get_activities로 최근 N개
        raw_list = self.api.get_activities(0, count)
        return [self._format_activity_summary(a) for a in raw_list[:count]]

    def _format_activity_summary(self, raw: dict) -> dict:
        """API 응답을 정규화된 활동 요약으로 변환."""
        activity_type = raw.get("activityType", {})
        return {
            "activity_id": str(raw.get("activityId", "")),
            "name": raw.get("activityName", ""),
            "sport": activity_type.get("typeKey", "unknown"),
            "start_time": raw.get("startTimeLocal", ""),
            "distance_km": round((raw.get("distance", 0) or 0) / 1000, 1),
            "duration": _seconds_to_hms(raw.get("duration")),
            "avg_hr": raw.get("averageHR"),
            "max_hr": raw.get("maxHR"),
            "calories": raw.get("calories", 0),
        }

    def get_exercise_sets(self, activity_id: str) -> list[dict]:
        """웨이트 트레이닝 세트 조회 (REST 세트 제외)."""
        raw = self.api.get_activity_exercise_sets(activity_id)
        sets = raw.get("exerciseSets", [])
        return [
            {
                "exercise": s.get("exerciseName", "UNKNOWN"),
                "weight": s.get("weight"),
                "reps": s.get("repetitionCount"),
                "start_time": s.get("startTime"),
            }
            for s in sets
            if s.get("setType") == "ACTIVE"
        ]

    def get_activity_detail(self, activity_id: str) -> dict:
        """종목 자동 감지 후 종목별 상세 데이터 통합 조회."""
        act = self.api.get_activity(activity_id)
        if not act:
            return {}

        activity_type = act.get("activityType", {})
        sport = activity_type.get("typeKey", "unknown")

        result = {
            "activity_id": activity_id,
            "name": act.get("activityName", ""),
            "sport": sport,
            "start_time": act.get("startTimeLocal", ""),
            "distance_km": round((act.get("distance", 0) or 0) / 1000, 1),
            "duration": _seconds_to_hms(act.get("duration")),
            "avg_hr": act.get("averageHR"),
            "max_hr": act.get("maxHR"),
            "calories": act.get("calories", 0),
        }

        # 종목별 추가 데이터
        if sport in self.RUNNING_SPORTS:
            result["vo2_max"] = act.get("vO2MaxValue")
            result["cadence"] = act.get("averageRunningCadenceInStepsPerMinute")
            result["avg_pace"] = _speed_to_pace(act.get("averageSpeed"))
            result["splits"] = self.get_activity_splits(activity_id)
            result["hr_zones"] = self.get_activity_hr_zones(activity_id)

        elif sport in self.STRENGTH_SPORTS:
            result["exercise_sets"] = self.get_exercise_sets(activity_id)

        elif sport in self.SWIMMING_SPORTS:
            result["swolf"] = act.get("averageSwolf")
            result["avg_strokes"] = act.get("averageStrokes")
            result["splits"] = self.get_activity_splits(activity_id)
            result["hr_zones"] = self.get_activity_hr_zones(activity_id)

        elif sport in self.HIKING_CYCLING_SPORTS:
            result["elevation_gain"] = act.get("elevationGain")
            result["elevation_loss"] = act.get("elevationLoss")
            result["avg_power"] = act.get("avgPower")
            result["splits"] = self.get_activity_splits(activity_id)
            result["hr_zones"] = self.get_activity_hr_zones(activity_id)

        else:
            # 기타 종목: splits 시도
            try:
                result["splits"] = self.get_activity_splits(activity_id)
                result["hr_zones"] = self.get_activity_hr_zones(activity_id)
            except Exception:
                pass

        return result
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_garmin_data.py::TestGetLastActivity tests/test_garmin_data.py::TestGetActivityDetailBySport tests/test_garmin_data.py::TestGetExerciseSets -v`
Expected: ALL PASS

- [ ] **Step 5: garmin_tools.py에 `get_last_activity` tool 등록**

`core/garmin_tools.py`의 `create_garmin_mcp_server` 함수 내부에 추가:

```python
    LAST_ACTIVITY_SCHEMA = {
        "count": Annotated[int, "조회할 활동 수 (기본 1, 최대 10)"],
    }

    @tool("get_last_activity", "최근 활동 조회 (가장 마지막 운동부터). count로 개수 지정", LAST_ACTIVITY_SCHEMA)
    async def get_last_activity(args):
        count = min(args.get("count", 1), 10)
        return _json_response(garmin_client.get_last_activity(count=count))
```

`create_sdk_mcp_server` tools 목록에 `get_last_activity` 추가.

- [ ] **Step 6: garmin_tools 테스트 추가**

```python
# tests/test_garmin_tools.py에 추가

class TestGetLastActivityTool:
    def test_tool_registered(self, garmin_server):
        tool_names = [t.name for t in garmin_server.tools]
        assert "get_last_activity" in tool_names

    @pytest.mark.asyncio
    async def test_default_count_1(self, mock_garmin_client, garmin_server):
        mock_garmin_client.get_last_activity.return_value = {
            "activity_id": "999", "name": "러닝", "sport": "running",
        }
        tool_fn = next(t for t in garmin_server.tools if t.name == "get_last_activity")
        result = await tool_fn.handler({})
        mock_garmin_client.get_last_activity.assert_called_with(count=1)

    @pytest.mark.asyncio
    async def test_count_capped_at_10(self, mock_garmin_client, garmin_server):
        mock_garmin_client.get_last_activity.return_value = []
        tool_fn = next(t for t in garmin_server.tools if t.name == "get_last_activity")
        await tool_fn.handler({"count": 50})
        mock_garmin_client.get_last_activity.assert_called_with(count=10)
```

Run: `python3 -m pytest tests/test_garmin_tools.py tests/test_garmin_data.py -v`
Expected: ALL PASS

- [ ] **Step 7: 커밋**

```bash
git add core/garmin_data.py core/garmin_tools.py tests/test_garmin_data.py tests/test_garmin_tools.py
git commit -m "feat: add get_last_activity tool + sport-aware activity detail"
```

---

### Task 9: `get_activities` 반환 필드 확장 + 페이스 계산 유틸리티 (P0)

**Files:**
- Modify: `core/garmin_data.py` — `get_activities` 필드 확장, `_speed_to_pace` 유틸리티, splits/hr_zones 보강
- Modify: `tests/test_garmin_data.py` — 확장 필드 + 페이스 테스트

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_garmin_data.py에 추가

class TestSpeedToPace:
    """_speed_to_pace 유틸리티 함수 테스트."""

    def test_normal_pace(self):
        from core.garmin_data import _speed_to_pace
        # 2.78 m/s ≈ 10 km/h ≈ 6:00 /km
        pace = _speed_to_pace(2.78)
        assert pace == "5:59"  # 반올림 허용

    def test_slow_pace(self):
        from core.garmin_data import _speed_to_pace
        # 1.67 m/s ≈ 6 km/h ≈ 10:00 /km
        pace = _speed_to_pace(1.67)
        assert pace == "9:59" or pace == "10:00"

    def test_zero_speed(self):
        from core.garmin_data import _speed_to_pace
        assert _speed_to_pace(0) is None

    def test_none_speed(self):
        from core.garmin_data import _speed_to_pace
        assert _speed_to_pace(None) is None


class TestGetActivitiesExtendedFields:
    """get_activities 확장 필드 테스트."""

    def test_includes_extended_fields(self, mock_garmin):
        client, api = mock_garmin
        api.get_activities_by_date.return_value = [{
            "activityId": "123", "activityName": "러닝",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-04-22 07:00:00",
            "distance": 6000.0, "duration": 2700.0,
            "averageHR": 154, "maxHR": 172, "calories": 500,
            "elevationGain": 35.0,
            "averageRunningCadenceInStepsPerMinute": 162.0,
            "avgPower": None,
            "aerobicTrainingEffect": 3.2,
            "anaerobicTrainingEffect": 1.5,
            "vO2MaxValue": 37.0,
        }]
        start = datetime.date(2026, 4, 22)
        end = datetime.date(2026, 4, 22)
        data = client.get_activities(start, end)
        act = data[0]
        assert act["elevation_gain"] == 35.0
        assert act["cadence"] == 162.0
        assert act["avg_power"] is None
        assert act["training_effect_aerobic"] == 3.2
        assert act["training_effect_anaerobic"] == 1.5
        assert act["vo2_max"] == 37.0

    def test_missing_extended_fields_are_none(self, mock_garmin):
        client, api = mock_garmin
        api.get_activities_by_date.return_value = [{
            "activityId": "456", "activityName": "걷기",
            "activityType": {"typeKey": "walking"},
            "startTimeLocal": "2026-04-22 12:00:00",
            "distance": 3000.0, "duration": 2400.0,
            "averageHR": 95, "maxHR": 110, "calories": 150,
        }]
        start = datetime.date(2026, 4, 22)
        end = datetime.date(2026, 4, 22)
        data = client.get_activities(start, end)
        act = data[0]
        assert act["elevation_gain"] is None
        assert act["cadence"] is None
        assert act["vo2_max"] is None


class TestActivitySplitsPace:
    """get_activity_splits의 pace_min_km 필드 테스트."""

    def test_splits_include_pace(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_splits.return_value = {"lapDTOs": [
            {"distance": 1000.0, "duration": 360.0, "averageHR": 145,
             "maxHR": 164, "averageSpeed": 2.78, "elevationGain": 5.0, "calories": 90},
        ]}
        data = client.get_activity_splits("123")
        assert "pace_min_km" in data[0]
        assert data[0]["pace_min_km"] is not None


class TestHrZonesPercent:
    """get_activity_hr_zones의 zone_pct 필드 테스트."""

    def test_hr_zones_include_pct(self, mock_garmin):
        client, api = mock_garmin
        api.get_activity_hr_in_timezones.return_value = [
            {"zoneNumber": 1, "secsInZone": 600.0, "zoneLowBoundary": 100},
            {"zoneNumber": 2, "secsInZone": 1200.0, "zoneLowBoundary": 130},
            {"zoneNumber": 3, "secsInZone": 600.0, "zoneLowBoundary": 150},
        ]
        data = client.get_activity_hr_zones("123")
        total_pct = sum(z["zone_pct"] for z in data)
        assert abs(total_pct - 100.0) < 0.1  # 합계 100%
        assert data[1]["zone_pct"] == 50.0  # 1200 / 2400 = 50%
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_garmin_data.py::TestSpeedToPace tests/test_garmin_data.py::TestGetActivitiesExtendedFields tests/test_garmin_data.py::TestActivitySplitsPace tests/test_garmin_data.py::TestHrZonesPercent -x --tb=short`
Expected: FAIL

- [ ] **Step 3: 구현**

`core/garmin_data.py`에 유틸리티 함수 추가:

```python
def _speed_to_pace(speed_mps: float | None) -> str | None:
    """m/s → "M:SS" /km 페이스 변환."""
    if not speed_mps:
        return None
    secs_per_km = 1000.0 / speed_mps
    minutes = int(secs_per_km // 60)
    seconds = int(secs_per_km % 60)
    return f"{minutes}:{seconds:02d}"
```

`get_activities` 반환 dict에 확장 필드 추가:

```python
    # 기존 필드에 이어서
    "elevation_gain": act.get("elevationGain"),
    "cadence": act.get("averageRunningCadenceInStepsPerMinute"),
    "avg_power": act.get("avgPower"),
    "training_effect_aerobic": act.get("aerobicTrainingEffect"),
    "training_effect_anaerobic": act.get("anaerobicTrainingEffect"),
    "vo2_max": act.get("vO2MaxValue"),
```

`get_activity_splits` 반환 dict에 `pace_min_km` 추가:

```python
    "pace_min_km": _speed_to_pace(lap.get("averageSpeed")),
```

`get_activity_hr_zones` 반환에 `zone_pct` 추가:

```python
    def get_activity_hr_zones(self, activity_id: str) -> list[dict]:
        raw = self.api.get_activity_hr_in_timezones(activity_id)
        if not raw:
            return []
        total_secs = sum(z.get("secsInZone", 0) or 0 for z in raw)
        return [
            {
                "zone": z.get("zoneNumber"),
                "minutes": round((z.get("secsInZone", 0) or 0) / 60, 1),
                "zone_floor_bpm": z.get("zoneLowBoundary"),
                "zone_pct": round(((z.get("secsInZone", 0) or 0) / total_secs) * 100, 1) if total_secs > 0 else 0,
            }
            for z in raw
        ]
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_garmin_data.py::TestSpeedToPace tests/test_garmin_data.py::TestGetActivitiesExtendedFields tests/test_garmin_data.py::TestActivitySplitsPace tests/test_garmin_data.py::TestHrZonesPercent -v`
Expected: ALL PASS

- [ ] **Step 5: 커밋**

```bash
git add core/garmin_data.py tests/test_garmin_data.py
git commit -m "feat: extend get_activities fields, add pace utility, hr_zone percentages"
```

---

### Task 10: `inbody` → `body_metrics` 리네임 + `source` 컬럼 (P1)

**Files:**
- Rename: `core/inbody_data.py` → `core/body_metrics.py`
- Rename: `core/inbody_parser.py` → `core/body_metrics_parser.py`
- Rename: `tests/test_inbody_data.py` → `tests/test_body_metrics.py`
- Rename: `tests/test_inbody_parser.py` → `tests/test_body_metrics_parser.py`
- Modify: `core/preprocessor.py` — import 경로 변경
- Modify: `core/report.py` — import 경로 변경
- Modify: `bot/main.py` — import 경로 변경
- Modify: 리네임된 파일 내 클래스/함수명 변경

- [ ] **Step 1: 테스트 작성 — 리네임 후 구조 + source 컬럼 + body_fat_pct optional**

```python
# tests/test_body_metrics.py (test_inbody_data.py를 복사 후 수정)
"""Body Metrics 데이터 관리 테스트."""
import csv
import os
import tempfile

import pytest

from core.body_metrics import BodyMetricsManager


@pytest.fixture
def csv_path():
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    yield path
    os.unlink(path)


class TestBodyMetricsManager:
    def test_add_entry_with_source(self, csv_path):
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-20", weight_kg=75.0, body_fat_pct=18.0,
                       muscle_mass_kg=33.0, bmi=24.5, source="manual")
        rows = mgr.read_all()
        assert rows[0]["source"] == "manual"

    def test_add_entry_default_source(self, csv_path):
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-20", weight_kg=75.0)
        rows = mgr.read_all()
        assert rows[0]["source"] == "manual"

    def test_add_entry_weight_only(self, csv_path):
        """body_fat_pct 없이 체중만 기록 가능."""
        mgr = BodyMetricsManager(csv_path)
        mgr.add_entry(date="2026-04-20", weight_kg=75.0)
        rows = mgr.read_all()
        assert rows[0]["weight_kg"] == 75.0
        assert rows[0]["body_fat_pct"] is None

    def test_backward_compat_old_csv(self, csv_path):
        """source 컬럼 없는 기존 CSV도 읽을 수 있어야 함."""
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "weight_kg", "body_fat_pct", "muscle_mass_kg", "bmi"])
            writer.writeheader()
            writer.writerow({"date": "2026-04-01", "weight_kg": "74.0",
                             "body_fat_pct": "19.0", "muscle_mass_kg": "32.5", "bmi": "24.0"})
        mgr = BodyMetricsManager(csv_path)
        rows = mgr.read_all()
        assert rows[0]["source"] == "unknown"  # 기존 데이터는 "unknown"


class TestBodyMetricsParser:
    def test_import_path(self):
        from core.body_metrics_parser import BodyMetricsParser
        assert BodyMetricsParser is not None
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_body_metrics.py -x --tb=short`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.body_metrics'`

- [ ] **Step 3: 파일 리네임 + 클래스 리네임**

```bash
# 파일 리네임
git mv core/inbody_data.py core/body_metrics.py
git mv core/inbody_parser.py core/body_metrics_parser.py
git mv tests/test_inbody_data.py tests/test_body_metrics.py
git mv tests/test_inbody_parser.py tests/test_body_metrics_parser.py
```

`core/body_metrics.py` 내부 수정:
- `class InBodyDataManager` → `class BodyMetricsManager`
- CSV fieldnames에 `"source"` 추가
- `add_entry()` 시그니처: `body_fat_pct=None` (기본값 None으로 변경)
- `add_entry()`에 `source="manual"` 파라미터 추가
- `get_all()`에서 `row.get("source", "unknown")` 처리

`core/body_metrics_parser.py` 내부 수정:
- `class InBodyParser` → `class BodyMetricsParser`

참조 업데이트 (import + 변수명 + dict 키 + UI 문자열 전부 변경):

```python
# core/preprocessor.py
# import 변경:
from core.body_metrics import BodyMetricsManager
# create_weekly_summary() 파라미터: inbody= → body_metrics=
# 반환 dict 키: "inbody" → "body_metrics"

# core/report.py
# import 변경:
from core.body_metrics import BodyMetricsManager
# s.get("inbody") → s.get("body_metrics")
# UI 문자열: "## 🏋️ 체성분 (InBody)" → "## 🏋️ 체성분"

# bot/main.py
# import 변경:
from core.body_metrics import BodyMetricsManager
from core.body_metrics_parser import BodyMetricsParser
# 변수명: inbody → body_metrics_mgr
# 상수: INBODY_CSV_PATH → BODY_METRICS_CSV_PATH
# _collect_health_context(): context["inbody"] → context["body_metrics"]
# generate_weekly_report(): inbody_data → body_metrics_data
```

메서드명은 기존 이름 유지 (`read_all`, `read_latest`, `add_entry`, `get_trend`).
`get_all` 등으로 리네임하지 않음 — 불필요한 변경 최소화.

기존 테스트 파일(`test_preprocessor.py`, `test_report.py`, `test_thread.py`)에서 `inbody` 참조도 `body_metrics`로 업데이트.

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_body_metrics.py tests/test_body_metrics_parser.py -v`
Expected: ALL PASS

- [ ] **Step 5: 전체 테스트로 참조 깨짐 없는지 확인**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS (기존 테스트 포함)

- [ ] **Step 6: 커밋**

```bash
git add -A
git commit -m "refactor: rename inbody → body_metrics, add source column, make body_fat_pct optional"
```

---

### Task 11: Body Metrics MCP tool 추가 + handle_inbody 제거 (P1)

**Files:**
- Create: `core/body_metrics_tools.py`
- Create: `tests/test_body_metrics_tools.py`
- Modify: `bot/main.py` — `handle_inbody` 제거, 모든 메시지를 Claude로 라우팅
- Modify: `core/llm.py` — body_metrics MCP 서버 전달

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_body_metrics_tools.py
"""Body Metrics MCP tool 테스트."""
import json
from unittest.mock import MagicMock

import pytest

from core.body_metrics_tools import create_body_metrics_mcp_server


@pytest.fixture
def mock_metrics_manager():
    mgr = MagicMock()
    mgr.get_all.return_value = [
        {"date": "2026-04-20", "weight_kg": 75.0, "body_fat_pct": 18.0,
         "muscle_mass_kg": 33.0, "bmi": 24.5, "source": "manual"},
        {"date": "2026-04-13", "weight_kg": 75.5, "body_fat_pct": 18.5,
         "muscle_mass_kg": 32.8, "bmi": 24.7, "source": "manual"},
    ]
    return mgr


@pytest.fixture
def metrics_server(mock_metrics_manager):
    return create_body_metrics_mcp_server(mock_metrics_manager)


class TestBodyMetricsMcpServer:
    def test_server_created(self, metrics_server):
        assert metrics_server is not None
        assert metrics_server.name == "body_metrics"

    def test_has_required_tools(self, metrics_server):
        tool_names = [t.name for t in metrics_server.tools]
        assert "add_body_measurement" in tool_names
        assert "get_body_metrics_trend" in tool_names
        assert "get_body_metrics_history" in tool_names


@pytest.mark.asyncio
class TestAddBodyMeasurementTool:
    async def test_add_weight_only(self, mock_metrics_manager, metrics_server):
        tool_fn = next(t for t in metrics_server.tools if t.name == "add_body_measurement")
        result = await tool_fn.handler({"weight_kg": 74.5})
        mock_metrics_manager.add_entry.assert_called_once()
        call_kwargs = mock_metrics_manager.add_entry.call_args[1]
        assert call_kwargs["weight_kg"] == 74.5

    async def test_requires_at_least_one_field(self, mock_metrics_manager, metrics_server):
        tool_fn = next(t for t in metrics_server.tools if t.name == "add_body_measurement")
        result = await tool_fn.handler({})
        data = json.loads(result["content"][0]["text"])
        assert "error" in data


@pytest.mark.asyncio
class TestGetBodyMetricsHistoryTool:
    async def test_returns_recent_entries(self, mock_metrics_manager, metrics_server):
        tool_fn = next(t for t in metrics_server.tools if t.name == "get_body_metrics_history")
        result = await tool_fn.handler({"count": 5})
        data = json.loads(result["content"][0]["text"])
        assert len(data) == 2


@pytest.mark.asyncio
class TestGetBodyMetricsTrendTool:
    async def test_returns_trend_for_field(self, mock_metrics_manager, metrics_server):
        tool_fn = next(t for t in metrics_server.tools if t.name == "get_body_metrics_trend")
        result = await tool_fn.handler({"field": "weight_kg", "days": 30})
        data = json.loads(result["content"][0]["text"])
        assert "values" in data or isinstance(data, list)
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_body_metrics_tools.py -x --tb=short`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.body_metrics_tools'`

- [ ] **Step 3: body_metrics_tools.py 구현**

```python
# core/body_metrics_tools.py
"""Body Metrics MCP tool 정의 — Claude Agent SDK 인프로세스 서버."""
import datetime
import json
from typing import Annotated

from claude_agent_sdk import tool, create_sdk_mcp_server


def _json_response(data) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}


def create_body_metrics_mcp_server(metrics_manager):
    """BodyMetricsManager를 감싸는 인프로세스 MCP 서버 생성."""

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
        all_rows = metrics_manager.get_all()
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

        all_rows = metrics_manager.get_all()
        filtered = [r for r in all_rows if r.get("date", "") >= cutoff]

        values = []
        for r in filtered:
            val = r.get(field)
            if val is not None:
                values.append({"date": r["date"], "value": val})

        return _json_response({"field": field, "days": days, "values": values})

    return create_sdk_mcp_server(
        name="body_metrics",
        tools=[add_body_measurement, get_body_metrics_history, get_body_metrics_trend],
    )
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_body_metrics_tools.py -v`
Expected: ALL PASS

- [ ] **Step 5: bot/main.py에서 handle_inbody 제거 + MCP 서버 연결**

`bot/main.py` 수정:

```python
# import 추가
from core.body_metrics_tools import create_body_metrics_mcp_server

# handle_inbody 핸들러 및 is_inbody_message() 분기 제거

# MCP 서버 생성
body_metrics_mcp = create_body_metrics_mcp_server(body_metrics_mgr)

# mcp_servers dict에 추가
mcp_servers["body_metrics"] = body_metrics_mcp
```

`on_message` 수정 — `is_inbody_message()` 분기 제거, 모든 메시지가 Claude로 전달되도록:

```python
# 변경 전:
# if is_inbody_message(content):
#     await handle_inbody(message, content)
#     return

# 변경 후: 해당 분기 전체 삭제. 모든 메시지가 아래 Claude 호출로 진행.
```

- [ ] **Step 6: 전체 테스트 실행**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: 커밋**

```bash
git add core/body_metrics_tools.py tests/test_body_metrics_tools.py bot/main.py
git commit -m "feat: add body_metrics MCP tools, remove handle_inbody in favor of Claude routing"
```

---

### Task 12: `.claude/skills/` 도입 + ClaudeAgentOptions 설정 (P1)

**Files:**
- Create: `.claude/skills/` 디렉토리
- Modify: `core/llm.py` — `cwd`, `setting_sources`, `allowed_tools` 추가
- Modify: `tests/test_llm.py` — 새 옵션 테스트
- Modify: `bot/main.py` — `cwd` 전달

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_llm.py에 추가

class TestClaudeAgentOptionsExtended:
    """ClaudeSDKAdapter의 확장 옵션 테스트."""

    @pytest.mark.asyncio
    async def test_cwd_passed_to_options(self):
        adapter = ClaudeSDKAdapter(cwd="/path/to/project")

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter.ask("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert options.cwd == "/path/to/project"

    @pytest.mark.asyncio
    async def test_setting_sources_includes_user_and_project(self):
        adapter = ClaudeSDKAdapter(cwd="/path/to/project")

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter.ask("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert "user" in options.setting_sources
            assert "project" in options.setting_sources

    @pytest.mark.asyncio
    async def test_allowed_tools_includes_skill(self):
        adapter = ClaudeSDKAdapter(cwd="/path/to/project")

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter.ask("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert "Skill" in options.allowed_tools

    @pytest.mark.asyncio
    async def test_no_cwd_by_default(self):
        adapter = ClaudeSDKAdapter()

        with patch("core.llm.query") as mock_query:
            mock_query.return_value = mock_async_gen([])
            await adapter.ask("system", "test")
            call_args = mock_query.call_args
            options = call_args.kwargs.get("options") or call_args[1].get("options")
            assert not hasattr(options, 'cwd') or options.cwd is None
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_llm.py::TestClaudeAgentOptionsExtended -x --tb=short`
Expected: FAIL

- [ ] **Step 3: 구현**

`core/llm.py`의 `ClaudeSDKAdapter` 수정:

```python
class ClaudeSDKAdapter(LLMAdapter):
    """Claude Agent SDK — 구독 모델 기반."""

    def __init__(self, model: str = "claude-sonnet-4-20250514",
                 mcp_servers: dict | None = None,
                 cwd: str | None = None):
        self.model = model
        self.mcp_servers = mcp_servers or {}
        self.cwd = cwd

    async def _call_claude(self, system_prompt, user_message, on_text=None):
        options_kwargs = {
            "system_prompt": system_prompt,
            "model": self.model,
            "max_turns": 15,
        }
        if self.mcp_servers:
            options_kwargs["mcp_servers"] = self.mcp_servers
        if self.cwd:
            options_kwargs["cwd"] = self.cwd
            options_kwargs["setting_sources"] = ["user", "project"]
            options_kwargs["allowed_tools"] = [
                "Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill",
            ]
        # ... 기존 로직 유지
```

`create_llm_adapter`에도 `cwd` 파라미터 추가:

```python
def create_llm_adapter(adapter_type="claude", model=None, mcp_servers=None, cwd=None):
    if adapter_type == "claude":
        kwargs = {}
        if model:
            kwargs["model"] = model
        if mcp_servers:
            kwargs["mcp_servers"] = mcp_servers
        if cwd:
            kwargs["cwd"] = cwd
        return ClaudeSDKAdapter(**kwargs)
    raise ValueError(f"Unknown adapter type: {adapter_type}")
```

`.claude/skills/` 디렉토리 생성:

```bash
mkdir -p .claude/skills
```

- [ ] **Step 4: bot/main.py에서 cwd 전달**

```python
# bot/main.py
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

llm = create_llm_adapter(
    LLM_ADAPTER_TYPE,
    model=LLM_MODEL,
    mcp_servers=mcp_servers,
    cwd=PROJECT_ROOT,
)
```

- [ ] **Step 5: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_llm.py -v`
Expected: ALL PASS

- [ ] **Step 6: 커밋**

```bash
git add core/llm.py tests/test_llm.py bot/main.py .claude/skills/
git commit -m "feat: add cwd/setting_sources/Skill to ClaudeSDKAdapter, create .claude/skills/"
```

---

### Task 13: system.md 최소화 + 스킬 파일 구조 정의 (P1)

**Files:**
- Modify: `prompts/system.md` — 핵심 페르소나 + 데이터 원칙만으로 축소
- Create: `.claude/skills/activity-evaluation.md` — 구조만 (내용은 별도 작성 예정)
- Create: `.claude/skills/sleep-analysis.md` — 구조만
- Create: `.claude/skills/body-composition.md` — 구조만
- Create: `.claude/skills/science-reference.md` — 구조만

- [ ] **Step 1: system.md 축소**

```markdown
# prompts/system.md

당신은 이중 역할을 가진 AI 비서입니다:
1. **건강 코치** — Garmin/체성분 데이터 기반 과학적 분석
2. **범용 비서** — 일반 질문, 웹 검색, 파일 작업 등

## 핵심 원칙
- 한국어로 응답
- 데이터에 근거한 분석, 추측 시 명시
- 수치는 표로 정리, 트렌드 변화 시 원인 가설 제시
- 응답은 Discord에서 읽기 편한 길이

## 데이터 활용
[데이터 컨텍스트]에는 최근 7일 요약이 포함됩니다.
상세 분석이 필요하면 Garmin/Body Metrics 도구를 사용하세요.
요약으로 충분하면 도구를 호출하지 마세요 (토큰 절약).

## 스킬 활용
.claude/skills/ 에 전문 분석 프레임워크가 있습니다.
운동 평가, 수면 분석, 체성분 분석 시 해당 스킬을 참고하세요.
```

- [ ] **Step 2: 스킬 파일 구조 생성**

각 파일은 최소한의 구조만 정의. 실제 내용은 `/skill-creator` 전문가가 별도 작성.

```markdown
# .claude/skills/activity-evaluation.md
---
name: activity-evaluation
description: 종목별 운동 활동 평가 프레임워크
trigger: 운동 분석, 러닝 평가, 웨이트 리뷰, 활동 피드백
---

# 운동 활동 평가 프레임워크

> TODO: /skill-creator 전문가가 작성 예정

## 역할
종목별(러닝, 웨이트, 수영, 하이킹, 사이클) 운동 데이터를 분석하고 개선점을 제안.

## 종목별 평가 기준
- 러닝: 페이스, 심박 존 분포, VO2 Max 트렌드, 케이던스
- 웨이트: 볼륨(세트x무게x렙), 근육군 밸런스, 점진적 과부하
- 수영: SWOLF, 스트로크 효율, 페이스
- 하이킹/사이클: 고도 대비 심박, 파워

## 사용할 도구
- get_activities, get_activity_detail, get_last_activity
- get_activity_splits, get_activity_hr_zones
```

```markdown
# .claude/skills/sleep-analysis.md
---
name: sleep-analysis
description: 수면 데이터 분석 프레임워크
trigger: 수면 분석, 수면 품질, 잠, 깊은 수면, REM
---

# 수면 분석 프레임워크

> TODO: /skill-creator 전문가가 작성 예정

## 역할
수면 데이터(총수면, 깊은수면, REM, 점수)를 분석하고 개선 방안 제시.

## 분석 관점
- 수면 시간 충분성 (7-9시간 권장)
- 수면 구조 (깊은수면 비율, REM 비율)
- HRV와 수면 품질 상관관계
- 주간 트렌드 변화

## 사용할 도구
- get_sleep, get_hrv, get_daily_summary
```

```markdown
# .claude/skills/body-composition.md
---
name: body-composition
description: 체성분 분석 프레임워크
trigger: 체성분, 체중, 체지방, 근육량, BMI, 인바디
---

# 체성분 분석 프레임워크

> TODO: /skill-creator 전문가가 작성 예정

## 역할
체성분 데이터(체중, 체지방률, 골격근량, BMI)를 분석하고 목표 대비 진행 상황 평가.

## 분석 관점
- 체중 변화 트렌드
- 체지방률 vs 근육량 비율 변화
- BMI 범주 (저체중/정상/과체중/비만)
- 목표 대비 진척도

## 사용할 도구
- get_body_metrics_history, get_body_metrics_trend, add_body_measurement
```

```markdown
# .claude/skills/science-reference.md
---
name: science-reference
description: 과학적 기준 레퍼런스 (ACSM, WHO, NSCA)
trigger: 기준, 권장, 가이드라인, ACSM, WHO, NSCA, 논문
---

# 과학적 기준 레퍼런스

> TODO: /skill-creator 전문가가 작성 예정

## 역할
건강/운동 분석 시 과학적 기준과 가이드라인 참조.

## 주요 출처
- ACSM (American College of Sports Medicine): 운동 처방 가이드라인
- WHO: 신체활동 권장량, BMI 분류
- NSCA (National Strength and Conditioning Association): 근력 트레이닝 기준
- AHA: 심박수 존 분류

## 포함 기준 (예시)
- 심박수 존: Zone 1-5 (최대심박수 %)
- VO2 Max: 연령/성별 기준 분류표
- 수면: 성인 권장 7-9시간 (NSF)
- 체지방률: 성별/연령별 기준 범위
- 점진적 과부하: 주당 볼륨 증가 권장 (5-10%)
```

- [ ] **Step 3: 커밋**

```bash
git add prompts/system.md .claude/skills/
git commit -m "refactor: minimize system.md, scaffold .claude/skills/ structure"
```

---

### Task 14: CLAUDE.md 문서 최종 업데이트 (P2)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Architecture 섹션 업데이트**

CLAUDE.md의 Architecture 섹션을 최종 구조에 맞게 업데이트:

```markdown
## Architecture

\```
core/           추상화 레이어 + 데이터 접근
  llm.py          LLMAdapter ABC → ClaudeSDKAdapter (claude-agent-sdk, 구독 모델, MCP 서버 + cwd/skills 지원)
  channel.py      MessagingChannel ABC → DiscordChannel (discord.py)
  garmin_data.py   GarminConnectClient (python-garminconnect API 기반, 종목별 상세 조회)
  garmin_tools.py  Garmin MCP tool 정의 (@tool + create_sdk_mcp_server)
  body_metrics.py  Body Metrics CSV CRUD (data/inbody.csv, source 컬럼)
  body_metrics_parser.py 자연어 파싱 → 구조화 데이터 (정규식 기반)
  body_metrics_tools.py  Body Metrics MCP tool 정의
  preprocessor.py  원시 데이터 → 통계 요약 (평균, 트렌드, 이상치)
  report.py        주간/월간 마크다운 리포트 생성

bot/main.py     Discord 봇 엔트리포인트 (스레드 기반 대화 세션)
prompts/        시스템 프롬프트 (system.md) + 개인 목표 (goals.md), 마크다운 분리
.claude/skills/ 전문 분석 스킬 파일 (운동평가, 수면분석, 체성분, 과학기준)
\```
```

- [ ] **Step 2: Key Patterns 업데이트**

기존 패턴 유지 + 추가:

```markdown
- **하이브리드 데이터 접근**: 기본 컨텍스트(preprocessor 요약 7일치)는 항상 포함. Claude가 상세 분석이 필요할 때만 Garmin/Body Metrics MCP tool을 호출하여 추가 데이터 조회. 토큰 절약과 분석 깊이를 동시에 확보.
- **인프로세스 MCP 서버**: `claude_agent_sdk`의 `@tool` + `create_sdk_mcp_server()`로 봇 프로세스 내에서 Garmin + Body Metrics 도구를 직접 제공. 별도 프로세스 불필요.
- **범용 에이전트**: `allowed_tools`에 빌트인 도구(Bash, Read, Glob 등) + Skill을 포함하여 건강 질의뿐 아니라 일반 질문에도 응답 가능.
- **.claude/skills 패턴**: 전문 분석 프레임워크를 `.claude/skills/`에 마크다운으로 분리. `setting_sources=["user", "project"]`로 Claude가 스킬을 자동 인식. system.md는 핵심 페르소나만 유지.
```

- [ ] **Step 3: Data Sources 업데이트**

```markdown
## Data Sources

- **GarminDB SQLite**: `~/HealthData/DBs/garmin.db` (garmindb 패키지가 관리, 읽기 전용)
  - 테이블: `sleep`, `daily_summary`, `hrv`, `resting_heart_rate`, `activities`, `stress`
- **Garmin Connect API**: python-garminconnect 패키지를 통해 직접 API 호출. 토큰은 `~/.garminconnect/`에 캐시.
  - 요약: sleep, daily_summary, hrv, activities, stress
  - 상세: activity_detail (종목별 자동 감지 — 러닝 splits/cadence/VO2, 웨이트 exercise_sets, 수영 SWOLF 등)
  - 유틸: get_last_activity (최근 활동 빠른 조회)
- **Body Metrics CSV**: `data/inbody.csv` (MCP tool 또는 자연어 파싱으로 행 추가)
  - 컬럼: `date, weight_kg, body_fat_pct, muscle_mass_kg, bmi, source`
  - source: "manual" (기본), "inbody", "unknown" (하위 호환)
```

- [ ] **Step 4: 기존 "InBody" 참조를 "Body Metrics"로 변경**

CLAUDE.md 전체에서 `InBody CSV` → `Body Metrics CSV`, `inbody_data.py` → `body_metrics.py`, `inbody_parser.py` → `body_metrics_parser.py`, `InBodyDataManager` → `BodyMetricsManager`, `InBodyParser` → `BodyMetricsParser` 등 변경.

Gotchas 섹션의 "InBody falsy 값" 항목도 "Body Metrics falsy 값"으로 수정.

- [ ] **Step 5: Environment 섹션에 의존성 추가**

```markdown
- `garminconnect` — Garmin Connect API 클라이언트 (python-garminconnect)
- `claude_agent_sdk` — Claude Agent SDK (@tool, create_sdk_mcp_server, ClaudeAgentOptions)
```

- [ ] **Step 6: 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: final CLAUDE.md update — body_metrics, skills, hybrid architecture"
```
