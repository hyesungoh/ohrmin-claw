"""ALLOWED_USERS 권한 제어 테스트 (TDD RED 단계)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord


def _make_mock_message(author_id=111, is_bot=False, in_thread=False):
    """테스트용 Discord Message mock 생성."""
    msg = MagicMock(spec=discord.Message)
    msg.content = "테스트 메시지"
    msg.author = MagicMock()
    msg.author.id = author_id
    msg.author.bot = is_bot
    if in_thread:
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        thread.typing = MagicMock()
        thread.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        thread.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        msg.channel = thread
    else:
        msg.channel = MagicMock(spec=discord.TextChannel)
    msg.create_thread = AsyncMock()
    return msg


class TestAllowedUsersPermissionCheck:
    """ALLOWED_USERS 화이트리스트 권한 체크 테스트."""

    @pytest.mark.asyncio
    async def test_allowed_user_can_trigger_query(self, monkeypatch):
        """ALLOWED_USERS={111}이고 author.id=111이면 handle_health_query가 호출되어야 함."""
        import bot.main as main_module

        monkeypatch.setattr(main_module, "ALLOWED_USERS", {111})

        msg = _make_mock_message(author_id=111)

        with patch.object(main_module, "handle_health_query", new_callable=AsyncMock) as mock_handle:
            await main_module.on_message(msg)
            mock_handle.assert_called_once()

    @pytest.mark.asyncio
    async def test_disallowed_user_is_ignored(self, monkeypatch):
        """ALLOWED_USERS={111}이고 author.id=999이면 handle_health_query가 호출되지 않아야 함."""
        import bot.main as main_module

        monkeypatch.setattr(main_module, "ALLOWED_USERS", {111})

        msg = _make_mock_message(author_id=999)

        with patch.object(main_module, "handle_health_query", new_callable=AsyncMock) as mock_handle:
            await main_module.on_message(msg)
            mock_handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_allowed_users_blocks_everyone(self, monkeypatch):
        """ALLOWED_USERS=set()이면 모든 메시지가 무시되어야 함 (화이트리스트 정책)."""
        import bot.main as main_module

        monkeypatch.setattr(main_module, "ALLOWED_USERS", set())

        msg = _make_mock_message(author_id=111)

        with patch.object(main_module, "handle_health_query", new_callable=AsyncMock) as mock_handle:
            await main_module.on_message(msg)
            mock_handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_message_filtered_before_auth_check(self, monkeypatch):
        """봇 자신의 메시지는 권한 체크 전에 이미 필터되어야 함."""
        import bot.main as main_module

        monkeypatch.setattr(main_module, "ALLOWED_USERS", {111})

        # 봇 자신의 메시지: author == channel._client.user 로 판단
        msg = MagicMock(spec=discord.Message)
        msg.content = "봇 메시지"
        msg.author = main_module.channel._client.user  # 봇 자신
        msg.channel = MagicMock(spec=discord.TextChannel)

        with patch.object(main_module, "handle_health_query", new_callable=AsyncMock) as mock_handle:
            await main_module.on_message(msg)
            mock_handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_message_from_disallowed_user_is_ignored(self, monkeypatch):
        """스레드 내 미허용 유저 메시지도 무시되어야 함."""
        import bot.main as main_module

        monkeypatch.setattr(main_module, "ALLOWED_USERS", {111})

        msg = _make_mock_message(author_id=999, in_thread=True)

        with patch.object(main_module, "handle_health_query", new_callable=AsyncMock) as mock_handle:
            await main_module.on_message(msg)
            mock_handle.assert_not_called()


class TestParseAllowedUsers:
    """ALLOWED_USERS 환경변수 파싱 테스트."""

    def test_parse_valid_ids(self):
        """환경변수 '123, 456' → {123, 456}로 파싱되어야 함."""
        import bot.main as main_module

        result = main_module.parse_allowed_users("123, 456")
        assert result == {123, 456}

    def test_parse_ignores_invalid_entries(self, capsys):
        """환경변수 'abc,123' → {123}으로 파싱되고, 'abc'에 대한 경고가 출력되어야 함."""
        import bot.main as main_module

        result = main_module.parse_allowed_users("abc,123")
        assert result == {123}

        captured = capsys.readouterr()
        assert "abc" in captured.out or "abc" in captured.err
