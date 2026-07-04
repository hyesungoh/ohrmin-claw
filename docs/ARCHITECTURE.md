# 아키텍처 (Architecture)

> README.md의 심화 동반 문서. 시스템을 처음 만지는 미래의 세션이 빠르게 올바른 멘탈 모델을 잡고 "어디에 뭐가 있는지"를 찾도록 돕는 지도. 설치·사용법은 README.md 참조.

## 1. 개요 (Overview)

ohrmin-claw는 Garmin Connect + 체성분 데이터 기반 개인 AI 건강 비서 Discord 봇이다. 요청 모델은 한 줄로 요약된다: **Discord 메시지 → 스레드 세션 → Claude(시스템 프롬프트 + 항상 포함되는 7일 건강 컨텍스트 + on-demand MCP 도구) → 스트리밍 응답**. 봇은 LLM과 메시징 채널을 각각 ABC로 추상화하며, Claude Agent SDK를 인프로세스 MCP 서버(Garmin / 체성분 / 메모리)와 함께 구동한다. 라이브 DB는 없다 — Garmin 데이터는 매 질의마다 API로 직접 가져온다.

## 2. 시스템 다이어그램

```
Discord 메시지 도착
   │
   ▼
on_message (bot/main.py:441)
   │  ├─ 자기 메시지 무시 (:443)
   │  ├─ 화이트리스트 게이트 ALLOWED_USERS (:447-448)   ← 빈 set = 전원 무시(안전 기본)
   │  └─ content/이미지 추출 (:450-456)
   │
   ├─ "주간 리포트" 포함? ──yes──▶ generate_weekly_report (:459-463, :279-316)
   │                                (스레드/세션/히스토리 우회, 비-스트리밍)
   │  no
   ▼
handle_health_query (:228-276)
   │
   ├─ _build_system_prompt (:230)        system.md + goals.md + [기억]memory.md + [사용자]user.md
   ├─ _collect_health_context_async (:232)  최근 7일 Garmin 요약 + 체성분 최근값 (baseline)
   │
   ├─ 스레드인가?  (:234)
   │    ├─ yes(후속): idle 만료 검사(:241) → 히스토리 build+compress(:245-248)
   │    └─ no(첫메시지): create_thread(이름=첫100자)(:252), history=None
   │
   ▼
llm.ask_with_context(system, content, context, history, on_text)  (:265-269)
   │  async with target.typing():
   │  ClaudeSDKAdapter._call_claude → Agent SDK query() (max_turns=15)
   │      │  ├─ 필요 시 MCP 도구 호출 (mcp__garmin__* / mcp__body_metrics__* / mcp__memory__*)
   │      │  └─ 각 TextBlock 도착 → on_text 콜백
   │      ▼
   │  on_text(text) = send_reply(target, text)  (:261-262)
   │      └─ _split_message로 2000자 분할 후 순차 전송  (하나의 답변이 여러 메시지로 나뉠 수 있음)
   │
   ▼
메모리 자동 추출 (:272-276)  MEMORY_MODE==auto 시 extract_and_save
```

## 3. 레이어 & 모듈 맵

