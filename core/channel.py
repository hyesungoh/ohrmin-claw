"""채널 추상화 레이어 — 메시징 인터페이스."""
from abc import ABC, abstractmethod
from typing import Callable

import discord


class MessagingChannel(ABC):
    """메시징 채널 공통 인터페이스."""

    @abstractmethod
    async def send(self, message: str) -> None:
        ...

    @abstractmethod
    def on_message(self, handler: Callable) -> None:
        ...

    @abstractmethod
    def run(self) -> None:
        ...


class DiscordChannel(MessagingChannel):
    """Discord 메시징 채널."""

    MAX_MESSAGE_LENGTH = 2000

    def __init__(self, token: str, channel_id: int | None = None):
        self._token = token
        self._channel_id = channel_id
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._handler: Callable | None = None

    async def send(self, message: str) -> None:
        channel = self._client.get_channel(self._channel_id)
        if not channel:
            return
        for chunk in self._split_message(message):
            await channel.send(chunk)

    def on_message(self, handler: Callable) -> None:
        self._handler = handler

        @self._client.event
        async def on_message(msg: discord.Message):
            if msg.author == self._client.user:
                return
            if self._handler:
                response = await self._handler(msg.content)
                if response:
                    for chunk in self._split_message(response):
                        await msg.channel.send(chunk)

    def run(self) -> None:
        self._client.run(self._token)

    def _split_message(self, message: str) -> list[str]:
        if len(message) <= self.MAX_MESSAGE_LENGTH:
            return [message]
        chunks = []
        while message:
            if len(message) <= self.MAX_MESSAGE_LENGTH:
                chunks.append(message)
                break
            split_at = message.rfind("\n", 0, self.MAX_MESSAGE_LENGTH)
            if split_at == -1:
                split_at = self.MAX_MESSAGE_LENGTH
            chunks.append(message[:split_at])
            message = message[split_at:].lstrip("\n")
        return chunks
