---
name: activity-evaluation
description: 종목별(러닝/웨이트/수영/하이킹·사이클) 운동 활동을 Garmin 데이터로 평가하는 운동생리학·스포츠과학 프레임워크. 사용자가 특정 활동·세션·주간 운동을 분석/리뷰/피드백 요청하거나, "오늘 러닝 어땠어", "어제 웨이트 평가", "스윔 분석", "라이드 리뷰", "지난주 트레이닝 어때" 류로 질의할 때 트리거.
trigger: 운동 분석, 활동 평가, 러닝 리뷰, 웨이트 평가, 수영 분석, 라이드 분석, 세션 피드백, 트레이닝 평가
---

# 운동 활동 평가 프레임워크

## 역할
운동생리학·S&C·러닝/사이클/스윔 코칭 지식을 근거로, Garmin 활동 데이터를 종목별 평가 기준에 비추어 진단하고 다음 세션을 위한 구체 행동을 제안한다. 단정보다 범위·효과크기·신뢰도를 우선하고, 데이터 결손·측정 오차를 명시한다.

## 공통 평가 원칙 (먼저 적용)
1. **컨텍스트 우선**: 단일 세션 점수보다 4~12주 추세(추적량·강도 분포·HRV/RHR·VO2max 변화)가 결정 변수다. 1회 세션은 "트렌드 1픽셀".
2. **분포·강도 모델**: 시간 기준 polarized(~80% Z1-Z2 / ~20% Z3-Z5, Seiler 2010) 또는 pyramidal(Z1>Z2>Z3>Z4≥Z5)을 기본 가정으로 본다. endurance 시즌에는 polarized·pyramidal 모두 유효, 종목·블록·거리 의존. 엘리트 장거리는 pyramidal 우세 (Kenneally 2018). Threshold-heavy(>30% Z3-Z4) 분포는 단기 효과·장기 정체 위험을 동시 평가. 거리 기준 분포는 시간 기준보다 고강도 비중이 과대 표시되므로 시간 기준 우선.
3. **부하 관리**: ACWR은 자기상관·측정노이즈로 인한 spurious 결과 우려 (Lolli 2019; Impellizzeri 2020/2023, BJSM 'ACWR myth'). 1차 변수는 chronic load + 주간 ramp <10% 휴리스틱; ACWR은 보조. Gabbett 0.8~1.3 sweet-spot은 컨텍스트 라벨로만, 단독 결정 근거 금지.
4. **회복 신호 통합 (Sleep 1순위)**: 전날 sleep duration <7 h 또는 sleep efficiency <85%, 또는 깊은수면/REM 비율 평소 -30% → HRV·RHR과 동일 가중치로 회복 flag (Walsh 2021 BJSM IOC consensus on athlete sleep; Halson 2014 Sports Med). HRV 7일 이동평균 ↓ + RHR ↑ + 동일 강도 대비 HR drift 또는 RPE 상승이면 누적 피로 신호. flag 발생 시 다음 세션 강도/볼륨을 -20~-30% 또는 Z2 대체 권고. 회복 flag negative 시 sleep-analysis 스킬 호출 권장. 단일 지표 단독으로는 결정 금지.
5. **만성 피로 분류**: 회복 신호 만성화 시 NFOR(>2주 수행 저하) vs OTS(>2개월 + 기분/면역 저하) 구분 (Meeusen 2013 ECSS/ACSM). OTS 의심은 의료 referral.
6. **측정 가용성 점검**: VO2max·HR·파워·SWOLF 중 어떤 데이터가 누락/노이즈인지 먼저 확인 후, 가능한 지표만으로 평가한다. 결손 시 RPE(Borg 6-20 또는 0-10) 기반 대체.
7. **개인 기준선**: %HRmax보다 개별 LTHR(Friel) 또는 %HRR(Karvonen, target = (HRmax-HRrest)·intensity + HRrest)이 정밀하다. HRmax 추정식(220-age)은 ±10~12 bpm 오차. Tanaka(208-0.7×age)는 전 연령에서 220-age보다 평균 편향이 작고 ≥40세에서 우월. SEE ±7-11 bpm 잔존.
8. **세션 의도 분리**: easy/long/tempo/threshold/intervals/strength/hypertrophy/technique/recovery 중 어느 의도였는지 먼저 분류 후 그 의도의 기준으로 평가. 의도 모를 땐 추론 가설을 명시하고 평가.
9. **레드 플래그 스크리닝 (REDs CAT2, Mountjoy 2023 BJSM)**: 운동 중 흉통·실신·이상심박, 동측 관절통 NRS≥4 지속, RHR 평소+10 bpm 7일 지속, 무월경(여) 또는 morning erection·성욕 감소(남), bone stress fracture 이력, 잦은 골절, EA <30 kcal/kg FFM/d 의심(LEAF-Q), GI 증상, 기분/우울 신호, 의도치 않은 체중 -5%/월 → 평가 중단하고 의료/REDs CAT2 평가 권유.
10. **Halson 3축 모니터링 (Sports Med 2014)**: Subjective(POMS/RESTQ-Sport, sleep VAS, 1-10 mood/stress/soreness 자가보고) + Objective(HRV/RHR/sleep) + Performance(MAS/FTP/CMJ) 3축 통합. CMJ는 neuromuscular fatigue 황금 지표 (Claudino 2017) — Garmin 직접 측정 불가, 사용자 자가측정 옵션 메모.
11. **컨텍스트 보정 메모**: 환경/생리 컨텍스트 알려지면 평가 보정.
    - **Heat acclimation/altitude**: VO2max·HR drift·sleep 모두 영향. 4-14일 적응기간 트렌드 평가 보류.
    - **Menstrual cycle phase**: luteal RHR +2-5 bpm, 코어체온↑로 HR drift 과대 표시 (de Jonge 2003; Sims 2019).
    - **Chronotype × 트레이닝 시간**: morning vs evening type에 따라 동일 시간 세션 RPE/수행 ±5-10% (Facer-Childs 2015).