| 모듈 (경로) | 책임 | 핵심 진입점 |
|---|---|---|
| `core/llm.py` | LLM 추상화. Claude Agent SDK를 ABC 뒤로 감싸 스트리밍 + 인프로세스 MCP + cwd/스킬 옵션 제공 | `ClaudeSDKAdapter._call_claude` (:46), `create_llm_adapter` (:104) |
| `core/channel.py` | 메시징 추상화. discord.py Client + 2000자 분할 로직 | `DiscordChannel._split_message` (:60-73) |
| `core/garmin_data.py` | Garmin Connect 인증 + 원시 API를 summary/detail dict로 정규화 | `GarminConnectClient` (:59), `get_activity_detail`(스마트 디스패처, :238) |
| `core/garmin_tools.py` | GarminConnectClient 메서드를 인프로세스 MCP 도구로 노출 | `create_garmin_mcp_server` (:31) |
| `core/body_metrics.py` | 체성분 행 CSV CRUD (data/inbody.csv) | `BodyMetricsManager` (:6), `upsert_entry` (:60) |
| `core/body_metrics_tools.py` | 체성분 CRUD를 MCP 도구로 노출 | `create_body_metrics_mcp_server` (:13) |
| `core/body_metrics_parser.py` | 자유형 한국어 텍스트 → 체성분 숫자 정규식 추출 (MCP와 병렬 경로) | `BodyMetricsParser.parse` (:26) |
| `core/memory.py` | 영구 메모리 관리 (memory.md/user.md), 추출·통합·인젝션 방어 | `MemoryManager` (:77), `_save_or_consolidate` (:228) |
| `core/memory_tools.py` | 메모리 CRUD를 MCP 도구로 노출 | `create_memory_mcp_server` (:12) |
| `core/preprocessor.py` | 원시 레코드 → 통계 요약 (순수, 무상태 staticmethod) | `HealthPreprocessor.create_weekly_summary` (:238) |
| `core/report.py` | 요약 dict → Discord 마크다운 리포트 | `ReportGenerator.weekly_report` (:7) |
| `core/apple_health_reader.py` | iCloud Health Auto Export JSON → inbody.csv upsert | `sync_from_icloud` (:15) |
| `core/context_compressor.py` | 히스토리 20개 초과 시 중간 구간 LLM 요약 (Hermes식) | `ContextCompressor.compress` (:47) |
| `core/session_manager.py` | 스레드별 idle 타이머 (인메모리) → 새 세션 여부 판정 | `SessionManager.is_expired` (:17) |
| `bot/main.py` | 봇 엔트리포인트. 전체 서브시스템 배선 + 이벤트 핸들러 + 라이프사이클 오케스트레이션 | `on_message` (:441), `handle_health_query` (:228) |

## 4. 핵심 설계 패턴

- **어댑터 패턴 (LLM + 채널 ABC)** — `LLMAdapter`(llm.py:10)와 `MessagingChannel`(channel.py:8)이 각각 ABC. 봇은 ABC + 팩토리에만 의존한다. **왜**: 새 백엔드 = 서브클래스 1개 + 팩토리 분기 1개. `.env`의 `LLM_ADAPTER`로 런타임 전환.
- **인프로세스 MCP 서버** — `@tool` + `create_sdk_mcp_server()`로 Garmin/체성분/메모리 도구를 봇 프로세스 안에서 직접 제공(별도 프로세스 불필요). **왜**: 별도 stdio MCP 프로세스 없이 Python 객체(클라이언트/매니저)를 클로저로 잡아 도구화, 배포·운영 단순.
- **하이브리드 데이터 접근 (baseline 항상 + MCP on-demand)** — 최근 7일 preprocessor 요약은 항상 `context=`로 부착(llm.py:82-101), 깊은 데이터는 Claude가 필요할 때만 MCP 도구로 조회. **왜**: 토큰 절약과 분석 깊이를 동시에 확보.
- **스레드 = 세션** — 채널 첫 메시지는 스레드를 자동 생성(이름=첫 100자, main.py:252), 스레드 내 후속은 이전 대화 이력을 로드해 세션으로 이어감. **왜**: Discord 스레드를 자연스러운 대화 세션 경계로 사용.
- **컨텍스트 압축 (Hermes식)** — 히스토리 20개 초과 시 첫 1개 + 마지막 6개는 원본 보호, 중간 구간만 별도 LLM 호출로 요약 1개로 교체(context_compressor.py). **왜**: 긴 스레드에서도 토큰을 억제하되 최신·최초 맥락은 손실 없이 유지.
- **영구 메모리 (MEMORY vs USER)** — `memory.md`(환경 사실/건강 패턴)와 `user.md`(선호도/커뮤니케이션 스타일)를 분리, 매 질의 후 자동 추출(auto 모드). **왜**: 사실 레이어와 페르소나 튜닝 레이어를 분리해 각각 독립적으로 진화.
- **프롬프트 분리 · hot-reload** — system.md/goals.md/memory.md/user.md 4개를 매 질의마다 재읽기(캐시 없음, main.py:230). **왜**: 봇 재시작 없이 목표·페르소나·메모리 수정이 다음 메시지에 즉시 반영.
- **.claude/skills 자동발견 (cwd 스위치)** — `setting_sources=["user","project"]`가 `cwd` 제공 시에만 설정(llm.py:60-66). Agent SDK가 `<cwd>/.claude/skills/`를 스캔해 각 SKILL.md 자동 등록. **왜**: cwd 하나가 스킬/빌트인 도구/bypassPermissions 전체의 마스터 스위치. system.md는 스킬 존재만 언급하고 바디는 온디맨드 로드.

