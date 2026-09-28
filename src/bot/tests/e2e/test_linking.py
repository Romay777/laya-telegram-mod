"""End-to-end: the primary Linking path via the startgroup deep link (§10).

The promotion is a fabricated `my_chat_member` Update; assertions only look at
the recorded Bot API calls and the DB state (§17).
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import EditMessageText, GetChat, GetChatMember
from app.db.models import AdminSubscription, BotUser, Chat, LinkIntent
from app.domain.linking import DEFAULT_EXPIRY_SECONDS, DEFAULT_LADDER
from app.linking.deep_link import INTENT_TTL
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import FIXED_NOW, TestApp, app_fixture
from tests.support.telegram import (
    BOT_USER,
    chat_facts,
    member_administrator,
    member_member,
    member_owner,
)
from tests.support.updates import (
    my_chat_member_update,
    private_callback_update,
    start_update,
    user,
)

# The Postgres container is shared, so every test gets its own Admin and chat.
_admin_ids = count(900, 10)
_chat_ids = count(-100200, -10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
def second_chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


@pytest.fixture
async def quick_app(postgres_url: str) -> AsyncIterator[TestApp]:
    """The same app with a 50 ms prompt lifetime, so self-deletion is observable."""
    async with app_fixture(postgres_url, prompt_delete_after_s=0.05) as app:
        yield app


async def started_admin(app: TestApp, admin_id: int) -> int:
    """/start with the language picked; returns the Admin's Menu message id."""
    await app.feed(start_update(admin_id, "en"))
    menu_message_id = app.session.calls_of("SendMessage")[0].result.message_id
    await app.feed(
        private_callback_update(
            admin_id, "menu:set-language:en", menu_message_id, language_code="en"
        )
    )
    return menu_message_id


async def linked_menu(app: TestApp, admin_id: int) -> int:
    """The Admin started, picked a language and pressed Add to chat."""
    menu_message_id = await started_admin(app, admin_id)
    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )
    app.session.calls.clear()  # only the promotion's calls matter from here on
    return menu_message_id


async def the_intent(session_maker: async_sessionmaker[AsyncSession], admin_id: int) -> LinkIntent:
    async with session_maker() as db:
        return (
            await db.execute(select(LinkIntent).where(LinkIntent.user_id == admin_id))
        ).scalar_one()


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat | None:
    async with session_maker() as db:
        return await db.get(Chat, chat_id)


async def stored_subscription(
    session_maker: async_sessionmaker[AsyncSession], chat_id: int, admin_id: int
) -> AdminSubscription | None:
    async with session_maker() as db:
        return await db.get(AdminSubscription, (chat_id, admin_id))


async def test_add_to_chat_shows_the_deep_link_and_stores_a_one_hour_intent(
    app: TestApp, admin_id: int
) -> None:
    menu_message_id = await started_admin(app, admin_id)

    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )

    names = app.session.call_names()
    # The bot's username for the link, the screen, and the button press answer.
    assert names[3:] == ["GetMe", "EditMessageText", "AnswerCallbackQuery"]
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert edit.message_id == menu_message_id  # the same self-editing Menu message
    (open_picker,), (added_already,), (back,) = edit.reply_markup.inline_keyboard
    assert open_picker.text == "🔵 Open the group picker"
    assert added_already.callback_data == "menu:added-already:"  # the fallback path
    assert back.callback_data == "menu:home:"

    intent = await the_intent(app.session_maker, admin_id)
    assert open_picker.url == (
        f"https://t.me/laya_moderator_bot?startgroup={intent.token}"
        "&admin=delete_messages+restrict_members"
    )
    assert intent.user_id == admin_id  # bound to the Admin who pressed the button
    assert intent.expires_at == FIXED_NOW + INTENT_TTL  # valid for 1 hour


async def test_promotion_links_the_chat_and_edits_the_linkers_menu(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await linked_menu(app, admin_id)
    # Check 3 runs against getChatMember: the person who added the bot is its creator.
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )

    assert app.session.call_names() == ["GetChatMember", "EditMessageText"]
    edit = app.session.calls_of("EditMessageText")[0].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    lines = (edit.text or "").splitlines()
    assert lines[0] == "✅ My Chat linked"
    assert "Mode: Observation Mode" in lines  # every chat starts in Observation Mode
    assert "Backend: Laya" in lines
    assert "Sensitivity: Balanced" in lines
    (back,) = edit.reply_markup.inline_keyboard[0]
    assert back.callback_data == "menu:home:"

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.linker_id == admin_id
    assert chat.mode == "observation"
    assert chat.backend == "laya"
    assert chat.sensitivity == "balanced"
    assert chat.chat_language == "en"  # the Linker's interface language
    assert list(chat.ladder) == list(DEFAULT_LADDER)
    assert chat.expiry_seconds == DEFAULT_EXPIRY_SECONDS
    assert chat.linked_at == FIXED_NOW

    subscription = await stored_subscription(app.session_maker, chat_id, admin_id)
    assert subscription is not None
    assert subscription.alert_mode == "all"  # the Linker's alert subscription

    async with app.session_maker() as db:
        left = (
            (await db.execute(select(LinkIntent).where(LinkIntent.user_id == admin_id)))
            .scalars()
            .all()
        )
    assert left == []  # the token was single use