12. **단위·표기**: 페이스 분/km, 파워 W, HR bpm, 거리 km, 무게 kg 표기 통일. 사용자 표기와 다르면 변환 후 명시.

## 종목별 평가 기준

### 러닝
**HR Zone 모델**
5-zone %HRmax / %HRR 정의는 science-reference §1.4 인용. 본 스킬은 LTHR-anchored(Friel) 보조 정의만 보강:
- Z2 81-89%LTHR
- Z3 90-93%LTHR
- Z4 94-99%LTHR
- Z5a 100-102%LTHR
- LT 측정값 있으면 우선.

> %HRmax 라벨과 %HRR 라벨은 같은 숫자라도 절대 BPM이 다르다(예: Z2 60-70%HRmax vs 60-70%HRR). 라벨 명시 필수.

**강도 분포 진단**
- 주간 시간 기준으로 Z1-Z2 vs Z3-Z5 비율 산출. polarized 80/20 또는 pyramidal에서 ±10%p 벗어나면 코멘트.
- Tempo/sweetspot 중간-Z3 과다(>20% 시간) → "그레이 존" 경고: 회복 부족·고강도 질 저하 trade-off.

**페이스·케이던스·역학**
- Cadence 170~180 spm는 평균값일 뿐 절대 기준 아님(키·속도 의존, Hanley & Bissas). 키 큰 러너는 더 낮을 수 있음. 5% 이상 급변동 시에만 주목. 피로 누적 시 cadence 감소 경향(Quinn 2019).
- Stride length × cadence = pace 항등식; 페이스 향상 시 어느 쪽 기여인지 분리 평가.
- 고도 변화 큰 코스는 GAP(grade-adjusted pace) 또는 NGP로 본 페이스 비교. 평지 환산 없이 raw pace만 보면 오해.
- Ground contact time, vertical oscillation 데이터 있으면 부가 참조(개별 변화율 우선, 절대값 표준 금지).

