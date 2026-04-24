# Health Manager - 아키텍처 설계 문서

## 목표

Garmin + InBody 데이터를 기반으로 과학적 건강 매니지먼트를 받을 수 있는 개인 AI 비서 구축.

- 원할 때 자연어로 건강 데이터 질의
- 스케줄링으로 주기적 리포트 자동 수신 (Discord 기본, 채널 교체 가능)
- 과학적 근거 기반 분석 및 조언
- LLM/메시징 채널 추상화로 유연한 교체 가능

---

## 핵심 도구

| 도구 | 역할 | 링크 |
|------|------|------|
| **GarminDB** | Garmin Connect 데이터 수집 → SQLite 저장 | https://github.com/tcgoetz/GarminDB |
| **Health Auto Export** | (선택) iPhone Apple Health → iCloud Drive 자동 동기화 | https://www.healthyapps.dev |
| **Claude Agent SDK** | LLM 호출 (내부적으로 CLI 활용, 구독 모델) | https://github.com/anthropics/claude-code/tree/main/packages/agent-sdk |
| **discord.py** | Discord 봇 (기본 채널, 추상화로 교체 가능) | https://github.com/Rapptz/discord.py |
| **SQLite** | 건강 데이터 저장소 (macOS 내장) | - |

---

## 시스템 구조

```
┌─────────────────────────────────────────────────────┐
│                    사용자 (나)                        │
│            Discord (기본) / Telegram (추후)             │
└──────────────────┬──────────────────────────────────┘
                   │ 메시지
                   ▼
┌─────────────────────────────────────────────────────┐
│            Python 애플리케이션 (네이티브)              │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │ 채널 추상화 레이어 (MessagingChannel)          │   │
│  │  - DiscordChannel (기본)                      │   │
│  │  - TelegramChannel (추후)                     │   │
│  └──────────────────┬───────────────────────────┘   │
│                     │                               │
│  ┌──────────────┐   │   ┌──────────────┐            │
│  │ cron 스케줄러 │───┘   │ 로컬 전처리   │            │
│  │ (리포트 자동화)│       │ (통계 요약)   │            │
│  └──────────────┘       └──────┬───────┘            │
│                                ▼                    │
│  ┌──────────────────────────────────────────────┐   │
│  │ LLM 어댑터 레이어 (LLMAdapter)                 │   │
│  │  - ClaudeSDKAdapter (Agent SDK, 구독 모델)    │   │
│  │  - CodexSDKAdapter (추후)                     │   │
│  │  - 요약 데이터 기반 인사이트 생성               │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────┬──────────────────────────────────┘
                   │ SQL 쿼리 / CSV 읽기
                   ▼
┌─────────────────────────────────────────────────────┐
│                  데이터 레이어                        │
│                                                     │
│  ┌─────────────────────┐  ┌──────────────────────┐  │
│  │ GarminDB (SQLite)   │  │ Health Auto Export   │  │
│  │                     │  │ (iCloud Drive CSV)   │  │
│  │ - 심박수            │  │                      │  │
│  │ - 수면              │  │ - 체지방률            │  │
│  │ - 활동/운동          │  │ - 골격근량            │  │
│  │ - 스트레스           │  │ - 체중               │  │
│  │ - HRV              │  │ - BMI               │  │
│  │ - 체중              │  │ (InBody 동기화 데이터) │  │
│  │ - 일/주/월 요약      │  │                      │  │
│  └─────────────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 데이터 흐름

### 1. 데이터 수집 (GarminDB)

```bash
# 초기 설정 - Garmin Connect 인증 정보
~/.GarminDb/GarminConnectConfig.json

# 전체 히스토리 다운로드 (최초 1회)
garmindb_cli --all --download --import --analyze