## 5. 요청 라이프사이클 (상세)

on_message 핸들러 `bot/main.py:441-474`부터의 단계별 트레이스:

1. **자기 메시지 무시** (:443).
2. **화이트리스트 게이트** (:447-448) — `author.id not in ALLOWED_USERS`면 조용히 return. 빈 set이면 전원 무시(안전 기본).
3. **content strip + 이미지 추출** (:450, :453). 텍스트도 이미지도 없으면 return (:455-456).
4. **주간 리포트 숏컷** (:459-463) — 메시지에 "주간 리포트"/"weekly report" 포함 시 `generate_weekly_report()` 실행 후 `send_reply`, return. **스레드/세션/히스토리를 모두 우회**하며 이 경로만 비-스트리밍(반환값 사용).
5. 그 외에는 이미지를 temp에 저장(:466) → `handle_health_query(...)` (:468-472) → `finally cleanup_temp_images()` (:473-474).

`handle_health_query` `:228-276`:

6. **시스템 프롬프트 빌드** (:230) + **baseline 건강 컨텍스트** async 수집 (:232, `asyncio.to_thread`).
7. **스레드 분기** (`isinstance(message.channel, discord.Thread)`, :234):
   - **스레드 내(후속)**: `session_mgr.is_expired(thread_id)` 검사(:241). 만료면 `clear` + `history=None`(새 세션). 아니면 `build_history_from_thread(exclude_last=True)`(:245, 최대 50개), non-empty면 `context_compressor.compress(history, llm)`(:248). `update_activity`(:250).
   - **스레드 아님(채널 첫 메시지)**: `target = await message.create_thread(name=content[:100])`(:252) — 첫 100자로 스레드 자동 생성. `history=None`.
8. **이미지 경로 처리** (:257-259) — image_paths 있으면 `[첨부 이미지]` 블록을 prepend하고 temp 경로를 나열해 Read하라 지시(인라인 아닌 파일시스템 경로 전달).
9. **스트리밍 콜백 설정** (:261-262) — `on_text(text) = send_reply(target, text)`.
10. **LLM 호출** (:264-269) — `async with target.typing():` 안에서 `llm.ask_with_context(full_system, content, context, history, on_text)`. 각 `TextBlock` 도착마다 on_text가 즉시 발동 → `_split_message` 분할 후 전송. **하나의 논리적 답변이 여러 Discord 메시지로 나뉠 수 있음**.
11. **메모리 자동 추출** (:272-276) — `MEMORY_MODE=="auto"`면 `conversation = (history or []) + [{"role":"user","content":content}]`로 `memory_mgr.extract_and_save(llm, conversation)`. **추출 입력에 방금 생성된 어시스턴트 답변은 미포함** (history + 새 사용자 메시지만).

## 6. 데이터 소스 & 파이프라인

**라이브 DB 없음.** 런타임은 매 질의마다 Garmin API를 직접 호출해 인메모리 요약을 만든다. GarminDB/SQLite는 backup.sh 전용이며 라이브 컨텍스트가 아니다.

### A. Garmin Connect (라이브 API → preprocessor → LLM context)

```
Garmin Connect API  (python-garminconnect; 토큰 캐시 ~/.garminconnect/)
   │
   ▼  GarminConnectClient.get_sleep/get_daily_summary/get_hrv/get_activities/get_stress
   │  (7일 윈도우 week_ago..today)                              bot/main.py:173-178
   ▼  HealthPreprocessor.summarize_*() → 통계 요약               bot/main.py:180-188
   ▼  context dict ("default context", 항상 부착)
   ▼  llm.ask_with_context(system, user_msg, context, ...)      bot/main.py:265
```

### B. Body Metrics (3 writer → 1 CSV → 1 reader)

```
manual chat text --> body_metrics_parser(정규식)  --+
MCP tool call      -------------------------------  +--> BodyMetricsManager(CRUD)
Apple Health JSON  -> apple_health_reader.sync ---  +          │
                                                               ▼  data/inbody.csv (append/upsert)
                                          read_latest() -------> context["body_metrics"]  bot/main.py:190-192
```

### C. Apple Health (iCloud → reader → CSV → 자동분석) — 유일한 완전 자동 루프