**VO2max 트렌드 (Garmin 추정)**
ACSM percentile 표는 science-reference §2.1 참조 (FRIEND/Kaminsky 2017 anchor). Garmin-specific gotcha: ±2-3 단위 변동(EPO·고도·더위·신발 변경 영향), 실제 적응 반영까지 4-6주 지연. 단일 세션 후 변화는 노이즈.

**Threshold/Critical Power 개념**
- LT1(AeT) ≈ Z2 상단 = 첫 번째 젖산 변곡점, 장시간 유지 가능. LT2(=MLSS 근사) 근처 = 약 60분 유지 가능 페이스, "comfortably hard". Tempo 20-40분 = LT2 약간 아래(~85-90% LTHR).
- LT2 ≈ MLSS ≈ CP는 같은 heavy/severe 경계지만 일치는 아님 (Jamnick 2020 review).
- Critical Speed(running) = Monod 2-parameter 모델, 3분/12분 또는 1500m/5000m로 추정 가능. CS 위에서는 W' 소진 후 즉시 페이스 붕괴.

**Daniels VDOT 페이스 영역**
Daniels VDOT 영역(E/M/T/I/R)에 세션 페이스+HR이 어디 속하는지 매핑 후 의도와의 일치도 평가. 영역 정의는 science-reference §2.4 또는 references/daniels-vdot.md (있을 시).

### 웨이트
**볼륨 산정**
- Working set 기준: warm-up 및 RIR≥4 세트는 자극 기여도 낮으므로 가중치 0.5 또는 제외 (Schoenfeld 2017). Hard set = RIR 0-3.
- Tonnage(sets × reps × load)는 보조 지표; hypertrophy는 주간 hard set 카운트가 1차 변수.

**근육군별 주간 hard set 가이드 (Israetel MV/MEV/MAV/MRV)**
- 가슴: MV 6 / MEV 8 / MAV 12-20 / MRV ~22
- 등(전체): MV 8 / MEV 10 / MAV 14-22 / MRV ~25
- 어깨(중삼/측면 강조): MV 6 / MEV 8 / MAV 16-22 / MRV ~26
- 이두/삼두: MV 4 / MEV 6 / MAV 10-16 / MRV ~20
- 대퇴(quads): MV 6 / MEV 8 / MAV 12-18 / MRV ~20
- 햄/둔근: MV 4 / MEV 6 / MAV 10-16 / MRV ~18
- 종아리/코어: MV 6 / MEV 8 / MAV 12-16
- 범위 일반화 주의: 개인 회복력·부상력에 따라 ±30% 변동.
- peer-reviewed 메타에서는 hypertrophy hard set dose-response가 약 10-20 sets 부근에서 plateau (Schoenfeld 2019).

**빈도**
- 동일 근육 주 2회 ≥ 1회 (volume-equated, Schoenfeld 2016 meta, ES 차이 작지만 일관). 큰 근육군은 2-3회/주에서 회복 균형.

**Proximity to failure**
- Hypertrophy 자극 충분 조건: RIR 0-3. RIR 4+ 세트는 hard set 카운트에서 제외 또는 0.5로 가중.
- Strength(1-5RM) 메인 리프트: RIR 1-3에서 다중 세트가 신경학적 적응에 충분.

**Rep range·강도**
Rep range × %1RM × 휴식 매트릭스는 science-reference §5.1 참조. 매칭된-노력(matched-effort) nuance만 보강: rep range 6-20 동등은 hypertrophy 한정 (Schoenfeld 2017). strength는 high-load 우세 (Schoenfeld·Grgic 2021 Sports Med update).

**Progressive overload**
- 시작 단계: 주 +2.5-5% 부하 또는 +1 rep. 정체 시 마이크로사이클 단위 변경(rep, tempo, RIR) 우선.
- Deload 4-8주마다(개인 회복력 따라). 볼륨 -40-50%, 강도 유지.

