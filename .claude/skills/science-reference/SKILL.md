---
name: science-reference
description: 운동·수면·체성분·영양 분야의 권위 있는 수치 컷오프(ACSM, AHA/ACC, WHO, NSCA, NSF/AASM, ISSN) 표준 레퍼런스. 다른 스킬(sleep-analysis, activity-evaluation, body-composition)이 기준값 인용 시 또는 사용자가 "기준/권장/가이드라인"을 물을 때 로드.
trigger: 기준, 권장, 가이드라인, 컷오프, ACSM, WHO, NSCA, AHA, ISSN, NSF, AASM, 정상범위, 표준, 정상치, 논문, 근거
---

# 과학적 기준 레퍼런스 (Science Reference)

> Canonical numeric cutoffs. 다른 분석 스킬은 이 문서를 인용하라.
> 본 문서는 일반인구 기준이며 개인 목표·맥락은 호출자가 적용한다.
> **표 boundary 컨벤션**: 별도 표기 없으면 inclusive. "or higher" 표기 시 lower bound inclusive.

## 0. 라우팅 맵 (시블링 스킬 핸드오프)

| 질문 유형 | 위임 스킬 | 본 문서 연결 섹션 |
|---|---|---|
| 수면 단계·코칭·심층 패턴 분석, 만성 <6h 부상 위험 | **sleep-analysis** | §6 (수면) ↔ |
| 종목별 강도·zone·페이스 평가, training-load context | **activity-evaluation** | §1.4 HR zone, §7 HRV ↔ |
| 체성분 변화·트렌드·BMI/BF%/WC 해석 | **body-composition** | §3 ↔ |
| 본 문서 cutoffs 단순 인용 | (이 스킬 자체) | — |

> 한국인 사용자 컨텍스트: BMI/허리둘레는 **Asian-Pacific (WPRO/IDF/KSSO) cuts**를 우선 적용.

## 0.1 Progressive Disclosure — references/ 로드 가이드

| 사용자 요청 | 로드할 파일 |
|---|---|
| 정확한 VO2max 백분위(연령×성별), Cooper 표, MET compendium | `references/vo2max-detail.md`를 사용자가 정확한 numeric 표를 요청할 때 로드 |
| RMSSD 연령대별 평균±SD 표 | `references/hrv-detail.md`를 사용자가 정확한 numeric 표를 요청할 때 로드 |
| 청소년/노인 등 성인 외 연령대 수면시간, N3 연령 변화 | `references/sleep-age-detail.md`를 사용자가 정확한 numeric 표를 요청할 때 로드 |

---

## 1. 심혈관 / 심박수

### 1.1 최대심박수 (HRmax) 추정식
| 공식 | 수식 | 권장 대상 | 비고 |
|---|---|---|---|
| Fox/Haskell | 220 − age | 청소년·일반 | 가장 흔하지만 SD ±10–12 bpm |
| Tanaka 2001 | 208 − 0.7 × age | 성인 전체 | 메타분석 기반, **성인 1순위** |
| Gulati (여성) | 206 − 0.88 × age | 성인 여성 | 트레드밀 검증 |

> Gotcha: Tanaka는 주로 Caucasian 코호트 검증. 동아시아 적용 시 약간의 재보정 필요할 수 있음 (Sarzynski 2013; Nikolaidis 2015).

### 1.2 안정시심박수 (RHR, AHA)
| RHR (bpm) | 분류 |
|---|---|
| < 60 | 트레인드/우수 |
| 60–70 | 정상 |
| 70–80 | 정상 상한 (정상 60–100 bpm 범위 내) |
| > 80 | 검토 권장 (단, 카페인·스트레스·탈수 제외) |

> RHR 만으로는 병리 분류 금지. 증상·트렌드 동반 시에만 검토.

### 1.3 HRR / Karvonen 공식
- HRR (Heart Rate Reserve) = HRmax − RHR
- Target HR = (HRR × intensity%) + RHR
- 예) HRmax 190, RHR 60, 70% 강도 → (130 × 0.70) + 60 = **151 bpm**