async def test_a_basic_group_gets_its_own_upgrade_explanation(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await linked_menu(app, admin_id)

    await app.feed(my_chat_member_update(chat_id, "group", linker_id=admin_id, title="Old Chat"))

    assert app.session.call_names() == [
        "EditMessageText"
    ]  # nothing live to check: not a supergroup
    edit = app.session.calls_of("EditMessageText")[0].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    text = edit.text or ""
    assert "Old Chat" in text
    assert "basic group" in text
    assert "supergroup" in text  # the explanation says how the chat is upgraded
    (check_against,), (back,) = edit.reply_markup.inline_keyboard
    assert check_against.text == "🔵 Check again"
    assert check_against.callback_data == f"link-check:{chat_id}"
    assert back.callback_data == "menu:home:"

    assert await stored_chat(app.session_maker, chat_id) is None
    assert await the_intent(app.session_maker, admin_id) is not None  # not consumed


async def test_missing_rights_are_listed_exactly(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu_message_id = await linked_menu(app, admin_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        my_chat_member_update(
            chat_id,
            "supergroup",
            linker_id=admin_id,
            title="My Chat",
            can_delete_messages=True,
            can_restrict_members=False,
        )
    )

    edit = app.session.calls_of("EditMessageText")[0].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    text = edit.text or ""
    assert "My Chat" in text
    assert "restrict members" in text
    assert "delete messages" not in text  # exactly what is missing
    assert await stored_chat(app.session_maker, chat_id) is None


async def test_a_linker_who_is_not_an_admin_is_told_so(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await linked_menu(app, admin_id)
    # getChatMember says the person who added the bot is a plain member.
    app.session.script(GetChatMember, member_member(user(admin_id)))

    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )

    edit = app.session.calls_of("EditMessageText")[0].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    assert "not an admin" in (edit.text or "")
    (check_against,), _ = edit.reply_markup.inline_keyboard
    assert check_against.callback_data == f"link-check:{chat_id}"
    assert await stored_chat(app.session_maker, chat_id) is None


async def test_check_again_succeeds_once_the_rights_are_fixed(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """The failure was missing rights; the bot was demoted and promoted again."""
    menu_message_id = await linked_menu(app, admin_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        my_chat_member_update(
            chat_id,
            "supergroup",
            linker_id=admin_id,
            title="My Chat",
            can_restrict_members=False,  # the missing right
        )
    )
    app.session.calls.clear()

    # 🔵 Check again: the bot's rights were fixed in the meantime.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))  # the bot, both rights
    app.session.script(GetChatMember, member_owner(user(admin_id)))  # the Linker
    await app.feed(
        private_callback_update(
            admin_id, f"link-check:{chat_id}", menu_message_id, language_code="en"
        )
    )

    assert app.session.calls_of("SendMessage") == []  # still the one self-editing Menu
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    assert (edit.text or "").startswith("✅ My Chat linked")

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.linker_id == admin_id
    subscription = await stored_subscription(app.session_maker, chat_id, admin_id)
    assert subscription is not None and subscription.alert_mode == "all"

    async with app.session_maker() as db:
        left = (
            (await db.execute(select(LinkIntent).where(LinkIntent.user_id == admin_id)))
            .scalars()
            .all()
        )
    assert left == []  # the intent was consumed by this completion


async def test_a_linker_who_blocked_the_bot_is_marked_unreachable(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """The link itself does not depend on the Menu edit going through (§9)."""
    await linked_menu(app, admin_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    app.session.script(
        EditMessageText,
        TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user"),
    )

    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )

    # The chat is linked regardless; only the Menu edit failed.
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.linker_id == admin_id
    assert await stored_subscription(app.session_maker, chat_id, admin_id) is not None

    async with app.session_maker() as db:
        linker = await db.get(BotUser, admin_id)
    assert linker is not None
    assert linker.reachable is False  # §9: a 403 marks the Admin unreachable


async def test_an_expired_token_links_nothing(app: TestApp, admin_id: int, chat_id: int) -> None:
    """The intent is valid for one hour; a promotion after that links nothing."""
    await linked_menu(app, admin_id)
    app.clock.advance(timedelta(hours=1, seconds=1))
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )

    # The checks ran, but the single-use token was spent by the clock:
    # no Menu change and no Linked Chat.
    assert app.session.call_names() == ["GetChatMember"]
    assert app.session.calls_of("EditMessageText") == []
    assert await stored_chat(app.session_maker, chat_id) is None


async def test_a_used_token_is_not_asked_for_twice(
    app: TestApp, admin_id: int, chat_id: int, second_chat_id: int
) -> None:
    """The first success consumes the intent; a second promotion finds nothing."""
    await linked_menu(app, admin_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )
    assert await stored_chat(app.session_maker, chat_id) is not None
    app.session.calls.clear()

    # The same Admin adds the bot to another group without pressing Add to chat again.
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        my_chat_member_update(second_chat_id, "supergroup", linker_id=admin_id, title="Other")
    )

    assert app.session.calls_of("EditMessageText") == []  # no Menu change
    assert await stored_chat(app.session_maker, second_chat_id) is None
    assert await stored_subscription(app.session_maker, second_chat_id, admin_id) is None


async def test_check_again_after_the_intent_expired_offers_a_new_link(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await linked_menu(app, admin_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        my_chat_member_update(
            chat_id,
            "supergroup",
            linker_id=admin_id,
            title="My Chat",
            can_restrict_members=False,
        )
    )
    app.clock.advance(timedelta(hours=2))  # the failure sat on the screen too long
    app.session.calls.clear()

    # Everything is fine now, but the one-hour intent is gone.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(
            admin_id, f"link-check:{chat_id}", menu_message_id, language_code="en"
        )
    )

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "expired" in (edit.text or "")
    (back,) = edit.reply_markup.inline_keyboard[0]
    assert back.callback_data == "menu:home:"
    assert await stored_chat(app.session_maker, chat_id) is None


async def test_a_linker_who_never_started_is_prompted_in_the_group(
    quick_app: TestApp, admin_id: int, chat_id: int
) -> None:
    """Someone who added the bot without ever opening it cannot be messaged (§10 step 6)."""
    quick_app.session.script(GetChatMember, member_owner(user(admin_id)))

    await quick_app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )

    assert quick_app.session.call_names() == ["GetChatMember", "GetMe", "SendMessage"]
    sent = quick_app.session.calls_of("SendMessage")[0].method
    assert sent.chat_id == chat_id  # one message, in the group
    assert "Start" in (sent.text or "")
    assert "@laya_moderator_bot" in (sent.text or "")  # where to open the bot
    prompt_message_id = quick_app.session.calls_of("SendMessage")[0].result.message_id

    # Nothing is linked yet: linking waits for their /start.
    assert await stored_chat(quick_app.session_maker, chat_id) is None

    # The prompt deletes itself (the harness shrinks the 10 minutes to 50 ms).
    await asyncio.sleep(0.2)
    (delete,) = quick_app.session.calls_of("DeleteMessage")
    assert (delete.method.chat_id, delete.method.message_id) == (chat_id, prompt_message_id)


