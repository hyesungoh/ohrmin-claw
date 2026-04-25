"""체성분 자연어 파싱 — Discord 채팅 입력을 구조화 데이터로 변환."""
import datetime
import re


class BodyMetricsParser:
    """자연어 체성분 측정 결과를 파싱한다."""

    KEYWORDS = ["인바디", "inbody", "체성분"]

    PATTERNS = {
        "weight_kg": re.compile(r"체중\s*(\d+\.?\d*)\s*kg", re.IGNORECASE),
        "body_fat_pct": re.compile(r"체지방률?\s*(\d+\.?\d*)\s*%", re.IGNORECASE),
        "muscle_mass_kg": re.compile(r"골격근량?\s*(\d+\.?\d*)\s*kg", re.IGNORECASE),
        "bmi": re.compile(r"BMI\s*(\d+\.?\d*)", re.IGNORECASE),
    }

    DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")

    @classmethod
    def is_body_metrics_message(cls, message: str) -> bool:
        lower = message.lower()
        return any(kw in lower for kw in cls.KEYWORDS)

    @classmethod
    def parse(cls, message: str) -> dict | None:
        result = {}
        has_data = False

        date_match = cls.DATE_PATTERN.search(message)
        result["date"] = date_match.group(1) if date_match else datetime.date.today().isoformat()

        for field, pattern in cls.PATTERNS.items():
            match = pattern.search(message)
            if match:
                result[field] = float(match.group(1))
                has_data = True
            else:
                result[field] = None

        return result if has_data else None
