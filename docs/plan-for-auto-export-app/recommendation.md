# Apple Health 데이터 자동 수집 — 권장 계획

> 기반: research-findings.md (2026-04-26 리서치 결과)
> 2차 리서치 반영 (Health Auto Export 상세 + InBody 동기화 흐름)
> 3차 반영: 실제 앱 설치 후 JSON 구조 검증 + 코드베이스 통합 분석 (2026-04-26)

---

## 사용자 목표

> 매일 아침 InBody 측정 → Apple Health에 자동 동기화 → Discord 봇이 체성분 데이터를 알고 다이어트 피드백에 활용

---

## 결론 요약

1. macOS Swift HealthKit 앱은 **불가능** (iPhone 데이터가 Mac HealthKit 스토어로 동기화되지 않음)
2. Health Auto Export 앱은 **iCloud Drive에 CSV/JSON 파일을 자동으로 써줌** → 서버 불필요
3. Mac에서 Python이 **iCloud Drive 로컬 경로의 파일을 그냥 읽으면 됨**

---

## 전체 데이터 흐름

```
InBody 체중계 (매일 아침 측정)
  │  BLE/Wi-Fi
  ▼
InBody 앱 (iOS)
  │  HealthKit 자동 쓰기 (앱 설정에서 1회 활성화)
  ▼
Apple Health (iPhone)
  │  weight, body_fat_pct, lean_body_mass, bmi 저장됨
  ▼
Health Auto Export 앱 (iOS, Premium $6.99/년)
  │  iCloud Drive Automation (매일 자동, 잠금 해제 시)
  ▼
iCloud Drive (클라우드)
  │  자동 동기화 (수초~수분)
  ▼
Mac 로컬 파일시스템
  ~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/
    Documents/daily inbody/HealthAutoExport-2026-04-26.json
  │
  ▼
Python tasks.loop (매 1시간 폴링)
  │  core/apple_health_reader.py — JSON 파싱 → data/inbody.csv에 upsert
  │  새 데이터?
  ├─ NO → 종료
  └─ YES
      ▼
    Discord 채널(NOTIFY_CHANNEL_ID)에 스레드 자동 생성
      │  _collect_health_context() → llm.ask_with_context()
      ▼
    Claude 분석 결과 자동 전송 → 사용자 후속 질문 가능 (기존 스레드 대화)
```

**서버 불필요. HTTP 불필요. 파일만 읽으면 됨.**

---

## Health Auto Export 앱 상세

### 앱이 해주는 것

Apple Health 데이터를 **자동으로** 지정한 위치에 CSV/JSON 파일로 내보내주는 iOS 앱.

### 내보내기 방법 (6가지)

| 방법 | 서버 필요 | 우리가 쓸 것 |
|------|----------|-------------|
| **iCloud Drive** (CSV/JSON) | **불필요** | **이것** |
| Google Drive | 불필요 | - |
| Dropbox | 불필요 | - |
| REST API (HTTP POST) | 필요 | - |
| MQTT | 필요 | - |
| Home Assistant | 필요 | - |

### 가격

| 티어 | 가격 | 자동 내보내기 |
|------|------|-------------|
| 무료 | $0 | 위젯/차트만 |
| Basic | $2.99 (1회) | 수동 내보내기만 |
| **Premium 연간** | **$6.99/년** | **자동 스케줄** |
| Premium 월간 | $1.99/월 | 자동 스케줄 |
| Premium 평생 | $24.99 (1회) | 자동 스케줄 |

> 7일 무료 체험으로 Premium 전체 기능 테스트 가능

### 자동화 설정 옵션

| 설정 | 옵션 |
|------|------|
| 주기 | 5분~60분 / 1~24시간 / 1~7일 / 주간 |
| 기간 | "마지막 동기화 이후" / "오늘" / "어제" / "최근 7일" |
| 포맷 | CSV 또는 JSON |
| 위치 | iCloud Drive 폴더 (자동 생성) |

**추천 설정**: 주기 = 매 1시간, 기간 = "마지막 동기화 이후", 포맷 = JSON

### JSON 출력 포맷 (실제 검증됨)

