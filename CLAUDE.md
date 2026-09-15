# ohrmin-claw

Garmin + 체성분 데이터 기반 개인 AI 건강 비서 Discord 봇.

## Commands

```bash
# 봇 실행
python3 bot/main.py

# 테스트
python3 -m pytest tests/ -v

# Garmin 데이터 동기화
bash garmindb/sync.sh

# SQLite 백업
bash scripts/backup.sh

# 초기 환경 세팅
bash scripts/setup.sh
```

## Architecture

> **전체 아키텍처 심화 문서: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — 요청 라이프사이클, 데이터 파이프라인, MCP 도구 카탈로그, 부트 시퀀스·배선, 게처를 file:line 근거와 함께 정리. 시스템을 깊이 이해하거나 변경하기 전에 먼저 참조할 것. 아래는 요약 맵.
>
> **시스템 진화 로드맵: [`docs/ROADMAP.md`](docs/ROADMAP.md)** — Hermes Agent 비교 기반 5축 진화 방향(선제 스케줄러 · 웹 도구 · FTS5 장기기억 · 학습 루프 · 조종 가능 루프)과 성숙도 시퀀스. 새 기능을 설계할 때 참조.

```
core/           추상화 레이어 + 데이터 접근
  llm.py          LLMAdapter ABC(백엔드 공통 계약) → ClaudeSDKAdapter (claude-agent-sdk, 구독 모델) + create_llm_adapter_from_config 팩토리
  llm_config.py   config.json 로드·검증(stdlib) + deprecated LLM_ADAPTER/LLM_MODEL 이관 규칙
  preflight.py    기동 전 백엔드별 인증/설치 검사 (API 키 env 거부 · claude auth status · codex auth.json · XAI_API_KEY)
  llm_errors.py   LLMError(usage_limit/auth_expired/runtime_unavailable/generic) + 사용자 안내 문구 + StartupError
  observability.py [llm] startup · [llm] WARN UNVERIFIED · [gate] · [llm] turn 로그 형식 (capsys 테스트로 고정)
  safety_gate.py  게이트 규칙(evaluate_tool_gate 등) + 단일 판정 decide(CanonicalToolCall, privileged, runtime_guard)
  gate_wiring.py  백엔드당 게이트 wiring 1개: ClaudeHookWiring / CodexHookWiring / GrokApprovalWiring (+기동 probe)
  hooks/codex_gate_hook.py  Codex PreToolUse 훅 명령 (stdlib·3.9 문법, 게이트 엔드포인트에 판정 위임, fail-closed)
  tool_server/    공유 도구 계층: spec(ServerSpec) · schema(strict JSON Schema) · capability(priv/ro) · claude_sdk_bridge · http_server(SharedToolServer)
  runtimes/       codex_adapter · grok_adapter · jsonrpc_stdio(공용 JSON-RPC stdio) · process(spawn 가드) · tool_names(정규 도구명 변환)
  skill_registry.py 봇 측 스킬 카탈로그 + skills MCP 도구(load_skill/read_skill_file)
  channel.py      MessagingChannel ABC → DiscordChannel (discord.py)
  garmin_data.py   GarminConnectClient (python-garminconnect API 기반, 종목별 상세 조회)
  garmin_tools.py  Garmin 도구 정의 (core.tool_server.spec의 @tool → ServerSpec, transport 중립)
  body_metrics.py  Body Metrics CSV CRUD (data/inbody.csv, source 컬럼)
  apple_health_reader.py iCloud JSON(Health Auto Export) → inbody.csv 자동 동기화
  body_metrics_parser.py 자연어 파싱 → 구조화 데이터 (정규식 기반)
  body_metrics_tools.py  Body Metrics MCP tool 정의
  preprocessor.py  원시 데이터 → 통계 요약 (평균, 트렌드, 이상치)
  report.py        주간/월간 마크다운 리포트 생성
  memory.py        영구 메모리 관리 (prompts/memory.md + prompts/user.md, Hermes식)
  context_compressor.py  대화 이력 압축 (보호 구간 + LLM 요약)
  session_manager.py     세션 타임아웃 관리 (idle 24시간 기본)
  scheduler.py     NL cron 스케줄러 (의존성 없는 5필드 cron 매처 + 상대 one-shot + 원자적 JSON 스토어)
  schedule_tools.py  Schedule MCP tool 정의 (schedule_create/list/pause/resume/remove)

bot/main.py     Discord 봇 엔트리포인트 (스레드 기반 대화 세션 + cron_tick_loop)
config.json     LLM 백엔드 설정 (llm.backend · 백엔드별 model/bin · claude.skills, 비밀값 없음)
tests/contract/ 백엔드 공통 계약 스위트 (claude_native · claude_registry · codex · grok fake)
prompts/        시스템 프롬프트 (system.md) + 개인 목표 (goals.md) + 메모리 (memory.md, user.md)
.claude/skills/ 전문 분석 스킬 파일 (운동평가, 수면분석, 체성분, 과학기준)
```