### 1.4 HR Zone — 두 시스템 병존
> **한국인 일반 적용 default: %HRR Karvonen 우선. %HRmax 단독은 노인·트레인드 인구에서 오차 큼.**

**ACSM 11e 3-zone (강도 분류 표준):**
| 강도 | %HRmax | %HRR |
|---|---|---|
| Light | <64 | 40–59 |
| Moderate | 64–76 | 40–59 |
| Vigorous | 77–95 | 60–89 |

**5-zone (Polar/Garmin convention, %HRmax 기반):**
| Zone | %HRmax | %HRR (근사) | 주요 효과 |
|---|---|---|---|
| Z1 | 50–60 | 30–40 | 회복·워밍업 |
| Z2 | 60–70 | 40–50 | 지방산화·기초유산소 |
| Z3 | 70–80 | 50–70 | 유산소 능력 |
| Z4 | 80–90 | 70–85 | 젖산역치 |
| Z5 | 90–100 | 85–100 | VO2max·무산소 |

> **동일 라벨, 다른 척도, 다른 절대 BPM**: ACSM moderate(64–76 %HRmax)와 5-zone Z2(60–70 %HRmax)는 다름. 인용 시 시스템을 명시할 것. 종목별 zone 해석은 **activity-evaluation** 스킬 참조.

### 1.5 혈압 (AHA/ACC 2017)
| 분류 | SBP (mmHg) | / | DBP (mmHg) |
|---|---|---|---|
| Normal | <120 | and | <80 |
| Elevated | 120–129 | and | <80 |
| Stage 1 HTN | 130–139 | or | 80–89 |
| Stage 2 HTN | ≥140 | or | ≥90 |
| Hypertensive crisis | >180 또는 >120 | — | — |

> Hypertensive crisis: SBP >180 **또는** DBP >120 중 어느 쪽이든 충족 시 분류. 즉시 의료 평가.
> 한국 KSH 2022 hypertension 가이드라인은 AHA/ACC와 호환되나 일부 한국인 데이터 기반 부분 — 상세는 KSH 원문 참조.

---

## 2. 유산소 능력 / VO2max

### 2.1 VO2max headline
- 측정 단위: mL/kg/min.
- 일반 성인 대략적 정상: 남성 30–45, 여성 25–40. **연령×성별 백분위 정확값은 `references/vo2max-detail.md` 로드** (FRIEND registry / ACSM 11e).
- 웨어러블 VO2max 추정 ±15% — 트렌드용.

### 2.2 MET headline
- 1 MET ≈ 3.5 mL/kg/min (좌업 기준 산소소비).
- Light <3, Moderate 3–<6, Vigorous ≥6 MET (ACSM).
- **활동별 MET 표는 `references/vo2max-detail.md` 로드** (Ainsworth 2011 Compendium).

### 2.3 Cooper 12분 달리기 → VO2max (headline)
**공식 (Cooper 1968 JAMA, canonical)**: VO2max (mL/kg/min) = (distance_m − 504.9) / 44.73
- `distance_m`: **미터(m). km 입력 금지.**
- 변환표는 `references/vo2max-detail.md` 참조.

---

## 3. 체성분 (분석은 body-composition 스킬)

### 3.1 BMI (kg/m²) — WHO Global vs Asian-Pacific (WPRO) vs KSSO
| 분류 | WHO Global | Asian-Pacific (WPRO) | KSSO (한국) |
|---|---|---|---|
| 저체중 | <18.5 | <18.5 | <18.5 |
| 정상 | 18.5–24.9 | 18.5–22.9 | 18.5–22.9 |
| 과체중 | 25.0–29.9 | 23.0–24.9 | 23.0–24.9 |
| 비만 1단계 | 30.0–34.9 | 25.0–29.9 | 25.0–29.9 |
| 비만 2단계 | 35.0–39.9 | ≥30.0 | 30.0–34.9 |
| 비만 3단계 | ≥40.0 | — | ≥35.0 |

> **한국인은 Asian-Pacific (또는 KSSO) 기준 적용**. 트렌드 해석은 body-composition 스킬에 위임.