```json
{
  "data": {
    "metrics": [
      {
        "name": "weight_body_mass",
        "units": "kg",
        "data": [
          { "qty": 104.59999999999999, "date": "2026-04-26 12:47:00 +0900", "source": "InBody" },
          { "qty": 0, "date": "2026-04-26 22:30:00 +0900", "source": "단축어" }
        ]
      },
      {
        "name": "body_fat_percentage",
        "units": "%",
        "data": [
          { "qty": 32.700000000000003, "date": "2026-04-26 12:47:00 +0900", "source": "InBody" }
        ]
      },
      {
        "name": "lean_body_mass",
        "units": "kg",
        "data": [
          { "qty": 70.399993896484375, "date": "2026-04-26 12:47:00 +0900", "source": "InBody" }
        ]
      },
      {
        "name": "body_mass_index",
        "units": "count",
        "data": [
          { "qty": 30.199999999999999, "date": "2026-04-26 12:47:00 +0900", "source": "InBody" }
        ]
      }
    ]
  }
}
```

> **주의**: 파일명은 `HealthAutoExport-YYYY-MM-DD.json`. 메트릭 순서는 파일마다 다를 수 있으므로 `name` 기반 탐색 필수.

### 데이터 품질 주의사항

- **혼합 소스**: 같은 메트릭에 `source: "InBody"`와 `source: "단축어"` (qty=0) 항목이 섞일 수 있음 → `source == "InBody" and qty > 0` 필터 필요
- **부동소수점 노이즈**: `lean_body_mass`가 `70.399993896484375` 같은 값으로 기록됨 → 소수점 2자리 round 처리
- **날짜 gap**: InBody 측정을 안 한 날(예: 4/11)은 파일 자체가 없음

---

## InBody → Apple Health 데이터 매핑

### 동기화되는 필드 (4개)

| InBody 측정값 | Apple Health 타입 | 단위 |
|--------------|------------------|------|
| 체중 | `bodyMass` | kg |
| 체지방률 | `bodyFatPercentage` | % (0.0~1.0) |
| 제지방량 | `leanBodyMass` | kg |
| BMI | `bodyMassIndex` | count (Apple Health 내부 표기) |

### 동기화되지 않는 필드 (InBody 앱에만 존재)

- **골격근량 (SMM)** — Apple Health에 해당 필드 없음
- 내장지방 레벨
- 기초대사량 (BMR)
- 체수분량 (TBW/ICW/ECW)
- 부위별 근육/지방 분석
- InBody 점수

### 중요: 골격근량 vs 제지방량

| | 골격근량 (SMM) | 제지방량 (LBM) |
|---|---|---|
| 정의 | 골격근만 | 체중 - 지방 (근육+뼈+장기+수분) |
| 일반 범위 | 체중의 40~50% | 체중의 60~75% |
| InBody 앱 | 정확히 표시 | - |
| Apple Health | **없음** | `leanBodyMass`로 저장 |

**실용적 대응**: 
- `data/inbody.csv`의 `muscle_mass_kg` 컬럼은 Apple Health 경유 시 LBM 값이 들어감
- `source` 컬럼으로 구분: `"apple_health"` (LBM) vs `"inbody"` (SMM, 채팅 직접 입력)
- 분석 스킬에서 source에 따라 다른 기준값 적용 필요

**대안**: InBody 앱에서 직접 골격근량을 Discord에 입력하면 정확한 SMM 값 유지 가능 (현재 방식)

---

## Mac에서 iCloud Drive 파일 읽기

### 파일 경로 (실제 검증됨)

```python
from pathlib import Path

# .env의 APPLE_HEALTH_EXPORT_DIR로 오버라이드 가능
HAE_DIR = Path.home() / "Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/daily inbody"
```

> 파일명 패턴: `HealthAutoExport-YYYY-MM-DD.json`

### 중요: "Keep Downloaded" 설정

Finder에서 `Health Auto Export` 폴더 우클릭 → **"지금 다운로드"** 또는 **"항상 이 Mac에 유지"**
→ iCloud가 파일을 클라우드 전용으로 evict하지 않도록 방지

### Python 구현 예시 (실제 JSON 구조 반영)

