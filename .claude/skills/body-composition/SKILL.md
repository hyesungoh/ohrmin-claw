---
name: body-composition
description: 체성분(체중·체지방률·골격근량·BMI·허리둘레) 트렌드 분석, 다이어트/증량/리컴포지션 페이즈 평가, 측정법 신뢰도 판정, 인바디 vs 가정용 체중계 데이터 해석을 위한 프레임워크. 체중·체지방·근육량·BMI·인바디·허리둘레·다이어트·증량·감량·리컴프 등 키워드에서 트리거.
trigger: 체성분, 체중, 체지방, 근육량, 골격근, BMI, 인바디, 허리둘레, FFMI, 다이어트, 감량, 증량, 벌크, 리컴프, recomp
---

# 체성분 분석 프레임워크

## 역할
인바디 CSV(`data/inbody.csv`) + 자가 입력 데이터를 분석해 체중·체지방률·골격근량·BMI 트렌드를 해석하고, 측정 노이즈를 걸러내며, 다이어트/증량/리컴포지션 페이즈를 평가한다. 진단(질병)·처방(영양/약물)은 하지 않는다. **본 스킬은 성인 대상**이며 소아·청소년은 out-of-scope (별도 성장곡선 기반 평가 필요).

## 측정법 신뢰도 (높음 → 낮음)

```
DXA  ≈  4-compartment model
   >  BodPod ≈ ADP (air displacement)
   >  underwater weighing (UWW)
   >  multi-frequency BIA (인바디 770/970, MC-980)
   >  skinfold (ISAK Level 2-3 측정자 기준)
   >  single-frequency BIA (가정용 인바디 H30, 로컬 헬스장 기기)
   >  bioimpedance scale (체중계형 BIA — 발만 닿는 형식)
```

핵심 포인트:
- **DXA / 4C**: 연구 골드 스탠다드. CV ≈ 1-2% BF. 단 hydration·posture·식사 영향 받음 (Toomey 2017 Sports Med) — 같은 조건 표준화 필요. 일반인이 자주 찍기엔 비용·방사선 부담.
- **인바디(multi-frequency BIA)**: 그룹 평균 DXA 대비 ±2-3% BF, **개인 LoA(Bland-Altman) ±5-7%BF** (Esco 2019; Antonio 2019). >30%BF 비만층, 수분 변동 직후(운동·식사·생리)에는 정확도 급락.
- **Skinfold (Jackson-Pollock 등)**: ISAK Level 2-3 측정자에 한해 CV<2%; 일반 PT는 CV 3-5%로 가정용 BIA보다 우수하다 보장 못함.
- **Single-frequency BIA / 발만 닿는 체중계**: 트렌드 추적용. 절대값 신뢰 낮음.
- **공통**: 같은 기기·같은 조건에서 **반복 측정한 변화량(Δ)** 이 절대값보다 신뢰도 높다.

### 프로젝트 source 컬럼 매핑
CSV의 `source` 컬럼 ↔ 신뢰도 ladder:
- `inbody` — multi-frequency BIA tier (헬스장/의료기관 770·970·MC-980 가정). 트렌드·절대값 모두 활용.
- `manual` — 사용자 자가 입력. 가정용 BIA 또는 체중만 측정한 경우 다수. **체중은 신뢰, BF/SMM 절대값은 보수적으로**.
- `unknown` — 하위 호환용. 출처 표시 누락 → 트렌드 참고만.
- 비교 시 같은 source끼리 묶어서 Δ 분석. 혼합 비교는 명시적 caveat.

## 측정 조건 표준화 (인바디·가정용 BIA 공통)
- 아침, **공복 ≥4시간**, 배뇨/배변 직후
- **운동 후 12시간 경과** (일시 탈수·근비대 노이즈 제거)
- 음주·고염식·고탄수 식사 다음 날 X
- 같은 시간대·같은 옷차림·같은 기기
- 여성: 생리 주기 기록 (난포기 측정 권장)
- **수분 상태만으로도 일간 0.5–1.5%BF 변동** 가능 → 단일 측정으로 판단 금지