### 3.2 체지방률 headline (ACE, American Council on Exercise)
| 남성 | 분류 | 여성 |
|---|---|---|
| 6–13% | Athletes | 14–20% |
| 14–17% | Fitness | 21–24% |
| 18–24% | Acceptable | 25–31% |
| ≥25% | Obese | ≥32% |

> 출처는 ACE (실용 분류). 연령별 percentile은 ACSM 11e 별도 참조. 디바이스간 ±3–5%p 편차 → 동일 디바이스 트렌드 우선.

### 3.3 허리둘레·비율 (질병 위험 cut)
| 지표 | 남성 위험 | 여성 위험 |
|---|---|---|
| 허리둘레 (WHO Global) | ≥94 cm (high ≥102) | ≥80 (high ≥88) |
| 허리둘레 (IDF Asian) | ≥90 cm | ≥80 cm |
| 허리둘레 (KSSO 한국) | ≥90 cm | ≥85 cm |
| WHR (허리/엉덩이) | ≥0.90 | ≥0.85 |
| WHtR (허리/키) | ≥0.50 | ≥0.50 |

---

## 4. 신체활동 권장량 (WHO 2020 / ACSM)

| 대상 | 권장량 |
|---|---|
| 성인 18–64 | 중강도 150–300 분/주 **또는** 고강도 75–150 분/주 (혹은 동등 조합) + 근력운동 ≥2일/주 (모든 주요 근육군) |
| 노인 65+ | 위 동일 + **다요소 운동(균형 + 근력 포함) ≥3일/주** (Bull et al. 2020 BJSM) |
| 어린이·청소년 5–17 | 중–고강도 평균 ≥60 분/일 + 주 3회 근·골 강화 |
| 임산부 | 중강도 ≥150 분/주 (의학적 금기 없을 시) |

추가 권장:
- 좌업시간 최소화, 가벼운 활동으로 자주 break.
- WHO 2020은 기존 **"≥10분 bouts" 규칙을 폐지** — 어떤 길이도 누적 인정.
- 더 많은 활동(>300 분/주 중강도)은 추가 이점.
- **mod ↔ vig 환산: 1 vig 분 = 2 mod 분** (강도가 동일하게 충족되어야 함).

---

## 5. 근력 / 저항성 운동 (NSCA / ACSM / Schoenfeld)

### 5.1 목표별 부하·반복 (NSCA Essentials 4e)
| 목표 | 강도 (%1RM) | 반복 | 세트 | 휴식 |
|---|---|---|---|---|
| 최대근력 | 85–100 | 1–5 | 2–6 | 2–5 분 |
| 근비대 | 67–85 | 6–12 | 3–6 | 30–90 초 |
| 근지구력 | ≤67 | 12–20+ | 2–3 | ≤30 초 |
| 파워 (단일) | 80–95 | 1–2 | 3–5 | 2–5 분 |
| 파워 (다관절) | 30–60 | 3–6 | 3–5 | 2–5 분 |

> 매칭된 노력(near-failure)이면 6–30RM 범위 내 비대 효과 유사 (Schoenfeld 2017).

### 5.2 주간 볼륨 (Schoenfeld/Israetel meta-derived)
| 주당 세트/근육군 | 효과 |
|---|---|
| <10 | 유지 가능, 성장 제한 |
| 10–20 | **효과적 범위** (대부분의 트레이니) |
| 20–30 | 상급자 일부 추가 이점, 회복 부담 |
| >30 | 수확 체감, 부상 위험 |

### 5.3 빈도·진행
- **빈도**: ≥2회/주 per muscle group (1회 대비 비대 ~+48%, Schoenfeld 2016).
- **점진적 과부하**: 초보자 주당 **2–10%** 부하 증가 가능; 중·상급자 1–3%/주 또는 사이클당.
- **Deload**: 4–8주마다 50–60% 볼륨 1주.

---

## 6. 수면 (NSF 2015 / AASM)

### 6.1 연령별 권장 수면시간 (headline — 성인 중심)
| 연령 | 권장 (h) | 적정 (h) |
|---|---|---|
| 청소년 14–17세 | 8–10 | 7–11 |
| 청년 18–25세 | 7–9 | 6–11 |
| 성인 26–64세 | 7–9 | 6–10 |
| 노인 65+ | 7–8 | 5–9 |

