---
name: sleep-analysis
description: 수면 데이터(총수면, 단계, 효율, HRV)를 PSG 기준 문헌과 wearable 검증 결과에 대비해 평가하고, 운동 부하·시간생물학·LEA/REDs 컨텍스트를 통합한 한국어 코칭 리포트를 생성하는 프레임워크. 트리거: 수면 분석, 수면 품질, 잠, 깊은 수면, REM, 수면 점수, 수면 효율, 새벽 각성
trigger: 수면 분석, 수면 품질, 잠, 깊은 수면, REM, 수면 효율, 잠 깊이, 새벽 각성, 수면 점수
---

# 수면 분석 프레임워크

## 역할
수면 의학·시간생물학(chronobiology) 관점에서 Garmin 수면/HRV/일일 요약 데이터를 평가하고, PSG(polysomnography) 기준 문헌과 wearable 검증 결과를 함께 고려한 코칭 리포트를 작성한다. 단일 야간 수치보다 7일 추세와 일관성을 우선한다.

---

## 1. 핵심 평가 축 (성인 일반 컷오프)

### 1.1 양적 지표
| 지표 | 정상 범위 | 주의 | 비고 |
|---|---|---|---|
| Total Sleep Time (TST) | 7–9 h (NSF 2015) | <7 h or >10 h | 청소년 8–10h, 65+ 7–8h |
| Sleep Efficiency (SE) | ≥85% | 75–85% borderline, <75% poor | SE = TST / Time-in-bed |
| Sleep Onset Latency (SOL) | 10–20 min | <5 min 또는 >30 min | <5 min은 sleep debt, >30 min은 insomnia 신호(주 3회+ 3개월+ 만성일 때 ICSD-3 기준 평가) |
| WASO (각성 시간) | <30 min | 30–45 분 borderline, >45 min 단편화 |
| REM latency | 약 70–110 min | 일관되게 <60 min | 우울/금단/sleep debt 의심 (단일 야간 변동은 노이즈) |

### 1.2 단계 비율 (성인, % of TST)
- N3 (deep): 13–23% — 25세 이후 연령에 따라 점진적 감소(연 0.5–1%p)
- REM: 20–25% — 새벽 후반(4·5번째 cycle)에 집중
- N1+N2 (light): 50–60%

> 위 세 범위는 **독립 envelope**이며 partition share(합 100%)가 아니다. 각각 자체 정상 범위에 들어오는지 본다. 절대 분량보다 비율과 7일 median 추세를 우선한다. 단계 비율이 정상이어도 TST가 6h 미만이면 절대량 부족으로 판단.

### 1.3 연령 가이드 (NSF 2015)
- 18–25: 권장 7–9h, 9h까지 허용
- 26–64: 7–9h
- 65+: 7–8h, deep 비율 자연 감소(8–15% 흔함) — 노화에 의한 감소를 병리로 오인 금지

---

## 2. HRV–수면 상관

### 2.1 해석 원칙 (Plews & Buchheit 2013, IJSPP)
- 야간 RMSSD(`get_hrv`)는 부교감 회복의 proxy. **개인 baseline 대비 변화**가 절대값보다 중요.
- RMSSD는 right-skewed 분포 → **raw 값 대신 ln-RMSSD**로 비교한다.
- 의미 있는 부교감 억제: **야간 ln-RMSSD의 7일 이동평균이 28–60일 chronic mean − SWC(0.5 × 개인 CV) 미만**으로 떨어졌을 때 (Plews & Buchheit 2013).
- 단일 야간 ±5–10 ms 변동, 특히 PPG 기반 측정치는 노이즈 범위(§6 Gotcha 11).
- TST 또는 deep% 감소 + ln-RMSSD 동반 하락 → 회복 부족(과훈련, 알코올, 스트레스, 질병 prodrome) 의심.
- TST 정상이지만 ln-RMSSD만 만성 하락 → 심야 음주, 늦은 식사, 만성 스트레스, 수면 무호흡, 또는 §2.1.1 LEA/REDs 가능성.