# 이후 일일 업데이트 (cron)
garmindb_cli --latest --download --import --analyze
```

생성되는 SQLite DB 파일:
- `garmin.db` — 일일 모니터링 (HR, 스텝, 스트레스)
- `garmin_activities.db` — 활동/운동 기록
- `garmin_summary.db` — 일/주/월/연 요약

### 2. InBody 데이터 수집

InBody 데이터는 월 1-2회 측정으로 데이터 양이 극소 (연간 12-24행). 두 가지 수집 경로를 지원한다.

#### 방법 A: 채팅 직접 입력 (기본, POC)

Discord 채팅으로 InBody 측정 결과를 직접 입력하면 봇이 파싱하여 CSV에 저장.

```
나: "인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1"
봇: "✅ InBody 데이터 저장 완료 (2026-04-25)"
```

- 별도 앱 설치 불필요
- 봇이 자연어를 파싱 → `data/inbody.csv`에 행 추가
- 과거 데이터도 채팅으로 입력 가능: "인바디 결과 2026-04-01 체중 73kg ..."

#### 방법 B: 자동 동기화 (추후 옵션)

자동화가 필요해지면 아래 수단 중 택 1:
- **Health Auto Export** iOS 앱 ($6.99/년) → iCloud Drive CSV 자동 동기화
- **Apple Health XML** 수동 내보내기 → healthkit-to-sqlite 파싱
- **iOS Shortcut** → HTTP POST로 서버에 전송

두 방법 모두 동일한 `data/inbody.csv`에 데이터를 쌓으므로 분석 코드는 동일.

```python
# InBody 데이터 읽기 — 입력 방식에 무관하게 동일 CSV
import pandas as pd
df = pd.read_csv("data/inbody.csv")
# columns: date, weight_kg, body_fat_pct, muscle_mass_kg, bmi
```

### 3. 질의 흐름 (사용자 → 답변)

```
사용자: "이번 주 수면 품질 어때?"
    ↓
Discord 봇 수신
    ↓
Python: GarminDB SQLite에서 최근 7일 수면 데이터 조회
    ↓
Python: 로컬 전처리 (평균, 트렌드, 이상치 계산)
    ↓
LLM Adapter (Claude Agent SDK): 요약 데이터 기반 인사이트 생성
    ↓
채널 추상화 → 사용자에게 전달 (Discord 등)
```

### 4. 스케줄 리포트 흐름

```
cron (매주 월요일 오전 9시)
    ↓
Python: GarminDB + InBody CSV 데이터 수집
    ↓
Python: 로컬 전처리 + 리포트 템플릿 생성 (표, 수치)
    ↓
Claude: 인사이트 및 조언만 생성 (토큰 절약)
    ↓
리포트 조합 → Discord 전송
```

---

## 프롬프트 설계

Claude에 전달하는 시스템 프롬프트와 개인 목표를 **마크다운 파일로 분리**하여 수정하기 쉽게 관리한다.

### prompts/system.md — 건강 전문가 페르소나

```markdown
당신은 과학적 근거 기반의 건강 분석 전문가입니다.
- 한국어로 응답
- 데이터에 근거한 분석, 추측 금지
- 개선 제안 시 논문/가이드라인 기반
- 응답은 간결하게, Discord에서 읽기 편한 길이
```

### prompts/goals.md — 개인 목표 (수시 수정)

```markdown
## 현재 목표
- 체지방률 15% 이하 유지
- 주 4회 이상 운동
- 평균 수면 7시간 이상
- 안정시 심박수 55 이하
```

봇이 Claude API를 호출할 때 두 파일을 읽어 시스템 프롬프트에 합쳐 전달한다. 목표를 변경하고 싶으면 `goals.md`만 수정하면 된다.

---

## LLM 어댑터 패턴

LLM 호출을 **어댑터 패턴**으로 추상화하여, Claude 외 다른 LLM SDK로도 교체 가능하게 설계한다.

```python
from abc import ABC, abstractmethod

class LLMAdapter(ABC):
    """LLM 호출 공통 인터페이스"""
    @abstractmethod
    def ask(self, system_prompt: str, user_message: str) -> str: ...

    @abstractmethod
    def ask_with_context(self, system_prompt: str, user_message: str, context: dict) -> str: ...