> 소아·영유아 가이드는 NSF 2015 원문 참조. 연령대별 추가 detail은 `references/sleep-age-detail.md` 로드.

### 6.2 정상 수면 지표 (성인)
| 지표 | 정상 |
|---|---|
| Sleep efficiency | ≥85% |
| Sleep onset latency | ≤30 분 |
| WASO (각성시간) | <30–40 분 |
| 야간 각성 | ≤1회/밤 |

> ↔ **sleep-analysis** 스킬: 만성 패턴·코칭은 위임. §6.2 지표 이상치 발견 시 hand-off.

### 6.3 성인 수면 단계 분포 (AASM, 전체수면 대비)
| 단계 | 비율 |
|---|---|
| N1 | 2–5% |
| N2 | 45–55% |
| N3 (deep) | 13–23% |
| REM | 20–25% |

> N3는 30대 이후 약 −2%/decade 감소 (Ohayon 2004). 연령 보정 필요.
> **수면 단계 임상 해석·트렌드 분석은 sleep-analysis 스킬에 위임**.

---

## 7. HRV / 자율신경

### 7.1 RMSSD 해석 원칙
- RMSSD는 **개인 baseline 추적이 핵심** — 절대값 비교는 제한적.
- 지속적 ≥1 SD 하락 = 회복부족·과훈련·질환 신호 (Plews & Buchheit).
- 7일 rolling average로 측정-노이즈 완화 권장.
- 측정 조건: 기상 직후 누운 자세, 동일 시간/디바이스.

### 7.2 RMSSD 인구 평균 headline
- 일반 성인 RMSSD 대략 20–50 ms 범위, 연령 ↑ 시 감소.
- **연령대별 평균±SD 표는 `references/hrv-detail.md` 로드** (Voss 2015 / Umetani 1998).
- ↔ **activity-evaluation** 스킬: training-load context에서 HRV 트렌드 사용.

---

## 8. 영양 / 단백질 / 수분 (ISSN, ACSM, Thomas 2016)

### 8.1 단백질 (ISSN position stand)
| 상황 | g/kg/day |
|---|---|
| 좌업 (RDA) | 0.8 |
| 일반 활동인 | 1.2–1.6 |
| 근비대·트레이닝 | 1.6–2.0 (상한 ~1.62 supporting Morton 2018 meta) |
| 다이어트 컷 | **2.3–3.1 g/kg FFM** (Helms 2014 JISSN) **또는 2.0–2.4 g/kg BW** (Phillips/ACSM/ISSN 통합) |
| 마스터스 60+ 활동인 | 1.2–2.0 |

배분 (Areta 2013): **끼당 0.4 g/kg × 4회 q3h가 동량을 2회 또는 8회 분할보다 단백 합성 우수**. 일반 가이드 끼당 0.4–0.55 g/kg, 3–5회 균등 섭취.

> Helms 2014 anchor는 **FFM (lean body mass) 기준**. BW 기준과 혼동 금지.

### 8.2 탄수화물 (ACSM/AND/DoC Joint Position — Thomas DT et al. MSSE 2016)
| 트레이닝 부하 | g/kg/day |
|---|---|
| 저강도·기술훈련 | 3–5 |
| 중강도 1h/일 | 5–7 |
| 지구성 1–3h/일 | 6–10 |
| 초지구성 4h+/일 | 8–12 |

### 8.3 지방
- 최소 0.5–1.0 g/kg, 총 칼로리의 **20% 이상** 유지 (호르몬·지용성비타민).
- 장기간 <20% 지속 비권장.

### 8.4 수분 (Sawka 2007 ACSM stand; McDermott 2017 NATA)
| 시점 | 권장 |
|---|---|
| 운동 2–4h 전 | 5–10 ml/kg (예: 70kg → 350–700ml) |
| 운동 중 | 갈증 + 0.4–0.8 L/h |
| 운동 후 회복 | **체중 손실 1kg당 1.0–1.5 L (4시간 내)** |
| 일일 baseline | 갈증 기반 충분, 소변색 옅은 노란색 |