#### 2.1.1 LEA / REDs 감별 (Mountjoy 2023, BJSM REDs CAT2)
- "TST 정상 + ln-RMSSD 만성 하락"이고 명확한 훈련 부하 원인이 없을 때:
  - **저에너지가용성(LEA / RED-S)** 가능성 추가 — 무월경, 체중 급락, BMI 급감, 식사 제한 동반 시.
  - 시블링 스킬 `activity-evaluation`의 RED-S 적색 깃발 스크리닝 항목과 교차 점검 권장.

### 2.2 Overnight RMSSD validity confound
Garmin overnight RMSSD는 수면 단계 구성에 따라 편향:
- **SWS-rich 구간**에서 상승, **REM-rich 구간**에서 하강 (Plews & Buchheit 2013; Vesterinen 2016).
- → 단계 구성이 크게 다른 야간 간 직접 비교는 신중. 7일 이동평균을 기본 단위로.
- 가능하면 **morning supine 1–5 min 측정** 병행 권장 (단계 confound가 작고 신뢰도 높음).

### 2.3 운동 부하의 single-night 영향
- 전일 고부하(예: HIIT, long run, 1RM session) 또는 **22시 이후 종료 세션** 다음 야간은 HR↑·RMSSD↓·SWS 시점 이동(early-night 집중)이 **정상 급성 반응**.
- 24시간 데이터만 보고 "만성 부교감 억제"로 분류 금지.
- **7일 추세에서 패턴화**될 때(7d ln-RMSSD < chronic − SWC가 5일 이상 연속)만 회복 부채 신호로 라벨.

### 2.4 만성 억제 플래그 (operational)
- 7일 ln-RMSSD median이 chronic mean − 1×SWC 미만 + resting HR baseline +5 bpm 이상 7일 이상 지속 → "chronic suppression".
- 이 조합 충족 시 §11에서 `activity-evaluation` 회복 액션 라우팅.

---

## 3. 일관성·시간생물학 지표

### 3.1 Bedtime variability (취침시각 표준편차)
- 7일 SD <30 min: 안정
- 30–60 min: 중간
- >60 min: 일주기 불안정(circadian disruption)

### 3.2 Social jetlag (Wittmann & Roenneberg 2006)
- 주중·주말 mid-sleep(취침과 기상의 중간 시각) 차이.
- <1 h 정상, 1–2 h 경도, >2 h는 metabolic·기분 리스크 증가 보고.

### 3.3 Weekend catch-up
- 주말 TST가 주중 대비 +2 h 이상 → 누적 sleep debt **시그널**(원인 표시), 해결책 아님.
- Depner 2019 (Curr Biol): 주말 회복수면은 인슐린 감수성·체중 변화 등 대사 결과를 복원하지 못함. 따라서 catch-up 자체를 권고로 제시 금지.
- 권고: 주중 30–60분 일찍 자는 분산을 우선하고, 주말 +2h는 "현재 상태 표지"로만 주석.

### 3.4 운동 시점·강도와 수면 (Stutz 2019, Sports Med meta)
- **vigorous exercise <1h pre-bed**: SOL↑, TST↓, SE↓로 disruptive — 단, 개인차 큼.
- **≥1h pre-bed**: 영향 중립이거나 SWS 약간 증가.
- 운영 규칙: 야간 HR↑·RMSSD↓·SWS 시점 이동을 anomaly로 플래그하기 **전에 사용자에게 훈련 종료 시각/강도를 1회 확인**할 것 (Skein 2018도 동일 결론).

---

## 4. Wearable 정확도 Gotchas (반드시 사용자에게 절대 수치를 단정하지 말 것)

Chinoy 2020, de Zambotti 2019, Roberts 2020 등 검증 문헌 요약:
- TST: consumer 트래커는 PSG 대비 보통 **과대추정**(평균 +5–20 분).
- WASO: **과소추정** 경향 — "잘 잤다"가 실제보다 낙관적.
- 단계별: N3(deep)·REM 분류 정확도는 stage-level kappa 약 0.3–0.5(보통 수준). REM 분류가 가장 약함(kappa ~0.3) → wearable REM%는 7일 이상 일관된 패턴일 때만 인용.
- Sleep onset: actigraphy 기반은 누운 시각을 잠든 시각으로 판정하는 오류가 흔함.
- Sleep score (Garmin Body Battery, sleep score)는 **proprietary composite**. 알고리즘 비공개·미검증. 점수 자체에 과의존 금지, 구성 지표(SE·deep·REM·HRV)를 우선 인용.

