"""컨텍스트 압축 — Hermes식 보호 구간 + LLM 요약."""

COMPRESS_PROMPT = """\
다음은 사용자와 AI 건강 코치의 대화 중간 부분입니다.
이 내용을 간결하게 요약하세요. 핵심 사실, 수치, 결정 사항만 남기세요.
요약은 한국어로 작성하세요.

대화:
{conversation}"""


class ContextCompressor:
    """대화 이력이 길어지면 중간 구간을 LLM으로 압축."""

    def __init__(
        self,
        protect_first_n: int = 1,
        protect_last_n: int = 6,
        compress_threshold: int = 20,
    ):
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.compress_threshold = compress_threshold

    def needs_compression(self, messages: list[dict]) -> bool:
        return len(messages) > self.compress_threshold

    def split_regions(
        self, messages: list[dict]
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """메시지를 head(보호) / middle(압축 대���) / tail(보호)로 분할."""
        n = len(messages)
        first = min(self.protect_first_n, n)
        last = min(self.protect_last_n, n - first)

        if first + last >= n:
            # 보호 구간이 전체를 커버 — middle 없음
            head = messages[:first]
            tail = messages[first:]
            return head, [], tail

        head = messages[:first]
        tail = messages[n - last:]
        middle = messages[first:n - last]
        return head, middle, tail

    async def compress(
        self, messages: list[dict], llm
    ) -> list[dict]:
        """필요 시 중간 구간을 LLM 요약으로 대체."""
        if not self.needs_compression(messages):
            return messages

        head, middle, tail = self.split_regions(messages)

        if not middle:
            return messages

        # 중간 구간을 텍스트로 변환
        conv_text = "\n".join(
            f"{'사용자' if m['role'] == 'user' else '어시스턴트'}: {m.get('content', '')}"
            for m in middle
        )
        prompt = COMPRESS_PROMPT.format(conversation=conv_text)
        summary = await llm.ask("컨텍스트 압축기", prompt)

        summary_msg = {
            "role": "system",
            "content": f"[이전 대화 요약]\n{summary}",
        }

        return head + [summary_msg] + tail