## 핵심 지표 cutoffs (descriptive only)

> 본 스킬은 *해석*에 집중하고 출처·숫자는 `science-reference` 스킬에 위임한다.

### BMI
WHO Global / WPRO Asian-Pacific BMI 컷오프 표는 science-reference §3.1 참조. 한국인 평가 시 WPRO + KSSO 2018 우선.

- BMI **U-curve mortality** 동아시아 cohort에서 healthy range가 더 좁다(≈22–25, Zheng 2017 NEJM Asia Cohort Consortium; Chen 2013 Lancet Asia). 한국인은 동아시아 코호트 우선.
- BMI는 근육량·체형 무시 → 단독 비만 진단 금지 (Prentice & Jebb 2001 Obes Rev; Romero-Corral 2008 Int J Obes — BMI vs DXA misclassification ~50%).

### 체지방률
ACSM/ACE descriptive ranges는 science-reference §3.2 참조 (출처 ACE — ACSM 11e는 age-stratified percentile). 본 스킬은 "설명적, prescriptive 아님" + 개인 목표는 goals.md/system prompt에서 주입된다는 원칙만 명시.

### FFMI (Fat-Free Mass Index)
- 공식: `FFMI = FFM_kg / (height_m)^2`
- 키 보정: `FFMI + 6.1 × (1.8 − height_m)` (남성 기준).
- **FFMI 계산 시 인바디 SMM 대신 FFM = weight × (1 − BF%/100) 으로 환산 후 사용**. SMM은 FFM의 약 50–55%이므로 직접 대입 시 FFMI 과소평가됨.
- Kouri 1995의 자연인 ~25는 N=157 보디빌더 표본 기준 분포 상단 (95th percentile 근사)이지 절대 상한이 아님. 키 작은 lean 운동선수에서 25-26 초과 사례 다수 보고. **"상위 분포"로 해석**.

### 허리둘레 (WC)
허리둘레 컷오프(IDF 2006 Asian, KSSO 2018 한국)는 science-reference §3.3 참조.
- 출처 정정: WC cutoff은 **IDF 2006 + WHO 2008 Expert Consultation + KSSO 2018**(한국). Ross 2020 (J Intern Med)은 cutoff 제정이 아니라 'WC를 vital sign으로 측정·기록' 권고. 한국인은 KSSO 여 ≥85cm / 남 ≥90cm 적용.
- 본 스킬 운영 메모: 현재 CSV 스키마에 허리둘레 없음 — 사용자에게 수동 측정·기록 권장 (manual 메모 형태).

## 페이즈별 평가 원칙

### 감량 (Cut)
- **권장 속도: 0.5–1%/주 체중 감소** (Helms 2014, JISSN). >1%/주는 LBM loss 위험 ↑.
- LBM 보존 요인 (처방이 아닌 평가 체크리스트):
  - 단백질 **1.6–2.4 g/kg/day** (Phillips 2016, ISSN protein stand)
  - 저항 운동 자극 유지
  - **modest deficit** (extreme deficit 회피)
  - 수면 ≥7h (cut 페이즈 동시 모니터링; 수면 5.5h vs 8.5h 비교에서 fat loss 동일하나 LBM loss 늘고 fat loss 비율 감소 — Nedeltcheva 2010 Ann Intern Med)
- **Refeed/diet break는 evidence mixed**. MATADOR (Byrne 2018) intermittent 방법은 fat loss 보존 우월 보고. 명시적 사용자 요청 시에만 평가에 포함, 기본 권고에서는 제외.

### 증량 (Lean Bulk)
- **트레이닝 경험자: 0.25–0.5%/주**
- **초보자(novice)**: 0.5–1%/주 가능
- 너무 빠른 증량 → fat gain 비율 ↑. 체지방률 동시 모니터링.

