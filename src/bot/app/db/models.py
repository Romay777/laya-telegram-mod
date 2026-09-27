"""SQLAlchemy models for the persistent state of the Instance.

Only the tables the walking skeleton needs live here so far; later tickets add
their own tables in new Alembic revisions (see ARCHITECTURE §12 for the full
data model).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BotUser(Base):
    """A Telegram user who has started the bot in private (an Admin)."""

    __tablename__ = "bot_user"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # The Admin's interface language; NULL until the choice on the language screen.
    language: Mapped[str | None] = mapped_column(String(8))
    # The single Menu message, edited in place to move between screens (§13).
    menu_message_id: Mapped[int | None] = mapped_column()
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reachable: Mapped[bool] = mapped_column(Boolean, default=True)


class FsmState(Base):
    """One aiogram FSM key, persisted so state survives restarts (§12).

    `thread_id` is 0 when the key has no forum topic, so the composite primary
    key stays NOT NULL.
    """

    __tablename__ = "fsm_state"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=0)
    destiny: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    state: Mapped[str | None] = mapped_column(String(256))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
