"""Integration: the backend incident fan-out (§5, §9).

An incident alert reaches the chat's Admins whose mode includes incidents —
`all` and `appeals` (§9). It carries the backend and the reason, no buttons;
every copy is recorded in `admin_alert` under the subject `incident`, so
the recovery follow-up finds exactly the chats that were told. The Admin
Alert itself is part of the router ticket's flow: it is sent by the fanout,
the pipeline decides when.
"""

from itertools import count

import pytest
from aiogram import Bot
from aiogram.methods import GetChatMember
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
_chat_ids = count(-100870, -1)
_admin = count(40)

LINKER = next(_admin)  # 41: mode all
OFF = next(_admin)  # 42: mode off
APPEALS_ONLY = next(_admin)  # 43: mode appeals


@pytest.fixture
async def core() -> BaseCore:
    return await started_core()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


async def a_chat(db_session: AsyncSession, clock: FakeClock) -> Chat:
    chat = await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=LINKER,
        linked_at=clock.now(),
        chat_language="en",
    )
    subs = AdminSubscriptionRepository(db_session)
    await subs.set_mode(chat.chat_id, user_id=LINKER, alert_mode="all")
    await subs.set_mode(chat.chat_id, user_id=OFF, alert_mode="off")
    await subs.set_mode(chat.chat_id, user_id=APPEALS_ONLY, alert_mode="appeals")
    return chat


def a_fanout(core: BaseCore, clock: FakeClock):
    from app.alerts.fanout import AlertFanout

    async def no_sleep(delay: float) -> None:
        pass

    return AlertFanout(core=core, clock=clock, pace_s=0.0, sleep=no_sleep)


async def seed_users(db_session: AsyncSession, clock: FakeClock, *admin_ids: int) -> None:
    for admin_id in admin_ids:
        await BotUserRepository(db_session).get_or_create(admin_id, started_at=clock.now())


async def test_the_incident_alert_reaches_all_and_appeals_modes(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat = await a_chat(db_session, clock)
    await seed_users(db_session, clock, LINKER, OFF, APPEALS_ONLY)
    bot = Bot("42:test-token", session=FakeBotSession())
    bot.session.script(GetChatMember, member_owner(user(LINKER)))
    bot.session.script(GetChatMember, member_administrator(user(APPEALS_ONLY)))
    fanout = a_fanout(core, clock)

    await fanout.backend_incident_alert(
        bot,
        db_session,
        admin_cache=AdminCache(clock=clock, ttl_s=300.0),
        chat=chat,
        backend="Jev",
        reason="authentication failed",
        incident_id=7,
        using_laya=True,
    )

    # §9: incidents reach `all` and `appeals`; `off` gets nothing.
    sent_to = [call.method.chat_id for call in bot.session.calls_of("SendMessage")]
    assert sent_to == [LINKER, APPEALS_ONLY]
    (first, second) = bot.session.calls_of("SendMessage")
    assert first.method.text == "⚠️ Jev is unavailable: authentication failed. Using Laya"
    assert first.method.reply_markup is None  # no buttons on an incident alert
    assert second.method.text == first.method.text

    # The copies are recorded under the incident, so recovery finds them (§5).
    copies = await AlertRepository(db_session).alerts_for("incident", 7)
    assert [copy.admin_id for copy in copies] == [LINKER, APPEALS_ONLY]


async def test_the_recovery_follow_up_goes_to_exactly_the_chats_that_were_told(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat = await a_chat(db_session, clock)
    await seed_users(db_session, clock, LINKER, OFF, APPEALS_ONLY)
    bot = Bot("42:test-token", session=FakeBotSession())
    bot.session.script(GetChatMember, member_owner(user(LINKER)))
    bot.session.script(GetChatMember, member_administrator(user(APPEALS_ONLY)))
    fanout = a_fanout(core, clock)
    await fanout.backend_incident_alert(
        bot,
        db_session,
        admin_cache=AdminCache(clock=clock, ttl_s=300.0),
        chat=chat,
        backend="Jev",
        reason="rate limited",
        incident_id=8,
        using_laya=True,
    )
    bot.session.calls.clear()
    bot.session.script(GetChatMember, member_owner(user(LINKER)))
    bot.session.script(GetChatMember, member_administrator(user(APPEALS_ONLY)))

    await fanout.backend_recovery_alerts(
        bot,
        db_session,
        admin_cache=AdminCache(clock=clock, ttl_s=300.0),
        incident_id=8,
        backend="Jev",
    )

    # The same Admins, one "✅ Jev is back" each — none to the `off` Admin.
    sent_to = [call.method.chat_id for call in bot.session.calls_of("SendMessage")]
    assert sent_to == [LINKER, APPEALS_ONLY]
    (first, _second) = bot.session.calls_of("SendMessage")
    assert first.method.text == "✅ Jev is back"
    assert first.method.reply_markup is None


async def test_a_recovery_for_an_incident_nobody_was_told_about_sends_nothing(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    bot = Bot("42:test-token", session=FakeBotSession())
    fanout = a_fanout(core, clock)

    await fanout.backend_recovery_alerts(
        bot,
        db_session,
        admin_cache=AdminCache(clock=clock, ttl_s=300.0),
        incident_id=404,
        backend="Jev",
    )

    assert bot.session.calls_of("SendMessage") == []
