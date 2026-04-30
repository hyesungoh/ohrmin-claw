# iOS 단축어로 Apple Health → ohrmin-claw 연동 가이드

## 개요

iOS 단축어(Shortcuts)를 사용하여 Apple Health 데이터를 ohrmin-claw로 자동 전송하는 방법.

> **전제 조건**
> - iPhone (iOS 16+)
> - 단축어 앱 (기본 설치됨)
> - Apple Health에 InBody 등 체성분 데이터가 동기화되어 있을 것
> - ohrmin-claw에 HTTP 수신 엔드포인트가 구현되어 있을 것 (아래 서버 설정 참고)

---

## 1. 단축어 만들기

### Step 1: 새 단축어 생성

1. iPhone에서 **단축어** 앱 열기
2. 우측 상단 **+** 탭
3. 이름: `ohrmin-claw 내보내기` _(스크린샷 갱신 필요)_

### Step 2: 날짜 변수 설정

| 순서 | 액션 | 설정 |
|------|------|------|
| 1 | **현재 날짜** | 변수명: `오늘` |
| 2 | **날짜 조절** | `오늘`에서 7일 빼기 → 변수명: `시작일` |

### Step 3: 건강 데이터 쿼리

각 데이터 타입별로 **"건강 샘플 찾기"** 액션을 추가합니다.

#### 체중

| 필드 | 값 |
|------|-----|
| 유형 | 체중 (Body Mass) |
| 시작일 | `시작일` 변수 |
| 종료일 | `오늘` 변수 |
| 정렬 | 최신순 |
| 제한 | 1개 (최신값만) |

→ 결과를 변수 `체중결과`에 저장

#### 체지방률

| 필드 | 값 |
|------|-----|
| 유형 | 체지방률 (Body Fat Percentage) |
| 시작일 | `시작일` 변수 |
| 종료일 | `오늘` 변수 |
| 정렬 | 최신순 |
| 제한 | 1개 |

→ 결과를 변수 `체지방결과`에 저장

#### 제지방량 (골격근량)

| 필드 | 값 |
|------|-----|
| 유형 | 제지방 체중 (Lean Body Mass) |
| 시작일 | `시작일` 변수 |
| 종료일 | `오늘` 변수 |
| 정렬 | 최신순 |
| 제한 | 1개 |

→ 결과를 변수 `제지방결과`에 저장

### Step 4: JSON 사전 생성

**"사전"** 액션 추가 후 아래 키-값 입력:

```json
{
  "date": "2026-04-26",
  "weight_kg": 72.5,
  "body_fat_pct": 15.2,
  "muscle_mass_kg": 34.5,
  "source": "ios_shortcut"
}
```

실제 설정:

| 키 | 타입 | 값 |
|----|------|-----|
| `date` | 텍스트 | `오늘` 변수 (날짜 형식: yyyy-MM-dd) |
| `weight_kg` | 숫자 | `체중결과`의 값 |
| `body_fat_pct` | 숫자 | `체지방결과`의 값 |
| `muscle_mass_kg` | 숫자 | `제지방결과`의 값 |
| `source` | 텍스트 | `ios_shortcut` |

> **팁**: 날짜 형식을 `yyyy-MM-dd`로 맞추려면 "날짜 포맷" 액션을 사용하세요.
> `오늘` 변수 → 날짜 포맷 → 사용자 지정: `yyyy-MM-dd`

### Step 5: HTTP POST 전송

**"URL 콘텐츠 가져오기"** (Get Contents of URL) 액션 추가:

| 필드 | 값 |
|------|-----|
| URL | `http://{맥북IP}:5000/api/health` |
| 방식 | POST |
| 헤더 | `Content-Type`: `application/json` |
| 헤더 | `Authorization`: `Bearer {토큰}` |
| 요청 본문 | JSON — Step 4의 사전 |

> **로컬 네트워크**: 맥북과 같은 Wi-Fi에 있어야 합니다.
> 맥북 IP 확인: `시스템 설정 → Wi-Fi → 세부사항 → IP 주소`

### Step 6: 결과 알림

**"알림 보내기"** 액션 추가:

- 성공 시: `✅ 체성분 데이터 전송 완료`
- 실패 시: **"조건문(if)"** 으로 응답 코드 확인 → `❌ 전송 실패`

---

## 2. 자동화 설정 (선택)

수동 실행이 아닌 자동 실행을 원하면:

1. 단축어 앱 → **자동화** 탭
2. **개인용 자동화** → **+**
3. 트리거 선택:

| 트리거 | 추천도 | 설명 |
|--------|--------|------|
| **매일 시간** (예: 오전 9시) | ★★★ | 가장 안정적 |
| **Wi-Fi 연결** (집 Wi-Fi) | ★★☆ | 귀가 시 자동 실행 |
| **충전 시작** | ★★☆ | 매일 충전 습관 있으면 유용 |

