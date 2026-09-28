"""Integration: `AlertFanout` (§9) — the fan-out module's public interface.

Recipients are the chat's current Admins whose alert mode for the chat is
`all`; a 403 marks an Admin unreachable and skipped from then on; every
sent message is recorded so every copy can be edited later; and private
alerts are paced about one message per second per Admin.
"""

from itertools import count

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import GetChatMember, SendMessage
from aiogram_i18n.cores.base import BaseCore
from app.clock import FakeClock
from app.db.models import Chat
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.chats import ChatRepository
from app.db.repositories.subscriptions import AdminSubscriptionRepository
from app.db.repositories.users import BotUserRepository
from app.linking.admin_cache import AdminCache
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_session import FakeBotSession
from tests.support.i18n import started_core
from tests.support.telegram import member_administrator, member_owner
from tests.support.updates import user

# The Postgres container is shared, so every test gets its own chat and people.
_chat_ids = count(-100820, -1)
_admin = count(20)

LINKER = next(_admin)  # 21
SECOND = next(_admin)  # 22
APPEALS_ONLY = next(_admin)  # 23
GONE = next(_admin)  # 24

TEXT = "Buy cheap crypto now, DM me"
ENTITIES = [{"offset": 0, "length": 3, "type": "bold"}]


@pytest.fixture
async def core() -> BaseCore:
    return await started_core()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def a_bot() -> Bot:
    return Bot("42:test-token", session=FakeBotSession())


async def a_chat(db_session: AsyncSession, clock: FakeClock) -> Chat:
    chat = await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=LINKER,
        linked_at=clock.now(),
        chat_language="en",
    )
    subs = AdminSubscriptionRepository(db_session)
    await subs.set_mode(chat.chat_id, user_id=LINKER, alert_mode="all")  # the Linker default
    await subs.set_mode(chat.chat_id, user_id=SECOND, alert_mode="all")
    await subs.set_mode(chat.chat_id, user_id=APPEALS_ONLY, alert_mode="appeals")
    await subs.set_mode(chat.chat_id, user_id=GONE, alert_mode="all")
    return chat


def a_fanout(core: BaseCore, clock: FakeClock):
    """The fan-out with a sleep recorder instead of real waiting."""
    from app.alerts.fanout import AlertFanout

    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    fanout = AlertFanout(core=core, clock=clock, pace_s=1.0, sleep=record_sleep)
    fanout.sleeps = sleeps  # type: ignore[attr-defined] — read by the assertions
    return fanout


async def seed_users(db_session: AsyncSession, clock: FakeClock, *admin_ids: int) -> None:
    for admin_id in admin_ids:
        await BotUserRepository(db_session).get_or_create(admin_id, started_at=clock.now())


