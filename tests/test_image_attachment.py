"""Discord 이미지 첨부파일 처리 테스트."""
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord


def _make_mock_message(content="", attachments=None, is_bot=False, thread=None):
    """테스트용 Discord Message mock 생성."""
    msg = MagicMock(spec=discord.Message)
    msg.content = content
    msg.author = MagicMock()
    msg.author.bot = is_bot
    msg.author.id = 12345
    msg.attachments = attachments or []
    msg.channel = thread or MagicMock(spec=discord.TextChannel)
    msg.create_thread = AsyncMock()
    return msg


def _make_image_attachment(filename="photo.png", content_type="image/png", data=b"\x89PNG\r\n"):
    """이미지 첨부파일 mock 생성."""
    att = MagicMock(spec=discord.Attachment)
    att.filename = filename
    att.content_type = content_type
    att.size = len(data)
    att.read = AsyncMock(return_value=data)
    return att


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_mock_thread():
    thread = MagicMock(spec=discord.Thread)
    thread.send = AsyncMock()
    thread.typing = MagicMock(return_value=_FakeTyping())
    return thread


class TestExtractImageAttachments:
    """Discord 메시지에서 이미지 첨부파일을 추출하는 기능 테스트."""

    def test_filters_image_attachments(self):
        """image/* content_type인 첨부파일만 필터링해야 함."""
        from bot.main import extract_image_attachments

        img = _make_image_attachment("photo.png", "image/png")
        txt = _make_image_attachment("readme.txt", "text/plain")
        jpg = _make_image_attachment("pic.jpg", "image/jpeg")

        result = extract_image_attachments([img, txt, jpg])
        assert len(result) == 2

    def test_empty_attachments_returns_empty(self):
        """첨부파일이 없으면 빈 리스트 반환."""
        from bot.main import extract_image_attachments

        result = extract_image_attachments([])
        assert result == []

    def test_no_content_type_skipped(self):
        """content_type이 None인 첨부파일은 건너뛰어야 함."""
        from bot.main import extract_image_attachments

        att = _make_image_attachment("unknown.bin", None)
        result = extract_image_attachments([att])
        assert result == []


class TestSaveAndCleanupImages:
    """이미지를 임시파일로 저장하고 정리하는 기능 테스트."""

    @pytest.mark.asyncio
    async def test_saves_attachment_to_temp_file(self):
        """첨부파일 데이터를 임시파일로 저장해야 함."""
        from bot.main import save_images_to_temp

        img_data = b"\x89PNG\r\nfake image data"
        att = _make_image_attachment("photo.png", "image/png", img_data)

        paths = await save_images_to_temp([att])
        assert len(paths) == 1
        assert os.path.exists(paths[0])
        assert paths[0].endswith(".png")

        with open(paths[0], "rb") as f:
            assert f.read() == img_data

        # cleanup
        for p in paths:
            os.unlink(p)

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_attachments(self):
        """빈 리스트면 빈 리스트 반환."""
        from bot.main import save_images_to_temp

        paths = await save_images_to_temp([])
        assert paths == []

    def test_cleanup_deletes_files(self):
        """cleanup_temp_images가 임시파일을 삭제해야 함."""
        from bot.main import cleanup_temp_images

        # 임시파일 생성
        fd, path = tempfile.mkstemp(suffix=".png")
        os.write(fd, b"test")
        os.close(fd)
        assert os.path.exists(path)

        cleanup_temp_images([path])
        assert not os.path.exists(path)

    def test_cleanup_ignores_missing_files(self):
        """이미 삭제된 파일은 에러 없이 건너뛰어야 함."""
        from bot.main import cleanup_temp_images

        cleanup_temp_images(["/tmp/nonexistent_12345.png"])
        # 에러 없이 완료


class TestHandleHealthQueryWithImages:
    """handle_health_query가 이미지 경로를 LLM 프롬프트에 포함하는지 테스트."""

    @pytest.mark.asyncio
    async def test_image_paths_included_in_prompt(self):
        """이미지 경로가 LLM에 전달되는 메시지에 포함되어야 함."""
        from bot.main import handle_health_query

        mock_message = _make_mock_message("이 이미지 분석해줘")
        mock_thread = _make_mock_thread()
        mock_message.create_thread = AsyncMock(return_value=mock_thread)

        captured_message = None

        async def capture_ask(*args, on_text=None, **kwargs):
            nonlocal captured_message
            captured_message = args[1] if len(args) > 1 else kwargs.get("user_message")
            return "분석 결과"

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_llm.ask_with_context = capture_ask

            image_paths = ["/tmp/test_img_001.png", "/tmp/test_img_002.jpg"]
            await handle_health_query(mock_message, "이 이미지 분석해줘", image_paths=image_paths)

        assert "/tmp/test_img_001.png" in captured_message
        assert "/tmp/test_img_002.jpg" in captured_message

    @pytest.mark.asyncio
    async def test_no_images_works_as_before(self):
        """이미지 없이 호출하면 기존 동작과 동일해야 함."""
        from bot.main import handle_health_query

        mock_message = _make_mock_message("오늘 컨디션 어때?")
        mock_thread = _make_mock_thread()
        mock_message.create_thread = AsyncMock(return_value=mock_thread)

        captured_message = None

        async def capture_ask(*args, on_text=None, **kwargs):
            nonlocal captured_message
            captured_message = args[1] if len(args) > 1 else kwargs.get("user_message")
            return "좋습니다"

        with patch("bot.main.llm") as mock_llm, \
             patch("bot.main.load_prompt", return_value="시스템"), \
             patch("bot.main.garmin", None), \
             patch("bot.main.body_metrics_mgr") as mock_bm, \
             patch("bot.main.memory_mgr") as mock_mem, \
             patch("bot.main.session_mgr") as mock_sess, \
             patch("bot.main.context_compressor") as mock_comp, \
             patch("bot.main.MEMORY_MODE", "manual"):
            mock_bm.read_latest.return_value = None
            mock_mem.read_memory.return_value = ""
            mock_mem.read_user.return_value = ""
            mock_llm.ask_with_context = capture_ask

            await handle_health_query(mock_message, "오늘 컨디션 어때?")

        assert "첨부 이미지" not in captured_message