```
Health Auto Export 앱(iPhone) -> HealthAutoExport-*.json을 iCloud Drive 폴더에 기록
   ▼ (iCloud가 Mac으로 동기화)
APPLE_HEALTH_EXPORT_DIR  (기본: ~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/daily inbody)
   ▼ health_sync_loop (discord.tasks, 2분마다)              bot/main.py:416-423
   ▼ sync_from_icloud(hae_dir, mgr)                          core/apple_health_reader.py:15
   │   JSON 파싱 → source=="InBody" & qty>0 필터 → inbody.csv upsert
   ▼ new_rows 반환(진짜 신규 (date,source) 키만)
   ▼ (new_rows 있으면) _run_auto_analysis → NOTIFY_CHANNEL_ID로 게시   bot/main.py:423
```

iOS 단축어 HTTP 경로(`docs/ios-shortcut-guide.md`)는 **미구현/향후 계획**. 오늘 동작하는 Apple Health 경로는 위 C의 iCloud JSON 2분 폴링뿐이다.

### data/inbody.csv 스키마

헤더: `date,weight_kg,body_fat_pct,muscle_mass_kg,bmi,source`

| 컬럼 | 타입 | 의미 |
|---|---|---|
| `date` | ISO YYYY-MM-DD | 측정일. `source`와 함께 dedup 키 |
| `weight_kg` | float | 체중 |
| `body_fat_pct` | float | 체지방률 |
| `muscle_mass_kg` | float | **Apple Health 경로는 Lean Body Mass(LBM), 골격근량(SMM) 아님** (reader.py:10 주석) |
| `bmi` | float | 0.0/빈값 가능 |
| `source` | enum | `manual`(기본), `inbody`, `unknown`(레거시), `apple_health`, `ios_shortcut`(미구현 의도) |

### "default context" dict 형태 (항상 포함)

`_collect_health_context()` (main.py:167-194)가 조립, `asyncio.to_thread`로 오프스레드(:197-199). garmin 클라이언트 초기화됐을 때만 Garmin 키가 채워지고, `body_metrics`는 `read_latest()` non-empty일 때만 포함된다.

```python
{
  "sleep": {"baseline_7d": {avg_total_hours, avg_score, min_hours, max_hours, avg_bedtime, trend},
            "last_night": {hours, score, efficiency_pct, deep_pct_delta, hrv_z,
                           avg_rr, awake_count, bedtime, sleep_insight}},  # last_night None 가능
  "heart_rate": {avg_rhr, min_rhr, max_rhr, trend},
  "hrv":        {avg_weekly, trend, status_distribution},
  "activities": {total_count, total_calories, total_distance, total_time_hours, by_sport},
  "stress":     {avg_stress, max_stress, min_stress, trend},
  "body_metrics": { inbody.csv 최근 행 }   # read_latest() non-empty일 때만
}
```

윈도우는 고정 7일(`week_ago = today - 7d`, main.py:170-171).

## 7. MCP 도구 카탈로그

3개의 인프로세스 SDK MCP 서버. Claude가 보는 정식명은 `mcp__<server>__<tool>`. Garmin 도구만 블로킹 API를 `asyncio.to_thread`로 감싸고(async), 체성분/메모리는 로컬 I/O라 동기 호출한다.

| 정식명 | 목적 | 동시성 |
|---|---|---|
| `mcp__garmin__get_sleep` | 수면 요약(단계/SpO2/호흡/점수) | async |
| `mcp__garmin__get_daily_summary` | RHR/HR/스트레스/걸음/거리/칼로리 | async |
| `mcp__garmin__get_hrv` | HRV weekly/last-night/baseline/status | async |
| `mcp__garmin__get_activities` | 기간 내 활동 목록(정규화 dict) | async |
| `mcp__garmin__get_stress` | 일별 평균 스트레스 | async |
| `mcp__garmin__get_activity_detail` | 종목 자동감지 상세(러닝 splits/웨이트 sets/수영 SWOLF 등) | async |
| `mcp__garmin__get_activity_splits` | lap distance/duration/HR/pace/elevation | async |
| `mcp__garmin__get_activity_hr_zones` | zone별 분 + zone % | async |
| `mcp__garmin__get_last_activity` | 최근 활동 빠른 조회(count 최대 10 캡) | async |
| `mcp__body_metrics__add_body_measurement` | 체성분 행 추가(측정 필드 ≥1 필수, source 기본 manual) | sync |
| `mcp__body_metrics__get_body_metrics_history` | 최근 N개/N일 내 이력(count 기본 10) | sync |
| `mcp__body_metrics__get_body_metrics_trend` | 한 필드 시계열(field 기본 weight_kg, days 30) | sync |
| `mcp__memory__list_memory` | 메모리 엔트리 나열(target ∈ memory/user) | sync |
| `mcp__memory__add_memory` | 엔트리 추가(빠른 append → 오버플로우 시 LLM 통합) | sync |
| `mcp__memory__replace_memory` | 엔트리 교체 | sync |
| `mcp__memory__remove_memory` | 엔트리 삭제 | sync |