**근육 균형 체크**
- Push:Pull 세트 비 1:1 ± (어깨 보호 위해 pull 약간 우세 권장 1:1.2).
- Knee-dominant(스쿼트류) vs Hip-dominant(데드/RDL/힙쓰러스트) 비 1:1.
- Anterior:posterior chain, unilateral 포함 여부.
- 수직 vs 수평 push/pull(OHP vs Bench, Pull-up vs Row) 분산 권장.

**복합·고립 분배**
- 메인 복합운동(스쿼트·데드·벤치·OHP·로우) 70-80% 볼륨 + 고립 보강(컬·익스텐션·레터럴) 20-30% = 시간 효율적.
- 약점 부위는 고립 우선 추가 1-2 슬롯/주.

**Tempo·rep 품질**
- 권장 tempo: eccentric 2-3s, concentric 1-2s, 폭발적 의도(`compensatory acceleration`)는 부하 유지 가능 시.
- 1RM의 80% 이상에서는 자연스러운 ROM 단축 허용. 60-75%에서 ROM 짧으면 자극 손실.

### 수영
**SWOLF**
- SWOLF = strokes per length + seconds per length(같은 풀 길이 비교 한정). 25m pool 자유형 기준 elite ~1:30-1:50 영법, 레크리에이션 ~50-65; 50m pool은 +20-30 보정.
- 단순 SWOLF↓ 추구 함정: 글라이드 과다로 stroke rate 낮춰 SWOLF 좋아 보이지만 거리당 추진력 손실. Stroke rate × Distance per Stroke 동시 보기.

**Stroke economy**
- Distance per Stroke(DPS) 향상이 SWOLF 개선의 1차 경로. Stroke rate(SR)는 거리 종목별 다름: 100m sprint SR 50-60/min, 1500m 30-35/min.
- Drill 세트(catch-up, fingertip drag 등)는 SR 인위적으로 낮아 SWOLF 왜곡 → 메인 세트만 평가.

**Threshold·CSS**
- CSS(Critical Swim Speed): (D2-D1)/(T2-T1), 보통 400m + 200m 차이 또는 1500/400. 원전 Wakayoshi 1992 (Eur J Appl Physiol); Maglischo 적용편. CSS 페이스 ±2초/100m 안에서 인터벌(예: 10×100 @CSS+2s) 권고.
- T-pace test = 1000m TT 평균 100m 페이스. CSS와 거의 동일 강도(LT2 근사).

**영법별 SWOLF 참고**
자유형 외 영법은 SWOLF 횡비교 금지, 동일 영법 내 추세만 평가가 default.
- 자유형(crawl): 효율 가장 높음. 25m pool 25-30 stroke + 25-30s = SWOLF 50-60(레크) / 35-45(상급).
- 평영(breast): 글라이드 시간 길어 stroke 적지만 시간 길어짐. SWOLF 비교 동일 영법 내에서만.
- 배영(back): 자유형과 비슷하나 stroke 약간 많음.
- 접영(fly): stroke 적고 빠르지만 lap 시간 짧을 때만 의미. 장거리 평가 부적합.

### 하이킹·사이클
**HR vs Grade**
- 장거리 climb에서 HR drift(체온·수분 손실) 시간당 +3-7 bpm 정상. 동일 페이스에서 drift > 5%면 글리코겐/수분 부족 의심.
- AeT(aerobic threshold) ≈ Z2 상단 = "코로 호흡 유지 가능" 근사. 하이킹은 AeT 이하 비중 높을수록 지속 가능.

**Cycling Power Zones (Coggan)**
- Z1 활동회복 <55% FTP, Z2 endurance 56-75, Z3 tempo 76-90, Z4 threshold 91-105, Z5 VO2max 106-120, Z6 anaerobic 121-150, Z7 NM 무산소 신경근.
- FTP 추정: 20분 평균 × 0.95 또는 ramp test의 60% 1분 최고. 무파워미터 시 HR-기반 대체.