> **EAH (exercise-associated hyponatremia) 경고**: 장시간(>2h) 또는 sodium-poor 환경에서 과수분은 EAH 위험. ad libitum / 발한율 측정 개별화 권장 (McDermott 2017 NATA, Sawka 2007 ACSM).

### 8.5 카페인 (ISSN; Gardiner 2023 Sleep Med Rev)
- 에르고제닉 용량: **3–6 mg/kg (≈200–500 mg)**, 운동 30–60분 전.
- 수면 차단: ergogenic doses는 취침 **≥9h 전 cutoff 권장** (Gardiner 2023 meta). 일반 섭취는 ≥6h 전 최소.
- >9 mg/kg 부작용·이점 정체.

---

## 9. 회복 / 부상 / RED-S

### 9.1 ACWR (Acute:Chronic Workload Ratio, Gabbett 2016)
| ACWR (7d acute / 28d chronic) | 부상 위험 |
|---|---|
| <0.8 | Undertraining (detraining) |
| 0.8–1.3 | **Sweet spot** |
| 1.3–1.5 | 주의 |
| >1.5 | 고위험 |

> Lolli 2019: 분자가 분모에 포함되어 수학적 결합(coupling) → 해석 시 **uncoupled ACWR** 또는 EWMA 권장. 절대 기준 아님, 트렌드 보조 지표로.

### 9.2 진행률 휴리스틱
- **주당 +10% 이내** (러닝 거리·세트 수·총 부하).
- 신규 자극 도입 후 첫 2주는 +5%로 보수적.

### 9.3 수면-부상 연관
- Milewski 2014 (청소년 운동선수): **수면 <8h 시 부상 위험 1.7배**, <6h 만성 시 추가 상승.
- 수면 부족 + 고강도 트레이닝 누적은 ACWR 모니터링과 별개로 단독 위험인자.
- ↔ **sleep-analysis** 스킬: 만성 <6h 패턴 발견 시 본 §9.3와 교차 hand-off.

### 9.4 RED-S / Energy Availability
- **EA cutoff: <30 kcal/kg FFM/day** = Low Energy Availability 위험 (Mountjoy 2018/2023 BJSM IOC consensus).
- 산식: EA = (energy intake − exercise energy expenditure) / FFM kg.
- 지속 시 menstrual·골·내분비·면역·심혈관 영향. 임상 평가 필요.

---

## 10. Gotchas

1. **HRmax 공식 SD ±10–12 bpm** — 공식은 인구 평균. 정밀 처방은 GXT 또는 maximal effort field test 필요.
2. **웨어러블 VO2max 추정 ±15%** — Garmin/Apple은 트렌드용. 절대값은 lab test와 차이.
3. **BMI는 근육량 misclassify** — 운동선수는 과체중·비만으로 잘못 분류. 항상 허리둘레 또는 체지방률과 병행.
4. **Asian-Pacific cut이 WHO Global보다 낮음** — 한국인은 BMI 23+ 과체중, 25+ 비만. WHO Global 30 기준 사용 시 위험 과소평가.
5. **활동 강도 누적은 가능하나 강도가 동일하게 충족되어야 함** — mod ↔ vig 환산: 1 vig 분 = 2 mod 분. 다른 강도 단순 합산 금지.
6. **ISSN 단백질 범위는 분배 섭취 가정** — 끼당 0.4 g/kg × 3–5회 전제. 1일 1식 몰빵은 상한이 더 낮을 수 있음.
7. **체지방률 측정 디바이스별 편차 ±3–5%p** — InBody/DEXA/캘리퍼/BIA 결과는 동일 디바이스로 트렌드 추적, 디바이스 간 절대값 비교 금지.
8. **HR Zone은 LTHR 기반이 더 정확** — %HRmax는 근사. 진지한 트레이닝은 lactate threshold HR 또는 critical power 기반 zone 권장.
9. **ACWR은 보조지표** — 단독 판단 금지. RPE, 수면, HRV, 통증과 종합 해석.
10. **고령자·임산부·기저질환자는 별도 가이드** — 본 문서의 일반인구 cut 직접 적용 금지.

---

## 11. 시블링 스킬 가이드 (cross-reference summary)