async def test_linking_completes_when_the_prompted_linker_presses_start(
    quick_app: TestApp, admin_id: int, chat_id: int
) -> None:
    quick_app.session.script(GetChatMember, member_owner(user(admin_id)))
    await quick_app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )
    quick_app.session.calls.clear()

    # The Linker opens the bot and presses /start; the checks run once more.
    quick_app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    quick_app.session.script(GetChatMember, member_administrator(BOT_USER))
    quick_app.session.script(GetChatMember, member_owner(user(admin_id)))
    await quick_app.feed(start_update(admin_id, "en"))

    assert quick_app.session.call_names() == [
        "GetChat",
        "GetChatMember",
        "GetChatMember",
        "SendMessage",
    ]
    chat = await stored_chat(quick_app.session_maker, chat_id)
    assert chat is not None
    assert chat.linker_id == admin_id
    subscription = await stored_subscription(quick_app.session_maker, chat_id, admin_id)
    assert subscription is not None and subscription.alert_mode == "all"

    # The normal /start flow continues: a first-timer sees the language screen.
    (language_screen,) = quick_app.session.calls_of("SendMessage")
    assert language_screen.method.chat_id == admin_id
    assert language_screen.method.text == "Choose your language"

    # The pending link was consumed: another /start is the ordinary flow.
    quick_app.session.calls.clear()
    await quick_app.feed(start_update(admin_id, "en"))
    names = quick_app.session.call_names()
    assert "GetChat" not in names  # nothing left to re-check
    assert names == ["EditMessageText"]  # the ordinary /start: the Menu is re-edited
