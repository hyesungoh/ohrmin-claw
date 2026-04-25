"""채널 추상화 레이어 테스트."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.channel import MessagingChannel, DiscordChannel


class TestMessagingChannelInterface:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            MessagingChannel()

    def test_discord_channel_is_messaging_channel(self):
        with patch("discord.Client"):
            channel = DiscordChannel(token="test_token")
            assert isinstance(channel, MessagingChannel)


class TestDiscordChannel:
    @pytest.mark.asyncio
    async def test_send_message(self):
        with patch("discord.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_channel = AsyncMock()
            mock_client.get_channel = MagicMock(return_value=mock_channel)
            mock_client_cls.return_value = mock_client

            channel = DiscordChannel(token="test_token")
            channel._client = mock_client
            channel._channel_id = 12345

            await channel.send("테스트 메시지")
            mock_channel.send.assert_called_once_with("테스트 메시지")

    def test_message_too_long_splits(self):
        """Discord는 2000자 제한이 있으므로 긴 메시지를 분할해야 함."""
        channel = DiscordChannel.__new__(DiscordChannel)
        chunks = channel._split_message("a" * 2500)
        assert len(chunks) == 2
        assert len(chunks[0]) <= 2000