## Key Patterns

- **어댑터 패턴**: LLM과 메시징 채널 모두 ABC로 추상화. 새 어댑터 추가만으로 교체 가능. LLM 백엔드는 `config.json`의 `llm.backend`로 선택.
- **멀티 LLM 백엔드 (config.json 재시작 전환)**: `llm.backend ∈ {claude, codex, grok}` — Claude=`claude_agent_sdk`(구독), Codex=`codex app-server` 2개(priv/ro, ChatGPT 구독), Grok=`grok agent stdio` ACP(`XAI_API_KEY`). 전환은 재시작 단위이며 다른 백엔드로의 자동 대체·런타임 전환 명령은 없다. 한도/인증 오류는 `LLMError` 종류별 한국어 안내만 보낸다. **Codex·Grok은 문서 기반 프로토콜로 구현되고 fake로만 검증됨(실계정 live 미검증)** — 기동 시 `[llm] WARN UNVERIFIED ...` 출력.
- **Preflight fail-fast**: `main()`이 Discord 토큰 검사보다 먼저 `config.json` 오류(`LLM_CONFIG_ERROR`)·`run_preflight`(구독 인증·CLI 설치)를 확인하고, 실패 시 `❌ [LLM] <원인>` / `해결: <명령>` 출력 후 exit 1. 런타임 핸드셰이크(`llm.start()`: 게이트 probe 포함)는 `setup_hook`에서 수행하며 실패 시 클라이언트 종료 → exit 1. 폴백 없음.
- **스레드 기반 대화**: 일반 채널 메시지 → Discord 스레드 자동 생성 → 스레드 내 후속 질문 시 이전 대화 이력을 Claude에 전달. 스레드 = 세션 단위.
- **하이브리드 데이터 접근**: 기본 컨텍스트(preprocessor 요약 7일치)는 항상 포함. Claude가 상세 분석이 필요할 때만 Garmin/Body Metrics MCP tool을 호출하여 추가 데이터 조회. 토큰 절약과 분석 깊이를 동시에 확보.
- **공유 도구 계층 (ServerSpec 1벌 + transport 2종)**: `core/*_tools.py`는 transport 중립 `ServerSpec`(22개: garmin 9(Garmin 로그인 시만) · body_metrics 3 · memory 4 · schedule 5 · session_search 1, registry 모드면 skills 2 추가)을 반환. strict JSON Schema(선택 키 nullable) + None 인자 제거 래퍼는 공통. Claude = `claude_sdk_bridge.to_sdk_servers()`로 봇 프로세스 내 SDK 인프로세스 서버(priv/ro 세트 2개), Codex/Grok = `SharedToolServer`가 봇 이벤트 루프 안에서 127.0.0.1 임시 포트 streamable HTTP로 노출(경로 `/t/<token>/mcp/<server>`, per-boot 토큰 priv/ro 2개). 공유 상태(Garmin 세션·CronStore·memory_mgr·session_index)는 봇 프로세스 하나에 유지.
- **단일 게이트 `decide()` + 백엔드별 wiring 1개**: 판정은 `core/safety_gate.py` `decide()` 하나. 권한 라우팅 키 = `approve_skill_writes is True`(인터랙티브 오너 턴만 priv). 강제 3층: ① 서버측 `CallerCapability` — ro 토큰/세트에서 mutation MCP 7종(schedule create/pause/resume/remove, memory add/replace/remove)은 훅·승인 발생 여부와 무관하게 핸들러 미호출 deny ② wiring — Claude=인프로세스 PreToolUse 훅, Codex=`core/hooks/codex_gate_hook.py` → `POST /t/<token>/gate`, Grok=ACP `session/request_permission` 응답(+permission 없이 실행 관측 시 사후 tripwire `session/cancel`) ③ 런타임 샌드박스 — Codex ro 프로세스 `sandbox_mode="read-only"`. Codex/Grok은 `runtime_guard=True`로 `.claude/` 경로 쓰기도 deny(Claude CLI 가드 에뮬레이션). `allowed_tools`는 스티어링 전용이며 Codex/Grok에는 적용되지 않는다.
- **범용 에이전트**: `allowed_tools`에 빌트인 도구(Bash, Read, Glob 등) + Skill을 포함하여 건강 질의뿐 아니라 일반 질문에도 응답 가능. 봇 코드는 정규 도구 어휘(Claude 명명, `mcp__<server>__<tool>`)만 쓰고 백엔드 표기 변환은 `core/runtimes/tool_names.py`(어댑터 경계)에서만.
- **.claude/skills 패턴**: 전문 분석 프레임워크를 `.claude/skills/`에 마크다운으로 분리. system.md는 핵심 페르소나만 유지. 로딩 모드 2종 — `llm.claude.skills=native`(기본): `setting_sources=["user", "project"]`로 Claude CLI가 스킬 자동 인식 / `registry`(Codex·Grok은 항상): `SkillRegistry`가 매 턴 스캔한 카탈로그를 system prompt에 넣고 `mcp__skills__load_skill`·`read_skill_file`로 본문 제공(Claude registry는 `setting_sources=["project"]`, `skills=[]`).
- **계약 스위트**: `tests/contract/`가 같은 시나리오를 `claude_native`·`claude_registry`·`codex`·`grok` fake 하니스로 실행해 콜백 순서·정규 도구명·게이트 결정·반환 문자열·로그 동일성을 검증. Codex/Grok fake는 문서 기반(`verified=false`).
- **프롬프트 분리**: `prompts/system.md`(페르소나)와 `prompts/goals.md`(개인 목표)를 마크다운으로 분리. 봇 재시작 없이 goals.md만 수정하면 반영됨.
- **영구 메모리**: `prompts/memory.md`(환경/패턴) + `prompts/user.md`(사용자 선호도)에 LLM이 자동 추출한 장기 기억을 저장. `MEMORY_MODE=auto|manual`로 모드 전환. 시스템 프롬프트에 자동 포함.
- **컨텍스트 압축**: 대화 이력이 20개 초과 시 중간 구간을 LLM으로 요약. 첫 메시지(1개) + 최근 메시지(6개)는 원본 보호. Hermes 방식.
- **세션 타임아웃**: 스레드 idle 24시간(기본) 초과 시 히스토리 미로드하여 새 세션 취급. `SESSION_IDLE_TIMEOUT` 환경변수로 조정 가능.
- **유저 화이트리스트**: `ALLOWED_USERS` 환경변수(쉼표 구분 Discord User ID)에 등록된 사용자만 응답. 빈 값이면 모든 메시지 무시 (안전 기본값).
- **NL cron 스케줄러**: LLM이 자연어 예약 요청을 5필드 cron으로 변환해 `schedule_create` 호출 → `data/cron_jobs.json`에 원자적 영속. `@tasks.loop(minutes=1) cron_tick_loop`가 due 잡을 `run_agent_to_channel`로 실행 (per-job try/except로 실패 격리, 상대 one-shot은 발화 후 자기 삭제). `SCHEDULER_ENABLED`로 게이팅. 무인 초기자는 축소 도구셋(`UNATTENDED_ALLOWED_TOOLS`, schedule_list만)으로 mutation 제외.
- **Garmin 429 완화**: `_collect_health_context_async`에 공유 세마포어(동시 1) + 단기 TTL 캐시를 두어 겹치는 초기자(on_message·cron_tick·2분 루프)의 동시 Garmin 버스트를 직렬화·중복 제거. 새 데이터 도착 시 `_invalidate_context_cache`로 강제 재수집.

