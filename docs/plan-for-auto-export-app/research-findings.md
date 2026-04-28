# macOS HealthKit Swift 앱 리서치 결과

> 조사일: 2026-04-26
> 에이전트 3개 병렬 리서치 종합

---

## 핵심 결론: macOS HealthKit 앱은 현실적으로 불가능

세 가지 독립적인 조사 모두 동일한 결론에 도달했다:
**macOS에서 HealthKit을 통해 iPhone 건강 데이터를 읽는 것은 불가능하다.**

### 이유 1: Mac HealthKit 스토어는 iPhone과 분리됨

- macOS 13(Ventura)부터 HealthKit **프레임워크**는 존재하지만, 데이터 스토어는 기기별로 격리됨
- iPhone의 건강 데이터는 iCloud에 암호화 백업되지만, Mac의 HealthKit 스토어로 동기화되지 않음
- 즉, Mac에서 HealthKit 쿼리를 실행하면 **빈 결과**가 반환됨
- Apple Watch → iPhone 동기화는 자동이지만, iPhone → Mac 동기화는 존재하지 않음

### 이유 2: 유료 개발자 계정 필수 ($99/년)

| 질문 | 답변 |
|------|------|
| 무료 Apple ID로 macOS HealthKit 접근? | **불가** |
| macOS 지원 기능 목록에 HealthKit 포함? | **미포함** (iOS에만 있음) |
| 서명 없는 앱으로 HealthKit 접근? | **불가** |
| Ad-hoc 서명으로 접근? | **불가** |

- `com.apple.developer.healthkit` entitlement은 Apple이 발급하는 프로비저닝 프로파일 필요
- macOS에서는 App Store 경로가 사실상 유일한 배포 방법
- 유료 ADP($99/년) 없이는 프로비저닝 프로파일 발급 자체가 불가

### 이유 3: CLI 도구 접근 불가

| 접근 방식 | 가능 여부 |
|-----------|----------|
| Swift CLI (swift build) | **불가** — entitlement 서명 불가 |
| macOS GUI 앱 (.app bundle) | 프레임워크는 있으나 데이터 없음 |
| Mac Catalyst (iOS → Mac 포팅) | `isHealthDataAvailable()` → `false` |
| iOS 앱 Apple Silicon 실행 | `isHealthDataAvailable()` → `false` |

---

## macOS HealthKit 상세 현황

### 프레임워크 가용성

| macOS 버전 | HealthKit 프레임워크 | Health 앱 | 비고 |
|-----------|---------------------|----------|------|
| 12 (Monterey) | 없음 | 없음 | |
| 13 (Ventura) | 있음 | 없음 | 최초 지원, 데이터 스토어 비어있음 |
| 14 (Sonoma) | 있음 | 없음 | Mac Catalyst 개선 |
| 15 (Sequoia) | 있음 | 없음 | |
| 26 (2026 베타) | 확장 | 미정 | 약물 추적 API 추가, 아직 베타 |

### 쿼리 가능한 데이터 타입 (API 자체는 존재)

| 타입 | macOS API | 실제 데이터 |
|------|-----------|-----------|
| bodyMass (체중) | macOS 13+ | 없음 (iPhone에서 안 넘어옴) |
| bodyFatPercentage | macOS 13+ | 없음 |
| leanBodyMass | macOS 13+ | 없음 |
| heartRate | macOS 13+ | 없음 |
| heartRateVariabilitySDNN | macOS 13+ | 없음 |
| sleepAnalysis | macOS 13+ | 없음 |
| stepCount | macOS 13+ | 없음 |
| restingHeartRate | macOS 13+ | 없음 |
| vo2Max | macOS 13+ | 없음 |

> API는 있지만 데이터가 비어있으므로 의미 없음

---

## 실제로 동작하는 대안들

### 대안 A: Health Auto Export 앱 (권장)

- **비용**: $6.99/년 (연간 구독)
- **방식**: iPhone 앱 → REST API로 JSON 자동 전송
- **자동화**: 시간 간격 설정 가능 (매시간 등)
- **데이터 포맷**: 구조화된 JSON
- **서버 필요**: Mac에서 Flask/FastAPI 엔드포인트 실행

```
iPhone (Health Auto Export)
  → HTTP POST JSON
    → Mac의 Python Flask 서버 (포트 5050)
      → data/inbody.csv 또는 SQLite
        → Discord 봇이 읽어서 분석
```