class ClaudeSDKAdapter(LLMAdapter):
    """Claude Agent SDK — 내부적으로 CLI 활용, 구독 모델"""
    def ask(self, system_prompt: str, user_message: str) -> str:
        # claude-agent-sdk 사용
        ...

class CodexSDKAdapter(LLMAdapter):
    """OpenAI Codex SDK — 추후 구현"""
    def ask(self, system_prompt: str, user_message: str) -> str:
        # codex SDK 사용
        ...
```

- **기본 어댑터**: `ClaudeSDKAdapter` (Claude Agent SDK, 구독 모델로 과금 없음)
- **확장 가능**: `CodexSDKAdapter`, `OllamaAdapter` 등 새 어댑터 추가만으로 교체
- **설정으로 전환**: `.env`의 `LLM_ADAPTER=claude` 값만 변경하면 런타임에 어댑터 교체

---

## 토큰 절약 전략

원시 데이터를 Claude에 그대로 보내지 않고, **로컬 전처리**로 토큰을 절약한다.

1. **로컬 전처리**: Python으로 통계 요약(평균, 트렌드, 이상치)을 계산한 뒤 요약만 Claude에 전달
2. **리포트 템플릿화**: 주간 리포트의 고정 구조(표, 수치)를 Python으로 생성하고 Claude는 인사이트만 담당

```python
# 예시: 원시 데이터 대신 요약만 Claude에 전달
summary = {
    "period": "2026-04-13 ~ 2026-04-19",
    "avg_sleep_hours": 7.2,
    "avg_resting_hr": 58,
    "hrv_trend": "improving",
    "steps_avg": 8500,
    "stress_avg": 32,
    "body_fat_pct": 15.2,  # InBody 최신
}
# → Claude에는 이 요약만 전달 (토큰 50-80% 절감)
```

---

## 디렉토리 구조 (계획)

```
health-manager/
├── ARCHITECTURE.md          ← 이 문서
├── requirements.txt         ← Python 의존성
│
├── prompts/                 ← 프롬프트 설계 (마크다운)
│   ├── system.md            ← 건강 전문가 페르소나 + 지시사항
│   └── goals.md             ← 내 개인 목표 (수시 수정)
│
├── core/                    ← 추상화 레이어
│   ├── llm.py               ← LLMAdapter 인터페이스 + ClaudeSDKAdapter
│   ├── channel.py           ← MessagingChannel 인터페이스 + DiscordChannel
│   └── preprocessor.py      ← 로컬 전처리 (통계 요약)
│
├── bot/                     ← 봇 엔트리포인트
│   └── main.py              ← 앱 시작점 (채널 + LLM 조합)
│
├── garmindb/                ← GarminDB 관련
│   ├── config.json          ← Garmin Connect 인증
│   └── sync.sh              ← 데이터 동기화 스크립트
│
├── data/                    ← SQLite DB 파일 저장
│   ├── garmin.db
│   ├── garmin_activities.db
│   └── garmin_summary.db
│
├── backups/                 ← SQL 덤프 백업 (git 추적)
│   ├── garmin.sql           ← 고정 파일명, git diff 추적
│   ├── garmin_activities.sql
│   └── garmin_summary.sql
│
└── scripts/                 ← 유틸리티
    ├── setup.sh             ← 초기 환경 세팅
    └── backup.sh            ← DB → SQL 덤프 + git commit