## Data Sources

- **Garmin Connect API**: python-garminconnect 패키지를 통해 직접 API 호출. 토큰은 `~/.garminconnect/`에 캐시.
  - 요약: sleep, daily_summary, hrv, activities, stress
  - 상세: activity_detail (종목별 자동 감지 — 러닝 splits/cadence/VO2, 웨이트 exercise_sets, 수영 SWOLF 등)
  - 유틸: get_last_activity (최근 활동 빠른 조회)
- **Body Metrics CSV**: `data/inbody.csv` (MCP tool 또는 자연어 파싱으로 행 추가)
  - 컬럼: `date, weight_kg, body_fat_pct, muscle_mass_kg, bmi, source`
  - source: "manual" (기본), "inbody", "unknown" (하위 호환)

## Environment

`.env` 필수 항목:
- `GARMIN_USERNAME` / `GARMIN_PASSWORD` — Garmin Connect 인증
- `DISCORD_BOT_TOKEN` / `DISCORD_APPLICATION_ID` — Discord 봇
- `XAI_API_KEY` — `backend=grok`일 때만 필수 (Grok 자식 프로세스에만 전달)
- `LLM_ADAPTER` / `LLM_MODEL` — **deprecated** (백엔드·모델은 `config.json`). `LLM_ADAPTER`≠`llm.backend` → exit 1(같으면 WARN). Claude 모델 = `llm.claude.model`(non-null) > `LLM_MODEL`(WARN) > `claude-sonnet-4-20250514`, 둘 다 설정·불일치 → exit 1. codex/grok에서 `LLM_MODEL`은 WARN 후 무시
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `CODEX_API_KEY` — 설정돼 있으면 각각 claude / codex 백엔드 기동 거부 (구독 로그인만 허용)
- `ALLOWED_USERS` — 응답 허용할 Discord User ID (쉼표 구분, 필수). 비워두면 모든 메시지 무시
- `MEMORY_MODE` — `auto` (기본, 대화 후 자동 추출) | `manual` (명시적 요청 시만)
- `SESSION_IDLE_TIMEOUT` — 세션 idle 타임아웃 분 (기본: `1440` = 24시간)
- `NOTIFY_CHANNEL_ID` — 자동 분석 결과 전송 Discord 채널 ID (미설정 시 자동 분석 비활성화)
- `SCHEDULER_ENABLED` — cron 스케줄러 kill-switch (`1`/`true` 시 활성, 기본 비활성)
- `MAX_CRON_JOBS` — 등록 가능한 cron 잡 상한 (기본 `50`, 초과 생성 거부)
- `HEALTH_CONTEXT_TTL` — Garmin 컨텍스트 캐시 TTL 초 (기본 `90`, 겹치는 초기자 429 완화)
- `APPLE_HEALTH_EXPORT_DIR` — iCloud Drive 내 Health Auto Export 폴더 경로 (기본값: `~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/daily inbody`)
- 백엔드별 로그인: claude = `claude login`(claude.ai 구독) / codex = `codex login`(ChatGPT 구독, `~/.codex/auth.json`) / grok = Grok Build CLI(`~/.grok/bin/grok`) + `XAI_API_KEY`

