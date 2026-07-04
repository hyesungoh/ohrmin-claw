# 시스템 진화 로드맵 (System Evolution Roadmap)

> Nous Research **Hermes Agent** 프레임워크와의 심층 비교를 기반으로, ohrmin-claw의 **워크플로우 아키텍처를 어느 방향으로 진화시킬지** 정리한 문서. `docs/ARCHITECTURE.md`가 "지금 어떻게 생겼나"라면, 이 문서는 "앞으로 어디로 갈까"다. 특정 대화 세션의 단편적 버그 수정이 아니라 **시스템 수준의 도약**에 초점을 둔다.

---

## 0. 배경 — 왜 Hermes를 봤나

Hermes Agent는 Nous Research의 오픈소스 "self-improving" 에이전트 프레임워크다. 그 본질은 단순 응답기가 아니라 **① 선제적이고, ② 도구 접근이 넓고, ③ 스스로 절차·기억을 학습·큐레이트하며, ④ 긴 자율 실행 중에도 조종 가능한** 에이전트라는 데 있다.

**우리는 이미 절반쯤 Hermes다.** 아래는 이미 차용·구현된 것들이라 로드맵에서 제외된다:

| 이미 있음 | 근거 |
|---|---|
| MEMORY / USER 메모리 분리 (`prompts/memory.md` + `prompts/user.md`) | Hermes의 MEMORY.md/USER.md 착안 |
| 메모리 문자 상한 + 오버플로우 시 LLM 통합 | `core/memory.py:8-9`(2200/1375자), `_save_or_consolidate` (`:228-260`) |
| 컨텍스트 압축 (첫/마지막 보호 + 중간 요약) | `core/context_compressor.py` (Hermes식) |
| 인프로세스 MCP 도구 서버 | `create_sdk_mcp_server` (garmin/body_metrics/memory) |
| 선제 발화 (부분) | 2분 iCloud 폴링 → 자동 분석 (`bot/main.py:416`) |

즉 "새로 차용"이 아니라 **남은 빈칸 채우기 + 정적인 것을 동적으로 진화**시키는 것이 과제다.

**SDK 역량 확인**: 아래 설계는 모두 설치된 `claude_agent_sdk==0.1.66`에서 실재를 확인했다 — `web_search`/`web_fetch`(ServerToolName), `AgentDefinition`+`agents`, `HookMatcher`/`can_use_tool`, `ClaudeSDKClient.interrupt()`.

---

## 1. 다섯 개의 진화 축

각 축: **지금 → 목표 → 차용 → 설계 스케치 → 근거/effort**. 설계 스케치는 미래 세션이 곧바로 착수할 수 있게 코드 앵커를 포함한다.

### 축 1 — 응답기 → 상주 비서 (선제성의 일반화)

- **지금**: 능동 트리거가 2분 iCloud 루프 **하나**뿐. 데이터가 도착해야만 말하고, 하드코딩돼 있다.
- **목표**: 자연어로 정의되는 스케줄 잡을 *가진* 시스템. "매주 일요일 20시 트레이닝 리뷰", "월간 체성분 리포트", "대회 D-30 카운트다운 코칭". 각 잡 = 풀 Garmin/체성분 컨텍스트를 가진 **에이전트 턴**(셸 명령이 아님 — 이게 Hermes의 핵심 통찰).
- **차용**: Hermes `cronjob` 도구 + `deliver:` 라우팅 모델.
- **설계 스케치**:
  1. `bot/main.py:376`의 `_run_auto_analysis`를 `run_agent_to_channel(prompt, channel_id, thread_name)`로 추출 리팩터. 자동 분석은 이 함수의 한 호출자가 되고, cron 잡이 또 다른 호출자가 된다 (**공유 언락**).
  2. `core/scheduler.py`: `data/cron_jobs.json`에 원자적(temp+rename) 영속. 잡 = `{id, prompt, schedule, deliver_channel_id, next_run_iso, last_run_iso, paused}`. `deliver_channel_id` 기본값 = 기존 `NOTIFY_CHANNEL_ID`.
  3. `@tasks.loop(minutes=1) cron_tick_loop`: due·비-paused 잡 → `run_agent_to_channel(...)` → `next_run` 재계산.
  4. `schedule` MCP 도구 (create/list/pause/resume/remove), `memory_tools.py`와 동일한 인프로세스 `@tool` 패턴. 오너가 Discord에서 자연어로 등록.
  5. 스케줄 파싱: **의존성 없는 5필드 cron 매처(~25줄)**. `croniter` 도입 금지(8GB 네이티브, 의존성 최소). Hermes식 상대 one-shot(`30m`/`2h`/`1d`)도 지원(트리비얼, 실행 후 self-delete).
  6. **2분 iCloud 루프는 그대로 유지** — 그것은 *데이터 도착* 트리거이지 *시계* 트리거가 아니다. 별개의 initiator이므로 cron으로 억지 통합하지 말 것.
