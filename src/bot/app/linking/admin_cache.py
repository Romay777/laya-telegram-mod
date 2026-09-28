"""The Admin-status cache (§10): Admin access comes from Telegram, remembered briefly.

One entry per (chat, user) remembers what `getChatMember` said for
`admin_cache.ttl_s`, so every chat-scoped callback can re-check access
without asking Telegram each time. An answer Telegram did not give (an API
error) is never cached: the next interaction asks again.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.clock import Clock
from app.domain.linking import ADMIN_STATUSES


@dataclass(frozen=True, slots=True)
class _Entry:
    expires_at: datetime
    is_admin: bool


class AdminCache:
    def __init__(self, *, clock: Clock, ttl_s: float) -> None:
        self._clock = clock
        self._ttl = timedelta(seconds=ttl_s)
        self._entries: dict[tuple[int, int], _Entry] = {}

    async def is_admin(self, bot: Bot, chat_id: int, user_id: int) -> bool:
        """Is this user a current administrator or creator of the chat (§10)?"""
        key = (chat_id, user_id)
        now = self._clock.now()
        entry = self._entries.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.is_admin

        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except TelegramAPIError:
            return False  # no answer, no cache entry
        is_admin = member.status in ADMIN_STATUSES
        self._entries[key] = _Entry(expires_at=now + self._ttl, is_admin=is_admin)
        return is_admin