→ **운영 규칙**: 절대값(예: "deep 1h 12m")은 "approx" 표현, 추세·효율·일관성은 비교적 신뢰. 하루치 outlier보다 7일 median에 무게.

---

## 5. 분석 의사결정 절차 (실행 순서)

1. **데이터 로드**
   - `get_sleep`: 최근 7–14일 야간별 TST, deep, light, REM, awake, SE, SOL, restless, SpO2 평균/최저, respiration.
   - `get_hrv`: 동일 기간 overnight RMSSD (ln-transform 후 사용).
   - `get_daily_summary`: resting HR, stress avg, steps(전일 활동량 → 수면 영향).
2. **결측 체크** — null vs 0.0 구분(§6 Gotcha 1). 결측 야간은 계산에서 제외하되 사용자에게 알릴 것.
3. **요약 통계 계산**
   - 7일 median, IQR, 단순 추세(최근 3일 vs 이전 4일 평균 차이).
   - SE, deep%, REM%, bedtime SD, weekend delta, 7d ln-RMSSD vs 28d chronic (SWC 단위).
4. **임계값 매칭** — §1, §2의 컷오프 적용. borderline은 borderline으로 표기, 과단정 금지. 정량 컷오프는 `science-reference` 스킬을 단일 출처로 인용(중복 정의 금지).
5. **상관 점검**
   - TST↓ + ln-RMSSD↓ + restingHR↑ → 회복 부채.
   - TST 정상 + REM% 낮음(<15%, 7일 일관) → 알코올·항우울제·수면 무호흡 의심 질문.
   - SE 낮음 + WASO 큼 + SpO2 dip → 무호흡 가능성, 의료 평가 권유.
   - **very-short SOL (<5 min) + 낮은 REM%** → severe sleep debt 가능성.
   - **very-long SOL (>30 min) + 낮은 SE** → primary insomnia 또는 anxiety axis 의심.
6. **운동 컨텍스트 점검** (§3.4, §2.3) — 전일 훈련 종료 시각·강도 확인. anomaly 플래그 전에 부하-driven 가능성 배제.
7. **출력 생성** — §7 템플릿. 한국어, 코칭 톤. wearable 한계 1줄 디스클레이머 포함.
8. **검증 루프** — 출력 전 자기 점검:
   - 절대 수치를 "approx"로 약화했는가?
   - 단일 야간이 아닌 7일 추세를 인용했는가?
   - 권고가 행동 가능(actionable)·1주 내 실행 가능한가?
   - 의학적 진단을 내리지 않았는가? (의료 권유 phrasing은 §8 및 `science-reference` Disclaimers 참고)

---

## 6. Gotchas

1. **0.0 vs missing**: `deep_sleep_seconds == 0`은 "측정 실패 또는 wearable 미착용"인 경우가 많다. None과 0을 구분하여 0이면 그날 통계에서 제외하거나 "측정 결손"으로 표기.
2. **낮잠 미반영**: Garmin은 주 수면 윈도만 단계 분석하는 경우가 많다. 사용자가 낮잠 30분+ 보고하면 "기록 외" 메모를 남기고 TST에 합산하지 말 것.
3. **알코올의 stage 왜곡**: 음주 후 night는 수면 초반 deep 증가 → 후반 REM 감소·각성 증가 패턴이 흔하다. "deep% 정상이라 좋다"고 단정 금지, 다음날 REM%·WASO를 함께 본다.
4. **늦은 카페인**: 사용자가 "잠은 잘 옴"이라 해도 16시 이후 카페인 보고 있으면 객관 SE/N3을 신뢰하고 주관 졸림은 마스킹된 것으로 처리. Drake 2013 (JCSM): **400 mg를 취침 6h 전에 섭취해도 TST가 약 1h 감소**. ergogenic 도즈(≥3 mg/kg, 보통 200–400 mg)에서는 16:00 cutoff를 **최소선**으로 보고, 고용량은 **취침 9h 전 이전** 권장.
5. **단일 야간 noise**: TST 6.2h 한 번으로 결론 내지 말 것. 7일 median과 IQR을 우선. 이상치 1개는 "이벤트성"으로 분리 코멘트.
6. **Sleep score 과신 금지**: Garmin sleep score 80은 PSG 기반 정상과 동의어가 아니다. SE·WASO·HRV가 일치하지 않으면 raw 지표를 신뢰.
7. **REM rebound 오해**: 수면 박탈 후 회복 야간에 REM%가 30%+로 튈 수 있다. 이는 회복 신호이지 이상이 아님.
8. **호흡·SpO2 하한 신호**: 평균 SpO2 95%+ 정상이지만 nadir 88% 이하 또는 episodic dip은 무호흡 의심 — 의료 평가 권유 (진단은 PSG/HSAT 필요).
9. **시차/여행/생리주기**: 컨텍스트 누락 시 오해석. 사용자에게 "최근 시차/생리주기/질병/약물 변화" 1회 확인. **시차 적응 첫 2–3일의 REM%·SE는 정상 패턴이 아니므로 라벨 분리** (Sack 2009, NEJM jet-lag review).
10. **여성 황체기**: 황체기에 core temp ↑, deep% ↓, RMSSD ↓ 흔함. 1주 변동을 병리로 오인 금지.
11. **PPG RMSSD 노이즈**: PPG(광용적맥파) 기반 RMSSD는 chest-strap RR 대비 노이즈가 크다. **단일 야간 −10 ms 정도는 측정 노이즈 범위**일 수 있음 (Stone 2021, J Sports Sci). 7일 이동평균과 SWC 기준으로만 해석.

