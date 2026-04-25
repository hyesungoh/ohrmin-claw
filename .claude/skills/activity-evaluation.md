---
name: activity-evaluation
description: 종목별 운동 활동 평가 프레임워크
trigger: 운동 분석, 러닝 평가, 웨이트 리뷰, 활동 피드백
---

# 운동 활동 평가 프레임워크

> TODO: /skill-creator 전문가가 작성 예정

## 역할
종목별(러닝, 웨이트, 수영, 하이킹, 사이클) 운동 데이터를 분석하고 개선점을 제안.

## 종목별 평가 기준
- 러닝: 페이스, 심박 존 분포, VO2 Max 트렌드, 케이던스
- 웨이트: 볼륨(세트x무게x렙), 근육군 밸런스, 점진적 과부하
- 수영: SWOLF, 스트로크 효율, 페이스
- 하이킹/사이클: 고도 대비 심박, 파워

## 사용할 도구
- get_activities, get_activity_detail, get_last_activity
- get_activity_splits, get_activity_hr_zones