### 리컴포지션 (Recomp) 가능성
| 대상 | 가능성 |
|---|---|
| 초보자 (training age <1yr) | 매우 높음 |
| 리턴(detrained) | 높음 |
| 고체지방 (남>20%, 여>28%) | 중간-높음 |
| 트레이닝된 lean (남<12%, 여<20%) | 매우 느림 |

→ recomp 의심 시 **장기(8–12주) 트렌드** 봐야 판별. 근거: Longland 2016 (40% deficit + 2.4 g/kg protein → simultaneous fat loss + LBM gain in young men); Antonio 2014 high-protein overfeeding; Barakat 2020 narrative review.

### 정체기(Plateau) 진단
체중·BF가 2주 이상 정체 시 다음 후보 제시:
1. **Adaptive thermogenesis** — 체중 감량 후 RMR ~10–15% suppression이 수년 지속 가능 (Rosenbaum & Leibel 2010 Int J Obes; Fothergill 2016 'Biggest Loser 6yr' Obesity). Plateau를 단순 adherence drift로 오판 금지.
2. **Water/glycogen shift** — Glycogen 1g당 water 3-4g (Olsson & Saltin 1970). 총 글리코겐 ~400-500g → 저탄수 시작 2-4일 2-3kg shift는 정상 노이즈.
3. **Measurement noise** — 단일 측정 vs 이동평균 비교
4. **Adherence drift** — 주말 보상섭취·미기록
5. **Sleep/stress 악화** → cortisol/grehlin/leptin 변동·수분저류 (Spiegel 2004). plateau 진단 시 수면 효율·HRV 동시 점검 → sleep-analysis 호출.

## 트렌드 분석 (필수)

### 노이즈 처리
- **일일 체중은 ±1–2 kg 변동** (글리코겐, 수분, 나트륨, 음식 잔류, 변통).
- **항상 7일 rolling mean 또는 ≥2주 윈도우**로 평가.
- 체지방률은 측정 노이즈가 더 큼 → **2-3주 이동평균** 기본.
- "어제 1kg 빠졌어요" 같은 단일 데이터 포인트로 판단 금지.

### 의미 있는 변화 신호
- ≥2주 일관된 추세
- 이동평균 기울기 > 측정 노이즈(체중 ±0.5kg/주, BF ±0.5-1%)
- 체중·BF·SMM 세 지표가 **방향 일치** 시 신뢰도 ↑

### 체중 × 체지방률 × 근육량 동시 변화 패턴
| 패턴 | 해석 |
|---|---|
| W↓ BF↓ SMM↓ (BF 감소 < W 감소비율) | **Aggressive cut** — LBM loss 동반, 속도 점검 + activity-evaluation 호출(저항운동 볼륨 retention 확인) |
| W↓ BF↓ SMM≈ | **Ideal cut** — 목표 패턴 |
| W≈ BF↓ SMM↑ | **Recomposition** — 초보·리턴 사례 |
| W↑ BF≈ SMM↑ | **Lean bulk** — 이상적 증량 |
| W↑ BF↑ SMM↑ (BF↑ > SMM↑) | **Dirty bulk** — 잉여 칼로리 과다 |
| W≈ BF↑ SMM↓ | **Skinny fat 진행** — 자극·단백질 부족 |

**Phantom recomp 패턴**: W↓ BF↑ — 측정 노이즈/수분 변동 의심. 같은 source·같은 조건으로 **재측정** 권장. 단일 측정으로 패턴 단정 금지.

## Cardiometabolic flag (신규)

다음 중 하나라도 해당 시 cardiometabolic risk flag 작동:
- 허리둘레 KSSO/IDF cutoff 초과 (한국인 남 ≥90 / 여 ≥85)
- BMI ≥WPRO 비만 (≥25)
- 체지방률 obese 범주 (남 ≥25%, 여 ≥32%)
- 6개월 이상 W↑ BF↑ 패턴 지속