4. "실행할 단축어" → `ohrmin-claw 내보내기` 선택
5. **"실행 전 묻기" 끄기** (완전 자동화)

### 주의사항

- **잠금 해제 필수**: iOS는 잠금 상태에서 건강 데이터 접근을 차단합니다
- **첫 실행 시 권한 요청**: Apple Health 읽기 권한을 허용해야 합니다
- **네트워크 필요**: 맥북 서버가 켜져 있고 같은 네트워크에 있어야 합니다

---

## 3. 서버 설정 (ohrmin-claw 측)

### 구현 필요: HTTP 엔드포인트

현재 ohrmin-claw에는 HTTP 서버가 없습니다. 아래를 구현해야 합니다.

#### 필요한 작업

1. `core/webhook.py` — Flask/FastAPI 엔드포인트
2. `.env`에 `SHORTCUT_API_TOKEN` 추가
3. `bot/main.py`에서 Discord 봇 + HTTP 서버 동시 실행

#### 엔드포인트 스펙

```
POST /api/health
Content-Type: application/json
Authorization: Bearer {SHORTCUT_API_TOKEN}

Body:
{
  "date": "2026-04-26",         // ISO 날짜 (필수)
  "weight_kg": 72.5,            // 체중 kg (선택)
  "body_fat_pct": 15.2,         // 체지방률 % (선택)
  "muscle_mass_kg": 34.5,       // 골격근량 kg (선택)
  "bmi": 22.1,                  // BMI (선택)
  "source": "ios_shortcut"      // 출처 (자동)
}

Response 201:
{ "status": "success", "date": "2026-04-26" }

Response 401:
{ "error": "Unauthorized" }
```

#### 데이터 저장 경로

수신된 데이터는 기존 `data/inbody.csv`에 행 추가됩니다.
컬럼: `date, weight_kg, body_fat_pct, muscle_mass_kg, bmi, source`

---

## 4. 대안: Discord Webhook 방식 (서버 구현 없이)

HTTP 서버를 만들기 전에 더 간단한 방법:

### 설정

1. Discord 서버 → 채널 설정 → 연동 → 웹훅 만들기
2. 웹훅 URL 복사

### 단축어 수정

Step 5의 URL을 Discord 웹훅 URL로 변경:

```
URL: https://discord.com/api/webhooks/{id}/{token}
방식: POST
본문:
{
  "content": "인바디 결과 체중 72.5kg 체지방률 15.2% 골격근량 34.5kg"
}
```

→ Discord 채널에 메시지로 올라오고, 봇이 기존 자연어 파싱으로 처리합니다.

- **장점**: 서버 코드 변경 없음, 지금 바로 가능
- **단점**: Discord 경유, 메시지 형식 맞춰야 함

---

## 5. 쿼리 가능한 Apple Health 데이터 타입 참고

| 카테고리 | 타입 | 단축어 이름 |
|----------|------|-------------|
| 체성분 | 체중 | Body Mass |
| 체성분 | 체지방률 | Body Fat Percentage |
| 체성분 | 제지방량 | Lean Body Mass |
| 체성분 | BMI | Body Mass Index |
| 심박 | 심박수 | Heart Rate |
| 심박 | 안정시 심박 | Resting Heart Rate |
| 심박 | HRV | Heart Rate Variability |
| 수면 | 수면 분석 | Sleep Analysis |
| 활동 | 걸음 수 | Steps |
| 활동 | 활동 에너지 | Active Energy |
| 활동 | 운동 시간 | Exercise Time |
| 호흡 | 혈중 산소 | Blood Oxygen |
| 호흡 | 호흡률 | Respiratory Rate |

> **참고**: Garmin 데이터는 Apple Health에 제한적으로 동기화됩니다.
> 심박/수면/활동은 기존 `GarminConnectClient`로 가져오는 것이 더 정확합니다.
> iOS 단축어는 주로 **InBody 체성분 데이터** 수집에 활용하세요.

---

## 트러블슈팅

| 문제 | 해결 |
|------|------|
| "건강 데이터에 접근할 수 없음" | 설정 → 건강 → 데이터 접근 및 기기 → 단축어 → 읽기 허용 |
| 서버 연결 실패 | 같은 Wi-Fi인지 확인. 맥북 방화벽에서 포트 허용 |
| 데이터가 비어있음 | Apple Health에 해당 데이터가 실제로 있는지 확인 |
| 자동화가 실행 안 됨 | "실행 전 묻기" 꺼져있는지 확인. 잠금 해제 상태인지 확인 |
| 날짜 형식 오류 | "날짜 포맷" 액션으로 `yyyy-MM-dd` 명시 |