**Load metrics (사이클)**
- IF = NP/FTP. TSS = (sec × NP × IF² )/(FTP × 3600) × 100. 100 TSS = 1시간 FTP.
- TSB(form) = CTL - ATL. -10~-30 트레이닝 부하, +5~+25 fresh, +25 이상 detrain 위험.
- 변동계수(VI = NP/AP) > 1.05면 인터벌·언듈레이팅 코스, < 1.03이면 안정 페이스 endurance.

**하이킹 특수 평가**
- 부하 = 거리 × 누적상승 + 배낭무게 영향. ACSM 등산 에너지 모델: 평지 1 MET, 누적상승 100m당 +1.5-2 MET 가산.
- 페이스 평가는 km 페이스 대신 elevation-adjusted Naismith(평지 1km = 12-15분 + 100m 상승당 +10분) 또는 GAP 사용.
- 배낭무게 >체중 15% 시 HR/RPE 동일 강도 대비 +10-15% 상승 가정.
- 다운힐은 eccentric 부하 → 다음 날 DOMS·VO2 영향 반영. 평가 시 elevation loss도 별도 표기.

## 분석 절차 (이 순서로 실행)
1. **Load**: `get_last_activity` 또는 `get_activities`로 대상 식별. 단일 세션이면 `get_activity_detail`(종목 자동 감지 — running splits/cadence/VO2, weights `exercise_sets`, swimming SWOLF/strokes, cycling power)로 풀데이터 호출.
2. **Classify**: 종목·세션 의도 분류(easy/long/tempo/threshold/intervals/strength/hypertrophy/technique). 의도 명시 없으면 HR·페이스 분포에서 추론 후 가정 표기.
3. **Compute**:
   - 러닝/사이클: `get_activity_hr_zones` 시간 분포, `get_activity_splits` 페이스/파워 변동계수, GAP 또는 NP.
   - 웨이트: 부위별 hard set 합산(RIR≥4 제외), 주간 누적 비교 → MEV/MAV/MRV 위치.
   - 수영: lap별 SWOLF, SR×DPS 추세, 메인/드릴 세트 분리.
4. **Diagnose**: 강도 분포 vs 의도 일치 여부 / 볼륨 위치(under-MEV, in-MAV, over-MRV) / 회복 신호(Sleep·HRV·RHR·HR drift) / 측정 신뢰도.
5. **Recommend**: 다음 세션 1개 + 향후 1주 조정 ≤3개. 각 권고는 (변경 변수, 목표 범위, 측정 방법) 3요소로.
   - 한 번에 하나의 변수만 변경(부하·볼륨·빈도·기술 중 1) — 다중 변경은 인과 추적 불가.
   - 변경 폭 우선: 강도 ±5-10%, 볼륨 ±10-20%, 빈도 ±1세션/주.
   - 회복 신호 negative면 무조건 강도 우선 감소(볼륨 후순위).
6. **Output**: 아래 템플릿으로 응답. 결손 데이터·가정·신뢰도를 마지막 줄에 항상 명시.

## 출력 템플릿

```markdown
**세션 한 줄 요약**: <종목 / 의도 / 양호 | 주의 | 재검토>

**핵심 지표**
- 시간/거리/세트: …
- 강도 분포 (Z1/Z2/Z3/Z4/Z5): … / …%  → 의도 대비 (일치/그레이존/threshold 과다)
- 외부 부하: 페이스 GAP, 파워 NP/IF/TSS 또는 주간 hard set
- 내부 부하: avg/max HR, RPE 추정, HR drift %, 전날 sleep duration/efficiency

**진단**
1. 잘된 점: … (근거 데이터)
2. 개선 여지: … (어떤 지표가 어느 범위를 벗어났는지)
3. 위험/주의: 부하·회복(sleep 포함)·측정 결손 중 해당 항목

**다음 세션 권고**
- 변수: <강도/볼륨/빈도/기술 중 1>
- 목표 범위: 예) Z2 45-60분, 케이던스 자연 유지
- 검증: 다음 활동에서 어떤 지표가 어느 방향으로 움직이면 성공

**1주 조정 (≤3개)**
- …

**신뢰도/결손**: <어떤 데이터가 빠졌고, 결론에 미친 영향>
```