→ 사용자에게 다음을 자가 인지 가능 형태로 안내 (진단·처방 금지):
- 최근 측정한 **혈압(BP)**, **공복혈당/HbA1c**, **지질 패널(TC, LDL, HDL, TG)** 값이 있는지 확인.
- 없으면 측정 권유 (검진/외래).
- ATP III / IDF metabolic syndrome 기준 (참고용): WC 컷오프 + 다음 중 2개 — TG ≥150, HDL <40(남)/<50(여), BP ≥130/85, FPG ≥100.
- 출처: NCEP ATP III 2001 / IDF 2006 / KSSO 2018. 진단·처방은 의료진 영역.

## Sarcopenic obesity flag (신규)

대상: 50세 이상 OR 패턴 BF↑ + SMM↓ 동시 진행.
- EWGSOP2 2019 + AWGS 2019 (Asian Working Group) 기준:
  - SMI cutoff: 남 BIA <7.0 kg/m², 여 <5.7 kg/m² (science-reference §3.4 참조)
  - Grip strength: 남 <28kg, 여 <18kg (AWGS 2019)
- Sarcopenia flag 시 **저항운동 + 단백질 우선** 권고 → activity-evaluation 호출하여 운동 볼륨/강도 retention 점검.
- 진단은 의료진 영역. 봇은 패턴 인지·재측정·전문가 상담 권유까지.

## OSA 위험 cross-link
다음 모두 충족 시 STOP-BANG 자가체크 안내 + sleep-analysis 호출:
- BMI ≥30 또는 WPRO 비만 ≥25
- 목둘레 男 ≥43cm / 女 ≥40cm (사용자 보고)
- 코골이/주간 졸림 보고

## 분석 절차 (numbered)

1. **`get_body_metrics_history`** — 최근 4-12주 데이터 로드 (질문 맥락에 따라 윈도우 조정).
2. **결측·falsy 처리** — `muscle_mass_kg / bmi 가 0.0인 행은 CSV 입력 누락이므로 missing 처리`. CLAUDE.md 명시대로 falsy `or ""` 체크는 금지하고 `value is None or value == 0`으로 명시 비교한다.
3. **Source 컬럼 확인** — `manual` / `inbody` / `unknown` 비율 확인. inbody와 manual을 **혼합 비교 시 주의**(정확도 차이). 가능하면 같은 source끼리 트렌드 분리.
4. **Smoothing** — 7일 rolling mean (체중), 14-21일 rolling (BF, SMM). 데이터 부족 시 가능한 윈도우로 축소.
5. **Slope 계산** — `get_body_metrics_trend` 호출 또는 단순 선형회귀. 주당 변화율(%/wk) 추출.
6. **페이즈 분류** — 위 패턴 표 적용. Phantom recomp 의심 시 재측정 권고.
7. **Outlier flag** — 단일 일자가 ±2 SD 벗어나면 측정 조건 의심으로 별도 표기.
8. **Plateau 체크** — 최근 14일 slope 절댓값이 노이즈 임계 미만이면 정체 진단.
9. **Risk flag 평가** — Cardiometabolic / Sarcopenic obesity / OSA 트리거 점검.
10. **출력 템플릿 작성**.

## 출력 템플릿 (Korean health-coach tone)

### Standard 템플릿

```markdown
## 한눈 보기
- 기간: YYYY-MM-DD ~ YYYY-MM-DD (N일, 측정 M회)
- 체중: 시작 X.X → 최근 Y.Y kg (Δ Z.Z, 주당 W.W%)
- 체지방률: A.A% → B.B% (Δ C.C%p)
- 골격근량: D.D → E.E kg (Δ F.F)
- BMI: G.G (WHO / WPRO 범주)
- 허리둘레: H.H cm (KSSO 컷오프 대비) — 미기록 시 "측정 권장"
- Source: inbody N회 / manual M회 / unknown K회

## 핵심 트렌드
- 7일 이동평균 기준 [감량 / 증량 / 정체 / 리컴프 / phantom recomp] 패턴
- 의미 있는 추세 여부 (시작 vs 최근 비교, 노이즈 대비)

## 해석
- 페이즈 분류 근거 (W·BF·SMM 동시 변화 패턴 인용)
- 측정 신뢰도 코멘트 (source 일관성, 측정 조건 추정)
- 변동성 평가 (주중 노이즈 폭)

## Risk flag (해당 시만)
- Cardiometabolic flag: WC/BMI/BF/지속 패턴 트리거 → BP·FPG·지질 측정 권유
- Sarcopenic obesity flag: 50세+ or BF↑ SMM↓ → 저항운동 + 단백질 + activity-evaluation
- OSA flag: BMI≥30 + 목둘레 + 코골이 → STOP-BANG + sleep-analysis

## 권고 (관찰 기반, 처방 아님)
- 측정 일관성 개선 포인트 (조건 표준화)
- 트렌드 검증 위한 추가 데이터 수요
- 사용자 목표(goals.md)와 정렬 여부 코멘트

## 모니터링 포인트
- 다음 측정 권장 시점
- 함께 추적하면 좋은 지표 (허리둘레, 운동 볼륨, 수면)
- 재측정 권장 outlier 일자
```