| 질문 유형 | 위임 스킬 | 본 문서 연결 |
|---|---|---|
| 수면 단계·코칭·심층 패턴 분석 | **sleep-analysis** | §6.2 ↔, §9.3 만성 <6h 부상 ↔ |
| 종목별 강도·zone·페이스 평가, training-load | **activity-evaluation** | §1.4 HR zone, §7 HRV ↔ |
| 체성분 변화·트렌드·목표 진단 | **body-composition** | §3 BMI/BF%/WC ↔ |
| 본 문서 cutoffs 단순 인용 | (이 스킬 자체) | — |

> 본 스킬은 **수치 컷오프 단일 출처**. 해석·코칭·트렌드는 위 시블링이 담당. 한국인 사용자 컨텍스트 — Asian-Pacific cuts 우선 적용.

---

## 12. 참고 출처

- ACSM. *Guidelines for Exercise Testing and Prescription*, 11th ed. 2021.
- Whelton et al. AHA/ACC/multisociety hypertension guideline, 2017.
- WHO. *Global Recommendations on Physical Activity for Health*, 2020 update.
- Bull FC et al. World Health Organization 2020 guidelines on physical activity and sedentary behaviour. *Br J Sports Med*, 2020.
- Hirshkowitz et al. NSF Sleep Duration Recommendations. *Sleep Health*, 2015.
- Ohayon MM et al. Meta-analysis of quantitative sleep parameters. *Sleep*, 2004.
- AASM. Practice Parameters & ICSD-3.
- Tanaka, Monahan, Seals. Age-predicted maximal heart rate revisited. *JACC*, 2001.
- Schoenfeld et al. Frequency / volume / load meta-analyses, 2016–2017.
- Helms et al. Evidence-based recommendations for natural bodybuilding contest preparation. *JISSN*, 2014.
- Morton RW et al. Protein supplementation hypertrophy meta. *Br J Sports Med*, 2018.
- Areta JL et al. Timing and distribution of protein ingestion. *J Physiol*, 2013.
- Phillips SM. Dietary protein requirements for athletes. ISSN/ACSM consensus.
- Thomas DT, Erdman KA, Burke LM. ACSM/AND/DoC Joint Position: Nutrition and Athletic Performance. *Med Sci Sports Exerc*, 2016.
- ISSN position stands: protein (Jäger 2017), caffeine (Guest 2021).
- Sawka et al. ACSM Position Stand: Exercise and Fluid Replacement, 2007.
- McDermott BP et al. NATA Position Statement: Fluid Replacement for the Physically Active, 2017.
- Gardiner C et al. Caffeine and sleep meta-analysis. *Sleep Med Rev*, 2023.
- Mountjoy M et al. IOC consensus statement on Relative Energy Deficiency in Sport (RED-S). *Br J Sports Med*, 2018/2023.
- Gabbett. The training-injury prevention paradox. *Br J Sports Med*, 2016.
- Lolli et al. Mathematical coupling of ACWR. *Br J Sports Med*, 2019.
- Plews & Buchheit. HRV monitoring in athletes — multiple reviews.
- Voss A et al. Short-term HRV — gender and age. *PLoS One*, 2015.
- Umetani K et al. 24h HRV across nine decades. *JACC*, 1998.
- Nunan et al. HRV reference values meta-analysis. *PCE*, 2010.
- Milewski et al. Sleep and adolescent athlete injury. *J Pediatr Orthop*, 2014.
- Kaminsky LA et al. FRIEND CRF registry. *Mayo Clin Proc*, 2013/2017.
- Ainsworth BE et al. 2011 Compendium of Physical Activities. *Med Sci Sports Exerc*, 2011.
- Cooper KH. A means of assessing maximal oxygen intake. *JAMA*, 1968.
- Sarzynski 2013; Nikolaidis 2015 — HRmax ethnic recalibration.
- KSH (Korean Society of Hypertension) 2022 guideline.
- KSSO (Korean Society for the Study of Obesity) clinical guideline.
- Haff & Triplett (eds). NSCA *Essentials of Strength Training and Conditioning*, 4th ed.
- Bompa & Buzzichelli. *Periodization*, 6th ed.