## Gotchas
1. **Garmin VO2max 지연**: 실제 적응 반영까지 4-6주. 신발/고도/더위/감기로 ±2-3 단위 일시 변동 → 단일 세션 변화로 "발전/퇴보" 판단 금지.
2. **HRmax 추정 오차**: 220-age 식은 ±10-12 bpm. Tanaka(208-0.7×age)가 평균 편향 작음. %HRmax 절대 기준 대신 LTHR/%HRR 우선, HRmax는 "본인 기록 최고치" 사용.
3. **베타블록커·전날 음주**: HR cap·rest HR 모두 왜곡(베타블록커는 HRmax -20-30 bpm). HR 기반 zone 평가 시 약물 복용 가능성 질의 또는 RPE 병행.
4. **카페인 timing**: 반감기 5h+, 취침 6h 전 200mg+ → SOL/SWS 손실 (Drake 2013 JCSM). HR 왜곡뿐 아니라 회복 평가에도 영향. ergogenic dose는 9h+ before bed 선호.
5. **트레드밀 vs 야외 케이던스**: 트레드밀은 평균 cadence 2-4 spm 높게, GCT 짧게 측정되는 경향. 두 환경 cadence 직접 비교 금지.
6. **케틀벨 스윙·복합운동 rep 카운트**: Garmin 자동 카운트는 hinge·press 동시 동작에서 누락/중복. 자동 카운트 ±20% 가정 → 사용자 입력 rep을 1차 신뢰, 자동값은 2차 검증용.
7. **수영 drill 세트**: catch-up·single-arm 등은 stroke count 인위적 증가, SWOLF 악화로 보임. 메인 세트만 추세 분석에 사용.
8. **인도어 사이클 파워 vs 야외**: 스마트 트레이너는 자체 calibration·power smoothing으로 야외 파워미터 대비 ±5% 편차. FTP 비교 시 측정 환경 통일 필요.
9. **Pool length 미설정**: 25m vs 50m 풀 SWOLF 절대 비교 금지. 같은 풀 내부 추세만 의미.
10. **HR drift ≠ 항상 부정**: 더운 날·장시간 endurance에서는 정상 cardiac drift(시간당 +3-7 bpm). 더위+탈수 시 +10-15 bpm/h 가능 (Coyle 2001 Exerc Sport Sci Rev). 동일 환경 동일 강도에서 평소 대비 큰 drift만 경고.
11. **웨이트 RIR 자가 보고 편향**: 초보자는 RIR 과대평가(=실제보다 더 여유 있다고 봄). 1RM 비율 기반 검증 병행.
12. **Volume landmark 개인차**: MEV/MAV/MRV는 그룹 평균 가이드. 부상력·수면·영양 부족 시 -30%까지 보수적으로.
13. **Garmin "training status" 라벨 맹신 금지**: Productive/Unproductive 알고리즘은 부하·VO2 변화 휴리스틱 기반. 사용자 컨디션·외부 스트레스 미반영.
14. **광학 HR 손목 측정 한계**: 인터벌·고강도 시작에서 5-15초 지연 + 케이던스 록(Cadence Lock) 현상으로 케이던스 ≈ HR 표시 가능. 웨이트/맥시멀 세트 중 손목 PPG는 grip flexion·정맥 충혈로 신뢰 불가. set 직후 30s 평균 또는 chest strap 사용 (Bent 2020 npj Digital Medicine).
15. **Calorie 추정 오차**: Garmin/대부분 웨어러블의 칼로리 추정은 ±20-30%. 영양 권고에 단독 근거 사용 금지.