서버 조립은 `bot/main.py:91-106`에서 이뤄진다: `mcp_servers={}`(:91)에 각 `create_*_mcp_server(...)`의 반환 객체를 name 키로 삽입 — `garmin`(Garmin 로그인 성공 시만, :93-94), `body_metrics`(:97-98), `memory`(:102-103). 이후 `create_llm_adapter(..., mcp_servers=mcp_servers or None, cwd=PROJECT_ROOT)`(:106)로 전달.

## 8. 프롬프트 & 메모리 시스템

### 최종 시스템 프롬프트 조립 (4파일)

`_build_system_prompt()` (main.py:216-225)이 매 질의마다 `\n\n`로 연결:

```
system.md (페르소나/규칙)              <- load_prompt("system.md")   :218
goals.md  (개인 목표)                  <- load_prompt("goals.md")    :218
[기억]\n{memory.md}       (non-empty시) <- memory_mgr.read_memory()   :219,221-222
[사용자 프로필]\n{user.md} (non-empty시) <- memory_mgr.read_user()    :220,223-224
```

- **hot-reload**: 4파일 모두 매 질의 fresh read(캐시 없음) → 봇 재시작 없이 즉시 반영.
- **주간 리포트 경로는 다른 축소 프롬프트**: system.md + goals.md만 사용(:306-308), memory/user 의도적 제외 → 자동분석 인사이트에는 영구 메모리 미포함.
- 런타임 데이터(7일 요약 + 대화 이력 + 질문)는 시스템 프롬프트가 아닌 **사용자 메시지**에 `ask_with_context`가 주입(llm.py:82-101).

### MEMORY vs USER 분리

| 파일 | 카테고리 | 내용 |
|---|---|---|
| `prompts/memory.md` | MEMORY | 환경 사실/건강 패턴/습관(프로필, 측정법, 매크로, 트레이닝 스플릿/PR, HR zone). char 캡 2200 |
| `prompts/user.md` | USER | 선호도/커뮤니케이션 스타일/기대(금지 약어, 호칭·톤, 표/체크리스트 선호). char 캡 1375 |

분리는 추출 프롬프트(memory.py:20-36)가 강제한다: 단일 LLM 패스가 `MEMORY:` / `USER:`(또는 `NONE`) 접두 라인을 방출하고 파일로 분기(프롬프트 강제, 스키마 강제는 아님).

### 추출 트리거 · 오버플로우 통합

- **트리거**: `MEMORY_MODE=="auto"`면 매 건강 질의 후 `extract_and_save`(memory.py:202-226). `manual` 모드는 추출을 완전 스킵 — 메모리 변경은 MCP 도구로만.
- **char 캡**: 하드 캡 truncate(_write_raw :119-123). 엔트리 개수 캡이 아니라 전체 문자 캡.
- **오버플로우 통합**(_save_or_consolidate :228-260): `_append` 시도 → 용량 초과면 실패 dict 반환 → `CONSOLIDATION_PROMPT`(:38-53)로 LLM이 기존+신규를 캡 내로 병합/압축 → 인젝션 체크 후 기록. LLM 실패/빈값/인젝션이면 엔트리 드롭 + 경고(설계상 조용한 데이터 손실).
- **MCP add 경로도 통합 경유**(커밋 ef23ebc): `add_memory`(memory_tools.py:39-58)가 먼저 LLM 없이 빠른 append → 오버플로우 시 `_save_or_consolidate(llm, ...)`. `llm`은 지연 바인딩(생성 시 None, main.py:109 주입); None이면 오버플로우는 실패 dict 반환.
- **인젝션 방어**: `_INJECTION_PATTERNS` 정규식(:12-18)이 "ignore previous / you are now / new instructions / override prompt" 등을 `_append`(:179), `replace_entry`(:145), 통합 출력(:256)에서 차단.

