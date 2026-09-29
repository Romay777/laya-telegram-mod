"""Repository for `chat` rows (§12): Linked Chats and their defaults."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.classifiers.spec import LABELS
from app.db.models import Category, Chat, ChatCategory

#: The toggleable Categories of §13: every question-spec label but "clean".
CATEGORY_CODES = tuple(code for code in LABELS if code != "clean")


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, chat_id: int) -> Chat | None:
        return await self.session.get(Chat, chat_id)

    async def list_linked(self) -> list[Chat]:
        """Every Linked Chat the Home screen may offer (§13): everything not removed.

        Suspended chats stay listed — they are managed back to active, not
        re-linked. Ordered by when each chat was linked, then by id.
        """
        rows = await self.session.scalars(
            select(Chat).where(Chat.status != "removed").order_by(Chat.linked_at, Chat.chat_id)
        )
        return list(rows)

    async def due_observation_summaries(self, now: datetime) -> list[Chat]:
        """Observation Mode chats whose 48-hour summary is due once (§11, §13).

        `summary_sent` keeps the job from repeating: the offer is made once.
        """
        rows = await self.session.scalars(
            select(Chat)
            .where(
                Chat.mode == "observation",
                Chat.observation_summary_at.is_not(None),
                Chat.observation_summary_at <= now,
                Chat.summary_sent.is_(False),
            )
            .order_by(Chat.observation_summary_at)
        )
        return list(rows)

    async def create_linked(
        self,
        *,
        chat_id: int,
        title: str | None,
        linker_id: int,
        linked_at: datetime,
        chat_language: str,
        backend: str = "laya",
    ) -> Chat:
        """Insert the Linked Chat with the §12 defaults, or return the stored one.

        Column defaults carry mode, sensitivity, ladder and expiry; the caller
        passes what Linking knows: the chat itself and the Linker. The backend
        defaults to Laya but is Jev when Laya is not deployed (§5). Every
        Category in the table starts enabled (§12: custom Categories are
        reserved for later, so today that is all of them).
        """
        chat = await self.get(chat_id)
        if chat is not None:
            return chat

        chat = Chat(
            chat_id=chat_id,
            title=title,
            linker_id=linker_id,
            linked_at=linked_at,
            chat_language=chat_language,
            backend=backend,
        )
        self.session.add(chat)
        await self.session.flush()

        for (code,) in (await self.session.execute(select(Category.code))).all():
            self.session.add(ChatCategory(chat_id=chat_id, category_code=code, enabled=True))
        await self.session.flush()
        return chat

    async def enabled_categories(self, chat_id: int) -> tuple[str, ...]:
        """The Category codes the chat checks with, in question-spec order (§4).

        A Category with no row counts as enabled: the §12 default is all on,
        and a chat linked before a later builtin Category was seeded keeps
        working.
        """
        rows = await self.session.scalars(
            select(ChatCategory).where(ChatCategory.chat_id == chat_id)
        )
        enabled = {row.category_code: row.enabled for row in rows}
        return tuple(code for code in CATEGORY_CODES if code not in enabled or enabled[code])

    async def set_category_enabled(self, chat_id: int, code: str, *, enabled: bool) -> None:
        """Turn one Category on or off for the chat (§13)."""
        await self.session.execute(
            update(ChatCategory)
            .where(ChatCategory.chat_id == chat_id, ChatCategory.category_code == code)
            .values(enabled=enabled)
        )
        await self.session.flush()

    async def set_sensitivity(self, chat_id: int, sensitivity: str) -> None:
        """The Sensitivity preset the chat's thresholds come from (§3, §13)."""
        chat = await self.get(chat_id)
        if chat is None:
            return
        chat.sensitivity = sensitivity
        await self.session.flush()

    async def set_backend(self, chat_id: int, backend: str) -> None:
        """The Classifier Backend this chat's checks go to (§5, §13)."""
        chat = await self.get(chat_id)
        if chat is None:
            return
        chat.backend = backend
        await self.session.flush()

    async def set_chat_language(self, chat_id: int, chat_language: str) -> None:
        """The language of the chat's Notices, buttons and Member toasts (§15, §13)."""
        chat = await self.get(chat_id)
        if chat is None:
            return
        chat.chat_language = chat_language
        await self.session.flush()

    async def set_ladder(self, chat_id: int, ladder: tuple[int, ...]) -> None:
        """The chat's Penalty Ladder (§6, §12): 1-10 Steps in seconds, 0 = forever.

        Only the `chat` row changes: existing Violations keep the Step,
        Restriction and Expiry they were recorded with, and the new ladder
        applies from the next Violation.
        """
        chat = await self.get(chat_id)
        if chat is None:
            return
        chat.ladder = list(ladder)
        await self.session.flush()

    async def set_expiry(self, chat_id: int, expiry_seconds: int | None) -> None:
        """The chat's Expiry period (§6, §12): None means never.

        The period counts per Violation from when it was recorded; changing
        it never touches the Violations already in the table, and it never
        lifts a Restriction that is in place.
        """
        chat = await self.get(chat_id)
        if chat is None:
            return
        chat.expiry_seconds = expiry_seconds
        await self.session.flush()
