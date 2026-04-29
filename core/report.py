"""리포트 템플릿 생성 — 마크다운 포맷."""


class ReportGenerator:
    """주간/월간 건강 리포트를 마크다운으로 생성한다."""

    @staticmethod
    def weekly_report(summary: dict) -> str:
        s = summary
        sleep = s['sleep']['baseline_7d']
        lines = [
            f"# 📊 주간 건강 리포트",
            f"**기간**: {s['period']}",
            "",
            "---",
            "",
            "## 😴 수면",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 평균 수면 시간 | **{sleep['avg_total_hours']}시간** |",
            f"| 평균 수면 점수 | **{sleep['avg_score']}점** |",
            f"| 추세 | {_trend_emoji(sleep['trend'])} {sleep['trend']} |",
            "",
            "## ❤️ 심박수",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 평균 안정시 심박수 | **{s['heart_rate']['avg_rhr']} bpm** |",
            f"| 추세 | {_trend_emoji(s['heart_rate']['trend'])} {s['heart_rate']['trend']} |",
            "",
            "## 🏃 운동",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 총 운동 횟수 | **{s['activities']['total_count']}회** |",
            f"| 총 칼로리 | **{s['activities']['total_calories']} kcal** |",
        ]

        if s['activities'].get('total_distance'):
            lines.append(f"| 총 거리 | **{s['activities']['total_distance']} km** |")
        if s['activities'].get('total_time_hours'):
            lines.append(f"| 총 시간 | **{s['activities']['total_time_hours']}시간** |")
        if s['activities'].get('by_sport'):
            sports_str = ", ".join(f"{k}: {v}회" for k, v in s['activities']['by_sport'].items())
            lines.append(f"| 종목별 | {sports_str} |")

        lines.extend([
            "",
            "## 💚 HRV",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 주간 평균 | **{s['hrv']['avg_weekly']} ms** |",
            f"| 추세 | {_trend_emoji(s['hrv']['trend'])} {s['hrv']['trend']} |",
            "",
            "## 😰 스트레스",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 평균 스트레스 | **{s['stress']['avg_stress']}** |",
        ])

        if s.get("body_metrics") and s["body_metrics"]:
            ib = s["body_metrics"]
            lines.extend([
                "",
                "## 🏋️ 체성분",
                f"| 항목 | 값 |",
                f"|------|-----|",
            ])
            if ib.get("weight_kg"):
                lines.append(f"| 체중 | **{ib['weight_kg']} kg** |")
            if ib.get("body_fat_pct"):
                lines.append(f"| 체지방률 | **{ib['body_fat_pct']}%** |")
            if ib.get("muscle_mass_kg"):
                lines.append(f"| 골격근량 | **{ib['muscle_mass_kg']} kg** |")
            if ib.get("bmi"):
                lines.append(f"| BMI | **{ib['bmi']}** |")

        return "\n".join(lines)

    @staticmethod
    def monthly_report(summary: dict) -> str:
        s = summary
        sleep = s['sleep']['baseline_7d']
        lines = [
            f"# 📈 월간 건강 리포트",
            f"**기간**: {s['period']}",
            "",
            "---",
            "",
            "## 📊 종합 요약",
            "",
            f"- 평균 수면: **{sleep['avg_total_hours']}시간** ({sleep['trend']})",
            f"- 평균 안정시 심박수: **{s['heart_rate']['avg_rhr']} bpm** ({s['heart_rate']['trend']})",
            f"- 운동 횟수: **{s['activities']['total_count']}회**",
            f"- HRV 평균: **{s['hrv']['avg_weekly']} ms** ({s['hrv']['trend']})",
            f"- 평균 스트레스: **{s['stress']['avg_stress']}**",
        ]

        if s.get("body_metrics") and s["body_metrics"]:
            ib = s["body_metrics"]
            lines.append("")
            lines.append("## 🏋️ 체성분 변화")
            if ib.get("body_fat_pct"):
                lines.append(f"- 체지방률: **{ib['body_fat_pct']}%**")
            if ib.get("muscle_mass_kg"):
                lines.append(f"- 골격근량: **{ib['muscle_mass_kg']} kg**")

        return "\n".join(lines)


def _trend_emoji(trend: str) -> str:
    return {
        "improving": "📈",
        "worsening": "📉",
        "stable": "➡️",
    }.get(trend, "❓")