- **근거/effort**: 유저의 명시 목표("에이전트가 먼저 말함")의 진짜 일반화. **M**. 리스크: 오작동 잡이 Discord에 무단 발화 → 잡별 `max_turns` 캡 + `paused` 원터치 필수.

### 축 2 — 폐쇄형 → 개방형 도구 (능력 천장 제거)

- **지금**: Garmin/체성분/메모리 MCP + 로컬 빌트인(Bash/Read/…). **외부 세계 접근 0.** 유저가 날씨를 수동 입력하고, "서울 날씨?"에 답 못 함.
- **목표**: 웹(날씨·최신 영양/훈련 연구·대회 정보), 그리고 도구를 계속 늘려도 관리되는 구조.
- **차용**: `web_search`/`web_fetch` + Hermes의 toolset 조직화(네임스페이싱/그룹 토글).
- **설계 스케치**:
  - `core/llm.py:63` `allowed_tools`에 `"WebSearch", "WebFetch"` 추가. + `prompts/system.md`에 페르소나 한 줄("날씨·외부 정보는 WebSearch/WebFetch로 직접 확인").
  - **KR 지역 주의**: 구독 경로의 서버사이드 WebSearch가 지역 제한될 수 있음. **보장된 폴백**: 이미 `Bash`+`bypassPermissions`가 허용돼 있어 `curl 'wttr.in/Seoul?format=3'`이 오늘 당장 동작. WebSearch는 보너스로 취급하고 로드맵이 그것에 의존하지 않게 할 것.
  - toolset 확장 대비: MCP 도구는 이미 `mcp__<server>__<tool>` 네임스페이싱을 씀. 도구가 늘면 그룹 토글 도입.
- **근거/effort**: "다양하면서 제약 없는 도구" 목표 직결. 능력 천장을 없애 다른 축(스케줄 잡, 학습)이 웹을 활용 가능. **S**.

### 축 3 — 정적 메모리 → 살아있는 장기 기억 ⭐

- **지금**: 봇의 과거 기억 수단은 딱 둘 — 요약된 `memory.md`/`user.md`(freeze), 그리고 **현재 스레드**의 최근 50개 메시지(`bot/main.py:202`). 수십 개 스레드에 쌓인 몇 달치 일일 분석은 write-only로 **사장**된다.
- **목표**: 모든 과거 대화·분석이 검색되는 장기 기억. "지난 3개월 내 러닝 VO2max 코멘트 다 모아봐", "5월에 내 수면 결론이 뭐였지?"가 가능.
- **차용**: FTS5 세션 인덱스 + `session_search` 도구. (Honcho 같은 외부 서비스는 과함 — FTS5가 로컬·무의존 80% 해법.)
- **FTS5란**: SQLite 내장 전문검색 엔진. 파이썬 표준 `sqlite3`에 이미 포함(**추가 의존성·서버 없음**). 일반 `LIKE '%단어%'`가 전 행을 훑는 부분문자열 매칭이라면, FTS5는 **역색인**(단어→메시지 지도)을 미리 만들어 `MATCH '수면 효율'`을 관련도 순위(bm25)로 빠르게 반환. 구문/접두/불리언 검색, 스니펫 하이라이트 지원.
- **설계 스케치**:
  - `core/session_index.py`: stdlib `sqlite3` + FTS5 가상 테이블 `(thread_id, ts, role, content)`. `~/.hermes/state.db`에 대응.
  - `on_message`를 지나는 모든 메시지를 색인(작은 write 1회).
  - `session_search` MCP 도구: Hermes의 두 모드 반환 — (a) 원문 hit, (b) 선택적 LLM 요약 다이제스트.
  - 최초 1회 Discord 히스토리에서 백필.
