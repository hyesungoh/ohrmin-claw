"""ToolSpec · strict JSON Schema · 이름 규칙 · None 제거 · 필수 파라미터 검증 (AC-15 a/d/e).

스냅샷은 base(4d6e500)의 Annotated 정의에서 뽑은 이름·설명·타입을 strict 형태로 옮긴 것이다
(선택 파라미터 = [T, "null"], activity_id 3곳만 non-nullable).
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.body_metrics_tools import create_body_metrics_mcp_server
from core.garmin_tools import create_garmin_mcp_server
from core.memory_tools import create_memory_mcp_server
from core.schedule_tools import create_schedule_mcp_server
from core.session_search_tools import create_session_search_mcp_server
from core.tool_server.capability import CallerCapability, wrap_handler
from core.tool_server.schema import (
    MAX_TOOL_NAME_LENGTH,
    to_strict_json_schema,
    validate_server_spec,
    validate_tool_name,
)
from core.tool_server.spec import ParamSpec, ServerSpec, ToolSpec, canonical_tool_name, tool

NAME_RE = r"^[a-zA-Z0-9_-]{1,64}$"


def _opt(json_type, description):
    return {"type": [json_type, "null"], "description": description}


def _req(json_type, description):
    return {"type": json_type, "description": description}


def _obj(**properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


SNAPSHOT = {
    'mcp__garmin__get_sleep': (
        '수면 데이터 조회 (일별 총수면, 깊은수면, REM, 점수)',
        _obj(start=_opt('string', '시작 날짜 (YYYY-MM-DD). 생략 시 7일 전'), end=_opt('string', '종료 날짜 (YYYY-MM-DD). 생략 시 오늘')),
    ),
    'mcp__garmin__get_daily_summary': (
        '일별 건강 요약 (안정시 심박수, 걸음수, 칼로리, 스트레스)',
        _obj(start=_opt('string', '시작 날짜 (YYYY-MM-DD). 생략 시 7일 전'), end=_opt('string', '종료 날짜 (YYYY-MM-DD). 생략 시 오늘')),
    ),
    'mcp__garmin__get_hrv': (
        '심박변이도(HRV) 데이터 조회 (주간평균, 상태)',
        _obj(start=_opt('string', '시작 날짜 (YYYY-MM-DD). 생략 시 7일 전'), end=_opt('string', '종료 날짜 (YYYY-MM-DD). 생략 시 오늘')),
    ),
    'mcp__garmin__get_activities': (
        '운동 활동 목록 조회 (종목, 거리, 시간, 심박수, 칼로리)',
        _obj(start=_opt('string', '시작 날짜 (YYYY-MM-DD). 생략 시 7일 전'), end=_opt('string', '종료 날짜 (YYYY-MM-DD). 생략 시 오늘')),
    ),
    'mcp__garmin__get_stress': (
        '일별 스트레스 수준 조회',
        _obj(start=_opt('string', '시작 날짜 (YYYY-MM-DD). 생략 시 7일 전'), end=_opt('string', '종료 날짜 (YYYY-MM-DD). 생략 시 오늘')),
    ),
    'mcp__garmin__get_activity_detail': (
        '특정 활동의 상세 분석 (스플릿, HR 존, VO2 Max, 케이던스)',
        _obj(activity_id=_req('string', '활동 ID (get_activities로 먼저 조회)')),
    ),
    'mcp__garmin__get_activity_splits': (
        '특정 활동의 구간별(랩) 데이터 (페이스, 심박수, 고도)',
        _obj(activity_id=_req('string', '활동 ID (get_activities로 먼저 조회)')),
    ),
    'mcp__garmin__get_activity_hr_zones': (
        '특정 활동의 심박수 존별 시간 분포',
        _obj(activity_id=_req('string', '활동 ID (get_activities로 먼저 조회)')),
    ),
    'mcp__garmin__get_last_activity': (
        '최근 활동 조회 (가장 마지막 운동부터). count로 개수 지정',
        _obj(count=_opt('integer', '조회할 활동 수 (기본 1, 최대 10)')),
    ),
    'mcp__body_metrics__add_body_measurement': (
        '체성분 측정 기록 추가 (최소 1개 필드 필수)',
        _obj(date=_opt('string', '측정 날짜 (YYYY-MM-DD). 생략 시 오늘'), weight_kg=_opt('number', '체중 (kg)'), body_fat_pct=_opt('number', '체지방률 (%)'), muscle_mass_kg=_opt('number', '골격근량 (kg)'), bmi=_opt('number', 'BMI'), source=_opt('string', '데이터 출처 (manual, inbody 등). 기본 manual')),
    ),
    'mcp__body_metrics__get_body_metrics_history': (
        '체성분 측정 이력 조회 (최근 N건 또는 기간)',
        _obj(count=_opt('integer', '조회할 최근 기록 수 (기본 10)'), days=_opt('integer', '최근 N일 이내 기록만 조회 (선택)')),
    ),
    'mcp__body_metrics__get_body_metrics_trend': (
        '특정 체성분 지표의 트렌드 분석 (기간별 변화)',
        _obj(field=_opt('string', '분석할 필드명 (weight_kg, body_fat_pct, muscle_mass_kg, bmi)'), days=_opt('integer', '분석 기간 (일, 기본 30)')),
    ),
    'mcp__memory__list_memory': (
        '메모리 엔트리 목록 조회 (현재 사용량 포함)',
        _obj(target=_opt('string', "조회할 메모리 대상 ('memory' 또는 'user')")),
    ),
    'mcp__memory__add_memory': (
        '메모리 엔트리 추가 (용량 초과 시 LLM이 자동 통합)',
        _obj(target=_opt('string', "저장할 메모리 대상 ('memory' 또는 'user')"), content=_opt('string', '저장할 내용')),
    ),
    'mcp__memory__replace_memory': (
        '특정 메모리 엔트리를 새 내용으로 교체',
        _obj(target=_opt('string', "수정할 메모리 대상 ('memory' 또는 'user')"), index=_opt('integer', '교체할 엔트리 인덱스 (0부터 시작)'), content=_opt('string', '새 내용')),
    ),
    'mcp__memory__remove_memory': (
        '특정 메모리 엔트리 삭제',
        _obj(target=_opt('string', "삭제할 메모리 대상 ('memory' 또는 'user')"), index=_opt('integer', '삭제할 엔트리 인덱스 (0부터 시작)')),
    ),
    'mcp__schedule__schedule_create': (
        '예약/반복 작업 생성 (cron 또는 상대 시간). 자연어는 호출 전 cron으로 변환할 것',
        _obj(prompt=_opt('string', '발화 시 실행할 지시(프롬프트). 이 내용으로 에이전트 턴이 실행됨'), schedule=_opt('string', "5필드 cron('0 20 * * 0' = 매주 일 20시) 또는 상대 one-shot('30m'/'2h'/'1d'). 자연어는 호출 전에 cron으로 변환할 것"), deliver_channel_id=_opt('string', '결과 전송 Discord 채널 ID (생략 시 기본 알림 채널)'), max_turns=_opt('integer', '잡 실행 시 최대 턴 수 (기본 15)')),
    ),
    'mcp__schedule__schedule_list': (
        '등록된 예약/반복 작업 목록 조회',
        _obj(),
    ),
    'mcp__schedule__schedule_pause': (
        '스케줄 일시정지 (발화 중단, 삭제 아님)',
        _obj(id=_opt('string', '대상 스케줄 ID (schedule_list로 조회)')),
    ),
    'mcp__schedule__schedule_resume': (
        '일시정지된 스케줄 재개',
        _obj(id=_opt('string', '대상 스케줄 ID (schedule_list로 조회)')),
    ),
    'mcp__schedule__schedule_remove': (
        '스케줄 삭제',
        _obj(id=_opt('string', '대상 스케줄 ID (schedule_list로 조회)')),
    ),
    'mcp__session_search__search': (
        '과거 대화 기록을 전문 검색 (FTS5 bm25 랭킹, 관련도순)',
        _obj(query=_opt('string', '검색어 (과거 대화에서 찾을 키워드/문장)'), limit=_opt('integer', '최대 결과 수 (기본 10, 최대 50)')),
    ),
}


def _all_specs(garmin=None, metrics=None, memory=None, store=None, index=None) -> list[ServerSpec]:
    return [
        create_garmin_mcp_server(garmin or MagicMock()),
        create_body_metrics_mcp_server(metrics or MagicMock()),
        create_memory_mcp_server(memory or MagicMock()),
        create_schedule_mcp_server(store or MagicMock()),
        create_session_search_mcp_server(index or MagicMock()),
    ]


def _by_canonical(specs):
    return {
        canonical_tool_name(spec.name, t.name): (spec, t)
        for spec in specs
        for t in spec.tools
    }


class TestServerSpecs:
    def test_five_servers_22_tools(self):
        specs = _all_specs()
        assert [s.name for s in specs] == ["garmin", "body_metrics", "memory", "schedule", "session_search"]
        assert [len(s.tools) for s in specs] == [9, 3, 4, 5, 1]
        assert sum(len(s.tools) for s in specs) == 22

    def test_names_match_snapshot_in_order(self):
        assert list(_by_canonical(_all_specs())) == list(SNAPSHOT)

    @pytest.mark.parametrize("canonical", list(SNAPSHOT))
    def test_description_and_strict_schema_snapshot(self, canonical):
        spec, tool_spec = _by_canonical(_all_specs())[canonical]
        description, schema = SNAPSHOT[canonical]
        assert tool_spec.description == description
        assert to_strict_json_schema(tool_spec.params) == schema

    def test_tool_specs_expose_name_and_raw_handler(self):
        """TOOL_REGISTRY[name].handler(args) 패턴 보존 — handler는 원 핸들러(래핑 없음)."""
        from core.schedule_tools import TOOL_REGISTRY

        spec = create_schedule_mcp_server(MagicMock())
        assert set(TOOL_REGISTRY) == {t.name for t in spec.tools}
        for t in spec.tools:
            assert TOOL_REGISTRY[t.name] is t
            assert isinstance(t, ToolSpec)


class TestStrictSchema:
    def test_empty_params(self):
        assert to_strict_json_schema({}) == {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False,
        }

    def test_optional_is_nullable_required_is_not(self):
        schema = to_strict_json_schema({
            "a": ParamSpec("integer", "A"),
            "b": ParamSpec("string", "B", required=True),
        })
        assert schema["properties"]["a"] == {"type": ["integer", "null"], "description": "A"}
        assert schema["properties"]["b"] == {"type": "string", "description": "B"}
        assert schema["required"] == ["a", "b"]
        assert schema["additionalProperties"] is False

    def test_only_three_activity_id_params_are_required(self):
        required = [
            (canonical, key)
            for canonical, (_, t) in _by_canonical(_all_specs()).items()
            for key, param in t.params.items()
            if param.required
        ]
        assert required == [
            ("mcp__garmin__get_activity_detail", "activity_id"),
            ("mcp__garmin__get_activity_splits", "activity_id"),
            ("mcp__garmin__get_activity_hr_zones", "activity_id"),
        ]
        for canonical, _ in required:
            _, t = _by_canonical(_all_specs())[canonical]
            assert to_strict_json_schema(t.params)["properties"]["activity_id"]["type"] == "string"

    def test_every_non_required_param_is_nullable(self):
        for canonical, (_, t) in _by_canonical(_all_specs()).items():
            props = to_strict_json_schema(t.params)["properties"]
            for key, param in t.params.items():
                if not param.required:
                    assert props[key]["type"][1] == "null", (canonical, key)


class TestNameRules:
    def test_all_22_names_valid_for_claude_codex_and_grok_forms(self):
        import re

        specs = _all_specs()
        for spec in specs:
            validate_server_spec(spec)
            for t in spec.tools:
                for rendered in (canonical_tool_name(spec.name, t.name), f"{spec.name}__{t.name}"):
                    assert re.match(NAME_RE, rendered), rendered
                    assert len(rendered) <= MAX_TOOL_NAME_LENGTH
        longest = max(len(canonical_tool_name(s.name, t.name)) for s in specs for t in s.tools)
        assert longest == 43

    @pytest.mark.parametrize("server, tool_name", [
        ("bad server", "t"),
        ("s", "bad.tool"),
        ("", "t"),
        ("s", ""),
        ("서버", "t"),
    ])
    def test_invalid_characters_raise(self, server, tool_name):
        with pytest.raises(ValueError):
            validate_tool_name(server, tool_name)

    def test_mcp_prefixed_form_over_64_raises(self):
        # s__t = 60자(허용)이지만 mcp__s__t = 65자로 64자를 넘는 경계
        server, tool_name = "s" * 20, "t" * 38
        assert len(f"{server}__{tool_name}") == 60
        with pytest.raises(ValueError, match="max 64"):
            validate_tool_name(server, tool_name)

    def test_exactly_64_with_prefix_is_allowed(self):
        server, tool_name = "s" * 20, "t" * 37
        assert len(canonical_tool_name(server, tool_name)) == 64
        validate_tool_name(server, tool_name)

    def test_server_spec_validation_reports_bad_tool(self):
        @tool("bad name", "d", {})
        async def bad(args):
            return {}

        with pytest.raises(ValueError, match="bad name"):
            validate_server_spec(ServerSpec("ok", [bad]))


def _text(result):
    return json.loads(result["content"][0]["text"])


class TestNoneStripping:
    @pytest.mark.asyncio
    async def test_none_keys_removed_before_handler(self):
        recorder = AsyncMock(return_value={"content": [{"type": "text", "text": "{}"}]})
        spec = ToolSpec("t", "d", {"a": ParamSpec("integer", "A"), "b": ParamSpec("string", "B")}, recorder)
        handler = wrap_handler("s", spec, CallerCapability.PRIVILEGED)
        await handler({"a": None, "b": "x"})
        recorder.assert_awaited_once_with({"b": "x"})

    @pytest.mark.asyncio
    async def test_falsy_non_none_values_are_kept(self):
        recorder = AsyncMock(return_value={"content": []})
        spec = ToolSpec("t", "d", {"a": ParamSpec("integer", "A"), "b": ParamSpec("string", "B")}, recorder)
        await wrap_handler("s", spec, CallerCapability.READ_ONLY)({"a": 0, "b": ""})
        recorder.assert_awaited_once_with({"a": 0, "b": ""})

    @pytest.mark.asyncio
    async def test_get_last_activity_null_count_uses_default(self):
        """F5: count=None이 min(None, 10) TypeError가 아니라 기본값 1로 동작."""
        garmin = MagicMock()
        garmin.get_last_activity.return_value = {"activity_id": "1"}
        spec, t = _by_canonical(_all_specs(garmin=garmin))["mcp__garmin__get_last_activity"]
        await wrap_handler(spec.name, t, CallerCapability.READ_ONLY)({"count": None})
        garmin.get_last_activity.assert_called_once_with(count=1)

    @pytest.mark.asyncio
    async def test_add_body_measurement_null_source_uses_manual(self):
        """F5: source=None이 None 저장이 아니라 기본 manual."""
        metrics = MagicMock()
        spec, t = _by_canonical(_all_specs(metrics=metrics))["mcp__body_metrics__add_body_measurement"]
        args = {"date": None, "weight_kg": 70.5, "body_fat_pct": None, "muscle_mass_kg": None, "bmi": None, "source": None}
        result = await wrap_handler(spec.name, t, CallerCapability.PRIVILEGED)(args)
        assert _text(result)["status"] == "ok"
        kwargs = metrics.add_entry.call_args.kwargs
        assert kwargs["source"] == "manual"
        assert kwargs["weight_kg"] == 70.5

    @pytest.mark.asyncio
    async def test_body_metrics_history_null_days_and_count(self):
        """F5: days=None → 기간 필터 없음, count=None → 기본 10 (all_rows[:None] 아님)."""
        metrics = MagicMock()
        metrics.read_all.return_value = [{"date": f"2020-01-{i:02d}"} for i in range(1, 16)]
        spec, t = _by_canonical(_all_specs(metrics=metrics))["mcp__body_metrics__get_body_metrics_history"]
        result = await wrap_handler(spec.name, t, CallerCapability.READ_ONLY)({"count": None, "days": None})
        assert len(_text(result)) == 10


class TestRequiredParamValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", ["get_activity_detail", "get_activity_splits", "get_activity_hr_zones"])
    @pytest.mark.parametrize("args", [{"activity_id": None}, {}])
    async def test_null_or_missing_required_is_validation_error_not_keyerror(self, tool_name, args):
        garmin = MagicMock()
        spec, t = _by_canonical(_all_specs(garmin=garmin))[f"mcp__garmin__{tool_name}"]
        result = await wrap_handler(spec.name, t, CallerCapability.READ_ONLY)(args)
        assert result["is_error"] is True
        assert result["content"][0]["text"].startswith("Input validation error: ")
        assert "activity_id" in result["content"][0]["text"]
        getattr(garmin, tool_name).assert_not_called()

    @pytest.mark.asyncio
    async def test_raw_handler_still_raises_keyerror_without_wrapper(self):
        """래퍼가 없으면(원 핸들러 직접) 기존 의미 그대로 — 핸들러 본문 불변 확인."""
        spec, t = _by_canonical(_all_specs())["mcp__garmin__get_activity_detail"]
        with pytest.raises(KeyError):
            await t.handler({})

    @pytest.mark.asyncio
    async def test_required_present_calls_handler(self):
        garmin = MagicMock()
        garmin.get_activity_detail.return_value = {"id": "42"}
        spec, t = _by_canonical(_all_specs(garmin=garmin))["mcp__garmin__get_activity_detail"]
        result = await wrap_handler(spec.name, t, CallerCapability.READ_ONLY)({"activity_id": "42"})
        assert _text(result) == {"id": "42"}
        garmin.get_activity_detail.assert_called_once_with("42")