## 9. 스킬 시스템

`.claude/skills/` 아래 4개 디렉토리, 각 SKILL.md에 name/description/trigger frontmatter:

| 스킬 | 목적 |
|---|---|
| `activity-evaluation` | 종목별(러닝/웨이트/수영/하이킹·사이클) 운동 평가, 운동생리학 프레임워크 |
| `body-composition` | 체중/BF%/골격근/BMI/허리둘레 트렌드, 다이어트·증량·리컴프 페이즈, 측정 신뢰도 |
| `sleep-analysis` | 수면(TST/단계/효율/HRV) PSG 문헌 + wearable 검증 대비 평가, 트레이닝로드·시간생물학·LEA/REDs 통합 |
| `science-reference` | 권위 수치 컷오프(ACSM/AHA/WHO/NSCA/NSF-AASM/ISSN). 형제 스킬이 기준 인용하는 공유 참조 허브. references/ 하위에 hrv-detail.md 등 |

**자동발견 메커니즘**: `setting_sources=["user","project"]`가 **cwd 제공 시에만** 설정(llm.py:60-66). `cwd=PROJECT_ROOT`는 어댑터 생성 시 전달(main.py:106). "project" 포함 시 Agent SDK가 `<cwd>/.claude/skills/`를 스캔·각 SKILL.md 자동 등록, `Skill` 도구도 allowed_tools에 포함(:63-64). Claude가 name/description/trigger로 스킬을 인지하고 필요 시 바디를 온디맨드 로드한다. **cwd가 단일 마스터 스위치** — 없으면 skills/builtin-tools/setting_sources/bypassPermissions 모두 스킵.

## 10. 부트 시퀀스 & 배선

`bot/main.py`는 import 시점에 전 서브시스템을 순서대로 배선한다:

1. sys.path에 프로젝트 루트 삽입(:13) → `python3 bot/main.py` 실행 시 `core.*` import 해결.
2. `load_dotenv()`(:30) 후 env 로드(:33-48), `parse_allowed_users()`(:51-61) → `ALLOWED_USERS`(:64).
3. **데이터 소스**: `GarminConnectClient`는 두 크레덴셜 있을 때만 try/except로 생성 — 로그인 실패 시 `garmin=None`(:76-87). 로그인은 **eager**(생성 시 즉시). `BodyMetricsManager`는 항상 생성(:88).
4. **MCP 서버 dict 조립**(:91-103): garmin(있을 때만) → body_metrics → memory.
5. **LLM 어댑터 생성**(:106): `create_llm_adapter(..., mcp_servers, cwd=PROJECT_ROOT)`.
6. **순환 의존성 해소(핵심)**: `memory_mgr.llm = llm`을 어댑터 생성 **후** 주입(:109). 메모리 MCP 서버는 매니저를 필요로 하고(그래서 어댑터보다 먼저 생성돼야 함), 매니저는 오버플로우 통합에 LLM을 필요로 하지만 LLM은 MCP 서버 뒤에 만들어진다 — 이 late-bound 주입이 매듭을 푼다. 주입을 빠뜨리면 MCP `add_memory` 오버플로우가 통합 대신 조용히 실패.
7. `ContextCompressor()`, `SessionManager(idle_timeout_minutes=SESSION_IDLE_TIMEOUT)`(:112-113), `DiscordChannel(token=...)`(:116).

**왜 MCP-서버-먼저 순서인가**: 어댑터는 `mcp_servers` dict를 인자로 받으므로(스텝 5) 서버 객체들이 먼저 존재해야 한다. 반대로 메모리 매니저의 LLM은 어댑터가 있어야 채워지므로 6에서 역주입한다.

## 11. 자동화 & 스케줄