- **근거/effort**: 크로스세션 리콜 = 현재 zero-capability. 봇이 "매번 처음부터 생각하는 응답기"에서 "누적 이해를 가진 비서"로 바뀌는 지점. **M**. 트레이드오프: Discord 자체 검색이 일부 커버하지만, 그건 *에이전트가 도구로 질의*할 수 없다 — 이 차이가 핵심.

### 축 4 — 정적 스킬 → 성장하는 절차 라이브러리 (학습 루프) ⭐

- **지금**: `.claude/skills/` 수기 skill 4개(운동평가/체성분/수면/과학기준). 깊지만 **고정** — 봇이 어제 알아낸 좋은 분석법을 내일 잊는다.
- **목표**: 복잡한 분석을 성공적으로 해낸 뒤 그 절차를 skill로 캡처/개선 → 시간이 갈수록 분석 품질이 복리로 상승. **Hermes의 시그니처 능력.**
- **차용**: Hermes `skill_manage` 캡처 트리거(create/patch), 오너 승인 게이트.
- **설계 스케치**:
  - `_call_claude`(또는 `ask_with_context`)에 tool_use 카운터 추가. 한 턴이 5+ tool call로 성공 → reflection nudge 주입("복잡한 분석이었다. `.claude/skills/`에 저장할 재사용 절차가 있으면 제안하라").
  - 오너가 승인("ㅇㅇ 저장해") → Claude가 SKILL.md write/patch (이미 `Write`+`Skill`+`cwd=PROJECT_ROOT` 보유 → 기술적으로 가능. **단, 신규 SKILL.md가 재시작 없이 hot-load되는지 검증 필요**).
  - **안전장치(헬스 도메인 필수)**: ① 오너 승인 게이트(단일 유저라 곧 본인), ② 과학 컷오프(science-reference)는 자동 학습에서 **고정**, ③ `core/memory.py`의 인젝션 방어 스캐너를 skill write에도 재사용.
- **근거/effort**: 분석 노하우의 복리 축적 = 시스템 지능의 장기 상승. **M**. **정직한 텐션**: 헬스 조언이라 "무엇을 스스로 배우게 두고 무엇을 사람이 잠그느냐"의 경계 설계가 관건. 경계를 잘못 잡으면 봇이 자기 오류를 학습해 굳힐 수 있다 → 그래서 "제안 → 승인 → 반영"의 human-in-the-loop 유지.

### 축 5 — 블랙박스 → 관측·조종 가능한 루프

- **지금**: 긴 tool 실행 중 무엇을 하는지 안 보이고(무응답 체감), 생성 중 방향을 못 바꾼다.
- **목표**: 진행 상태 가시화 + 생성 중 코스 수정(steer).
- **차용**: Hermes streaming tool 피드 + `/busy steer` 모드.
- **설계 스케치**:
  - **tool-status 피드(S–M, 독립적)**: `_call_claude` 루프(`core/llm.py:71-74`)가 지금 `TextBlock`만 처리하고 `ToolUseBlock`은 무시. `ToolUseBlock`도 매칭해 `on_tool(name)` 콜백 발화 → 이름→한국어 상태 매핑(`mcp__garmin__*`→"💻 Garmin 조회 중…", `WebSearch`→"🔍 검색 중…"). `bot/main.py`에서 단일 transient 상태 메시지로 표시, 첫 `TextBlock` 도착 시 제거. **재구성 불필요, 오늘 동작.**
  - **steer/interrupt-and-redirect(M–L, 앵커)**: 일회성 `query()`를 스레드별 **`ClaudeSDKClient`**로 교체 + `dict[thread_id → client/task]`. 생성 중 새 메시지 → `client.interrupt()` 후 재-`query()`. `interrupt()`는 **SDK 확인됨**; "다음 tool call 후 주입, 인터럽트 없음"의 정확한 순서는 스트리밍-입력 모드로 **검증 필요**, interrupt-then-requeue가 확인된 폴백.
- **근거/effort**: 위 축들이 만드는 긴 자율 실행의 통제성. tool-status = **S–M**, steer = **M–L**(그리고 축 4 subagent의 substrate).

---

## 2. 성숙도 기준 시퀀스 (땜질이 아니라 층 쌓기)