```python
import json
from pathlib import Path

METRIC_MAP = {
    "weight_body_mass": "weight_kg",
    "body_fat_percentage": "body_fat_pct",
    "lean_body_mass": "muscle_mass_kg",   # LBM, not SMM
    "body_mass_index": "bmi",
}

def sync_from_icloud(hae_dir: Path, body_metrics_mgr) -> list[dict]:
    """iCloud Drive의 모든 JSON을 읽어 inbody.csv에 upsert. 반환: 새로 upsert된 행 목록."""
    existing = {(r["date"], r.get("source", "")) for r in body_metrics_mgr.read_all()}
    new_rows = []
    for f in sorted(hae_dir.glob("HealthAutoExport-*.json")):
        try:
            with open(f) as fp:
                raw = json.load(fp)
        except (json.JSONDecodeError, OSError):
            continue  # 부분 쓰기 또는 .icloud placeholder → 다음 폴링에서 재시도

        row = {}
        for metric in raw["data"]["metrics"]:
            key = METRIC_MAP.get(metric["name"])
            if not key:
                continue
            # source="InBody"이고 qty > 0인 항목만 취함
            valid = [d for d in metric["data"] if d.get("source") == "InBody" and d["qty"] > 0]
            if valid:
                row[key] = round(valid[-1]["qty"], 2)
                row["date"] = valid[-1]["date"][:10]

        if row.get("date"):
            row["source"] = "apple_health"
            is_new = (row["date"], row["source"]) not in existing
            body_metrics_mgr.upsert_entry(**row)   # 날짜+source별 덮어쓰기
            if is_new:
                new_rows.append(row)                # Claude 분석 트리거용
    return new_rows
```

> **참고**: `body_metrics.py`에 `upsert_entry()` 메서드 추가 필요 — 같은 `(date, source)` 조합이면 덮어쓰기, 없으면 신규 추가.

---

## 구현 계획

### Phase 1: 즉시 실행 — 기존 XML 파싱 (무료)

이미 보유한 `내보내기.xml` (927MB)에서 과거 체성분 이력 추출.

```
scripts/parse_apple_health.py
  → data/apple_health_body.csv (과거 체성분 이력)
```

### Phase 2: 자동화 — Health Auto Export + iCloud Drive

1. [x] iPhone에서 Health Auto Export 앱 설치 (완료)
2. [x] Automations → iCloud Drive → 메트릭: 체중, 체지방률, 체질량(LBM), 체질량지수(BMI)
3. [x] 주기: 매 1시간, 기간: "마지막 동기화 이후", 포맷: JSON
4. [x] Mac Finder에서 `Health Auto Export` 폴더 "항상 이 Mac에 유지" 설정
5. [x] 실제 JSON 구조 검증 완료 (20일치 데이터 확인)
6. [ ] `core/body_metrics.py` 수정 — `upsert_entry()` 메서드 추가 (date+source별 덮어쓰기)
7. [ ] `core/apple_health_reader.py` 구현 — iCloud JSON 파싱 + upsert 호출 + 새 데이터 반환
8. [ ] `.env`에 `APPLE_HEALTH_EXPORT_DIR` + `NOTIFY_CHANNEL_ID` 추가
9. [ ] `bot/main.py` 수정 — `tasks.loop` 백그라운드 동기화 + 자동 분석 피드백

### 자동 피드백 흐름 (방식 B)

새 데이터 감지 시 단순 알림이 아니라, **Claude가 분석한 피드백을 자동 전송**한다.

```
tasks.loop(hours=1) 실행
  │
  ▼
iCloud 폴더에서 JSON 파일 스캔
  │  CSV에 없는 날짜 감지?
  │
  ├─ NO → 아무것도 안 함
  │
  └─ YES
      │
      ▼
    CSV에 upsert
      │
      ▼
    NOTIFY_CHANNEL_ID 채널에 스레드 생성
      │
      ▼
    _collect_health_context() 호출 (기존 함수 재사용)
      │  Garmin 데이터 + 체성분 CSV + 최근 트렌드 수집
      ▼
    llm.ask_with_context() 호출 (기존 함수 재사용)
      │  "새 체성분 데이터가 들어왔다. 분석해줘" 프롬프트
      ▼
    Claude 분석 결과를 스레드에 전송
      │
      ▼
    사용자가 스레드에서 후속 질문 가능 (기존 스레드 대화 흐름)
```

### 사용자 경험 타임라인

```
07:00  인바디 측정
07:01  InBody 앱 → Apple Health 자동 동기화
~      Health Auto Export가 다음 주기에 JSON 생성 (1시간 이내)
~      iCloud → Mac 동기화 (수초~수분)
~      봇 폴링이 새 데이터 감지

~08:00~09:00  봇이 채널에 스레드 생성 + Claude 분석 결과 전송:
        (최대 ~2시간: HAE 주기 1h + iCloud 동기화 + 봇 폴링 1h)
        "📊 오늘의 체성분 데이터 (2026-04-27)
         체중 104.3kg (-0.3), 체지방률 32.5% (-0.2%p)

         [Claude 분석]
         체중이 꾸준히 감소하면서 제지방체중은 유지되고 있어
         지방 위주로 잘 빠지고 있습니다..."

~08:30  사용자가 스레드에서 후속 질문 가능
```

