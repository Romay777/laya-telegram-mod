"""SQLAlchemy models for the persistent state of the Instance.

Tables land with the tickets that need them, in new Alembic revisions; this
file keeps the §12 shapes. All timestamps are `timestamptz`, every chat-scoped
table carries `chat_id` with ON DELETE CASCADE (ADR-0001).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.linking import DEFAULT_EXPIRY_SECONDS, DEFAULT_LADDER


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


class LinkIntent(Base):
    """A one-time startgroup token, bound to the Admin who pressed Add to chat.

    Single use: consumed by the first successful Linking; it expires after an
    hour (§10).
    """

    __tablename__ = "link_intent"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Chat(Base):
    """A Linked Chat (§12), created on Linking with the defaults."""

    __tablename__ = "chat"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None]
    # active | suspended | removed; every chat starts active.
    status: Mapped[str] = mapped_column(String(16), default="active")
    # observation | auto; every chat starts in Observation Mode (§12).
    mode: Mapped[str] = mapped_column(String(16), default="observation")
    # laya | jev; `laya` while it is the deployed default (§12).
    backend: Mapped[str] = mapped_column(String(16), default="laya")
    # lenient | balanced | strict.
    sensitivity: Mapped[str] = mapped_column(String(16), default="balanced")
    # The language of Chat Notices and buttons, set from the Linker's language.
    chat_language: Mapped[str] = mapped_column(String(8), default="en")
    # Steps in seconds, 0 = forever; the §12 default ladder.
    ladder: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), default=lambda: list(DEFAULT_LADDER)
    )
    # Seconds until a Violation stops being Active; None = never.
    expiry_seconds: Mapped[int | None] = mapped_column(BigInteger, default=DEFAULT_EXPIRY_SECONDS)
    # The Admin who linked the chat.
    linker_id: Mapped[int] = mapped_column(BigInteger)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observation_summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Category(Base):
    """A kind of unwanted content; the builtin ones are seeded by migration."""

    __tablename__ = "category"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    builtin: Mapped[bool]


class ChatCategory(Base):
    """One Category switched on or off per chat (§12)."""

    __tablename__ = "chat_category"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat.chat_id", ondelete="CASCADE"), primary_key=True
    )
    category_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("category.code"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # None = use the chat's Sensitivity preset (§12).
    violation_threshold: Mapped[float | None] = mapped_column(Float)
    suspicion_threshold: Mapped[float | None] = mapped_column(Float)


class AdminSubscription(Base):
    """Which Admin Alerts an Admin receives for one chat (§9)."""

    __tablename__ = "admin_subscription"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat.chat_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # all | appeals | off; the Linker gets `all` on Linking.
    alert_mode: Mapped[str] = mapped_column(String(16), default="off")


class MessageCheck(Base):
    """One classifier check (§12): the Verdict and probabilities, never the text."""

    __tablename__ = "message_check"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chat.chat_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    # Edits are checked again from scratch (§4); this ticket checks originals only.
    is_edit: Mapped[bool] = mapped_column(Boolean, default=False)
    backend: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    spec_version: Mapped[int]
    # clean | suspicion | violation | skipped_short | skipped_timeout |
    # skipped_overload | skipped_unavailable (§12).
    outcome: Mapped[str] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    probabilities: Mapped[dict[str, float] | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FlaggedMessage(Base):
    """The stored text and entities of a flagged message (§12), purged on `purge_at`."""

    __tablename__ = "flagged_message"

    check_id: Mapped[int] = mapped_column(
        ForeignKey("message_check.id", ondelete="CASCADE"), primary_key=True
    )
    # Both go NULL when the retention passes; the row keeps the check linkage.
    text: Mapped[str | None] = mapped_column(Text)
    entities: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    purge_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Suspicion(Base):
    """A message whose Verdict fell in the middle confidence band (§12).

    The message stays in the chat; an Admin decides — or the scheduler
    auto-closes it as `expired` after `auto_close_h` (§11). Deciding is
    first-click-wins: the conditional update fires only while `pending`.
    """

    __tablename__ = "suspicion"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(
        ForeignKey("message_check.id", ondelete="CASCADE")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chat.chat_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    # pending | punished | dismissed | expired | superseded (§12).
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Violation(Base):
    """A confirmed case of a Member's message matching a Category (§12)."""

    __tablename__ = "violation"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chat.chat_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    check_id: Mapped[int] = mapped_column(ForeignKey("message_check.id"))
    category: Mapped[str] = mapped_column(String(32))
    # auto | admin; admin arrives when Suspicions can be punished (§9).
    source: Mapped[str] = mapped_column(String(16), default="auto")
    step_index: Mapped[int]
    # Seconds; 0 = forever, None is reserved for sender-chat bans (§12).
    restriction_seconds: Mapped[int | None] = mapped_column(BigInteger)
    restricted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[int | None] = mapped_column(BigInteger)
    notice_dropped: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Appeal(Base):
    """A Member's request to lift a Restriction (§8, §12).

    One per Violation — the UNIQUE constraint refuses a second. Deciding is
    first-click-wins: the conditional update fires only while `pending`.
    """

    __tablename__ = "appeal"

    id: Mapped[int] = mapped_column(primary_key=True)
    violation_id: Mapped[int] = mapped_column(
        ForeignKey("violation.id", ondelete="CASCADE"), unique=True
    )
    # pending | approved | rejected (§12).
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminAlert(Base):
    """One sent Admin Alert message, recorded so every copy can be edited (§9, §12)."""

    __tablename__ = "admin_alert"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chat.chat_id", ondelete="CASCADE"))
    admin_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    # violation | suspicion | appeal (§12).
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[int] = mapped_column()


class ChatNotice(Base):
    """The bot's own Violation announcement, deleted at `delete_at` (§7, §12)."""

    __tablename__ = "chat_notice"

    violation_id: Mapped[int] = mapped_column(
        ForeignKey("violation.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(BigInteger)
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