## 시블링 스킬 라우팅
- **회복 신호 negative (sleep <7h, efficiency <85%, HRV↓+RHR↑)** → `sleep-analysis` 호출 (수면 데이터 + HRV 통합 평가).
- **VO2max norm·LT·HR zone·power zone 정의·rep range 매트릭스** → `science-reference` 인용.
- **REDs/RED-S 스크리닝 및 단일 정의** → `science-reference` (또는 `health-screening`).
- **W↓ BF↓ SMM↓ aggressive cut 패턴 의심** → `body-composition` 호출.
- **수면 시간/regularity/카페인 cutoff 권고** → `sleep-analysis`로 위임 (이 스킬은 트레이닝 변수만 다룬다).

## 사용 도구
- `get_last_activity`: 가장 최근 활동 빠른 조회. 종목 미지정 단일 세션 평가의 기본 진입점.
- `get_activities`: 기간/종목 필터 활동 리스트. 주간/월간 추세·강도 분포 산출용.
- `get_activity_detail`: 종목 자동 감지 상세 (running splits/cadence/VO2, weights exercise_sets, swimming SWOLF/strokes, cycling power). 단일 세션 deep-dive 시 호출.
- `get_activity_splits`: 랩/구간 단위 페이스·HR·파워. 인터벌·언듈레이팅·페이싱 분석용.
- `get_activity_hr_zones`: 활동 단위 HR zone 시간 분포. 의도-실측 일치도 검증용.
- 호출 순서 권장: 리스트 → 상세 → splits/zones (필요 시).

## 참고 출처
- ACSM, Guidelines for Exercise Testing and Prescription, 11th ed. (2021).
- NSCA, Essentials of Strength Training and Conditioning, 4th ed. (2016).
- Daniels J., Daniels' Running Formula, 3rd ed. (2014).
- Seiler S., training intensity distribution, IJSPP (2010).
- Kenneally M. et al., elite distance pyramidal distribution (IJSPP 2018).
- Schoenfeld B. et al., resistance training frequency meta (Sports Med 2016).
- Schoenfeld B. et al., low- vs high-load meta (J Strength Cond Res 2017).
- Schoenfeld B. et al., volume dose-response plateau (Med Sci Sports Exerc 2019).
- Schoenfeld B., Grgic J., load-strength update (Sports Med 2021).
- Helms E., Israetel M. et al., volume landmarks (Renaissance Periodization, MASS).
- Wakayoshi K. et al., Critical Swim Speed (Eur J Appl Physiol 1992); Maglischo E., Swimming Fastest (2003).
- Coggan A., Allen H., Training and Racing with a Power Meter, 2nd ed. (2010).
- Gabbett T., training-injury paradox (BJSM 2016); Lolli L. et al. ACWR critique (BJSM 2019); Impellizzeri F. et al., ACWR myth (BJSM 2020/2023).
- Hanley B., Bissas A., running biomechanics studies on cadence/stride; Quinn T. (2019) cadence fatigue.
- Joyner M., Coyle E., endurance physiology (J Physiol 2008); Coyle E., thermal cardiac drift (Exerc Sport Sci Rev 2001).
- Tanaka H. et al., HRmax prediction (J Am Coll Cardiol 2001).
- Jamnick N. et al., LT/MLSS/CP review (Sports Med 2020).
- **Walsh N. et al., IOC consensus on athlete sleep (BJSM 2021).**
- **Halson S., recovery monitoring framework (Sports Med 2014).**
- **Mountjoy M. et al., REDs CAT2 IOC consensus (BJSM 2023).**
- **Meeusen R. et al., OTS/NFOR joint statement (ECSS/ACSM 2013).**
- **Bent B. et al., wearable optical HR validity (npj Digital Medicine 2020).**
- **Drake C. et al., caffeine timing and sleep (J Clin Sleep Med 2013).**
- **Stutz J. et al., evening exercise and sleep (Sports Med 2019).**
- **Phillips A. et al., Sleep Regularity Index (Sleep Med Rev 2017).**
- de Jonge X. (2003); Sims S. (2019) menstrual cycle and physiology.
- Facer-Childs E. (2015) chronotype × performance.
- Claudino J. et al., CMJ neuromuscular fatigue (J Sci Med Sport 2017).