`config.json` (커밋 대상, 비밀값 없음, 최상위 키 `llm`만 허용 — 위반 시 exit 1, 파일 부재 시 기본값 + WARN):
- `llm.backend` — `claude`(기본) | `codex` | `grok`
- `llm.claude` — `model`(null = 위 우선순위), `skills`: `native`(기본) | `registry`
- `llm.codex` — `model`(null = Codex 기본), `bin`(기본 `codex`)
- `llm.grok` — `model`(backend=grok이면 필수), `bin`(기본 `~/.grok/bin/grok`)

의존성:
- `garminconnect` — Garmin Connect API 클라이언트 (python-garminconnect)
- `claude_agent_sdk` — Claude Agent SDK (SdkMcpTool, create_sdk_mcp_server, ClaudeAgentOptions — import는 `core/llm.py`·`core/tool_server/claude_sdk_bridge.py`에만)
- `mcp` / `uvicorn` / `starlette` — `SharedToolServer`(streamable HTTP MCP + 게이트 엔드포인트). `bot/main.py`가 항상 import, 기동은 codex/grok에서만

## Testing

```bash
python3 -m pytest tests/ -v       # 전체 테스트
python3 -m pytest tests/test_garmin_data.py  # 개별 모듈
OHRMIN_FORBID_REAL_RUNTIMES=1 python3 -m pytest tests/contract -v              # 백엔드 공통 계약 스위트 (4 파라미터)
OHRMIN_FORBID_REAL_RUNTIMES=1 python3 -m pytest tests/contract -v -k codex     # 백엔드 하나만 (claude_native|claude_registry|codex|grok)
```

- 모든 core 모듈에 대응하는 테스트 파일 존재
- Garmin 테스트는 garminconnect.Garmin mock 사용 (실제 API 불필요)
- LLM 테스트는 `_call_claude` mock 사용. 계약 스위트는 `tests/contract/fakes/`의 fake 런타임(Claude SDK 주입 seam, 문서 기반 Codex app-server·Grok ACP 피어)과 실제 `SharedToolServer`·훅 스크립트를 사용
- `OHRMIN_FORBID_REAL_RUNTIMES=1`이면 실제 런타임 실행을 `RealRuntimeForbidden`으로 막는다 — preflight 기본 러너(`claude auth status`), patch되지 않은 SDK `query`/`ClaudeSDKClient`, `core/runtimes/process.py` spawn(현재 인터프리터 `sys.executable` 자식만 허용). 테스트 실행 시 설정 권장
- `@pytest.mark.ac16`(스킬 registry 계약)은 `claude_native` 파라미터를 생성하지 않고, `@pytest.mark.backends(...)`는 지정 백엔드만 생성한다(skip 아님)

