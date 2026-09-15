# 아키텍처 (Architecture)

> README.md의 심화 동반 문서. 시스템을 처음 만지는 미래의 세션이 빠르게 올바른 멘탈 모델을 잡고 "어디에 뭐가 있는지"를 찾도록 돕는 지도. 설치·사용법은 README.md 참조.

## 1. 개요 (Overview)

ohrmin-claw는 Garmin Connect + 체성분 데이터 기반 개인 AI 건강 비서 Discord 봇이다. 요청 모델은 한 줄로 요약된다: **Discord 메시지 → 스레드 세션 → Claude(시스템 프롬프트 + 항상 포함되는 7일 건강 컨텍스트 + on-demand MCP 도구) → 스트리밍 응답**. 봇은 LLM과 메시징 채널을 각각 ABC로 추상화하며, 기본 백엔드(`claude`)에서는 Claude Agent SDK를 인프로세스 MCP 서버(Garmin / 체성분 / 메모리 / 세션 검색 / 스케줄)와 함께 구동한다. LLM 백엔드는 `config.json`의 `llm.backend`(`claude` · `codex` · `grok`)로 재시작 단위 선택하며, 도구·스킬·안전 게이트 판정은 백엔드와 무관하게 봇 프로세스가 소유한다(§3 LLM 어댑터 레이어, §7, §10). 라이브 DB는 없다 — Garmin 데이터는 매 질의마다 API로 직접 가져온다.

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
| `core/llm.py` | LLM 공통 계약 `LLMAdapter` + Claude 어댑터(스트리밍 · SDK 인프로세스 MCP priv/ro 세트 · 게이트 훅 · 스킬 모드) + 설정 기반 팩토리 | `LLMAdapter` (:86), `ClaudeSDKAdapter._build_options` (:310), `create_llm_adapter_from_config` (:618) |
| `core/llm_config.py` | `config.json` 로드·stdlib 검증 + deprecated `LLM_ADAPTER`/`LLM_MODEL` 이관 규칙 | `load_llm_config` (:127), `load_llm_config_safe` (:187) |
| `core/preflight.py` | 기동 전 백엔드별 구독 인증·CLI 설치 검사 (러너·fs·which 주입 가능) | `run_preflight` (:104) |
| `core/llm_errors.py` | `LLMError`(usage_limit/auth_expired/runtime_unavailable/generic) + 사용자 문구, `StartupError`, 실제 런타임 실행 가드 | `LLMError` (:68), `is_llm_error_reply` (:94), `forbid_real_runtime` (:116) |
| `core/observability.py` | stdout 로그 형식(기동 요약·UNVERIFIED WARN·게이트 결정·턴 결과) | `UNVERIFIED_SURFACES` (:17), `format_gate_decision` (:89) |
| `core/safety_gate.py` | 게이트 규칙 + 정규 도구 호출 + 단일 판정 | `evaluate_tool_gate` (:88), `CanonicalToolCall` (:129), `decide` (:157) |
| `core/gate_wiring.py` | 백엔드당 게이트 wiring 1개 + 기동 probe | `ClaudeHookWiring` (:49), `CodexHookWiring` (:115), `GrokApprovalWiring` (:208) |
| `core/hooks/codex_gate_hook.py` | Codex PreToolUse 훅 명령 — stdlib·3.9, 게이트 엔드포인트에 위임, fail-closed | `main` (:36) |
| `core/tool_server/` | transport 중립 도구 정의 + strict schema + `CallerCapability` + transport 2종 | `spec.ServerSpec` (:33), `schema.to_strict_json_schema` (:14), `capability.wrap_handler` (:26), `claude_sdk_bridge.to_sdk_servers` (:13), `http_server.SharedToolServer` (:130) |
| `core/runtimes/` | Codex·Grok 어댑터, 공용 JSON-RPC stdio 클라이언트, spawn 가드, 정규 도구명 변환 | `codex_adapter.CodexAdapter` (:165), `grok_adapter.GrokAdapter` (:197), `jsonrpc_stdio.JsonRpcClient` (:36), `tool_names.CAPABILITY_MATRIX` (:186) |
| `core/skill_registry.py` | 봇 측 스킬 카탈로그 + `skills` MCP 도구(registry 모드) | `SkillRegistry` (:64), `create_skills_mcp_server` (:135) |
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
| `config.json` | LLM 백엔드 설정(커밋 대상, 비밀값 없음) — `llm.backend` + 백엔드별 `model`/`bin`, `claude.skills` | — |

### LLM 어댑터 레이어 (멀티 백엔드)

`LLMAdapter`(core/llm.py:86)는 봇·core가 쓰는 표면 전체를 abstract로 고정한다: `start()`(런타임 기동·핸드셰이크·게이트 probe·기동 요약), `ask()`(유틸 호출 — 실패 시 `LLMError` raise), `ask_with_context(..., approve_skill_writes, allowed_tools, thread_id, image_paths)`(스트리밍 — 실패 시 예외 없이 오류 안내를 on_text 1회 + 반환), `interrupt_session` / `end_session`(멱등) / `close_all` / `session_ids` / `has_session`. 공통 불변식: on_text = 도구 호출 사이의 완결 텍스트 세그먼트, on_tool = 정규 도구명(도구 호출 시작당 1회, counter 동일 증가), `thread_id` 지정 = 세션 재사용 + 신규/재생성 턴에만 이력 folding(`_augment_message` :99), system prompt·권한(priv/ro) 변경 = 세션 재생성, steer = interrupt-then-restart, `max_turns` 도달 = 턴 중단 후 수집 텍스트 반환.