### Quick 템플릿 (sleep-analysis 7.2 스타일, 캐주얼 질의용)

```markdown
**한 줄**: [페이즈 한 단어 + 주당 변화율]

- 체중 7d MA: X.X kg (Δ Z.Z, W.W%/wk)
- BF/SMM: A.A% / D.D kg (방향: ↓↑→ 일치 여부)
- 신호 강도: 노이즈 대비 [강/약/판단 보류]
- 포인트: [한 줄 코멘트 — 측정 조건 / 재측정 / 페이즈 align]
```

## Caveat — 인구·성별 적용 범위
본 framework는 sex-binary 한계. 트랜스/인터섹스, 노년층, 인종 차이(흑인 BMD↑, 동아시아 BMI 대비 visceral fat↑ — Lear 2010 Am J Clin Nutr; WHO 2004) 적용 시 **해석 보수적으로**. 소아·청소년은 out-of-scope.

## Gotchas (must check)

1. **0.0 ≠ missing이지만 도메인상 무의미**: `muscle_mass_kg=0.0`, `bmi=0.0`은 사용자가 미기재한 케이스. `or ""` 식 falsy 체크 금지(CLAUDE.md 명시), `value is None or value == 0`으로 둘 다 missing 처리.
2. **BMI false-positive**: 근육질·운동선수는 BMI 27+ 여도 BF 정상 (Prentice & Jebb 2001 Obes Rev; Romero-Corral 2008 Int J Obes — DXA 대비 misclassification ~50%). BMI만으로 비만 단정 금지.
3. **인바디 hydration sensitivity**: 운동 직후·식사 직후·고탄수 다음날 → 근육량 *과대*, 체지방률 *과소* 표시. 측정 조건 추정해서 코멘트.
4. **Cross-device 비교 금지**: 헬스장 인바디 ≠ 가정용 H30 ≠ 의료기관 770. **source가 다르면 절대값 비교 보류**, 같은 source끼리 Δ만 비교.
5. **아침 vs 저녁 1–2 kg**: 같은 날도 시간대 차이 큼. 시간대 정보 없으면 "조건 통제 필요" 코멘트.
6. **여성 생리 주기**: ±1–2 kg 수분 변동. 황체기 측정값은 별도 윈도우.
7. **나트륨/탄수 부하**: 외식·치팅 다음 날 +1–3 kg 가능. 단일 일자 outlier로 처리.
8. **고체지방군 BIA 부정확**: BF >30%에서 BIA는 fat을 *underestimate* (수분 분포 모델 한계). 인바디 절대값 대신 트렌드 위주.
9. **속옷·옷 무게**: manual 입력 시 옷 입은 채 측정한 데이터 섞이면 노이즈 +0.5-1kg.
10. **체중계 calibration drift**: 같은 기기도 시간 지나면 오차. ±0.5kg 점프는 calibration 의심.
11. **Manual 입력 typo**: 73.5 → 75.3 같은 자릿수 실수. ±2 SD 벗어난 단일값은 사용자에게 확인 요청.
12. **BMI 단독 비만 진단 금지**: 허리둘레·BF·체형 종합 평가 필요.
13. **단기 데이터 < 2주는 트렌드 판단 보류**: 노이즈 우세. "더 많은 측정 필요" 명시.
14. **SMM ≠ FFM**: FFMI 계산 시 인바디 SMM을 그대로 넣으면 안 됨. FFM ≈ weight × (1 − BF%/100).
15. **DXA/4C도 표준화 필요**: hydration·posture·식사 영향 (Toomey 2017). 기기 종류와 무관하게 측정 조건 일관성이 우선.