| 메커니즘 | 스케줄 | 내용 | 위치 |
|---|---|---|---|
| `health_sync_loop` (discord.tasks.loop) | 2분마다, 인프로세스 | `sync_from_icloud` → 새 행이면 `NOTIFY_CHANNEL_ID`로 자동분석 | bot/main.py:416-438 |
| `garmindb/sync.sh` | 제안 cron `0 6 * * *` (비자동) | Garmin 토큰 라이브니스 검사/만료 시 갱신 (데이터 sync 아님) | sync.sh:5; setup.sh:49-50 |
| `scripts/backup.sh` | 제안 cron `0 3 * * 0` (비자동) | GarminDB SQLite → SQL 덤프 + git commit | backup.sh:3; setup.sh:51-52 |

**2분 iCloud 루프만 진짜 자동**(on_ready에서 `is_running()` 가드로 시작, :436-438). 두 cron은 `setup.sh`가 라인만 출력·제안하며 설치하지 않는다 — 사용자가 수동 등록해야 한다.

## 12. 설정 (환경변수)

코드에서 실제로 읽는 변수만(모두 `bot/main.py`; core 모듈은 파라미터로 수령):

| 변수 | 목적 | 기본값 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Discord 봇 인증 | (필수) |
| `LLM_ADAPTER` | LLM 어댑터 선택 | `claude` |
| `LLM_MODEL` | 모델 id | (None → 어댑터 기본 `claude-sonnet-4-20250514`) |
| `GARMIN_USERNAME` / `GARMIN_PASSWORD` | Garmin 로그인 | (둘 다 없으면 garmin=None) |
| `MEMORY_MODE` | `auto`(대화 후 자동 추출) vs `manual`(MCP만) | `auto` |
| `SESSION_IDLE_TIMEOUT` | 세션 idle 분 | `1440` (24h) |
| `NOTIFY_CHANNEL_ID` | 자동분석 푸시 채널 | (미설정 → 자동분석 off) |
| `APPLE_HEALTH_EXPORT_DIR` | iCloud Health Auto Export 폴더 | `~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/daily inbody` |
| `ALLOWED_USERS` | 쉼표구분 Discord ID 화이트리스트 | (빈값 → 전원 무시, 안전 기본) |

## 13. 테스트

```bash
python3 -m pytest tests/ -v
```

모든 core/ 모듈에 대응 테스트가 존재한다(22개 파일, `pytest` + `pytest-asyncio`). 프롬프트 마크다운·스킬은 데이터라 테스트 없음.

| 영역 | 테스트 |
|---|---|
| Garmin | test_garmin_data.py, test_garmin_tools.py |
| 체성분 | test_body_metrics.py, test_body_metrics_parser.py, test_body_metrics_tools.py |
| Apple Health | test_apple_health_reader.py, test_auto_sync.py |
| LLM/채널 | test_llm.py, test_channel.py |
| 메모리 | test_memory.py, test_memory_tools.py, **test_memory_consolidation_on_add.py**, **test_memory_overflow_repro.py** |
| 세션/컨텍스트 | test_session_manager.py, test_context_compressor.py |
| 전처리/리포트 | test_preprocessor.py, test_report.py |
| 봇 통합 | test_main_async.py, test_main_auth.py, test_thread.py, test_image_attachment.py, test_integration_features.py |

**핵심 회귀 테스트**: `test_memory_consolidation_on_add.py` + `test_memory_overflow_repro.py`는 MCP `add_memory` → 오버플로우 통합 경로(커밋 ef23ebc)를 지킨다. Garmin 테스트는 `garminconnect.Garmin` mock, LLM 테스트는 `_call_claude` mock을 사용(실제 API 불필요).

## 14. 게처 & 비-자명 결정

각 항목은 변경 전에 알아야 할 함정이다.

