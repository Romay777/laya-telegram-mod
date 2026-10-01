"""Chat Notice rendering and sending (§7).

The text is the chat's Notice Template when one is stored (§14), and the
default one in the Chat Language otherwise — the rendering lives in
`template_render`, the sending here. The producer reads the template from
its own session and hands it over with the job, so the queue's drain posts
without a DB stop first. The Appeal button is left out while no Admin of
the chat receives Appeals; the caller decides and passes the Violation id
only then (§7, §8).
"""

from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    ReplyParameters,
)
from aiogram_i18n.cores.base import BaseCore

from app.i18n import GetText, translator_for
from app.menu.callbacks import AppealCallback
from app.notices.anchor import TOPIC, NoticeAnchor
from app.notices.template_render import render_notice, render_template_notice

__all__ = ["appeal_keyboard", "removal_time", "render_notice", "send_notice"]

#: The default text lives longer than a forever Restriction (§7): the notice
#: is removed `max_lifetime_h` after posting when the Restriction never ends.
DEFAULT_MAX_LIFETIME_H = 24


def appeal_keyboard(t: GetText, *, chat_id: int, violation_id: int) -> InlineKeyboardMarkup:
    """The one inline button of a Chat Notice: 🙋 It's a mistake (§7).

    The callback data carries the Violation id; the button speaks the Chat
    Language, like the rest of the notice (§15).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("notice-appeal-button"),
                    callback_data=AppealCallback(chat_id=chat_id, violation_id=violation_id).pack(),
                )
            ]
        ]
    )


def removal_time(
    now: datetime,
    *,
    restricted_until: datetime | None,
    max_lifetime_h: int = DEFAULT_MAX_LIFETIME_H,
) -> datetime:
    """When the notice deletes itself (§7): the Restriction's end, or the
    `max_lifetime_h` cap — the only timer a forever Restriction has."""
    if restricted_until is not None:
        return restricted_until
    return now + timedelta(hours=max_lifetime_h)


async def send_notice(
    bot: Bot,
    core: BaseCore,
    *,
    chat_id: int,
    chat_language: str,
    name: str,
    member_id: int,
    category: str,
    step_seconds: int,
    strike: int,
    ladder_len: int,
    template_text: str | None = None,
    template_entities: list[dict[str, Any]] | None = None,
    appeal_violation_id: int | None = None,
    anchor: NoticeAnchor | None = None,
) -> Message:
    """Post the Chat Notice in the chat's own language (§15).

    The Notice Template (§14) is rendered when `template_text` is given;
    the default text applies otherwise. With `appeal_violation_id` the
    notice carries the 🙋 It's a mistake button; without it — no Admin
    receives Appeals, the notice was dropped, or the sender was a channel —
    the button is left out (§7). The `anchor` places the notice where the
    Member can see it (§7): a forum topic, or the comment thread under a
    channel post.
    """
    t = translator_for(core, chat_language)
    text, entities = render_template_notice(
        t,
        text=template_text,
        entities=template_entities,
        member={"user_id": member_id, "name": name},
        category=category,
        step_seconds=step_seconds,
        active_violations=strike,
        ladder_len=ladder_len,
    )
    markup = (
        appeal_keyboard(t, chat_id=chat_id, violation_id=appeal_violation_id)
        if appeal_violation_id is not None
        else None
    )
    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        entities=[MessageEntity.model_validate(entity) for entity in entities],
        reply_markup=markup,
        **anchor_params(anchor),
    )


def anchor_params(anchor: NoticeAnchor | None) -> dict[str, Any]:
    """The sendMessage parameters that carry the anchor (§7).

    A topic anchor sends with `message_thread_id`; a reply anchor replies
    at the thread's root with `allow_sending_without_reply` — the root
    normally survives, but a deleted comment section must not lose the
    notice outright.
    """
    if anchor is None:
        return {}
    if anchor.kind == TOPIC:
        return {"message_thread_id": anchor.message_id}
    return {
        "reply_parameters": ReplyParameters(
            message_id=anchor.message_id, allow_sending_without_reply=True
        )
    }
