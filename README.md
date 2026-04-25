# Health Manager — 개인 AI 건강 비서

Garmin + InBody 데이터를 기반으로 Discord에서 자연어로 건강을 관리하는 개인 AI 비서입니다.
Claude AI가 수면, 심박, HRV, 운동, 체성분 데이터를 분석하여 과학적 근거 기반의 인사이트를 제공합니다.

---

## 목차

1. [무엇을 할 수 있나요?](#무엇을-할-수-있나요)
2. [사전 준비](#사전-준비)
3. [설치 및 설정](#설치-및-설정)
4. [봇 실행](#봇-실행)
5. [사용 방법](#사용-방법)
6. [자동화 설정 (cron)](#자동화-설정-cron)
7. [디렉토리 구조](#디렉토리-구조)
8. [아키텍처 개요](#아키텍처-개요)
9. [개인 목표 수정](#개인-목표-수정)
10. [문제 해결](#문제-해결)

---

## 무엇을 할 수 있나요?

Discord 채팅창에서 자연어로 건강 데이터를 질의하고 AI 인사이트를 받을 수 있습니다.

| 기능 | 예시 |
|------|------|
| 수면 분석 | "이번 주 수면 어때?" |
| 운동 분석 | "최근 운동 분석해줘" |
| 체성분 트렌드 | "최근 한 달 체지방 변화" |
| 회복 상태 확인 | "운동 후 회복이 잘 되고 있어?" |
| InBody 데이터 입력 | "인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1" |
| 주간 리포트 | "주간 리포트" |
| 월간 리포트 | "월간 리포트" |

- **Garmin Connect 데이터 자동 동기화** — 수면, 심박수, HRV, 스트레스, 활동/운동 기록
- **InBody 채팅 직접 입력** — 앱 설치 없이 채팅으로 체성분 기록
- **주간/월간 리포트 자동 발송** — Claude AI 인사이트 포함

---

## 사전 준비

시작하기 전에 아래 항목을 준비하세요.

### 필수 계정 및 도구

| 항목 | 확인 방법 |
|------|----------|
| **Garmin Connect 계정** | [garminconnect.com](https://connect.garmin.com) 로그인 확인 |
| **Discord Bot 토큰** | [Discord Developer Portal](https://discord.com/developers/applications) → 앱 생성 → Bot → Reset Token |
| **Discord Application ID** | Developer Portal → General Information |
| **Discord 서버 ID** | Discord 서버 우클릭 → ID 복사 (개발자 모드 필요) |
| **Claude Code 구독** | [claude.ai](https://claude.ai) 구독 후 터미널에서 `claude login` 실행 |
| **Python 3.11+** | `python3 --version` |

### Discord 봇 초대

1. Developer Portal → OAuth2 → URL Generator
2. Scopes: `bot` 체크
3. Bot Permissions: `Send Messages`, `Read Message History`, `View Channels` 체크
4. 생성된 URL로 봇을 내 서버에 초대

---

## 설치 및 설정

### 1단계: 저장소 클론

```bash
git clone <repository-url>
cd health-manager
```

### 2단계: 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 아래 항목을 채워 넣으세요.

```env
# Garmin Connect 계정
GARMIN_USERNAME=your-garmin-email@example.com
GARMIN_PASSWORD=your-garmin-password

# Discord 봇
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_APPLICATION_ID=your-application-id
DISCORD_GUILD_ID=your-server-id        # 선택 사항

# 응답 허용할 Discord User ID (쉼표 구분, 필수)
# 비워두면 모든 메시지를 무시합니다 (화이트리스트 방식)
# Discord 설정 → 고급 → 개발자 모드 ON → 본인 프로필 우클릭 → "Copy User ID"
ALLOWED_USERS=123456789012345678

# LLM 어댑터 (기본값: claude)
LLM_ADAPTER=claude

# 메모리 모드 (기본값: auto). manual = 사용자가 명시적으로 요청 시만 추출
MEMORY_MODE=auto

# 세션 idle 타임아웃 분 (기본값: 1440 = 24시간)
SESSION_IDLE_TIMEOUT=1440
```

> **주의**: `.env` 파일은 절대 git에 커밋하지 마세요. `.gitignore`에 이미 포함되어 있습니다.

### 3단계: 의존성 설치 및 초기 환경 세팅

```bash
bash scripts/setup.sh
```

이 스크립트는 다음을 수행합니다.
- Python 가상환경 생성 (`.venv/`)
- `requirements.txt` 의존성 설치
- 필요한 디렉토리 생성 (`data/`, `backups/`)

### 4단계: Garmin 데이터 초기 동기화

최초 1회 전체 히스토리를 다운로드합니다. 데이터 양에 따라 수 분 ~ 수십 분 소요될 수 있습니다.

```bash
bash garmindb/sync.sh
```

완료 후 `data/` 디렉토리에 아래 파일이 생성됩니다.

```
data/
├── garmin.db              ← 일일 모니터링 (심박, 스텝, 스트레스)
├── garmin_activities.db   ← 활동/운동 기록
└── garmin_summary.db      ← 일/주/월 요약
```

> **참고**: 이후 동기화는 최신 데이터만 가져오도록 자동화됩니다. ([cron 설정 참고](#자동화-설정-cron))

---

## 봇 실행

```bash
python3 bot/main.py
```

터미널에 아래 메시지가 표시되면 정상적으로 실행된 것입니다.

```
Health Manager 봇이 온라인입니다.
```

Discord 서버에서 봇이 온라인 상태인지 확인하세요.

---

## 사용 방법

봇이 있는 Discord 채널에서 자연어로 메시지를 보내면 됩니다.
메시지를 보내면 **자동으로 스레드가 생성**되고, 스레드 안에서 대화를 이어갈 수 있습니다. 이전 대화 맥락을 기억하므로 후속 질문이 가능합니다.

### 건강 데이터 질의

```
이번 주 수면 어때?
최근 운동 분석해줘
오늘 스트레스 어느 정도야?
내 HRV 트렌드 보여줘
운동 후 회복이 잘 되고 있어?
다음 주 운동 계획 짜줘
```

### InBody 데이터 입력

인바디 측정 후 결과를 채팅으로 직접 입력합니다. 자연어로 자유롭게 입력해도 봇이 파싱합니다.

```
인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1
```

과거 날짜 데이터를 소급 입력할 수도 있습니다.

```
인바디 결과 2026-03-15 체중 73kg 체지방률 16.1% 골격근량 34.2kg BMI 22.4
```

저장에 성공하면 봇이 확인 메시지를 보냅니다.

```
✅ InBody 데이터 저장 완료 (2026-04-25)
```

### 리포트 요청

```
주간 리포트
월간 리포트
```

수동으로도 요청할 수 있으며, cron으로 자동 발송되도록 설정할 수도 있습니다.

### 리포트 내용

**주간 리포트**
- 주간 운동 요약 (종류, 시간, 칼로리)
- 평균 심박수 및 안정시 심박수 트렌드
- 수면 품질 점수 및 패턴
- 스트레스 레벨 분석
- 체중/체지방 변화 (InBody 데이터 있을 시)
- 개선 포인트 및 다음 주 권장사항

**월간 리포트**
- 월간 운동량 대비 목표 달성률
- 신체 구성 변화 추이 (InBody)
- HRV 트렌드 → 회복 상태 평가
- 수면/스트레스/활동 상관관계 분석

---

## 자동화 설정 (cron)

`crontab -e` 명령으로 cron을 편집하고 아래 항목을 추가합니다. `/path/to/health-manager`는 실제 프로젝트 경로로 바꾸세요.

```cron
# 매일 06:00 — Garmin 데이터 동기화
0 6 * * * /path/to/health-manager/garmindb/sync.sh

# 매주 일요일 03:00 — SQLite 백업
0 3 * * 0 /path/to/health-manager/scripts/backup.sh
```

> **참고**: 주간/월간 리포트 자동 발송은 봇 프로세스 내부 스케줄러가 처리합니다. 봇이 실행 중인 상태에서 자동으로 전송됩니다.

### cron 등록 확인

```bash
crontab -l
```

---

## 디렉토리 구조

```
health-manager/
├── bot/
│   └── main.py              ← 봇 엔트리포인트
├── core/
│   ├── llm.py                  ← LLM 어댑터 (ClaudeSDKAdapter)
│   ├── channel.py              ← 채널 추상화 (DiscordChannel)
│   ├── garmin_data.py          ← Garmin Connect API 클라이언트
│   ├── garmin_tools.py         ← Garmin MCP 도구 (Claude가 호출)
│   ├── body_metrics.py         ← Body Metrics CSV CRUD
│   ├── body_metrics_parser.py  ← 자연어 InBody 파싱
│   ├── body_metrics_tools.py   ← Body Metrics MCP 도구
│   ├── preprocessor.py         ← 로컬 전처리 (통계 요약)
│   ├── report.py               ← 주간/월간 리포트 생성
│   ├── memory.py               ← 영구 메모리 관리
│   ├── context_compressor.py   ← 대화 이력 LLM 압축
│   └── session_manager.py      ← 스레드 세션 타임아웃
├── prompts/
│   ├── system.md            ← AI 건강 전문가 페르소나
│   ├── goals.md             ← 개인 건강 목표 (수정 가능)
│   ├── memory.md            ← 자동 추출된 환경/패턴 메모리
│   └── user.md              ← 자동 추출된 사용자 선호도
├── garmindb/
│   └── sync.sh              ← Garmin 데이터 동기화 스크립트
├── scripts/
│   ├── setup.sh             ← 초기 환경 세팅
│   └── backup.sh            ← SQLite → SQL 덤프 백업
├── data/                    ← 데이터 저장 (SQLite, inbody.csv)
├── backups/                 ← SQL 덤프 백업 파일
├── tests/                   ← 테스트
├── .env                     ← 환경변수 (시크릿, git 미포함)
├── .env.example             ← 환경변수 예시
├── requirements.txt         ← Python 의존성
└── ARCHITECTURE.md          ← 상세 아키텍처 설계 문서
```

---

## 아키텍처 개요

### 데이터 흐름

```
사용자 (Discord 채팅)
        ↓
  Discord 봇 수신 → 스레드 자동 생성
        ↓
  GarminDB SQLite 쿼리 / InBody CSV 읽기
        ↓
  로컬 전처리 (평균, 트렌드, 이상치 계산)
        ↓
  Claude AI (요약 데이터 + 대화 이력 기반 인사이트 생성)
        ↓
  스레드에 응답 전송 (후속 질문 시 이력 자동 포함)
```

### 설계 원칙

- **LLM 어댑터 패턴**: `ClaudeSDKAdapter`가 기본이며, `.env`의 `LLM_ADAPTER` 값만 변경하면 다른 LLM으로 교체 가능합니다.
- **채널 추상화**: `DiscordChannel`이 기본이며, Telegram 등 다른 채널로 교체 가능한 구조입니다.
- **스트리밍 응답**: Claude가 응답을 생성할 때 각 텍스트 블록을 즉시 Discord에 전송합니다. 사용자가 결과를 기다리지 않고 실시간으로 확인할 수 있습니다.
- **로컬 전처리**: 원시 데이터를 Claude에 그대로 보내지 않고, Python으로 통계 요약을 먼저 계산하여 전달합니다. 토큰 사용량을 50~80% 절감합니다.
- **네이티브 실행**: Docker 없이 macOS에서 직접 실행합니다. 맥북 에어 8GB 기준으로 최적화되어 있습니다.

자세한 아키텍처 설명은 [ARCHITECTURE.md](./ARCHITECTURE.md)를 참고하세요.

---

## 개인 목표 수정

Claude AI는 `prompts/goals.md`에 정의된 개인 목표를 참고하여 분석합니다. 목표가 바뀌면 이 파일만 수정하면 됩니다.

```markdown
# prompts/goals.md 예시

## 현재 목표
- 목표 체중 92kg를 향해 다이어트 (시작 몸무게 107kg)
- 골격근량 최대로 유지하며 다이어트 (시작 골격근량 41.5kg 수준)
- 러닝 능력치 향상
- 평균 수면 7시간 이상
```

봇을 재시작할 필요 없이 다음 질의 시점부터 반영됩니다.

---

## 문제 해결

### 봇이 응답하지 않을 때

1. 터미널에서 봇 프로세스가 실행 중인지 확인합니다.
2. `.env`의 `DISCORD_BOT_TOKEN` 값이 올바른지 확인합니다.
3. 봇이 해당 채널의 메시지 읽기/쓰기 권한을 가지고 있는지 확인합니다.

### Garmin 데이터가 업데이트되지 않을 때

```bash
bash garmindb/sync.sh
```

수동으로 동기화를 실행합니다. 인증 오류가 발생하면 `.env`의 Garmin 계정 정보를 확인합니다.

### InBody 데이터 파싱이 실패할 때

입력 형식에 숫자와 단위가 포함되어 있는지 확인합니다.

```
# 올바른 예시
인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1

# 숫자 앞에 단위가 있어야 합니다
체중: 72 kg, 체지방률: 15.2%
```

### 테스트 실행

```bash
python3 -m pytest tests/ -v
```

모든 테스트가 통과하면 정상입니다.

### 데이터 백업 수동 실행

```bash
bash scripts/backup.sh
```

`backups/` 디렉토리에 SQL 덤프 파일이 생성됩니다.

### SQLite 데이터 복원

```bash
sqlite3 data/garmin.db < backups/garmin.sql
sqlite3 data/garmin_activities.db < backups/garmin_activities.sql
sqlite3 data/garmin_summary.db < backups/garmin_summary.sql
```

---

## 기술 스택

| 항목 | 내용 |
|------|------|
| 언어 | Python 3.11+ |
| Discord 봇 | [discord.py](https://github.com/Rapptz/discord.py) |
| AI | [Claude Agent SDK](https://github.com/anthropics/claude-code/tree/main/packages/agent-sdk) (구독 모델) |
| Garmin 데이터 | [GarminDB](https://github.com/tcgoetz/GarminDB) → SQLite |
| 데이터 처리 | pandas |
| 저장소 | SQLite (Garmin) + CSV (InBody) |
| 실행 환경 | macOS 네이티브 (Docker 없음) |