```

InBody 데이터는 iCloud Drive에서 직접 읽으므로 별도 디렉토리 불필요.

---

## 스케줄 리포트 예시

### 주간 리포트 (매주 월요일)
- 주간 운동 요약 (종류, 시간, 칼로리)
- 평균 심박수 및 안정시 심박수 트렌드
- 수면 품질 점수 및 패턴
- 스트레스 레벨 분석
- 체중/체지방 변화 (InBody 데이터 있을 시)
- 개선 포인트 및 다음 주 권장사항

### 월간 리포트 (매월 1일)
- 월간 운동량 대비 목표 달성률
- 신체 구성 변화 추이 (InBody)
- HRV 트렌드 → 회복 상태 평가
- 수면/스트레스/활동 상관관계 분석

---

## 질의 예시

| 질문 | 데이터 소스 | 분석 방식 |
|------|-----------|----------|
| "이번 주 수면 어때?" | garmin.db (sleep) | 수면 단계, 시간, 점수 트렌드 |
| "최근 한 달 체지방 변화" | iCloud CSV (InBody) | 체지방률 추이, 골격근량 대비 |
| "운동 후 회복이 잘 되고 있어?" | garmin.db (HRV, stress) | HRV 추이 + 운동 강도 대비 회복 |
| "다음 주 운동 계획 짜줘" | garmin_activities.db + garmin.db | 최근 운동 패턴 + 회복 상태 기반 제안 |
| "내 BMI 대비 골격근량은 적절해?" | iCloud CSV (InBody) | 과학적 기준표 대비 분석 |

---

## 구현 순서 (TODO)

### Phase 1: 데이터 탐색
- [ ] GarminDB 설치 및 Garmin Connect 연동
  - 검증: `garmindb_cli --version` 실행 성공
- [ ] 초기 데이터 다운로드 및 SQLite 확인
  - 검증: `sqlite3 data/garmin.db "SELECT COUNT(*) FROM garmin_monitoring_hr_1_min"` > 0
- [ ] InBody 데이터 입력 기능 설계 (채팅 자연어 파싱 → CSV 저장)
  - 검증: 테스트 입력 "인바디 결과 체중 72kg 체지방률 15.2%" → `data/inbody.csv`에 행 추가
- [ ] Python으로 GarminDB SQLite 직접 탐색 (어떤 데이터가 유용한지 파악)
  - 검증: 탐색 스크립트 실행 시 수면/심박/운동 데이터 출력
- [ ] InBody CSV 데이터 탐색 (pandas)
  - 검증: `pandas.read_csv()` 로 체지방률/골격근량 데이터 로드 성공

### Phase 2: Claude 연동 + 추상화 레이어
- [ ] LLM 어댑터 레이어 설계 (LLMAdapter 인터페이스, 어댑터 패턴)
  - 검증: `LLMAdapter` ABC 클래스 정의, `ask()` 메서드 시그니처 확인
- [ ] ClaudeSDKAdapter 구현 (Claude Agent SDK, 구독 모델)
  - 검증: `ClaudeSDKAdapter().ask("안녕")` 호출 시 Claude 응답 반환
- [ ] 로컬 전처리 모듈 작성 (통계 요약)
  - 검증: GarminDB에서 최근 7일 수면 데이터 → 평균/트렌드 딕셔너리 반환
- [ ] 리포트 템플릿 생성 모듈 작성
  - 검증: 요약 데이터 입력 → 포맷팅된 마크다운 리포트 문자열 반환

### Phase 3: 자동화 + 채널 추상화
- [ ] 채널 추상화 레이어 설계 (MessagingChannel 인터페이스)
  - 검증: `MessagingChannel` ABC 클래스 정의, `send()`/`listen()` 메서드 시그니처 확인
- [ ] DiscordChannel 구현 (discord.py)
  - 검증: Discord 봇 온라인 상태 확인 + 테스트 메시지 수신/응답
- [ ] 주간/월간 리포트 cron 스케줄 등록
  - 검증: `crontab -l | grep health-manager` 엔트리 존재
- [ ] GarminDB 일일 동기화 cron 등록
  - 검증: `crontab -l | grep garmindb` 엔트리 존재
- [ ] SQL 덤프 백업 스크립트 작성 (backup.sh, 주 1회)
  - 검증: `bash scripts/backup.sh` 실행 → backups/*.sql 파일 생성

### 사전 준비 (시크릿)

autopilot 실행 전 `.env` 파일에 필요한 인증 정보:

| 시크릿 | 필수 | 취득 방법 |
|--------|------|----------|
| `GARMIN_USERNAME` | 필수 | Garmin Connect 가입 이메일 |
| `GARMIN_PASSWORD` | 필수 | Garmin Connect 비밀번호 |
| `DISCORD_BOT_TOKEN` | 필수 | [Discord Developer Portal](https://discord.com/developers) → Bot → Reset Token |
| `DISCORD_APPLICATION_ID` | 필수 | Developer Portal → General Information |
| `DISCORD_GUILD_ID` | 선택 | 개발 서버 우클릭 → Copy Server ID |
| Claude CLI 로그인 | 필수 | `claude login` (구독 모델, 별도 API 키 불필요) |
| Health Auto Export | 선택 (추후) | 자동화 필요 시 iPhone App Store ($6.99/년) |

`.env.example` 참고하여 `.env` 파일 생성: `cp .env.example .env`

---

## 기술 결정 사항

| 결정 | 선택 | 이유 |
|------|------|------|
| LLM | Claude (구독) | 이미 구독 중 |
| LLM 연동 | Claude Agent SDK (구독 모델) + 어댑터 패턴 | 별도 API 과금 없음, 어댑터로 Codex SDK 등 교체 가능 |
| Garmin 데이터 수집 | GarminDB | 풀 파이프라인, SQLite 자동 생성, FIT 파싱 |
| InBody 수집 | 채팅 직접 입력 (기본) / Health Auto Export (추후 옵션) | 앱 설치 불필요, 봇이 자연어 파싱 → CSV 저장. 자동화는 필요 시 추가 |
| 데이터 저장 | SQLite (Garmin) + CSV (InBody) | GarminDB 기본, macOS 내장, 별도 서버 불필요 |
| 메시징 | Discord (기본) + 채널 추상화 | embed/스레드 풍부, 무료, 추상화로 Telegram 등 교체 가능 |
| 스케줄링 | cron | macOS 내장, 추가 설치 불필요 |
| 토큰 절약 | 로컬 전처리 + 리포트 템플릿화 | 원시 데이터 대신 요약만 Claude에 전달 (50-80% 절감) |
| 데이터 백업 | SQL 덤프 (고정 파일명) → git, 주 1회 | diff 추적 가능, 원본은 Garmin Connect에 있으므로 과도한 백업 불필요 |
| 실행 환경 | 네이티브 (Docker 없음) | 맥북 에어 8GB에서 Docker는 RAM 낭비 (1.5-2GB), 개인 프로젝트에 격리 불필요 |

---

## 데이터 백업 전략

SQLite 파일은 바이너리이므로 SQL 텍스트 덤프로 변환하여 git 관리.
고정 파일명을 사용하여 git diff로 변화 추적 가능.

```bash
# backup.sh — 주 1회 cron으로 실행
sqlite3 data/garmin.db .dump > backups/garmin.sql
sqlite3 data/garmin_activities.db .dump > backups/garmin_activities.sql
sqlite3 data/garmin_summary.db .dump > backups/garmin_summary.sql
git add backups/ && git commit -m "weekly backup $(date +%Y%m%d)" && git push
```

- 고정 파일명 → git diff로 변화 추적 (날짜별 파일은 안티패턴)
- 주 1회 빈도 — 원본은 Garmin Connect에 있으므로 과도한 백업 불필요
- 복원: `sqlite3 new.db < backups/garmin.sql`
- InBody 데이터는 iCloud Drive CSV 원본이 있으므로 별도 백업 불필요

---

## 참고 링크

- GarminDB: https://github.com/tcgoetz/GarminDB
- garminconnect (Python API): https://github.com/cyberjunky/python-garminconnect
- Health Auto Export (iOS 앱): https://www.healthyapps.dev
- Anthropic Python SDK: https://github.com/anthropics/anthropic-sdk-python
- discord.py: https://github.com/Rapptz/discord.py
- InBody (공식 사이트): https://www.inbody.com
- Open Wearables (데이터 모델 참고): https://github.com/the-momentum/open-wearables