---

## 7. 출력 템플릿 (Discord, 한국어 코칭 톤)

> 사용자가 "수면 어땠어"처럼 캐주얼하게 물으면 짧은 버전, "분석/리포트"라고 명시하면 풀 템플릿.

### 7.1 풀 템플릿

```markdown
## 한눈 보기 (최근 7일)
- 평균 TST: **{tst_median} h** (목표 7–9h, 추세 {↑/→/↓})
- 수면 효율: **{se}%** ({normal/borderline/poor})
- 단계 비율: deep {deep_pct}% · REM {rem_pct}% · light {light_pct}%
- 야간 RMSSD: **{rmssd_median} ms** · 7d ln-RMSSD vs 28d chronic ({Δ_swc} SWC)
- 취침 일관성(SD): {bedtime_sd} 분
- 전일 운동 부하: {load_summary} (HRV 변동의 부하-기인 가능성 평가용)

## 주요 발견
1. {핵심 발견 1 — 수치 근거 1개 포함}
2. {핵심 발견 2}
3. {핵심 발견 3 — 상관·트렌드}

## 권고 (이번 주 실행)
- [ ] {1주 내 행동 1}
- [ ] {1주 내 행동 2}
- [ ] {1주 내 행동 3}

## 모니터링 포인트
- 다음 7일 동안 추적할 1–2개 지표와 임계값
- 임계값 도달 시 재분석 트리거

> Wearable 데이터는 PSG 대비 단계 분류 정확도가 제한적입니다(특히 REM/deep 절대 분량). 추세와 효율 중심으로 해석했습니다.
```

### 7.2 Quick 템플릿 (캐주얼 질문용)

```markdown
**최근 7일 수면 요약**
- TST {tst} h · SE {se}% · deep {deep_pct}% · REM {rem_pct}%
- 한 줄 평가: {핵심 메시지}
- 오늘 액션 1개: {actionable}
> wearable 단계 정확도 한계로 절대값은 approx, 추세 중심 해석.
```

---

## 8. 권고 작성 원칙 (defaults, not menus)

- **한 권고당 행동 1개, 시간/빈도 명시**: "수면 위생 개선" 대신 "오늘부터 7일간 23:00 ±15분 취침, 22:30 화면 차단".
- **3개 이내**: 더 나오면 우선순위 1·2·3 매기고 나머지는 다음 주로 미룬다.
- **임상 진단 금지**: "무호흡입니다" 대신 "수면 패턴이 ___을 시사합니다, 수면 클리닉 평가 권장" 형식 (정확한 phrasing은 `science-reference` §Disclaimers를 따른다).
- **사용자 목표는 봇이 별도 주입** — 본 스킬은 일반 권고만 작성. 목표 정보가 system prompt로 들어오면 그에 맞춰 톤만 조정.

---