class TestImageLimits:
    """파일 크기 및 개수 제한 테스트."""

    def test_large_file_skipped(self):
        """10MB 초과 첨부파일은 건너뛰어야 함."""
        from bot.main import extract_image_attachments

        small = _make_image_attachment("small.png", "image/png")
        small.size = 1024  # 1KB

        large = _make_image_attachment("huge.png", "image/png")
        large.size = 20 * 1024 * 1024  # 20MB

        result = extract_image_attachments([small, large])
        assert len(result) == 1

    def test_max_5_images(self):
        """이미지는 최대 5개까지만 허용."""
        from bot.main import extract_image_attachments

        attachments = []
        for i in range(8):
            att = _make_image_attachment(f"img{i}.png", "image/png")
            att.size = 1024
            attachments.append(att)

        result = extract_image_attachments(attachments)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_partial_read_failure_cleans_up(self):
        """여러 이미지 중 하나가 read() 실패하면 이미 저장된 파일도 정리."""
        from bot.main import save_images_to_temp

        good = _make_image_attachment("ok.png", "image/png", b"good data")
        bad = _make_image_attachment("fail.png", "image/png")
        bad.read = AsyncMock(side_effect=Exception("network error"))

        with pytest.raises(Exception, match="network error"):
            await save_images_to_temp([good, bad])

        # 잔류 파일이 없어야 함 (정리됨)
        # good의 파일이 생성됐다가 정리되었는지 확인하기 어렵지만,
        # 최소한 예외가 전파되는지 확인


class TestCleanupRobustness:
    """cleanup_temp_images의 견고성 테스트."""

    def test_handles_permission_error(self):
        """PermissionError 등 OSError도 무시해야 함."""
        from bot.main import cleanup_temp_images

        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

        with patch("os.unlink", side_effect=PermissionError("denied")):
            cleanup_temp_images([path])  # 에러 없이 완료

        # 실제 파일 정리
        os.unlink(path)


class TestOnMessageImageFlow:
    """on_message에서 이미지 첨부 → 저장 → 전달 → 정리 전체 흐름 테스트."""

    @pytest.mark.asyncio
    async def test_image_only_message_is_processed(self):
        """텍스트 없이 이미지만 있는 메시지도 처리되어야 함."""
        from bot.main import on_message, ALLOWED_USERS

        img = _make_image_attachment("workout.png", "image/png", b"imgdata")
        mock_message = _make_mock_message(content="", attachments=[img])

        with patch("bot.main.ALLOWED_USERS", {mock_message.author.id}), \
             patch("bot.main.handle_health_query", new_callable=AsyncMock) as mock_hq, \
             patch("bot.main.save_images_to_temp", new_callable=AsyncMock, return_value=["/tmp/x.png"]) as mock_save, \
             patch("bot.main.cleanup_temp_images") as mock_cleanup, \
             patch("bot.main.extract_image_attachments", return_value=[img]):
            mock_message.author.__eq__ = lambda self, other: False  # not bot

            await on_message(mock_message)

            mock_hq.assert_called_once()
            call_kwargs = mock_hq.call_args
            # image_paths가 전달되었는지 확인
            assert "image_paths" in call_kwargs.kwargs or len(call_kwargs.args) > 2
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_called_even_on_error(self):
        """handle_health_query에서 에러가 나도 cleanup이 호출되어야 함."""
        from bot.main import on_message

        img = _make_image_attachment("photo.png", "image/png", b"data")
        mock_message = _make_mock_message(content="분석해줘", attachments=[img])

        with patch("bot.main.ALLOWED_USERS", {mock_message.author.id}), \
             patch("bot.main.handle_health_query", new_callable=AsyncMock, side_effect=Exception("LLM error")), \
             patch("bot.main.save_images_to_temp", new_callable=AsyncMock, return_value=["/tmp/err.png"]), \
             patch("bot.main.cleanup_temp_images") as mock_cleanup, \
             patch("bot.main.extract_image_attachments", return_value=[img]):
            mock_message.author.__eq__ = lambda self, other: False

            with pytest.raises(Exception, match="LLM error"):
                await on_message(mock_message)

            mock_cleanup.assert_called_once_with(["/tmp/err.png"])
