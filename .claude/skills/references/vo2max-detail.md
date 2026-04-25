# VO2max Detail Reference

> Load when 사용자가 정확한 VO2max 백분위·Cooper·MET 표를 요청할 때.
> Anchor: ACSM *Guidelines for Exercise Testing and Prescription* 11e (2021), FRIEND registry (Kaminsky 2017 Mayo Clin Proc), Ainsworth 2011 Compendium, Cooper 1968 JAMA.

## 1. VO2max 백분위 (FRIEND registry, Kaminsky 2017 + ACSM 11e, ml/kg/min)

> 모든 백분위 값은 **approx ±3 mL/kg/min**. FRIEND (Fitness Registry and the Importance of Exercise National Database) 코호트 기반.

### 남성
| 연령 | Low (10p) | Fair (30p) | Good (50p) | Excellent (70p) | Superior (90p) |
|---|---|---|---|---|---|
| 20–29 | <38 | 38–43 | 44–48 | 49–53 | ≥54 |
| 30–39 | <35 | 35–40 | 41–45 | 46–50 | ≥51 |
| 40–49 | <32 | 32–37 | 38–42 | 43–47 | ≥48 |
| 50–59 | <28 | 28–33 | 34–38 | 39–43 | ≥44 |
| 60–69 | <25 | 25–29 | 30–34 | 35–39 | ≥40 |

### 여성
| 연령 | Low (10p) | Fair (30p) | Good (50p) | Excellent (70p) | Superior (90p) |
|---|---|---|---|---|---|
| 20–29 | <30 | 30–34 | 35–39 | 40–44 | ≥45 |
| 30–39 | <28 | 28–32 | 33–36 | 37–41 | ≥42 |
| 40–49 | <25 | 25–29 | 30–33 | 34–38 | ≥39 |
| 50–59 | <22 | 22–26 | 27–30 | 31–35 | ≥36 |
| 60–69 | <20 | 20–23 | 24–27 | 28–32 | ≥33 |

> 한국인 코호트는 별도 검증 부족 — FRIEND 미국 인구 기반. 절대값보다 트렌드 우선.

## 2. Cooper 12분 달리기 (Cooper 1968 JAMA)

**공식 (canonical)**: VO2max (mL/kg/min) = (distance_m − 504.9) / 44.73
- `distance_m`: **미터(m)**. km 입력 금지.

| 거리 (m) | 추정 VO2max (mL/kg/min) |
|---|---|
| 1600 | 24.5 |
| 2000 | 33.4 |
| 2400 | 42.4 |
| 2800 | 51.4 |
| 3200 | 60.3 |

> 다른 공식형(예: 35.97×km − 11.29)은 산출치가 위 표와 다르며 본 스킬에서는 비채택.

## 3. MET Compendium (Ainsworth 2011 MSSE, 발췌, approximate)

| 활동 | MET |
|---|---|
| 좌업 | 1.0 |
| 걷기 4 km/h | 3.0 |
| 걷기 6.5 km/h | 5.0 |
| 조깅 8 km/h | 6.0–7.0 |
| 러닝 10 km/h | 9.8 |
| 러닝 12+ km/h | 11.5–12 |
| 자전거 16–19 km/h | 6.8 |
| 자전거 20–25 km/h | 8.0 |
| 자전거 >25 km/h | 10+ |
| 수영 자유형 보통 | 6.0 |
| 수영 빠르게 | 8.0–9.8 |
| 근력운동 보통 | 3.5–6.0 |

> 값은 *2011 Compendium of Physical Activities* 기반 근사치. 개인 효율·기술에 따라 ±15%.

## Citations
- Kaminsky LA et al. The Importance of Cardiorespiratory Fitness in the United States: The Need for a National Registry. *Mayo Clin Proc*, 2013/2017 (FRIEND).
- Ainsworth BE et al. 2011 Compendium of Physical Activities. *Med Sci Sports Exerc*, 2011.
- Cooper KH. A means of assessing maximal oxygen intake. *JAMA*, 1968;203(3):201-204.
- ACSM. *Guidelines for Exercise Testing and Prescription*, 11th ed. 2021.