async def test_alerts_go_to_every_current_admin_with_mode_all(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat = await a_chat(db_session, clock)
    await seed_users(db_session, clock, LINKER, SECOND, GONE)
    gone = await BotUserRepository(db_session).get(GONE)
    assert gone is not None
    gone.reachable = False
    await db_session.flush()

    bot = a_bot()
    bot.session.script(GetChatMember, member_owner(user(LINKER)))
    bot.session.script(GetChatMember, member_administrator(user(SECOND)))
    fanout = a_fanout(core, clock)

    await fanout.violation_alert(
        bot,
        db_session,
        admin_cache=AdminCache(clock=clock, ttl_s=300.0),
        chat=chat,
        member_name="Spammer",
        category="spam",
        confidence=0.97,
        step_seconds=3600,
        flagged_text=TEXT,
        flagged_entities=ENTITIES,
        violation_id=5,
    )

    # The Linker and the opted-in Admin got a private alert; the
    # appeals-only Admin and the unreachable one did not (§9).
    sent_to = [call.method.chat_id for call in bot.session.calls_of("SendMessage")]
    assert sent_to == [LINKER, SECOND]
    asked = [call.method.user_id for call in bot.session.calls_of("GetChatMember")]
    assert asked == [LINKER, SECOND]  # the unreachable Admin is skipped before asking,
    # and the appeals-only Admin is not a recipient at all

    (first, second) = bot.session.calls_of("SendMessage")
    alert = first.method
    assert "My Chat" in alert.text
    assert "Spammer" in alert.text
    assert "Spam, 97%" in alert.text
    assert "1 hour" in alert.text
    assert TEXT in alert.text  # the deleted message, quoted
    (button,) = alert.reply_markup.inline_keyboard[0]
    assert button.text == "🟢 Lift restriction"

    # Every alert message sent is recorded: admin, message id, subject (§9).
    copies = await AlertRepository(db_session).alerts_for("violation", 5)
    assert [(copy.admin_id, copy.message_id) for copy in copies] == [
        (LINKER, first.result.message_id),
        (SECOND, second.result.message_id),
    ]


async def test_alerts_are_paced_one_per_second_per_admin(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat = await a_chat(db_session, clock)
    await seed_users(db_session, clock, LINKER, SECOND, GONE)
    for bystander in (SECOND, GONE):  # out of the way: only the Linker is paced here
        user_row = await BotUserRepository(db_session).get(bystander)
        assert user_row is not None
        user_row.reachable = False
    await db_session.flush()
    bot = a_bot()
    bot.session.script(GetChatMember, member_owner(user(LINKER)))
    fanout = a_fanout(core, clock)
    kwargs = {
        "admin_cache": AdminCache(clock=clock, ttl_s=300.0),
        "chat": chat,
        "member_name": "Spammer",
        "category": "spam",
        "confidence": 0.97,
        "step_seconds": 3600,
        "flagged_text": TEXT,
        "flagged_entities": ENTITIES,
    }

    await fanout.violation_alert(bot, db_session, violation_id=6, **kwargs)
    assert fanout.sleeps == []  # a first alert to an Admin is not delayed

    await fanout.violation_alert(bot, db_session, violation_id=7, **kwargs)
    assert fanout.sleeps == [1.0]  # the next alert to the same Admin waits a second

    await fanout.violation_alert(bot, db_session, violation_id=8, **kwargs)
    assert fanout.sleeps == [1.0, 1.0]


async def test_a_403_marks_the_admin_unreachable_and_skipped(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat = await a_chat(db_session, clock)
    await seed_users(db_session, clock, LINKER, SECOND, GONE)
    gone = await BotUserRepository(db_session).get(GONE)
    assert gone is not None
    gone.reachable = False  # out of the way: this test is about the Linker's 403
    await db_session.flush()
    bot = a_bot()
    bot.session.script(GetChatMember, member_owner(user(LINKER)))
    bot.session.script(GetChatMember, member_administrator(user(SECOND)))
    fanout = a_fanout(core, clock)
    kwargs = {
        "admin_cache": AdminCache(clock=clock, ttl_s=300.0),
        "chat": chat,
        "member_name": "Spammer",
        "category": "spam",
        "confidence": 0.97,
        "step_seconds": 3600,
        "flagged_text": TEXT,
        "flagged_entities": ENTITIES,
    }
    await fanout.violation_alert(bot, db_session, violation_id=9, **kwargs)

    # The Linker blocked the bot: their send is the next one to fail (§9).
    bot.session.script(
        SendMessage,
        TelegramForbiddenError(method=SendMessage(chat_id=LINKER, text="x"), message="blocked"),
    )
    bot.session.calls.clear()
    await fanout.violation_alert(bot, db_session, violation_id=10, **kwargs)

    linker = await BotUserRepository(db_session).get(LINKER)
    assert linker is not None and linker.reachable is False
    assert [call.method.chat_id for call in bot.session.calls_of("SendMessage")] == [
        LINKER,
        SECOND,
    ]  # the failed attempt to the Linker, then the second Admin's alert

    # From then on the Linker is skipped, without even being asked (§9).
    bot.session.calls.clear()
    await fanout.violation_alert(bot, db_session, violation_id=11, **kwargs)
    sent_to = [call.method.chat_id for call in bot.session.calls_of("SendMessage")]
    asked = [call.method.user_id for call in bot.session.calls_of("GetChatMember")]
    assert sent_to == [SECOND]
    assert LINKER not in asked