| backend | 어댑터 | 런타임 · 프로토콜 | 게이트 wiring | 도구 transport | 스킬 | 인증 |
|---|---|---|---|---|---|---|
| `claude` (기본) | `ClaudeSDKAdapter` (core/llm.py:179) | `claude_agent_sdk` — 스레드별 `ClaudeSDKClient` + one-shot `query()` | 인프로세스 PreToolUse 훅 | SDK 인프로세스 서버 priv/ro 세트 | `native`(기본) 또는 `registry` | `claude login` (claude.ai 구독) |
| `codex` | `CodexAdapter` (core/runtimes/codex_adapter.py:165) | `codex app-server` 프로세스 2개(priv/ro) + 자체 JSON-RPC stdio | 훅 스크립트 → `POST /t/<token>/gate` | `SharedToolServer` (HTTP) | registry | `codex login` (ChatGPT 구독) |
| `grok` | `GrokAdapter` (core/runtimes/grok_adapter.py:197) | `grok agent stdio` ACP 프로세스 1개, 격리 HOME | ACP `session/request_permission` 응답 + 사후 tripwire | `SharedToolServer` (HTTP) | registry | `XAI_API_KEY` (격리 HOME config.toml `env_key`) |

> **Codex·Grok은 공개 문서(Codex app-server README·hooks 문서, ACP 스키마, xAI Grok Build 문서) 기준으로 구현했고 문서 기반 fake(`tests/contract/fakes/`, `verified=false`)로만 검증됐다 — 실제 런타임 대상 live 검증 전(UNVERIFIED).** 해당 코드에는 `# provenance: <URL> verified=false` 주석이 있고, 기동 시 사용하는 표면마다 `[llm] WARN UNVERIFIED <surface-id> source=<url>`를 1줄씩 출력한다(목록 core/observability.py:17). claude `native`는 0줄, claude `registry`는 `claude.skills_filter` 1줄.

