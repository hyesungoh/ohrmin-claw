# ohrmin-claw — 개인 AI 건강 비서

**한국어** | [English](./README.en.md)

> 이름 유래: '오예성' + Claude의 'Claw' 매시업

Garmin 워치 + Apple Health + 인바디 데이터를 통합하여 Discord에서 자연어로 건강을 관리하는 AI 비서입니다.
Claude AI가 운동생리학, 수면의학, 체성분 분석 프레임워크를 적용하여 과학적 근거 기반의 인사이트를 제공합니다.

---

## 무엇이 다른가요?

일반 건강 대시보드는 수치를 보여줍니다. ohrmin-claw는 **해석**합니다.

- **자연어로 질문** — "이번 주 수면 어때?", "최근 운동 분석해줘"처럼 채팅하면 AI가 답합니다
- **대화가 이어집니다** — Discord 스레드 안에서 후속 질문이 가능하고, 이전 맥락을 기억합니다
- **능동적으로 알려줍니다** — 새 체성분 데이터가 들어오면 먼저 분석해서 알림을 보냅니다
- **과학적 근거** — ACSM, NSCA, Daniels VDOT, Israetel MEV/MAV/MRV 등 운동생리학 문헌을 기반으로 분석합니다
- **장기 기억** — 사용자의 건강 패턴과 선호도를 자동으로 학습하여 점점 맞춤형으로 진화합니다

---

## 주요 기능

| 기능 | 예시 |
|------|------|
| 수면 분석 | "이번 주 수면 어때?" |
| 운동 평가 | "어제 러닝 분석해줘" |
| 체성분 트렌드 | "최근 한 달 체지방 변화" |
| 회복 상태 | "운동 후 회복이 잘 되고 있어?" |
| 체성분 입력 | "인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg" |
| 이미지 분석 | 인바디 결과지 사진, 식단 사진 첨부 |
| 주간 리포트 | "주간 리포트" |
| 운동 계획 | "다음 주 운동 계획 짜줘" |

### 종목별 운동 분석

Claude가 Garmin 데이터를 종목별로 자동 감지하여 상세 분석합니다.

- **러닝** — 랩별 페이스/케이던스/VO2max, HR zone 분포, Daniels VDOT 매핑
- **웨이트** — 운동명/중량/횟수, 부위별 볼륨, 점진적 과부하 평가
- **수영** — SWOLF/스트로크, CSS 기반 강도 진단
- **사이클/하이킹** — FTP 기반 파워 존, TSS/IF, 고도보정 페이스

### 자동화

- **Apple Health 자동 동기화** — 2분 주기로 iCloud에서 체성분 데이터를 수집하고, 새 데이터 감지 시 Discord에 분석 결과를 자동 전송합니다
- **주간 리포트** — 수면/심박/HRV/활동/스트레스/체성분 7일 요약 + AI 인사이트

---

## 스크린샷

<!-- TODO: Discord 봇 동작 GIF/스크린샷 추가 -->
_준비 중. 스레드 기반 대화, 자동 분석 알림 캡처 예정._

---

## 데이터 소스

| 소스 | 연동 방식 | 수집 항목 |
|------|----------|----------|
| **Garmin Connect** | python-garminconnect API 직접 호출 | 수면, 심박수, HRV, 스트레스, 활동/운동 상세 |
| **Apple Health** | Health Auto Export 앱 → iCloud → 2분 폴링 | 체중, 체지방률, 제지방량, BMI |
| **수동 입력** | Discord 채팅으로 자연어 입력 | 체성분 수치 (인바디 등) |
| **이미지** | Discord 첨부파일 (최대 5개, 10MB) | 인바디 결과지, 식단 사진 등 |

---

## 사전 준비

