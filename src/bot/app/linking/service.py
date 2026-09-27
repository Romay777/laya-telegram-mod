"""The Linking service (§10): intents, promotion checks, and completing links.

One entry point per way the flow moves:

- `start_link` — the Admin pressed 🟢 Add to chat: mint the one-hour intent
  and return the startgroup deep link.
- `handle_promotion` — a `my_chat_member` update promoted the bot: run the
  checks, then link or report what is missing.
- `check_against` — the Admin pressed 🔵 Check again on the failure screen.
- `complete_pending_start` — the Linker who never started the bot just did.
"""

import asyncio
import contextlib
import secrets
from typing import cast

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.db.models import BotUser
from app.db.repositories.link_intents import LinkIntentRepository
from app.linking.deep_link import INTENT_TTL, startgroup_url


class LinkingService:
    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        clock: Clock,
        prompt_delete_after_s: float = 600.0,
    ) -> None:
        self._session_maker = session_maker
        self._clock = clock
        self._prompt_delete_after_s = prompt_delete_after_s
        self._deletion_tasks: set[asyncio.Task[None]] = set()

    async def start_link(self, *, bot: Bot, session: AsyncSession, user: BotUser) -> str:
        """Mint the single-use intent bound to `user`; return the deep link (§10)."""
        me = await bot.get_me()
        token = secrets.token_urlsafe(16)
        await LinkIntentRepository(session).create(
            token=token,
            user_id=user.user_id,
            expires_at=self._clock.now() + INTENT_TTL,
        )
        return startgroup_url(cast(str, me.username), token)

    async def delete_prompt_later(self, bot: Bot, chat_id: int, message_id: int) -> None:
        """Delete the group prompt after its 10 minutes (§10 step 6).

        In-process on purpose: when the scheduler (§11) lands, this due
        deletion moves into it. Held in a set so the task cannot be collected.
        """

        async def delete_later() -> None:
            await asyncio.sleep(self._prompt_delete_after_s)
            with contextlib.suppress(TelegramAPIError):
                await bot.delete_message(chat_id=chat_id, message_id=message_id)

        task = asyncio.create_task(delete_later())
        self._deletion_tasks.add(task)
        task.add_done_callback(self._deletion_tasks.discard)
