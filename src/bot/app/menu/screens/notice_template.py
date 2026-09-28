"""The Notice Template screens (§13, §14): current, Edit prompt, Preview.

One template applies per chat. The template screen shows the current
template — or the default text — rendered with sample values; Edit waits
for the Admin's formatted message; the Preview validates it, renders it
with sample values in the Chat Language, and offers 🟢 Save, Cancel and
🔴 Reset to default. The screen labels speak the Admin's language; the
rendered template inside speaks the Chat Language (§15).
"""

from collections.abc import Sequence
from typing import Any

from aiogram.types import DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from aiogram_i18n.cores.base import BaseCore

from app.domain.template import Replacement, TemplateError, Validation, validate
from app.i18n import GetText, translator_for
from app.menu.callbacks import (
    ChatSettingsCallback,
    NoticeTemplateCallback,
    NoticeTemplateEditCallback,
    NoticeTemplateResetCallback,
    NoticeTemplateSaveCallback,
)
from app.menu.screen import Screen
from app.menu.screens.buttons import DANGER
from app.notices.durations import duration_text
from app.notices.template_render import render_notice, render_template_notice

#: The §14 placeholder list, as the error message names it.
ALLOWED = "{user}, {reason}, {duration}, {strike}"

#: The longest values the Chat Language can put in a placeholder (§14): the
#: worst-case render against the 1024 limit.
_WORST_NAME = "x" * 64
_WORST_STEP_SECONDS = 30 * 86400  # 30 days, the longest duration phrase


def sample_member() -> dict[str, Any]:
    """The sample Member the preview renders with."""
    return {"user_id": 0, "name": "Sample Member"}


def worst_replacements(t: GetText) -> dict[str, Replacement]:
    """The longest placeholder values for the §14 worst-case render."""
    return {
        "user": Replacement(_WORST_NAME),
        "reason": Replacement(t("notice-reason-spam")),
        "duration": Replacement(duration_text(t, _WORST_STEP_SECONDS)),
        "strike": Replacement("99/99"),
    }


def template_screen(
    t: GetText,
    chat_t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    template_text: str | None,
) -> Screen:
    """The Notice Template screen: the current template, or the default (§14).

    `chat_t` speaks the Chat Language, so the rendered template reads the
    way the chat will see it.
    """
    name = chat_title if chat_title else "—"
    rendered = (
        _rendered_sample(chat_t, template_text)
        if template_text is not None
        else render_notice(chat_t, name="Sample Member", category="spam", step_seconds=3600)
    )
    return Screen(
        text="\n".join([name, "", t("menu-template-text"), "", rendered]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("menu-template-edit"),
                        callback_data=NoticeTemplateEditCallback(chat_id=chat_id).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=ChatSettingsCallback(chat_id=chat_id).pack(),
                    )
                ],
            ]
        ),
    )


def edit_screen(t: GetText, chat_title: str | None, *, chat_id: int) -> Screen:
    """Edit asks the Admin to send a message (§14); the input handler waits."""
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", t("menu-template-edit-text")]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("menu-template-cancel"),
                        callback_data=NoticeTemplateCallback(chat_id=chat_id).pack(),
                    )
                ]
            ]
        ),
    )


def preview_screen(
    t: GetText,
    chat_t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    text: str,
    entities: Sequence[MessageEntity],
    validation: Validation,
) -> Screen:
    """The Preview (§14): the template rendered with sample values, plus the
    🟢 Save / Cancel / 🔴 Reset to default choice.

    `validation` carries what §14 found: an unknown placeholder is an error
    that blocks saving; a missing `{user}` only warns. The rendered sample
    speaks the Chat Language.
    """
    name = chat_title if chat_title else "—"
    lines = [name, "", t("menu-template-preview-text")]
    if validation.unknown is not None:
        lines.append(t("menu-template-unknown", placeholder=validation.unknown, allowed=ALLOWED))
    elif validation.missing_user:
        lines.append(t("menu-template-missing-user"))
    elif not validation.fits:
        lines.append(t("menu-template-too-long"))
    lines.extend(["", _rendered_sample(chat_t, text)])
    save = InlineKeyboardButton(
        text=t("menu-template-save"),
        callback_data=NoticeTemplateSaveCallback(chat_id=chat_id).pack(),
        disabled=None if validation.ok else DisabledButton(),
    )
    return Screen(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [save],
                [
                    InlineKeyboardButton(
                        text=t("menu-template-cancel"),
                        callback_data=NoticeTemplateCallback(chat_id=chat_id).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("menu-template-reset"),
                        callback_data=NoticeTemplateResetCallback(chat_id=chat_id).pack(),
                        style=DANGER,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=ChatSettingsCallback(chat_id=chat_id).pack(),
                    )
                ],
            ]
        ),
    )


def validate_template(
    core: BaseCore,
    chat_language: str,
    text: str,
    entities: Sequence[MessageEntity],
) -> Validation:
    """§14 validation against the Chat Language's worst-case values."""
    return validate(
        text,
        [entity.model_dump(mode="json", exclude_none=True) for entity in entities],
        worst_replacements(translator_for(core, chat_language)),
    )


def strip_custom_emoji(
    text: str, entities: Sequence[MessageEntity]
) -> tuple[str, list[dict[str, Any]]]:
    """The message as §14 stores it: everything but `custom_emoji` entities.

    The fallback emoji characters of custom emoji are already in `text` and
    stay; the entities themselves are dropped, so their positions never
    break the stored template.
    """
    kept = [
        entity.model_dump(mode="json", exclude_none=True)
        for entity in entities
        if entity.type != "custom_emoji"
    ]
    return text, kept


def _rendered_sample(chat_t: GetText, text: str) -> str:
    """The template with sample values, in the Chat Language (§14).

    A template with unknown placeholders cannot be rendered; the raw text
    shows instead — the error line above already names the placeholder.
    """
    try:
        rendered, _ = render_template_notice(
            chat_t,
            text=text,
            entities=[],
            member=sample_member(),
            category="spam",
            step_seconds=3600,
            active_violations=1,
            ladder_len=3,
        )
    except TemplateError:
        return text
    return rendered
