"""End-to-end: Notice Templates (§14).

The Settings screen gains a Notice Template entry; Edit waits for the
Admin's formatted message, reads it and deletes it; the Preview renders
sample values in the Chat Language with 🟢 Save, Cancel and 🔴 Reset to
default; saving stores the text and entities as received; a Violation then
announces itself with the exact template text and entities. Assertions only
look at the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from aiogram.types import MessageEntity
from app.db.models import NoticeTemplate
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture, auto_moderation_chat, started_admin
from tests.support.telegram import member_member, member_owner
from tests.support.updates import (
    group_message_update,
    private_callback_update,
    private_text_update,
    user,
)

# The Postgres container is shared, so every test gets its own Admin and chat.
_admin_ids = count(3100, 10)
_chat_ids = count(-1003100, -10)

SPAMMY = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}

#: The Admin's formatted template: "{user} broke the rules" with the name in bold.
TEMPLATE_TEXT = "{user}, it looks like your message {reason}."
TEMPLATE_ENTITIES = [MessageEntity(type="bold", offset=0, length=6)]  # over "{user}"


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def member_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


def buttons_of(app: TestApp) -> dict[str, object]:
    edit = app.session.calls_of("EditMessageText")[-1].method
    return {button.text: button for row in edit.reply_markup.inline_keyboard for button in row}


async def open_settings(app: TestApp, admin_id: int, chat_id: int, menu: int) -> dict:
    await press(app, admin_id, f"chat-settings:{chat_id}", menu)
    edit = app.session.calls_of("EditMessageText")[-1].method
    return {button.text: button for row in edit.reply_markup.inline_keyboard for button in row}


async def open_template(app: TestApp, admin_id: int, chat_id: int, menu: int) -> None:
    await press(app, admin_id, f"chat-template:{chat_id}", menu)


async def start_edit(app: TestApp, admin_id: int, chat_id: int, menu: int) -> None:
    await press(app, admin_id, f"chat-template-edit:{chat_id}", menu)


async def press(app: TestApp, admin_id: int, data: str, menu: int) -> None:
    """One chat-scoped callback press; any unconsumed admin check is dropped.

    The §13 admin re-check is warm within the cache's TTL, so a scripted
    answer can outlive the press that meant to consume it — and would
    poison the next check (§13). Tests drop the leftovers.
    """
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_callback_update(admin_id, data, menu, language_code="en"))
    app.session.drop_scripted(GetChatMember)


async def send_template_input(
    app: TestApp, admin_id: int, text: str, entities: list[MessageEntity] | None = None
) -> None:
    await app.feed(
        private_text_update(admin_id, text, message_id=77, language_code="en", entities=entities)
    )


async def stored_template(
    session_maker: async_sessionmaker[AsyncSession], chat_id: int
) -> NoticeTemplate | None:
    async with session_maker() as db:
        return await db.get(NoticeTemplate, chat_id)


async def test_settings_carries_the_notice_template_entry(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)

    buttons = await open_settings(app, admin_id, chat_id, menu)

    assert "Notice Template" in buttons


async def test_the_template_screen_shows_the_default_when_nothing_is_stored(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await open_template(app, admin_id, chat_id, menu)

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "looks like spam" in (edit.text or "")  # the default text, rendered
    buttons = {button.text: button for row in edit.reply_markup.inline_keyboard for button in row}
    assert "Edit" in buttons
    # Nothing is stored, so there is nothing to reset: only Edit and Back.
    assert set(buttons) == {"Edit", "Back"}


async def test_edit_waits_for_the_message_reads_it_and_deletes_it(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await send_template_input(app, admin_id, TEMPLATE_TEXT, TEMPLATE_ENTITIES)

    # The read input is deleted (§13); the Preview renders it with samples.
    (deleted,) = app.session.calls_of("DeleteMessage")
    assert (deleted.method.chat_id, deleted.method.message_id) == (admin_id, 77)
    buttons = buttons_of(app)
    assert "🟢 Save" in buttons
    assert "Cancel" in buttons
    assert "🔴 Reset to default" in buttons


async def test_the_preview_renders_sample_values_in_the_chat_language(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await send_template_input(
        app,
        admin_id,
        "{user}, it looks like your message {reason}. {duration}, {strike}.",
    )

    edit = app.session.calls_of("EditMessageText")[-1].method
    # Sample values in the Chat Language (en here — the chat was linked so).
    assert "Sample Member, it looks like your message looks like spam." in (edit.text or "")
    assert "1 hour, 1/3" in (edit.text or "")


async def test_save_stores_the_text_and_entities_as_received(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    await send_template_input(app, admin_id, TEMPLATE_TEXT, TEMPLATE_ENTITIES)
    app.session.calls.clear()

    await press(app, admin_id, f"chat-template-save:{chat_id}", menu)

    stored = await stored_template(app.session_maker, chat_id)
    assert stored is not None
    assert stored.text == TEMPLATE_TEXT
    assert stored.entities == [
        {"type": "bold", "offset": 0, "length": 6}
    ]  # as received, no custom_emoji — §14
    assert stored.updated_by == admin_id
    assert "Edit" in buttons_of(app)  # back on the template screen


async def test_custom_emoji_entities_are_stripped_the_fallback_emoji_stays(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)

    # "Hi {user}" with a custom emoji after the name: its fallback 🙂 is text.
    text = "Hi {user} 🙂"
    entities = [
        MessageEntity(type="bold", offset=0, length=2),
        MessageEntity(type="custom_emoji", offset=10, length=2, custom_emoji_id="42"),
    ]
    await send_template_input(app, admin_id, text, entities)
    await press(app, admin_id, f"chat-template-save:{chat_id}", menu)

    stored = await stored_template(app.session_maker, chat_id)
    assert stored is not None
    assert stored.text == text  # the fallback emoji character stays (§14)
    assert [entity["type"] for entity in stored.entities] == ["bold"]  # custom_emoji gone


async def test_a_saved_formatted_template_announces_the_violation(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    await send_template_input(app, admin_id, TEMPLATE_TEXT, TEMPLATE_ENTITIES)
    await press(app, admin_id, f"chat-template-save:{chat_id}", menu)
    app.session.calls.clear()

    app.backend.script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))  # not an Admin
    await app.feed(
        group_message_update(
            chat_id, member_id, "Buy cheap crypto now", message_id=78, sender_name="Spammer"
        )
    )
    await app.notices.flush()

    (notice,) = [
        call for call in app.session.calls_of("SendMessage") if call.method.chat_id == chat_id
    ]
    assert notice.method.text == "Spammer, it looks like your message looks like spam."
    entities = notice.method.entities
    assert entities is not None
    bold = [entity for entity in entities if entity.type == "bold"]
    # The bold span contained {user}, so it stretches over the name (§14).
    assert [(entity.offset, entity.length) for entity in bold] == [(0, 7)]
    mentions = [entity for entity in entities if entity.type == "text_mention"]
    assert [(entity.offset, entity.length) for entity in mentions] == [(0, 7)]
    assert mentions[0].user is not None and mentions[0].user.id == member_id


async def test_unknown_placeholders_are_rejected_with_the_allowed_names(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await send_template_input(app, admin_id, "{user} and {frobnicate}")

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "frobnicate" in (edit.text or "")
    assert "{user}" in (edit.text or "")  # the allowed names are listed
    assert await stored_template(app.session_maker, chat_id) is None  # nothing saved
    # The screen still offers the way out: Cancel (and Back).
    assert "Cancel" in buttons_of(app)


async def test_a_template_without_user_saves_with_a_warning(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await send_template_input(app, admin_id, "{reason} happened")

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "user" in (edit.text or "") and "warning" in (edit.text or "").lower()
    assert "🟢 Save" in buttons_of(app)  # warned, but savable (§14)


async def test_reset_to_default_drops_the_stored_template(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    await send_template_input(app, admin_id, "{user} broke the rules")
    await press(app, admin_id, f"chat-template-save:{chat_id}", menu)
    assert await stored_template(app.session_maker, chat_id) is not None
    app.session.calls.clear()

    await press(app, admin_id, f"chat-template-reset:{chat_id}", menu)

    assert await stored_template(app.session_maker, chat_id) is None
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "looks like spam" in (edit.text or "")  # back to the default text


async def test_cancel_keeps_the_stored_template(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    await send_template_input(app, admin_id, "{user} broke the rules")
    await press(app, admin_id, f"chat-template-save:{chat_id}", menu)
    app.session.calls.clear()

    # A second edit round, cancelled this time.
    await open_template(app, admin_id, chat_id, menu)
    await start_edit(app, admin_id, chat_id, menu)
    await send_template_input(app, admin_id, "{user} something else")
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(
            admin_id, f"chat-template-cancel:{chat_id}", menu, language_code="en"
        )
    )

    stored = await stored_template(app.session_maker, chat_id)
    assert stored is not None
    assert stored.text == "{user} broke the rules"  # the earlier save stands


async def test_a_non_admin_cannot_touch_the_template(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))  # the Home render
    stranger_menu = await started_admin(app, member_id)

    await app.feed(
        private_callback_update(
            member_id, f"chat-template:{chat_id}", stranger_menu, language_code="en"
        )
    )

    answer = app.session.calls_of("AnswerCallbackQuery")[-1].method
    assert answer.text == "You are no longer an admin of this chat."
    assert await stored_template(app.session_maker, chat_id) is None