## Gotchas

- **Garmin Rate Limit**: Garmin Connect API는 빈번한 로그인 시 429 반환. `sync.sh`는 최근 3일만 동기화하여 부하 최소화.
- **Discord 2000자 제한**: `DiscordChannel._split_message()`로 줄바꿈 기준 분할. `bot/main.py`에서 이 메서드를 반드시 사용할 것.
- **Body Metrics falsy 값**: `muscle_mass_kg`/`bmi`가 0.0일 수 있음. `or ""`가 아닌 `if x is None`으로 비교할 것.
- **네이티브 실행**: Docker 없음. 맥북 에어 8GB 환경 기준.
- **Claude Agent SDK**: `claude_agent_sdk`의 `query()`는 async generator. `AssistantMessage`의 `TextBlock`만 수집하며, `on_text` 콜백으로 각 블록을 즉시 전송. `max_turns=15`로 multi-turn tool use 허용. `RateLimitEvent`는 건너뛰되, `status=="rejected"`(overage 비허용)와 `AssistantMessage.error ∈ {rate_limit, billing_error, authentication_failed}`는 `LLMError`(usage_limit/auth_expired)로 변환.
- **유틸 `ask`는 실패 시 raise**: `llm.ask`(압축·메모리 추출/통합)는 폴백 문자열 대신 `LLMError`를 raise한다(오류 문구가 memory.md를 덮어쓰던 버그 수정). 새 호출부는 `except LLMError`(또는 `Exception`)로 받을 것. 스트리밍 `ask_with_context`만 오류 안내를 on_text + 반환하며, 성공 판정은 `is_llm_error_reply()`로.
- **Codex 훅 스크립트 제약**: `core/hooks/codex_gate_hook.py`는 **stdlib만 · Python 3.9 문법 · `core` import 금지**(훅 command는 `[sys.executable, 절대경로]`지만 외부 런타임이 실행하므로 방어적으로 유지 — PATH의 `/usr/bin/python3`는 3.9). 모든 예외·타임아웃·비200은 deny + exit 2(fail-closed). `tests/test_codex_gate_hook_script.py`가 3.9 파싱·import 목록을 검사.
- **Codex = app-server 프로세스 2개**: priv(`danger-full-access`)·ro(`read-only`)를 동시에 띄운다(8GB 메모리 주의). `-c` 오버라이드로 MCP URL·훅·모델을 넘기고 사용자 `~/.codex/config.toml`은 수정하지 않는다. 자식 env에서 `OPENAI_API_KEY`/`CODEX_API_KEY` 제거. ro 턴은 셸 기반 Read/Glob/Grep이 Bash 게이트에 막히고 WebFetch는 없음(system prompt 부록으로 안내).
- **Grok 격리 HOME + 프로젝트 Claude 설정 검사**: Grok은 `<PROJECT_ROOT>/data/runtime/grok-home`을 HOME으로 쓰고 `start()`마다 `.grok/config.toml`(env_key=XAI_API_KEY, permission_mode=ask)을 재생성한다. 격리 HOME에 `auth.json`이 있으면 기동 거부. Grok은 프로젝트 `CLAUDE.md`·`.claude` 설정도 읽으므로 `.claude/settings.json`/`settings.local.json`에 `hooks`가 있거나 `.mcp.json`이 있으면 기동 거부, 게이트 대상 `permissions.allow` 규칙은 `WARN UNVERIFIED grok.project_claude_permissions`만 출력.
- **Grok 텍스트 1초 idle flush**: `agent_message_chunk`는 버퍼링 후 tool_call·턴 종료 시 flush하지만, 청크가 1초(`_TEXT_IDLE_FLUSH`) 끊겨도 flush → 하나의 답변이 Discord 메시지 여러 개로 나뉠 수 있음. system prompt는 ACP 필드 대신 첫 `session/prompt`의 `[시스템 지시]` 블록으로 전달.
- **UNVERIFIED WARN은 정상**: codex/grok 기동 시(및 claude `skills=registry` 시 `claude.skills_filter`) `[llm] WARN UNVERIFIED <surface> source=<url>`가 surface마다 1줄 출력된다. 문서 기반 가정(훅 `-c` 오버라이드·`approval_policy=never` 하 훅 발화·payload 형태, ACP HTTP MCP·permission 요청 발생·오류 형태 등)이 live로 확인되기 전까지 codex/grok 운영 사용은 비권장. claude native는 0줄.