| 층 | 무엇 | 왜 이 순서 |
|---|---|---|
| **기반** | 축 2(웹 도구) + 축 3(FTS5 장기기억) | 능력 천장·기억 천장을 먼저 제거. 위 축들이 이 위에 얹힘 |
| **선제성** | 축 1(NL 스케줄러) | 기반이 생기면 스케줄 잡이 웹·장기기억까지 활용 |
| **지능** | 축 4(학습 루프) | 장기기억(축 3)을 재료로 스킬 성장. 기반 없이는 무의미 |
| **조종성** | 축 5(steer / 상태 피드) | 위 것들이 만드는 길어진 자율 실행에 통제 필요. tool-status는 값싸서 아무 때나 선행 가능 |

**핵심**: 축 3(FTS5)과 축 4(학습 루프)가 진짜 시스템적 도약이다. 봇을 "매번 처음부터 생각하는 응답기"에서 "몇 달에 걸쳐 유저를 이해하고 분석법이 누적되는 비서"로 바꾸는 지점이기 때문.

---

## 3. 차용하지 않을 것 (SKIP + 이유)

| 대상 | 판정 | 이유 |
|---|---|---|
| Terminal 백엔드 (Docker/Modal/Daytona/SSH) | SKIP | fleet 인프라. 8GB 단일 신뢰 유저 네이티브엔 위협 모델 불일치. 이미 `bypassPermissions` Bash 직접 실행 |
| Honcho (외부 Dialectic 유저모델) | SKIP(미래 경로) | HTTP 서비스 + background 파인튜닝 모델. 단일 유저엔 과함. MEMORY/USER + FTS5가 80% 커버 |
| 메모리 nudge (주기적 저장 리마인더) | SKIP | 상한/통합은 이미 구현됨. `user.md`가 이미 상한 근처라 더 넣으면 준수 악화 |
| tool-loop 가드레일(반복 tool call 감지) | SKIP | `max_turns=15`가 이미 폭주 차단. 단일 유저엔 한계효용 낮음 |
| 플랫폼/어댑터 차용 (Telegram/Slack/… 게이트웨이) | SKIP | 오너 명시 — Discord+Claude로 충분, 워크플로우 진화가 목표 |

---

## 4. 의도적으로 뒤로 뺀 세션-특화 수정

초기 분석은 특정 Discord 로그의 개별 순간에 붙어 있었다. 이들은 위생 수준의 로컬 수정이며, **오너 판단으로 후순위**다(시스템 진화가 우선):

- **약어 출력 가드(P1)**: 약어 금지 규칙은 이미 `user.md`+`system.md`에 있고 매 턴 주입됨에도 위반됨 → 근본원인은 저장이 아닌 **준수**. 결정론적 출력 린터(`Stop` 훅/정규식 치환)가 확실한 해법이나, **오너가 "현재 상태로 괜찮음"으로 후순위 결정.** 필요 시 ~S로 언제든 추가 가능.
- **채널→스레드 안내(P3)**, **에러 폴백(P5, 401 원문 노출)**: 소규모 UX/위생 개선. 축 작업 중 곁다리로 처리.

이들은 시스템 축이 아니라 **국소 수정**이라 로드맵 본체에서 분리해 둔다.

---

## 5. 참고

**Hermes 출처(대표)**
- 스킬/메모리/cron: `hermes-agent.nousresearch.com/docs/user-guide/features/{skills,memory,cron}`
- 도구/위임: `.../features/{tools,delegation,mcp}`
- Honcho: `docs.honcho.to`, `github.com/plastic-labs/honcho`

**우리 코드 앵커**
- LLM/도구 게이트: `core/llm.py:53-77` (`_call_claude`, `allowed_tools` `:63-65`)
- 메모리(이미 구현된 상한/통합): `core/memory.py:8-9`, `:228-260`; `core/memory_tools.py:56`
- 선제 발화 템플릿: `bot/main.py:376` (`_run_auto_analysis`), 루프 `:416-438`
- 스레드 세션/스트리밍: `bot/main.py:228-276`, `:202-213`

**SDK 역량(claude_agent_sdk 0.1.66에서 실재 확인)**
- `ServerToolName{web_search, web_fetch}`, `AgentDefinition`+`agents`, `HookMatcher`/`can_use_tool`, `ClaudeSDKClient`/`interrupt()`

---

*이 로드맵은 Hermes Agent와의 비교(리서치 2 + 토론 3, 코드 검증 포함)를 종합한 결과다. 각 축은 독립적으로 착수 가능하나, §2 시퀀스를 따르면 후행 축이 선행 축의 기반을 활용한다.*