## 9. 사용 도구
- `get_sleep` — 야간별 단계·SE·SOL·WASO·SpO2·respiration
- `get_hrv` — overnight RMSSD (ln-transform 후 7일 이동평균으로 사용)
- `get_daily_summary` — resting HR, stress, steps (수면 영향 컨텍스트)

> 필요 시 7–14일 범위로 호출하여 7일 median과 28일 chronic baseline을 산출. 단일 야간만으로 결론 내지 말 것.

---

## 10. 참고 출처
- National Sleep Foundation. *Sleep duration recommendations: methodology and results summary.* Sleep Health, 2015 (Hirshkowitz et al.).
- AASM. *International Classification of Sleep Disorders, 3rd ed.* 2014; AASM Scoring Manual v2.6.
- Walker MP. *Why We Sleep.* Scribner, 2017.
- Carskadon MA, Dement WC. *Normal Human Sleep: An Overview.* Principles and Practice of Sleep Medicine.
- Buysse DJ et al. *The Pittsburgh Sleep Quality Index (PSQI).* Psychiatry Research, 1989.
- Wittmann M, Roenneberg T et al. *Social jetlag: misalignment of biological and social time.* Chronobiology International, 2006.
- Chinoy ED et al. *Performance of seven consumer sleep-tracking devices compared with polysomnography.* Sleep, 2020.
- de Zambotti M et al. *Wearable Sleep Technology in Clinical and Research Settings.* Med Sci Sports Exerc, 2019.
- Roberts DM et al. *Detecting sleep using heart rate and motion data from multisensor consumer-grade wearables.* Sleep, 2020.
- Van Cauter E et al. *Metabolic consequences of sleep and sleep loss.* Sleep Medicine, 2008.
- Plews DJ, Buchheit M et al. *Training adaptation and heart rate variability in elite endurance athletes.* IJSPP, 2013.
- Buchheit M. *Monitoring training status with HR measures: do all roads lead to Rome?* Front Physiol, 2014.
- Vesterinen V et al. *Heart rate variability in prediction of individual adaptation to endurance training.* Scand J Med Sci Sports, 2016.
- Stutz J et al. *Effects of evening exercise on sleep in healthy participants: a systematic review and meta-analysis.* Sports Med, 2019.
- Skein M et al. *The effect of overnight sleep characteristics following intense exercise.* Eur J Sport Sci, 2018.
- Drake C et al. *Caffeine effects on sleep taken 0, 3, or 6 hours before going to bed.* J Clin Sleep Med (JCSM), 2013.
- Depner CM et al. *Ad libitum weekend recovery sleep fails to prevent metabolic dysregulation.* Curr Biol, 2019.
- Mountjoy M et al. *2023 IOC consensus statement on REDs (RED-S CAT2).* BJSM, 2023.
- Stone JD et al. *Assessing the accuracy of popular commercial technologies that measure resting heart rate and heart rate variability.* J Sports Sci, 2021.
- Sack RL. *Jet lag.* NEJM, 2009.

---

## 11. 시블링 스킬 라우팅

- **회복 부채 → activity-evaluation 라우팅**: 7일 ln-RMSSD < (chronic mean − SWC) **AND** resting HR baseline +5 bpm 이상이 동시에 7일 이상 지속될 때, `activity-evaluation` 스킬에 "recovery flag = ON"으로 전달하여 강도/볼륨 −20–30% 감량 또는 Z2 대체를 권고하도록 한다.
- **운동+수면 동시 질의**: 사용자가 한 turn에 운동과 수면을 모두 묻거나 두 도메인이 얽힌 질문을 하면, 본 스킬에서 recovery flag를 먼저 계산한 뒤 `activity-evaluation`을 그 flag와 함께 호출한다.
- **컷오프 단일 출처**: 정량 임계값(연령별 TST 권장, SE 컷오프, RMSSD SWC 산식 등)은 향후 `science-reference` 스킬을 단일 출처로 인용한다. 본 스킬에서 새 숫자를 도입하지 않는다.
- **의료 권유 phrasing**: "수면 패턴이 ___을 시사합니다, 수면 클리닉 평가 권장" 형식. 구체 wording은 `science-reference` §Disclaimers가 존재하면 그 정의를 따른다.
- **LEA/REDs 의심 시**: §2.1.1 조건 충족 시 `activity-evaluation`의 RED-S 적색 깃발 스크리닝과 교차 점검을 명시적으로 트리거.
