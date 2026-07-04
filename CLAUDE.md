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
  llm.py          LLMAdapter ABC → ClaudeSDKAdapter (claude-agent-sdk, 구독 모델, MCP 서버 + cwd/skills 지원)
  channel.py      MessagingChannel ABC → DiscordChannel (discord.py)
  garmin_data.py   GarminConnectClient (python-garminconnect API 기반, 종목별 상세 조회)
  garmin_tools.py  Garmin MCP tool 정의 (@tool + create_sdk_mcp_server)
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
prompts/        시스템 프롬프트 (system.md) + 개인 목표 (goals.md) + 메모리 (memory.md, user.md)
.claude/skills/ 전문 분석 스킬 파일 (운동평가, 수면분석, 체성분, 과학기준)
```

## Key Patterns

- **어댑터 패턴**: LLM과 메시징 채널 모두 ABC로 추상화. 새 어댑터 추가만으로 교체 가능. `.env`의 `LLM_ADAPTER` 값으로 런타임 전환.
- **스레드 기반 대화**: 일반 채널 메시지 → Discord 스레드 자동 생성 → 스레드 내 후속 질문 시 이전 대화 이력을 Claude에 전달. 스레드 = 세션 단위.
- **하이브리드 데이터 접근**: 기본 컨텍스트(preprocessor 요약 7일치)는 항상 포함. Claude가 상세 분석이 필요할 때만 Garmin/Body Metrics MCP tool을 호출하여 추가 데이터 조회. 토큰 절약과 분석 깊이를 동시에 확보.
- **인프로세스 MCP 서버**: `claude_agent_sdk`의 `@tool` + `create_sdk_mcp_server()`로 봇 프로세스 내에서 Garmin + Body Metrics 도구를 직접 제공. 별도 프로세스 불필요.
- **범용 에이전트**: `allowed_tools`에 빌트인 도구(Bash, Read, Glob 등) + Skill을 포함하여 건강 질의뿐 아니라 일반 질문에도 응답 가능.
- **.claude/skills 패턴**: 전문 분석 프레임워크를 `.claude/skills/`에 마크다운으로 분리. `setting_sources=["user", "project"]`로 Claude가 스킬을 자동 인식. system.md는 핵심 페르소나만 유지.
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
- `LLM_ADAPTER` — `claude` (기본)
- `LLM_MODEL` — 사용할 모델 (선택, 기본값: `claude-sonnet-4-20250514`)
- `ALLOWED_USERS` — 응답 허용할 Discord User ID (쉼표 구분, 필수). 비워두면 모든 메시지 무시
- `MEMORY_MODE` — `auto` (기본, 대화 후 자동 추출) | `manual` (명시적 요청 시만)
- `SESSION_IDLE_TIMEOUT` — 세션 idle 타임아웃 분 (기본: `1440` = 24시간)
- `NOTIFY_CHANNEL_ID` — 자동 분석 결과 전송 Discord 채널 ID (미설정 시 자동 분석 비활성화)
- `SCHEDULER_ENABLED` — cron 스케줄러 kill-switch (`1`/`true` 시 활성, 기본 비활성)
- `MAX_CRON_JOBS` — 등록 가능한 cron 잡 상한 (기본 `50`, 초과 생성 거부)
- `HEALTH_CONTEXT_TTL` — Garmin 컨텍스트 캐시 TTL 초 (기본 `90`, 겹치는 초기자 429 완화)
- `APPLE_HEALTH_EXPORT_DIR` — iCloud Drive 내 Health Auto Export 폴더 경로 (기본값: `~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/daily inbody`)
- Claude CLI 로그인 필요: `claude login`

의존성:
- `garminconnect` — Garmin Connect API 클라이언트 (python-garminconnect)
- `claude_agent_sdk` — Claude Agent SDK (@tool, create_sdk_mcp_server, ClaudeAgentOptions)

## Testing

```bash
python3 -m pytest tests/ -v       # 전체 테스트
python3 -m pytest tests/test_garmin_data.py  # 개별 모듈
```

- 모든 core 모듈에 대응하는 테스트 파일 존재
- Garmin 테스트는 garminconnect.Garmin mock 사용 (실제 API 불필요)
- LLM 테스트는 `_call_claude` mock 사용

## Gotchas

- **Garmin Rate Limit**: Garmin Connect API는 빈번한 로그인 시 429 반환. `sync.sh`는 최근 3일만 동기화하여 부하 최소화.
- **Discord 2000자 제한**: `DiscordChannel._split_message()`로 줄바꿈 기준 분할. `bot/main.py`에서 이 메서드를 반드시 사용할 것.
- **Body Metrics falsy 값**: `muscle_mass_kg`/`bmi`가 0.0일 수 있음. `or ""`가 아닌 `if x is None`으로 비교할 것.
- **네이티브 실행**: Docker 없음. 맥북 에어 8GB 환경 기준.
- **Claude Agent SDK**: `claude_agent_sdk`의 `query()`는 async generator. `AssistantMessage`의 `TextBlock`만 수집하며, `on_text` 콜백으로 각 블록을 즉시 전송. `max_turns=15`로 multi-turn tool use 허용. `RateLimitEvent`는 자동으로 건너뜀.