### 호출 타이밍 전략

**봇 시작 시 1회 + 1시간 주기 백그라운드** (`discord.ext.tasks.loop`)

- Health Auto Export 앱 주기를 1시간으로 설정 → 폴링도 1시간
- `_collect_health_context()` + `llm.ask_with_context()` 재사용 → 새 코드 최소화
- `on_ready()`에서 태스크 시작, `before_loop`에서 `wait_until_ready()` 호출
- `on_ready()` 재호출(reconnect) 대비 `is_running()` 체크

### 중복 처리 전략

- **(date, source)** 조합이 같으면 최신 값으로 덮어쓰기
- 수동 입력(source="manual"/"inbody")과 자동 수집(source="apple_health")은 별도 행으로 공존
- Claude 분석은 **CSV에 새 (date, source) 조합이 추가된 경우에만** 트리거 (기존 행 업데이트 시에는 호출하지 않음)

### 구현 시 주의사항

| 심각도 | 이슈 | 해결 방향 |
|--------|------|----------|
| **높음** | `handle_health_query()`가 `discord.Message` 객체에 의존 → 백그라운드 태스크에서 직접 재사용 불가 | 분석 핵심 로직(`_collect_health_context()` → `llm.ask_with_context()` → 결과 전송)을 별도 함수로 분리하여 `handle_health_query()`와 백그라운드 태스크 양쪽에서 공유 |
| **높음** | 스레드 생성 API 차이: `message.create_thread()` vs `channel.create_thread()` | 백그라운드 태스크에서는 `channel.create_thread(name=..., type=discord.ChannelType.public_thread)` 사용. `on_ready()`에서 `fetch_channel(NOTIFY_CHANNEL_ID)`로 채널 객체를 캐싱 |
| **중간** | `_collect_health_context()`가 동기 함수 (Garmin HTTP 블로킹) | `asyncio.run_in_executor()`로 스레드풀 실행. 또는 당장은 허용하고 장기적으로 async화 |
| **중간** | 백그라운드 생성 스레드의 세션 미등록 → 후속 질문 시 컨텍스트 유실 | 스레드 생성 후 `session_mgr.update_activity(thread.id)` 명시 호출 |
| **중간** | 동시 Garmin API 호출 → Rate Limit(429) 위험 | `asyncio.Lock()`으로 Garmin 호출 직렬화, 또는 예외 catch 후 graceful skip |
| **중간** | iCloud 파일 부분 쓰기 → `json.JSONDecodeError` | `try/except (JSONDecodeError, OSError): continue` 패턴 적용 (코드 예시에 반영 완료) |

### 구현 비용

| 항목 | 비용 |
|------|------|
| Health Auto Export Premium | $6.99/년 (또는 $24.99 평생) |
| 서버 | **불필요** |
| 개발자 계정 | **불필요** |
| 코드 변경 | `apple_health_reader.py` 신규 + `body_metrics.py` upsert 추가 + `bot/main.py` 자동 분석 태스크 |
| HAE 앱 주기 | **1시간** (설정 완료) |

---

## Garmin vs Apple Health 역할 분담

| 데이터 | Garmin Connect | Apple Health (via HAE) | 비고 |
|--------|---------------|----------------------|------|
| 심박수 | **주력** | - | Garmin이 더 정확 |
| HRV | **주력** | - | |
| 수면 | **주력** | - | |
| 활동/운동 | **주력** | - | |
| 스트레스 | **주력** | - | Garmin 전용 |
| **체중** | - | **주력** (InBody→AH→HAE) | 매일 자동 |
| **체지방률** | - | **주력** | 매일 자동 |
| **제지방량** | - | **주력** (LBM) | 매일 자동 |
| **BMI** | - | **주력** | 매일 자동 |

**결론**: Garmin = 운동/수면/심박, Apple Health = 체성분(InBody). 깔끔한 역할 분리.

---

## 대안 (Health Auto Export 외)

| 방법 | 비용 | 자동화 | 서버 | 비고 |
|------|------|--------|------|------|
| **Health Auto Export** | $6.99/년 | 완전 자동 | 불필요 | **권장** |
| iOS 단축어 → iCloud 파일 저장 | 무료 | 반자동 | 불필요 | 설정 복잡 |
| Discord 채팅 직접 입력 | 무료 | 수동 | 불필요 | 현재 방식, SMM 정확 |
| XML 내보내기 + 파싱 | 무료 | 수동 | 불필요 | 일회성 분석용 |