## 시블링 스킬 라우팅
- **science-reference**: BMI WHO/WPRO/KSSO, 허리둘레 IDF/KSSO, ATP III/IDF metabolic syndrome, EWGSOP2/AWGS sarcopenia cutoff, ACSM/ACE BF% — 출처/숫자는 science-reference 위임. 본 스킬은 해석에 집중.
- **sleep-analysis**:
  1. cut 중 수면 <7h → LBM loss 위험↑ (Nedeltcheva 2010)
  2. BMI ≥30 / WC ≥cutoff / 목둘레 큰 사용자 → STOP-BANG OSA 스크리닝 트리거
  3. plateau 진단 시 수면 효율·HRV 동시 점검 (Spiegel 2004 cortisol·grehlin/leptin)
- **activity-evaluation**: W↓ BF↓ SMM↓ aggressive cut 패턴 감지 시 저항운동 볼륨/강도 retention 점검 트리거. Sarcopenic obesity flag 시 동일.
- **goals.md** (시스템에 의해 주입): 페이즈 분류 결과 vs 사용자 목표(감량/리컴프/유지) mismatch 시 알림.

## 사용 도구
- `get_body_metrics_history(days)` — 기간별 측정 이력 로드
- `get_body_metrics_trend(weeks)` — 주간/월간 추세·기울기
- `add_body_measurement(date, weight_kg, body_fat_pct, muscle_mass_kg, bmi, source)` — 신규 측정 기록 (자연어 입력 후 확인 시)
- 빌트인 도구(Read, Bash 등)는 CSV 직접 검증·계산 보조용

## 참고 출처 (short cites)
- Helms, Aragon, Fitschen 2014 — *Evidence-based recommendations for natural bodybuilding contest preparation*, JISSN
- Phillips & Van Loon 2011; Phillips 2016 — protein for athletes / muscle protein synthesis
- Jäger et al. 2017 — ISSN position stand: *Protein and exercise*
- Longland et al. 2016 — high-protein deficit recomposition (Am J Clin Nutr)
- Antonio et al. 2014 — high-protein overfeeding (JISSN); Barakat 2020 narrative review
- Byrne et al. 2018 — MATADOR intermittent diet break (Int J Obes)
- Rosenbaum & Leibel 2010; Fothergill 2016 — adaptive thermogenesis / Biggest Loser 6yr
- Nedeltcheva 2010 — sleep restriction body composition (Ann Intern Med)
- Spiegel 2004 — sleep × cortisol/leptin/ghrelin
- Olsson & Saltin 1970 — glycogen-water binding
- Esco 2019; Antonio 2019 — BIA Bland-Altman LoA
- Toomey et al. 2017 — DXA standardization (Sports Med)
- Prentice & Jebb 2001 Obes Rev; Romero-Corral 2008 Int J Obes — BMI misclassification
- Zheng 2017 NEJM Asia Cohort Consortium; Chen 2013 Lancet Asia — 동아시아 BMI U-curve
- Lear 2010 Am J Clin Nutr — 인종별 visceral fat
- Kouri 1995 — FFMI distribution upper bound (해석 주의)
- ATP III 2001 / IDF 2006 / KSSO 2018 — metabolic syndrome / WC cutoff
- EWGSOP2 2019; AWGS 2019 — sarcopenia cutoff
- Ross et al. 2020 — WC as vital sign (cutoff 제정 아님)
- science-reference 스킬 — 모든 컷오프 수치 1차 출처