- **팩토리**: `create_llm_adapter_from_config(config, ...)`(core/llm.py:610)가 backend별 어댑터를 만든다(codex/grok 모듈은 지연 import). 다른 백엔드로의 자동 대체는 없다. 기존 `create_llm_adapter("claude", ...)`(:592)는 호환용으로 남아 있다.
- **오류**: `LLMErrorKind = usage_limit | auth_expired | runtime_unavailable | generic`(core/llm_errors.py:12). 사용자 문구는 백엔드명·해결 명령만 담고 원시 provider 문자열은 노출하지 않으며, generic은 기존 폴백 문구(`지금 데이터를 못 불러왔어요, …`) 그대로다. Claude 분류는 SDK 신호(`classify_claude_message` core/llm.py:66: `AssistantMessage.error`, `RateLimitEvent.status=="rejected"`), Codex(`turn/completed` 실패의 `codexErrorInfo`)·Grok(JSON-RPC 오류의 429/401 등)은 문서 기반 추정(UNVERIFIED). 런타임 프로세스 크래시 = 해당 프로세스 세션 전부 무효화 → 다음 호출에서 1회 재기동 → 실패 시 runtime_unavailable.
- **Codex 세부**: argv = `[bin, "app-server", -c approval_policy="never", -c sandbox_mode=<danger-full-access|read-only>, -c mcp_servers.<s>.url=<priv|ro 토큰 URL>(+tool_timeout_sec=120) × 서버, -c hooks.PreToolUse=[…], (-c model=…)]`(`build_argv` :216). 자식 env = 부모 env − `OPENAI_API_KEY`/`CODEX_API_KEY` + `OHRMIN_GATE_URL`(`child_env` :202). 핸드셰이크 `initialize` → `account/read`(API 키 인증 표시 = StartupError, 형태 미확인 시 WARN 후 진행) → 두 프로세스 기동 후 훅 probe(`start` :339). 권한 라우팅 = `approve_skill_writes is True` → priv 프로세스, 그 외 ro. system prompt = `thread/start.developerInstructions`(+미지원 도구 부록), per-thread `config`는 쓰지 않는다(openai/codex#45361 hang 회피). 턴: `item/started` → on_tool, `item/completed(agentMessage)` → on_text, `turn/completed` → 반환. interrupt = `turn/interrupt`, end_session = `thread/archive`.
- **Grok 세부**: argv = `[expanduser(bin), "agent", "stdio"]`, env = {HOME=격리, PATH, LANG, XAI_API_KEY}만, cwd = PROJECT_ROOT(`child_env` :243). `start()`(:368)가 `XAI_API_KEY` 확인 → 프로젝트 Claude 설정 검사(`check_project_claude_settings` :89) → 격리 HOME `config.toml` 재생성·`auth.json` 존재 시 거부(`_prepare_home` :257) → `initialize`(`agentCapabilities.mcpCapabilities.http` 필수) → permission probe. 세션마다 `session/new`에 봇 MCP 서버를 http(priv 또는 ro 토큰 URL)로 전달하고, system prompt는 ACP 필드 대신 첫 `session/prompt`의 `[시스템 지시]` 블록으로 보낸다. Grok 도구 표기는 `<server>__<tool>`이므로 런타임에 넘기는 프롬프트·스킬 본문을 `render_tool_refs`(core/runtimes/tool_names.py:162)로 렌더한다. 이미지 capability가 없으면 고정 안내 후 텍스트만 처리. interrupt = `session/cancel`, end_session = `session/close`(-32601이면 이후 생략).

## 4. 핵심 설계 패턴

- **어댑터 패턴 (LLM + 채널 ABC)** — `LLMAdapter`(core/llm.py:86)와 `MessagingChannel`(channel.py:8)이 각각 ABC. 봇은 ABC + 팩토리(`create_llm_adapter_from_config`)에만 의존한다. **왜**: 새 백엔드 = 어댑터 1개 + 팩토리 분기 1개 + 계약 스위트 fake 1개. 백엔드는 `config.json` `llm.backend`로 선택하며 전환은 재시작 단위(런타임 전환·자동 대체 없음).
- **공유 도구 계층 (ServerSpec 1벌 + transport 2종)** — 도구 모듈은 `core.tool_server.spec`의 `@tool`로 transport 중립 `ServerSpec`을 반환하고(핸들러 본문 불변), strict JSON Schema·None 인자 제거·`CallerCapability`(priv/ro) 래퍼는 공통이다. Claude는 `to_sdk_servers()`로 봇 프로세스 안의 SDK 인프로세스 서버(priv/ro 세트 2개), Codex/Grok은 `SharedToolServer`가 봇 이벤트 루프 안에서 127.0.0.1 임시 포트 streamable HTTP로 노출한다. **왜**: Python 객체(Garmin 클라이언트·CronStore·memory_mgr·session_index)를 클로저로 잡은 도구를 백엔드와 무관하게 한 프로세스에서 공유하고(별도 도구 프로세스 없음 → 상태 분기·Garmin 재로그인 없음), Claude 기본 경로의 transport는 그대로 둔다.
- **하이브리드 데이터 접근 (baseline 항상 + MCP on-demand)** — 최근 7일 preprocessor 요약은 항상 `context=`로 부착(llm.py:82-101), 깊은 데이터는 Claude가 필요할 때만 MCP 도구로 조회. **왜**: 토큰 절약과 분석 깊이를 동시에 확보.
- **스레드 = 세션** — 채널 첫 메시지는 스레드를 자동 생성(이름=첫 100자, main.py:252), 스레드 내 후속은 이전 대화 이력을 로드해 세션으로 이어감. **왜**: Discord 스레드를 자연스러운 대화 세션 경계로 사용.
- **컨텍스트 압축 (Hermes식)** — 히스토리 20개 초과 시 첫 1개 + 마지막 6개는 원본 보호, 중간 구간만 별도 LLM 호출로 요약 1개로 교체(context_compressor.py). **왜**: 긴 스레드에서도 토큰을 억제하되 최신·최초 맥락은 손실 없이 유지.
- **영구 메모리 (MEMORY vs USER)** — `memory.md`(환경 사실/건강 패턴)와 `user.md`(선호도/커뮤니케이션 스타일)를 분리, 매 질의 후 자동 추출(auto 모드). **왜**: 사실 레이어와 페르소나 튜닝 레이어를 분리해 각각 독립적으로 진화.
- **프롬프트 분리 · hot-reload** — system.md/goals.md/memory.md/user.md 4개를 매 질의마다 재읽기(캐시 없음, main.py:230). **왜**: 봇 재시작 없이 목표·페르소나·메모리 수정이 다음 메시지에 즉시 반영.
- **.claude/skills 로딩 (native / registry)** — native(claude 기본): `setting_sources=["user","project"]`가 `cwd` 제공 시에만 설정(`_build_options` core/llm.py:310). Agent SDK가 `<cwd>/.claude/skills/`를 스캔해 각 SKILL.md 자동 등록. registry(codex·grok 항상, claude는 `llm.claude.skills=registry`): 봇 `SkillRegistry`가 카탈로그를 system prompt에 넣고 `mcp__skills__load_skill`로 본문 제공(§9). **왜**: cwd 하나가 스킬/빌트인 도구/bypassPermissions/게이트 훅 전체의 마스터 스위치이고, registry는 백엔드 간 스킬 로딩 동일성을 계약 테스트로 증명하기 위한 경로. system.md는 스킬 존재만 언급하고 바디는 온디맨드 로드.
- **단일 게이트 판정 + 백엔드별 wiring 1개** — 판정은 `decide()`(core/safety_gate.py:157) 하나, 런타임 신호 연결은 백엔드당 정확히 1개(Claude 훅 / Codex 훅 스크립트 / Grok permission 응답), mutation MCP는 서버측 capability가 별도로 차단(§7 권한·안전 게이트). **왜**: 정책은 봇이 소유하고 런타임은 운반만 한다 — 미검증 런타임이 훅·승인을 발생시키지 않는 최악의 경우에도 mutation 도구는 막힌다.
- **Preflight fail-fast** — `main()`이 Discord 토큰 검사보다 먼저 설정·구독 인증을 검사하고, `setup_hook`에서 런타임 핸드셰이크·게이트 probe까지 통과해야 서비스한다. 실패 = 원인 + 해결 명령 출력 후 exit 1(§10). **왜**: 잘못된 백엔드·API 키 과금 경로·게이트 배선 오류로 조용히 동작하는 상황을 기동 시점에 차단.

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

5개 서버 22개 도구(+ registry 모드의 `skills` 서버 2개). 봇 코드·게이트·on_tool이 쓰는 정규명은 `mcp__<server>__<tool>`(Claude·Codex 표기와 동일, Grok 런타임 표기는 `<server>__<tool>`). Garmin·세션 검색 도구는 블로킹 호출을 `asyncio.to_thread`로 감싸고(async), 체성분/메모리/스케줄/스킬은 로컬 I/O라 동기 호출한다.

모든 도구는 `ToolSpec`(core/tool_server/spec.py:25)으로 정의되고 스키마는 strict JSON Schema(core/tool_server/schema.py:14)다 — 전 키 `required` + `additionalProperties: false`, 선택 파라미터는 `[T, "null"]`. non-nullable(`required=True`)은 가드 없이 `args["x"]`로 읽는 파라미터(`activity_id` 3곳, skills 도구의 `name`/`path`)뿐이며, null/누락이면 핸들러 호출 전 `Input validation error` 결과를 돌려준다. None 값 키는 핸들러 호출 전에 제거되어 기존 `args.get(k, default)` 의미가 유지된다. Codex/Grok HTTP 경로는 mcp SDK의 스키마 검증을 끄고(`call_tool(validate_input=False)`) 선택 키 생략을 허용하며, 필수 키 누락·null 확인은 `wrap_handler`가 같은 `Input validation error` 결과로 한다. 이름 규칙: 서버·도구 `^[a-zA-Z0-9_-]+$`, `mcp__<s>__<t>`·`<s>__<t>` 모두 ≤64자(위반 시 기동 시 `ValueError`).

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
| `mcp__session_search__search` | 과거 대화 FTS5 전문 검색(bm25, limit 기본 10·최대 50) | async |
| `mcp__schedule__schedule_create` | 예약/반복 작업 생성(5필드 cron 또는 `30m`/`2h`/`1d` one-shot, 상한 `MAX_CRON_JOBS`) | sync |
| `mcp__schedule__schedule_list` | 등록된 스케줄 목록 | sync |
| `mcp__schedule__schedule_pause` | 스케줄 일시정지 | sync |
| `mcp__schedule__schedule_resume` | 일시정지 스케줄 재개 | sync |
| `mcp__schedule__schedule_remove` | 스케줄 삭제 | sync |
| `mcp__skills__load_skill` | (registry 모드) 스킬 본문 — frontmatter 제외, 백엔드 도구 표기 렌더 | sync |
| `mcp__skills__read_skill_file` | (registry 모드) 스킬 디렉터리 안 참조 파일(절대경로·`..`·디렉터리 밖 거부) | sync |

서버 조립은 `bot/main.py:145-178`에서 이뤄진다: `server_specs=[]`(:145)에 각 `create_*_mcp_server(...)`가 반환한 `ServerSpec`을 추가 — `garmin`(Garmin 로그인 성공 시만), `body_metrics`, `memory`, `session_search`, `schedule`, 그리고 `_uses_skill_registry()`(:171)일 때만 `skills`(:177-178). 이후 `build_llm_and_tool_server(LLM_CONFIG, server_specs)`(:181)가 transport를 고른다:

- **claude**: `to_sdk_servers(specs, PRIVILEGED)` / `to_sdk_servers(specs, READ_ONLY)`(core/tool_server/claude_sdk_bridge.py:13)로 SDK 인프로세스 서버 세트 2개 → `ClaudeSDKAdapter(mcp_servers=priv, readonly_mcp_servers=ro)`. 턴마다 `approve_skill_writes is True`면 priv 세트, 그 외 ro 세트를 옵션에 싣는다(`_build_options` core/llm.py:310). `tool_server=None`.
- **codex / grok**: `SharedToolServer(specs, backend)`(core/tool_server/http_server.py:130) — 서버 × capability마다 mcp lowlevel `Server` + `StreamableHTTPSessionManager`, Starlette 경로 `/t/{token}/mcp/{server}`(per-boot 토큰 priv/ro 2개, 미등록 토큰 404) + `POST /t/{token}/gate`(Codex 훅 판정), Host `127.0.0.1:*`만 허용, uvicorn 시그널 캡처 비활성. 생성만 import 시점에 하고 **기동은 `setup_hook`**(§10), 종료는 `_close_with_cleanup`에서 `llm.close_all()` 다음.

### 권한 · 안전 게이트

판정은 `decide(call, privileged, runtime_guard)`(core/safety_gate.py:157) 하나다. 입력은 정규 도구 어휘로 정규화된 `CanonicalToolCall`(:129)이고, 권한 라우팅 키는 `approve_skill_writes is True` — 인터랙티브 오너 턴(`handle_health_query`)만 True이며 cron·자동분석·주간 리포트·압축/메모리 추출 `ask`는 비특권(ro)이다. 규칙(`evaluate_tool_gate` :88)은 기존 그대로: science-reference 스킬 쓰기는 무조건 deny, 그 외 `.claude/skills/**` 쓰기는 priv만, ro 턴은 `Bash`·`Write`·`Edit`·`MultiEdit`·`NotebookEdit`과 mutation MCP 7종(`_MUTATION_MCP_TOOLS` :60 — schedule create/pause/resume/remove, memory add/replace/remove) deny.

| 층 | Claude | Codex | Grok |
|---|---|---|---|
| ① 서버측 capability | ro SDK 서버 세트 | ro 토큰 MCP URL | ro 토큰 MCP URL |
| ② wiring (백엔드당 1개) | PreToolUse `HookMatcher` 2개 → `ClaudeHookWiring.make_hook` (core/gate_wiring.py:60), `runtime_guard=False` | `-c hooks.PreToolUse` command = `[sys.executable, core/hooks/codex_gate_hook.py]` → `POST $OHRMIN_GATE_URL` → `_GateEndpoint` (core/tool_server/http_server.py:103), `runtime_guard=True` | `permission_mode="ask"` → `session/request_permission` → `GrokApprovalWiring.permission_response` (core/gate_wiring.py:268), `runtime_guard=True` |
| ③ 런타임 보조 | Claude CLI의 `.claude/` 쓰기 네이티브 가드 | ro 프로세스 `sandbox_mode="read-only"` | 없음 — permission 요청 없이 deny 대상 빌트인 실행이 관측되면 사후 tripwire(`session/cancel` + generic) |
| 기동 probe | 훅 콜백에 합성 Bash (ro=deny, priv=allow) | 런타임과 같은 argv/env로 훅 스크립트 실행 (ro=exit 2 + deny JSON, priv=exit 0) | 합성 permission 요청 (ro=reject_once, priv=allow_once) |

- ①은 `wrap_handler`(core/tool_server/capability.py:26)가 핸들러 호출 전에 판정해 deny면 `{"success": false, "error": <reason>, "denied_by": "capability"}`를 반환한다. 훅·permission 요청이 발생하지 않아도 mutation MCP는 막힌다(`tests/contract/test_contract_pessimistic.py`).
- Codex 훅 매처는 `CODEX_HOOK_MATCHER`(core/gate_wiring.py:100, Bash·apply_patch·Write·Edit·MultiEdit·NotebookEdit·mutation MCP 7종). `apply_patch`는 `*** Add File:`/`Update File:`/`Delete File:`/`Move to:` 헤더 경로 전부로 판정하고, 경로 추출에 실패하면(Grok edit/delete/move에 경로가 없는 경우, `runtime_guard=True`에서 Write류에 경로가 없는 경우 포함) priv여도 deny. 게이트 엔드포인트는 payload가 `Unknown`(도구명 없음)으로 정규화되면 priv·ro 모두 deny한다. 훅 스크립트는 예외·타임아웃(5s)·비200·잘못된 JSON 모두 deny + exit 2.
- Grok permission 응답은 allow면 `allow_once`만 고른다(`allow_always`는 이후 요청 없이 허용될 수 있어 선택하지 않음 — `allow_once`가 없으면 deny 로그 + `cancelled`), deny면 `reject_once` > `reject_always`. `tool_call`/`tool_call_update`는 알림 수신 즉시(read-loop 순서) 병합하므로 뒤이은 permission 요청은 스트림 소비 지연과 무관하게 병합된 kind·locations·rawInput으로 판정된다.
- `runtime_guard=True`(Codex/Grok): Write류 경로 세그먼트에 `.claude`가 있으면 deny(`.agent-made/<이름>/`로 안내) — Claude CLI 가드의 구조화 쓰기 부분만 에뮬레이션하며 셸 문자열 우회는 다루지 않는다.
- probe는 wiring 진입점과 엔드포인트만 증명한다. **Codex가 `approval_policy=never`에서 실제로 훅을 호출하는지, Grok이 모든 도구에 permission을 요청하는지는 live 미검증(UNVERIFIED)**이며 Grok ro 턴 빌트인에는 실행 전 강제 수단이 없다.
- 결정 로그(stdout): `[gate] backend=<b> cap=<priv|ro> tool=<canonical> decision=<allow|deny> via=<hook|approval|capability|tripwire|probe> reason=<…>[ executed=likely]`(core/observability.py:89). capability 층은 deny만 기록한다.
- 빌트인 비대칭: `CAPABILITY_MATRIX`(core/runtimes/tool_names.py:186) — Codex는 WebFetch가 없고 Read/Glob/Grep이 셸이라 ro 턴에서 쓸 수 없다. Codex/Grok system prompt 말미에 `backend_tool_note`(:209)로 안내한다. `allowed_tools`(예: `UNATTENDED_ALLOWED_TOOLS` bot/main.py:231)는 스티어링 전용이며 Codex/Grok에는 전달하지 않는다(`translate_allowed_tools` :229).

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

로딩 모드는 백엔드·설정에 따라 2종이다(`LLMConfig.skills_mode` core/llm_config.py:59 — codex/grok은 항상 `registry`, claude는 `llm.claude.skills`, 기본 `native`).

**native 모드 (claude 기본 — 자동발견)**: `setting_sources=["user","project"]`가 **cwd 제공 시에만** 설정(`_build_options` core/llm.py:310). `cwd=PROJECT_ROOT`는 어댑터 생성 시 전달(bot/main.py:181-209). "project" 포함 시 Agent SDK가 `<cwd>/.claude/skills/`를 스캔·각 SKILL.md 자동 등록, `Skill` 도구도 allowed_tools에 포함. Claude가 name/description/trigger로 스킬을 인지하고 필요 시 바디를 온디맨드 로드한다. **cwd가 단일 마스터 스위치** — 없으면 skills/builtin-tools/setting_sources/bypassPermissions/게이트 훅 모두 스킵.

**registry 모드 (codex·grok 항상, claude는 `skills=registry`)**: `SkillRegistry`(core/skill_registry.py:64)가 매 턴 `.claude/skills/*/SKILL.md`(심링크 추적)의 frontmatter `name`/`description`을 스캔해 `catalog_prompt()`(:93)로 `[전문 분석 스킬]` 블록을 만들고, `_build_system_prompt`(bot/main.py:430)가 goals.md 뒤·메모리 블록 앞에 삽입한다(백엔드 무관 바이트 동일). 본문은 `skills` 서버 도구 `load_skill`(frontmatter 제외, Grok은 도구 표기 렌더)·`read_skill_file`로 제공하며, on_tool에는 `Skill`로 표기된다. Claude registry는 추가로 `setting_sources=["project"]`, `skills=[]`(네이티브 목록 억제), allowed_tools의 `Skill` → `mcp__skills`. `skills=[]`를 CLI가 실제로 적용하는지는 미검증이라 기동 시 `WARN UNVERIFIED claude.skills_filter`를 출력한다. `core/skill_sync.py`·`core/learning.py`(스킬 저장·학습 루프)는 모드와 무관하게 동일.

## 10. 부트 시퀀스 & 배선

`bot/main.py`는 import 시점에 전 서브시스템을 순서대로 배선한다(**import 시점에는 exit하지 않는다** — 테스트가 `bot.main`을 import하므로 설정 오류는 `main()`이 처리):

1. sys.path에 프로젝트 루트 삽입(:14) → `python3 bot/main.py` 실행 시 `core.*` import 해결.
2. `load_dotenv()`(:50) 후 env 로드(:53-96), `LLM_CONFIG, LLM_CONFIG_ERROR = load_llm_config_safe(CONFIG_PATH, os.environ)`(:61) — `config.json` 검증 + deprecated `LLM_ADAPTER`/`LLM_MODEL` 이관, WARN 출력. 오류는 `LLM_CONFIG_ERROR`에 보관.
3. **데이터 소스**: `GarminConnectClient`는 두 크레덴셜 있을 때만 try/except로 생성 — 로그인 실패 시 `garmin=None`(:124-135). 로그인은 **eager**(생성 시 즉시). `BodyMetricsManager`·`SessionIndex`·`CronStore`는 항상 생성(:136-142).
4. **ServerSpec 목록 조립**(:145-178): garmin(있을 때만) → body_metrics → memory → session_search → schedule → (registry 모드) skills.
5. **transport + LLM 어댑터 생성**(`build_llm_and_tool_server` :181, 호출 :203-209): claude = SDK 인프로세스 priv/ro 세트 + `ClaudeSDKAdapter`, codex/grok = `SharedToolServer` **생성만**(기동 X) + 어댑터. `StartupError`면 `LLM_CONFIG_ERROR`에 기록하고 `llm=None`.
6. **순환 의존성 해소(핵심)**: `memory_mgr.llm = llm`을 어댑터 생성 **후** 주입(:212). 메모리 MCP 서버는 매니저를 필요로 하고(그래서 어댑터보다 먼저 생성돼야 함), 매니저는 오버플로우 통합에 LLM을 필요로 하지만 LLM은 MCP 서버 뒤에 만들어진다 — 이 late-bound 주입이 매듭을 푼다. 주입을 빠뜨리면 MCP `add_memory` 오버플로우가 통합 대신 조용히 실패. (중첩 `ask`는 비특권이라 Codex에서는 ro 프로세스로 가서 priv 턴과 교착하지 않는다.)
7. `ContextCompressor()`, `SessionManager(idle_timeout_minutes=SESSION_IDLE_TIMEOUT)`(:215-216), `DiscordChannel(token=...)`(:219).
8. `channel._client.close = _close_with_cleanup`(:1116 — `llm.close_all()` → `tool_server.stop()` → 원래 close), `channel._client.setup_hook = _llm_setup_hook`(:1143).

**왜 MCP-서버-먼저 순서인가**: 어댑터·`SharedToolServer`는 `ServerSpec` 목록을 인자로 받으므로(스텝 5) 서버 정의가 먼저 존재해야 한다. 반대로 메모리 매니저의 LLM은 어댑터가 있어야 채워지므로 6에서 역주입한다.

### 기동 순서 (`main()` :1146 → `setup_hook`)

1. `LLM_CONFIG_ERROR`가 있으면 `❌ [LLM] <원인>` / `   해결: <명령>` 출력 후 exit 1.
2. `run_preflight(LLM_CONFIG, os.environ, runner=PREFLIGHT_RUNNER, fs=os.path)`(:1152) — 실패 시 같은 형식으로 exit 1. **Discord 토큰 검사(:1156)보다 먼저** 실행된다.
3. `DISCORD_BOT_TOKEN`·`ALLOWED_USERS` 확인 → `channel.run()`(:1164).
4. Discord 로그인 직후 `_llm_setup_hook`(:1128): `tool_server.start()`(codex/grok만 — 127.0.0.1:0 바인드, 10s 안에 기동 못 하면 StartupError) → `await llm.start()`(런타임 기동·핸드셰이크·게이트 probe → `[llm] startup backend=… model=… skills=… gate=… tools=… tool_server=<off|127.0.0.1:port> auth=…` + `[llm] WARN UNVERIFIED …`). 예외 → 원인 출력 + `_STARTUP_FAILED=True` + 클라이언트 close → `channel.run()` 복귀 후 exit 1(:1165). 다른 백엔드로 대체하지 않는다.

preflight 검사(core/preflight.py, 러너·fs·which 주입 가능):

| backend | 검사 | 해결 안내 |
|---|---|---|
| claude | `ANTHROPIC_API_KEY` 설정 → 거부. CLI(SDK 번들 → PATH `claude`)로 `auth status --json` — 비정상 종료·JSON 오류·`loggedIn`≠true → 실패, `authMethod`≠`claude.ai` 또는 `apiProvider`≠`firstParty` → 실패 | `claude login` |
| codex | `OPENAI_API_KEY`/`CODEX_API_KEY` 설정 → 거부. `llm.codex.bin` 해석 실패 → 실패. `$CODEX_HOME/auth.json`(기본 `~/.codex/auth.json`) 부재 → 실패 | `codex login` |
| grok | `XAI_API_KEY` 없음 → 실패. `llm.grok.bin` 부재 → 실패 (`llm.grok.model` null은 config 검증 단계에서 거부) | `.env에 XAI_API_KEY=... 추가` |

codex `auth.json` 파일 검사는 keyring 저장 모드에서 오판할 수 있고, CLI 설치 안내 문자열도 live 미확인이다.

## 11. 자동화 & 스케줄

| 메커니즘 | 스케줄 | 내용 | 위치 |
|---|---|---|---|
| `health_sync_loop` (discord.tasks.loop) | 2분마다, 인프로세스 | `sync_from_icloud` → 새 행이면 `NOTIFY_CHANNEL_ID`로 자동분석 | bot/main.py:416-438 |
| `garmindb/sync.sh` | 제안 cron `0 6 * * *` (비자동) | Garmin 토큰 라이브니스 검사/만료 시 갱신 (데이터 sync 아님) | sync.sh:5; setup.sh:49-50 |
| `scripts/backup.sh` | 제안 cron `0 3 * * 0` (비자동) | GarminDB SQLite → SQL 덤프 + git commit | backup.sh:3; setup.sh:51-52 |

**2분 iCloud 루프만 진짜 자동**(on_ready에서 `is_running()` 가드로 시작, :436-438). 두 cron은 `setup.sh`가 라인만 출력·제안하며 설치하지 않는다 — 사용자가 수동 등록해야 한다.

## 12. 설정 (환경변수)

LLM 백엔드 설정은 env가 아니라 `config.json`(커밋 대상, 비밀값 없음)이다. 검증은 stdlib(`load_llm_config` core/llm_config.py:127): 최상위 키는 `llm`만, 섹션별 허용 키 외·타입 위반·JSON 오류 → exit 1, 파일 부재 → 기본값 + WARN.

| 키 | 목적 | 기본값 |
|---|---|---|
| `llm.backend` | `claude` \| `codex` \| `grok` (재시작 단위 전환) | `claude` |
| `llm.claude.model` | Claude 모델 (null → `LLM_MODEL` → `claude-sonnet-4-20250514`) | `null` |
| `llm.claude.skills` | `native`(CLI 네이티브 스킬) \| `registry`(봇 SkillRegistry) | `native` |
| `llm.codex.model` / `llm.codex.bin` | Codex 모델(null = Codex 기본) / CLI 경로·이름 | `null` / `codex` |
| `llm.grok.model` / `llm.grok.bin` | Grok 모델(**backend=grok이면 필수**) / CLI 경로 | `null` / `~/.grok/bin/grok` |

env는 코드에서 실제로 읽는 변수만(대부분 `bot/main.py`; core 모듈은 파라미터로 수령, LLM 인증 관련은 `core/preflight.py`·어댑터가 `os.environ`에서 읽음):

| 변수 | 목적 | 기본값 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Discord 봇 인증 | (필수) |
| `XAI_API_KEY` | Grok 인증 (backend=grok 전용, Grok 자식 프로세스에만 전달) | (grok이면 필수) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `CODEX_API_KEY` | 설정돼 있으면 claude / codex 백엔드 **기동 거부**(구독 로그인만 허용). Codex 자식 env에서는 제거 | (미설정) |
| `CODEX_HOME` | preflight의 Codex `auth.json` 위치 | `~/.codex` |
| `LLM_ADAPTER` | **deprecated** — `llm.backend`와 같으면 WARN, 다르면 exit 1 | (미설정 권장) |
| `LLM_MODEL` | **deprecated** — backend=claude에서 `llm.claude.model`이 null일 때만 사용(WARN), 둘 다 설정·불일치면 exit 1, codex/grok에서는 WARN 후 무시 | (미설정 권장) |
| `OHRMIN_FORBID_REAL_RUNTIMES` | `1`이면 실제 런타임 실행 금지(테스트 전용 가드) | (미설정) |
| `GARMIN_USERNAME` / `GARMIN_PASSWORD` | Garmin 로그인 | (둘 다 없으면 garmin=None) |
| `MEMORY_MODE` | `auto`(대화 후 자동 추출) vs `manual`(MCP만) | `auto` |
| `SESSION_IDLE_TIMEOUT` | 세션 idle 분 | `1440` (24h) |
| `NOTIFY_CHANNEL_ID` | 자동분석 푸시 채널 | (미설정 → 자동분석 off) |
| `APPLE_HEALTH_EXPORT_DIR` | iCloud Health Auto Export 폴더 | `~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/daily inbody` |
| `ALLOWED_USERS` | 쉼표구분 Discord ID 화이트리스트 | (빈값 → 전원 무시, 안전 기본) |

## 13. 테스트

```bash
python3 -m pytest tests/ -v
OHRMIN_FORBID_REAL_RUNTIMES=1 python3 -m pytest tests/contract -v           # 백엔드 공통 계약 스위트
OHRMIN_FORBID_REAL_RUNTIMES=1 python3 -m pytest tests/contract -v -k grok   # 백엔드 하나만
```

모든 core/ 모듈에 대응 테스트가 존재한다(`tests/test_*.py` 53개 + `tests/contract/` 9개, `pytest` + `pytest-asyncio`). 프롬프트 마크다운·스킬은 데이터라 테스트 없음.

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
| LLM 설정·기동·오류 | test_llm_config.py, test_preflight.py, test_llm_errors.py, test_memory_error_safety.py, test_observability.py |
| 공유 도구 계층 | test_tool_spec.py, test_caller_capability.py, test_claude_sdk_bridge.py, test_shared_tool_server_http.py(루프백 uvicorn + mcp 클라이언트) |
| 게이트·도구명·스킬 | test_safety_gate.py, test_codex_gate_hook_script.py(서브프로세스·3.9 문법·stdlib), test_tool_names.py, test_skill_registry.py, test_claude_skills_mode.py |
| Codex·Grok 어댑터 | test_jsonrpc_stdio.py, test_codex_adapter.py, test_grok_adapter.py |
| 계약 스위트 (`tests/contract/`) | test_contract_tools / skills / gate / pessimistic / session / unattended / errors / bot_paths / observability |

**핵심 회귀 테스트**: `test_memory_consolidation_on_add.py` + `test_memory_overflow_repro.py`는 MCP `add_memory` → 오버플로우 통합 경로(커밋 ef23ebc)를 지킨다. Garmin 테스트는 `garminconnect.Garmin` mock, LLM 테스트는 `_call_claude` mock을 사용(실제 API 불필요).

**계약 스위트**: `backend` 파라미터 = `claude_native` · `claude_registry` · `codex` · `grok`(tests/contract/conftest.py). 같은 시나리오(`scenario.py` DSL)를 백엔드별 fake 하니스로 돌려 on_text 순서·정규 on_tool·counter·게이트 결정/사유·반환 문자열·로그를 비교한다. Claude fake는 SDK 주입 seam(`query_fn`/`client_factory`) + CLI `.claude` 가드 모사, Codex·Grok fake는 **문서 기반** app-server / ACP 피어(`verified=false`)이며 실제 `SharedToolServer`와 훅 스크립트를 사용한다. `@pytest.mark.ac16`은 `claude_native`를 생성하지 않고, `@pytest.mark.backends(...)`는 지정 백엔드만 생성한다(`test_contract_pessimistic.py` = codex·grok: 훅/permission 미발생 시에도 capability deny·tripwire). fake 통과는 문서상 프로토콜과의 정합만 보장하며 실제 런타임 동작은 보장하지 않는다.

**실제 런타임 실행 가드**: `OHRMIN_FORBID_REAL_RUNTIMES=1`이면 preflight 기본 러너, patch되지 않은 SDK `query`/`ClaudeSDKClient`, `core/runtimes/process.py` spawn(현재 인터프리터 `sys.executable` 자식만 허용)이 `RealRuntimeForbidden`을 던진다. 어댑터 테스트는 `transport_factory` seam으로 in-memory transport를 주입한다.

## 14. 게처 & 비-자명 결정

각 항목은 변경 전에 알아야 할 함정이다.

- **라이브 DB 없음**: 런타임은 매 질의 Garmin API 직접 호출. GarminDB SQLite는 backup.sh 전용이고, `garmindb/sync.sh`는 이름과 달리 데이터 sync가 아니라 토큰 검증만 한다.
- **cwd = 스킬/도구/게이트 마스터 스위치**(`_build_options` core/llm.py:310): Claude 어댑터에 cwd가 없으면 setting_sources/allowed_tools/bypassPermissions/스킬 자동발견과 **PreToolUse 게이트 훅**이 전부 꺼진다(ro SDK 서버 세트의 capability 차단은 유지). 봇은 항상 `cwd=PROJECT_ROOT`를 넘긴다.
- **late-bound LLM 주입**(bot/main.py:212): 부트 순서 의존. 주입을 빠뜨리면 MCP `add_memory` 오버플로우 통합이 조용히 실패.
- **import 시점 설정 오류는 exit하지 않음**: `config.json` 오류·어댑터 생성 실패는 `LLM_CONFIG_ERROR`에만 기록되고 `llm=None`인 채로 import가 끝난다(테스트가 `bot.main`을 import). exit 1은 `main()`에서만 — 새 초기화 코드에서 `llm`이 None일 수 있음을 가정할 것.
- **유틸 `ask`는 raise**: `llm.ask`는 실패 시 폴백 문자열 대신 `LLMError`를 raise한다(오류 문구가 요약/통합 결과로 memory.md를 덮어쓰던 버그 수정). `core/memory.py`는 기존 `except Exception`으로 흡수(파일 불변), `core/context_compressor.py:68`은 `except LLMError`로 원본 이력 반환. 스트리밍 턴의 성공 판정은 `is_llm_error_reply()`(bot/main.py `turn_ok`).
- **Codex 훅 스크립트 제약**: `core/hooks/codex_gate_hook.py`는 stdlib만 · Python 3.9 문법 · `core` import 금지 · 패키지 `__init__` 없음. 외부 런타임이 실행하는 fail-closed 경계라서 방어적으로 유지한다(`tests/test_codex_gate_hook_script.py`가 `ast.parse(feature_version=(3, 9))`·import 목록 검사).
- **Codex app-server 2개**: priv/ro 권한 분할로 프로세스가 2개 뜬다(8GB 메모리 부담, one-shot `ask` thread는 archive하지 않음). 사용자 `~/.codex/config.toml`은 수정하지 않고 `-c` 오버라이드만 쓰므로 사용자 전역 Codex 설정(MCP 서버·훅 등)이 함께 적용될 수 있다(차단하지 않음, `CODEX_HOME`도 재지정하지 않음). ro 턴은 셸 기반 Read/Glob/Grep이 Bash 게이트에 막힌다.
- **Grok 격리 HOME · 프로젝트 Claude 설정**: HOME = `data/runtime/grok-home`(`data/`는 gitignore). `start()`마다 `.grok/config.toml`을 덮어쓰고, 격리 HOME에 `auth.json`이 생기면 기동 거부. Grok은 프로젝트 `CLAUDE.md`·`.claude/` 설정을 함께 읽으므로 `.claude/settings.json`·`settings.local.json`에 `hooks`가 있거나 `.mcp.json`이 있으면 기동 거부, 게이트 대상 `permissions.allow` 규칙(`Bash`·`Write`·`mcp__schedule__` 등 접두)은 `WARN UNVERIFIED grok.project_claude_permissions`만 출력하고 진행한다.
- **Grok 텍스트 분할**: `agent_message_chunk`를 버퍼링해 tool_call·턴 종료 시 flush하지만, 청크가 1초(`_TEXT_IDLE_FLUSH`) 끊겨도 flush → 하나의 답변이 Discord 메시지 여러 개로 더 잘게 나뉠 수 있다.
- **strict schema와 선택 키 생략**: 도구 스키마는 전 키 `required`지만 Codex/Grok HTTP 경로는 SDK 입력 검증을 끄므로(`validate_input=False`) 런타임이 선택 키를 생략해도 호출된다. 대신 타입·추가 키 검증도 하지 않으며 필수 키만 `wrap_handler`가 확인한다(live 미확인).
- **라이브 Claude 스레드의 옵션 고정**: 스레드별 `ClaudeSDKClient`는 connect 시점의 `max_turns`·`allowed_tools`를 유지한다. system prompt나 권한(priv/ro)이 바뀔 때만 재접속한다.
- **UNVERIFIED 표면**: codex/grok 기동 시 `[llm] WARN UNVERIFIED …`가 surface마다 출력되는 것이 정상이다. 대표 미검증 가정 — Codex: `-c hooks.PreToolUse` 적용·`approval_policy=never` 하 훅 발화·훅 payload 형태·훅의 env 상속, `developerInstructions`, `-c mcp_servers.*.url`, item 타입·`codexErrorInfo`·`account/read` 형태, `thread/archive`. Grok: http MCP·permission 요청 발생·`session/cancel` 후 재프롬프트·격리 HOME 자격 적용·tool_call/오류 형태·이미지·`[시스템 지시]` 블록 반영·`session/close`. live 확인 전까지 codex/grok 운영 사용은 비권장.
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