관련 오픈소스:
- [apple-health-ingester](https://github.com/irvinlim/apple-health-ingester) — Go 서버, InfluxDB 지원
- [health-auto-export-server](https://github.com/HealthyApps/health-auto-export-server) — Node.js + Grafana

### 대안 B: ai-health-sync-ios (TLS P2P 동기화)

- **비용**: 무료 (오픈소스)
- **방식**: iOS 앱이 TLS 서버 실행 → macOS CLI가 데이터 요청
- **설치**: `brew tap mneves75/tap && brew install healthsync`
- **사용**: `healthsync fetch --types steps,heart_rate --start 2025-01-01`
- **한계**: iOS 앱을 직접 빌드해야 함 (무료 Apple ID로 가능)

```
iPhone (ai-health-sync-ios 앱, TLS 서버)
  ← Mac CLI (healthsync fetch)
    → JSON stdout
      → Python subprocess로 호출
```

### 대안 C: Apple Health XML 수동 내보내기 + 파싱

- **비용**: 무료
- **방식**: iPhone 건강 앱 → 내보내기 → XML 파싱 스크립트
- **자동화**: 수동 (주기적으로 내보내기 필요)
- **현재 상태**: 927MB XML 파일 이미 보유

파싱 도구:
- [apple-health-exporter](https://github.com/mganjoo/apple-health-exporter) — Python, XML → Pandas
- [applehealth2csv](https://github.com/muquit/applehealth2csv) — CLI, XML → CSV/JSON

### 대안 D: iOS 단축어 (Shortcuts)

- **비용**: 무료
- **방식**: 단축어로 건강 데이터 쿼리 → HTTP POST
- **자동화**: 반자동 (트리거 설정 필요, 잠금 해제 시만)
- **문서**: `docs/ios-shortcut-guide.md` 참고

### 대안 E: Discord 채팅 직접 입력 (현재 방식)

- **비용**: 무료
- **방식**: "인바디 결과 체중 72kg 체지방률 15.2%" → 봇이 파싱
- **자동화**: 수동
- **현재 상태**: 이미 구현 완료

---

## 비용-효과 비교

| 대안 | 비용 | 난이도 | 자동화 | 데이터 범위 | 추천 |
|------|------|--------|--------|-----------|------|
| A. Health Auto Export | $6.99/년 | ★☆☆ | 완전 자동 | 전체 | **최우선** |
| B. ai-health-sync-ios | 무료 | ★★★ | CLI 호출 | 전체 | 개발자용 |
| C. XML 파싱 | 무료 | ★★☆ | 수동 | 전체 (시점) | 일회성 분석 |
| D. iOS 단축어 | 무료 | ★★★ | 반자동 | 선택적 | 복잡함 |
| E. 채팅 입력 | 무료 | ★☆☆ | 수동 | 체성분만 | 현재 방식 |
| ~~F. macOS Swift 앱~~ | ~~$99/년~~ | ~~★★★★~~ | ~~자동~~ | ~~없음~~ | **불가** |

---

## 출처

### Apple 공식 문서
- [HealthKit Documentation](https://developer.apple.com/documentation/healthkit)
- [HealthKit Entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.healthkit)
- [macOS Supported Capabilities](https://developer.apple.com/help/account/reference/supported-capabilities-macos/)
- [iOS Supported Capabilities](https://developer.apple.com/help/account/reference/supported-capabilities-ios/)
- [Configuring HealthKit access](https://developer.apple.com/documentation/xcode/configuring-healthkit-access)

### 개발자 커뮤니티
- [Apple Forums: Can I use HealthKit in macOS app?](https://developer.apple.com/forums/thread/94937)
- [Swift Forums: Entitlements with SwiftPM](https://forums.swift.org/t/use-macos-entitlements-with-swiftpm-or-swift-command-line/42230)
- [MacRumors: HealthKit has no iCloud sync](https://forums.macrumors.com/threads/healthkit-has-no-icloud-sync.1797092/)

### 오픈소스 프로젝트
- [Health Auto Export](https://github.com/Lybron/health-auto-export)
- [ai-health-sync-ios](https://github.com/mneves75/ai-health-sync-ios)
- [apple-health-ingester](https://github.com/irvinlim/apple-health-ingester)
- [apple-health-exporter](https://github.com/mganjoo/apple-health-exporter)
- [applehealth2csv](https://github.com/muquit/applehealth2csv)

### 기타
- [HealthKit macOS Xcode 26 beta APIs](https://github.com/dotnet/macios/wiki/HealthKit-macOS-xcode26.0-b1)
- [macOS code signing gist](https://gist.github.com/rsms/929c9c2fec231f0cf843a1a746a416f5)
- [WWDC20: Synchronize health data with HealthKit](https://developer.apple.com/videos/play/wwdc2020/10184/)