- **라이브 DB 없음**: 런타임은 매 질의 Garmin API 직접 호출. GarminDB SQLite는 backup.sh 전용이고, `garmindb/sync.sh`는 이름과 달리 데이터 sync가 아니라 토큰 검증만 한다.
- **cwd = 스킬/도구 마스터 스위치**(llm.py:60-66): cwd 없으면 setting_sources/allowed_tools/bypassPermissions/스킬 자동발견이 전부 꺼진다.
- **late-bound LLM 주입**(main.py:109): 부트 순서 의존. 주입을 빠뜨리면 MCP `add_memory` 오버플로우 통합이 조용히 실패.
- **인메모리 세션 상태**(session_manager.py): 봇 재시작 시 모든 idle 타이머 소실 → 재시작 후 첫 후속은 항상 `is_expired→False`라 Discord에서 히스토리 재로드. 매니저는 히스토리를 지우지 않고, "새 세션"은 `history=None`으로 과거 로드를 스킵할 뿐.
- **스트리밍이 답변을 쪼갬**: 각 `TextBlock`이 on_text→send_reply→_split_message로 독립 재청킹 → 하나의 논리적 답변이 여러 Discord 메시지로 나뉜다. 아웃바운드 텍스트는 전부 `send_reply`(main.py:161) 단일 통로를 지나 2000자 제한을 일괄 강제.
- **줄바꿈 없는 2000자 초과 단일 라인**: `_split_message`(channel.py:60-73)가 2000에서 하드 컷(중간 잘림). 정상 텍스트는 마지막 `\n` 경계 선호.
- **Falsy 0.0 트랩**: `body_metrics.py`의 직렬화/`upsert_entry`는 `if x is None`으로 올바르게 처리하지만, **`BodyMetricsManager.get_trend`(:107)와 `report.py` 체성분 섹션(:67-74)은 truthy 가드라 정당한 0.0을 드롭**(사소 버그, CLAUDE.md 경고 미준수). MCP `get_body_metrics_trend`(:78)는 `val is not None`으로 올바름.
- **Apple Health muscle_mass = LBM ≠ SMM**(reader.py:10): `muscle_mass_kg`에 들어가는 값이 Lean Body Mass라 실제 골격근량보다 크다 — 하위 분석 시 유의.
- **Garmin Local timestamp**(garmin_data.py:27-39): tz offset이 이미 박혀 있어 UTC로 읽어 wall-clock을 복원 — 의도적. local tz로 "고치지" 말 것.
- **monthly_report 프로덕션 데드 코드**(report.py:78): 앱 배선 없음(호출은 test_report.py에만). 실제로는 `weekly_report`만 사용된다.
- **report.py는 bot/main.py 형태의 sleep sub-dict 필요**: 두 리포트가 `s['sleep']['baseline_7d']`를 읽는데(:10,81) 이 중첩 형태는 main.py:184에서만 생성된다. 생 `summarize_sleep()` 결과를 직접 먹이면 KeyError.
- **주간 리포트는 축소 프롬프트**: system.md+goals.md만, memory/user 제외(main.py:306-308) → 자동분석 인사이트에 영구 메모리 미반영.
- **iOS 단축어 HTTP 미구현**(ios-shortcut-guide.md:150): `/api/health`(포트 5000, SHORTCUT_API_TOKEN)는 문서화됐으나 `core/webhook.py`가 없다. 동작하는 경로는 iCloud JSON 2분 폴링뿐. 단축어를 Discord webhook에 자연어 텍스트로 게시하면 기존 NL 파서가 수용.
- **sync_from_icloud는 신규 행만 반환**(reader.py:50-51): 재실행 시 이미 본 `(date,source)`는 재분석 안 함(중복 알림 방지). 같은 날 수정값은 재알림되지 않는 부작용.
- **sync.sh 하드코딩 파이썬 경로**(:11): `/opt/homebrew/opt/python@3.11/bin/python3.11` — Apple Silicon/Homebrew 전용, Intel/Linux에서 깨짐.
- **setup.sh는 .env 없으면 첫 실행 exit 1**(:16-20): 크레덴셜 강제(의도적).
- **압축은 추가 LLM 왕복**: 히스토리 >20에서만 발동하며 별도 `llm.ask` 호출(추가 지연/토큰). 압축 결과의 system-role 요약 라인이 어댑터에서 "어시스턴트:"로 라벨되는 사소한 fidelity note(크래시 아님).
- **auto 모드 메모리 중복 아티팩트**: memory.md는 큐레이트 블록 + 기계-append 혼재. auto 추출이 기존 큐레이트 블록 대비 dedup을 안 해 같은 목표가 중복 누적될 수 있음(memory.md:27-33 실제 사례).
- **§ 구분자는 구조적**: memory.md/user.md는 자유 마크다운이 아니라 `\n§\n` split 대상. 수동 편집 시 구분자 보존 필수.
- **channel.py 일부 레거시**: `DiscordChannel.on_message`/`send`는 main.py가 미사용(자체 richer on_message를 `channel._client`에 직접 등록). ABC를 완전히 경유하지 않는다.
