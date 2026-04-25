"""컨텍스트 압축 테스트 — Hermes식 보호 구간 + LLM 요약."""
import pytest
from unittest.mock import AsyncMock

from core.context_compressor import ContextCompressor


def _make_messages(n: int) -> list[dict]:
    """테스트용 메시지 N개 생성."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"메시지 {i}"})
    return msgs


class TestNeedsCompression:
    """압축 필요 여부 판단 테스트."""

    def test_short_history_no_compression(self):
        compressor = ContextCompressor()
        msgs = _make_messages(10)
        assert compressor.needs_compression(msgs) is False

    def test_long_history_needs_compression(self):
        compressor = ContextCompressor()
        msgs = _make_messages(30)
        assert compressor.needs_compression(msgs) is True

    def test_custom_threshold(self):
        compressor = ContextCompressor(compress_threshold=15)
        assert compressor.needs_compression(_make_messages(16)) is True
        assert compressor.needs_compression(_make_messages(14)) is False

    def test_empty_history(self):
        compressor = ContextCompressor()
        assert compressor.needs_compression([]) is False


class TestProtectedRegions:
    """보호 구간 테스트 — 첫 메시지 + 최근 메시지 보호."""

    def test_first_messages_protected(self):
        compressor = ContextCompressor(protect_first_n=2, protect_last_n=3)
        msgs = _make_messages(30)
        head, middle, tail = compressor.split_regions(msgs)
        assert head == msgs[:2]
        assert tail == msgs[-3:]
        assert len(middle) == 25

    def test_last_messages_protected(self):
        compressor = ContextCompressor(protect_first_n=1, protect_last_n=5)
        msgs = _make_messages(30)
        head, middle, tail = compressor.split_regions(msgs)
        assert tail == msgs[-5:]

    def test_no_middle_when_short(self):
        """보호 구간이 전체를 커버하면 middle이 비어야 함."""
        compressor = ContextCompressor(protect_first_n=3, protect_last_n=3)
        msgs = _make_messages(6)
        head, middle, tail = compressor.split_regions(msgs)
        assert middle == []
        assert len(head) + len(tail) == 6

    def test_overlap_handled_gracefully(self):
        """보호 구간이 겹칠 때 중복 없이 처리."""
        compressor = ContextCompressor(protect_first_n=5, protect_last_n=5)
        msgs = _make_messages(8)
        head, middle, tail = compressor.split_regions(msgs)
        assert middle == []
        # head + tail 합쳐도 원본 이하
        all_msgs = head + tail
        assert len(all_msgs) <= len(msgs)


class TestCompress:
    """LLM 기반 압축 테스트."""

    @pytest.mark.asyncio
    async def test_compress_replaces_middle_with_summary(self):
        compressor = ContextCompressor(
            protect_first_n=1,
            protect_last_n=2,
            compress_threshold=10,
        )
        msgs = _make_messages(15)
        llm = AsyncMock()
        llm.ask.return_value = "[요약] 메시지 1~12의 요약입니다."

        result = await compressor.compress(msgs, llm)

        # 결과: head(1) + summary(1) + tail(2) = 4
        assert len(result) == 4
        assert result[0] == msgs[0]  # 첫 메시지 보호
        assert "[요약]" in result[1]["content"]  # 요약 메시지
        assert result[1]["role"] == "system"
        assert result[-2:] == msgs[-2:]  # 마지막 2개 보호

    @pytest.mark.asyncio
    async def test_compress_short_returns_original(self):
        """임계값 미만이면 원본 반환."""
        compressor = ContextCompressor(compress_threshold=20)
        msgs = _make_messages(10)
        llm = AsyncMock()

        result = await compressor.compress(msgs, llm)
        assert result == msgs
        llm.ask.assert_not_called()

    @pytest.mark.asyncio
    async def test_compress_passes_middle_to_llm(self):
        """중간 구간이 LLM에 전달되는지 확인."""
        compressor = ContextCompressor(
            protect_first_n=1,
            protect_last_n=1,
            compress_threshold=5,
        )
        msgs = _make_messages(10)
        llm = AsyncMock()
        llm.ask.return_value = "요약"

        await compressor.compress(msgs, llm)

        call_args = llm.ask.call_args
        prompt = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("user_message", "")
        # 중간 메시지들이 프롬프트에 포함되어야 함
        assert "메시지 1" in prompt
        assert "메시지 8" in prompt

    @pytest.mark.asyncio
    async def test_compress_preserves_message_order(self):
        """압축 후 head → summary → tail 순서 유지."""
        compressor = ContextCompressor(
            protect_first_n=2,
            protect_last_n=2,
            compress_threshold=8,
        )
        msgs = _make_messages(12)
        llm = AsyncMock()
        llm.ask.return_value = "요약 내용"

        result = await compressor.compress(msgs, llm)
        assert result[0]["content"] == "메시지 0"
        assert result[1]["content"] == "메시지 1"
        assert "요약" in result[2]["content"]
        assert result[-1]["content"] == "메시지 11"