| 항목 | 비고 |
|------|------|
| **Python 3.11+** | `python3 --version`으로 확인 |
| **Claude Code 구독** | 터미널에서 `claude login` 실행 |
| **Garmin Connect 계정** | [connect.garmin.com](https://connect.garmin.com) |
| **Discord Bot 토큰** | [Developer Portal](https://discord.com/developers/applications)에서 발급 |

### Discord 봇 설정

1. Developer Portal → 앱 생성 → Bot → Reset Token
2. **Bot** 탭 → **Message Content Intent** 활성화 (필수)
3. **OAuth2** → URL Generator → Scopes: `bot`
4. Bot Permissions: `Send Messages`, `Create Public Threads`, `Send Messages in Threads`, `Read Message History`
5. 생성된 URL로 봇을 서버에 초대

---

## 설치 및 실행

### 1. 저장소 클론

```bash
git clone <repository-url>
cd ohrmin-claw
```

### 2. 초기 세팅

```bash
bash scripts/setup.sh
```

의존성 설치, 디렉토리 생성, `.env.example` → `.env` 복사를 수행합니다.
`.env`가 없으면 자동 생성 후 종료되므로, 값을 채운 뒤 다시 실행하세요.

### 3. 환경변수 설정

`.env` 파일을 열어 아래 항목을 채워 넣으세요.

```env
# 필수
GARMIN_USERNAME=your-garmin-email@example.com
GARMIN_PASSWORD=your-garmin-password
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_APPLICATION_ID=your-application-id
ALLOWED_USERS=123456789012345678    # Discord User ID (쉼표 구분)

# 선택
LLM_ADAPTER=claude                  # 기본값
LLM_MODEL=claude-sonnet-4-20250514  # 기본값
MEMORY_MODE=auto                    # auto | manual
SESSION_IDLE_TIMEOUT=1440           # 분 (기본 24시간)
NOTIFY_CHANNEL_ID=                  # 자동 분석 알림 채널 (미설정 시 비활성화)
APPLE_HEALTH_EXPORT_DIR=            # Health Auto Export iCloud 경로 (기본값 있음)
```

> `ALLOWED_USERS`를 비워두면 모든 메시지를 무시합니다 (화이트리스트 방식).
> Discord 설정 → 고급 → 개발자 모드 ON → 본인 프로필 우클릭 → "Copy User ID"

### 4. 봇 실행

```bash
python3 bot/main.py
```

```
✅ Garmin Connect 로그인 성공
✅ Garmin MCP 도구 등록 완료
🔒 허용된 유저: 1명
🚀 ohrmin-claw 봇 시작...
📊 Apple Health 자동 동기화 시작 (2분 주기)
```

Garmin 로그인이 실패해도 봇은 정상 실행됩니다 (체성분 기능만 동작).

---

## 사용 방법

봇이 있는 Discord 채널에서 메시지를 보내면 **자동으로 스레드가 생성**됩니다.
스레드 안에서 후속 질문을 이어갈 수 있으며, 이전 대화 맥락을 참조합니다.

```
이번 주 수면 어때?
→ (스레드 생성) AI가 7일간 수면 데이터를 분석하여 답변

어제보다 나아졌어?
→ (같은 스레드) 이전 답변을 참조하여 비교 분석
```

### 체성분 입력

```
인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1
```

과거 날짜도 가능합니다:

```
인바디 결과 2026-03-15 체중 73kg 체지방률 16.1%
```

### Apple Health 자동 동기화

iPhone에 **Health Auto Export** 앱을 설치하고 iCloud Drive로 자동 내보내기를 설정하면, 봇이 2분마다 새 데이터를 감지하여 자동 분석합니다. `NOTIFY_CHANNEL_ID`를 설정해야 알림이 전송됩니다.

---

## 자동화 설정 (cron)

```cron
# 매일 06:00 — Garmin 토큰 검증/갱신
0 6 * * * /path/to/ohrmin-claw/garmindb/sync.sh

# 매주 일요일 03:00 — 데이터 백업
0 3 * * 0 /path/to/ohrmin-claw/scripts/backup.sh
```

Apple Health 동기화와 자동 분석은 봇 프로세스 내부에서 2분 주기로 실행됩니다.

---

## 개인 목표 수정

`prompts/goals.md`를 수정하면 다음 질의부터 즉시 반영됩니다 (봇 재시작 불필요).

```markdown
# prompts/goals.md 예시

## 현재 목표
- 목표 체중 92kg를 향해 다이어트
- 골격근량 최대 유지
- 러닝 능력치 향상
- 평균 수면 7시간 이상
```

---

## 아키텍처

```
사용자 (Discord 채팅)
       │
       ▼
 Discord 봇 수신 → 스레드 자동 생성
       │
       ├─ 기본 컨텍스트: 최근 7일 Garmin 요약 (항상 포함)
       │
       ▼
 Claude AI (시스템 프롬프트 + 건강 컨텍스트 + 대화 이력)
       │
       ├─ 상세 분석 필요 시 → MCP 도구 호출 (Garmin/체성분/메모리)
       │
       ▼
 스레드에 응답 전송 (TextBlock 단위 스트리밍)
```

### 핵심 패턴

- **어댑터 패턴** — LLM과 채널 모두 ABC로 추상화. 새 어댑터 추가만으로 교체 가능
- **인프로세스 MCP 서버** — `claude_agent_sdk`의 `@tool` + `create_sdk_mcp_server()`로 별도 프로세스 없이 도구 제공
- **하이브리드 데이터 접근** — 기본 요약은 항상 포함, 상세 데이터는 Claude가 필요할 때만 MCP 도구로 조회
- **컨텍스트 압축** — 대화 20개 초과 시 중간 구간을 LLM으로 요약 (첫 1개 + 최근 6개 보호)
- **영구 메모리** — 대화에서 건강 패턴/사용자 선호도를 자동 추출하여 장기 저장
- **전문 스킬** — `.claude/skills/`에 운동평가, 수면분석, 체성분, 과학기준 프레임워크 탑재

---

## 디렉토리 구조

```
ohrmin-claw/
├── bot/
│   └── main.py                 # 봇 엔트리포인트
├── core/
│   ├── llm.py                  # LLM 어댑터 (ClaudeSDKAdapter)
│   ├── channel.py              # 채널 어댑터 (DiscordChannel)
│   ├── garmin_data.py          # Garmin Connect API 클라이언트
│   ├── garmin_tools.py         # Garmin MCP 도구 (9개)
│   ├── body_metrics.py         # 체성분 CSV CRUD
│   ├── body_metrics_tools.py   # 체성분 MCP 도구 (3개)
│   ├── body_metrics_parser.py  # 자연어 체성분 파싱
│   ├── memory.py               # 영구 메모리 관리
│   ├── memory_tools.py         # 메모리 MCP 도구 (4개)
│   ├── preprocessor.py         # 원시 데이터 → 통계 요약
│   ├── report.py               # 주간 마크다운 리포트
│   ├── context_compressor.py   # 대화 이력 압축
│   ├── session_manager.py      # 스레드 세션 타임아웃
│   └── apple_health_reader.py  # iCloud → inbody.csv 동기화
├── prompts/
│   ├── system.md               # AI 페르소나
│   ├── goals.md                # 개인 건강 목표
│   ├── memory.md               # 자동 추출 장기 기억
│   └── user.md                 # 사용자 선호도
├── .claude/skills/             # 전문 분석 스킬
│   ├── activity-evaluation/    # 운동 평가
│   ├── body-composition/       # 체성분 분석
│   ├── sleep-analysis/         # 수면 분석
│   └── science-reference/      # 과학 기준치
├── data/
│   └── inbody.csv              # 체성분 데이터
├── tests/                      # 테스트 (20개 파일)
├── scripts/
│   ├── setup.sh                # 초기 환경 세팅
│   └── backup.sh               # 데이터 백업
└── garmindb/
    └── sync.sh                 # Garmin 토큰 검증/갱신
```

---

## 기술 스택

| 항목 | 내용 |
|------|------|
| 언어 | Python 3.11+ |
| AI | [Claude Agent SDK](https://github.com/anthropics/claude-code/tree/main/packages/agent-sdk) (구독 모델) |
| Discord | [discord.py](https://github.com/Rapptz/discord.py) |
| Garmin | [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) (API 직접 호출) |
| 데이터 | CSV (체성분) |
| 실행 환경 | macOS 네이티브 (Docker 없음) |

---

## 테스트

```bash
python3 -m pytest tests/ -v
```

모든 core 모듈에 대응하는 테스트 파일이 있습니다. Garmin 테스트는 mock을 사용하여 실제 API 호출 없이 실행됩니다.

---

## 문제 해결

### 봇이 응답하지 않을 때

1. `ALLOWED_USERS`에 본인의 Discord User ID가 등록되어 있는지 확인
2. `DISCORD_BOT_TOKEN`이 올바른지 확인
3. Discord Developer Portal에서 **Message Content Intent**가 활성화되어 있는지 확인

### Garmin 인증 오류

```bash
bash garmindb/sync.sh
```

토큰 검증/갱신을 수동 실행합니다. Garmin은 빈번한 로그인 시 429를 반환할 수 있습니다.

### 체성분 파싱 실패

숫자와 단위가 포함되어 있는지 확인하세요:

```
# 올바른 예시
인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1
```
