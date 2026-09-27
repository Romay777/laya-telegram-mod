"""Integration: `link_intent` rows against Postgres (§12, §10).

The token is a single-use Linking intent: valid for one hour, bound to the
Admin who pressed Add to chat, and consumed by the first successful Linking.
"""

from datetime import timedelta
from itertools import count

from app.clock import FakeClock
from app.db.repositories.link_intents import LinkIntentRepository
from app.linking.deep_link import INTENT_TTL
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared across tests, so every test gets its own Admin.
_user_ids = count(601, 10)


async def test_a_created_intent_is_found_for_its_admin_until_it_expires(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    user_id = next(_user_ids)
    repo = LinkIntentRepository(db_session)

    await repo.create(token="tok-live", user_id=user_id, expires_at=clock.now() + INTENT_TTL)

    found = await repo.consume_valid(user_id, now=clock.now())
    assert found is not None
    assert found.token == "tok-live"
    assert found.user_id == user_id


async def test_an_expired_intent_is_not_returned(db_session: AsyncSession) -> None:
    clock = FakeClock()
    user_id = next(_user_ids)
    await LinkIntentRepository(db_session).create(
        token="tok-old", user_id=user_id, expires_at=clock.now() + INTENT_TTL
    )

    clock.advance(timedelta(hours=1, seconds=1))

    found = await LinkIntentRepository(db_session).consume_valid(user_id, now=clock.now())
    assert found is None


async def test_an_intent_is_valid_for_exactly_one_hour(db_session: AsyncSession) -> None:
    clock = FakeClock()
    user_id = next(_user_ids)
    repo = LinkIntentRepository(db_session)
    await repo.create(token="tok-edge", user_id=user_id, expires_at=clock.now() + INTENT_TTL)

    clock.advance(timedelta(hours=1) - timedelta(seconds=1))
    within_the_hour = await repo.consume_valid(user_id, now=clock.now())

    clock.advance(timedelta(seconds=1))
    after_the_hour = await repo.consume_valid(user_id, now=clock.now())

    assert within_the_hour is not None
    assert after_the_hour is None


async def test_consuming_takes_the_intent_out(db_session: AsyncSession) -> None:
    """Single use: the first consume wins, a reused token finds nothing."""
    clock = FakeClock()
    user_id = next(_user_ids)
    repo = LinkIntentRepository(db_session)
    await repo.create(token="tok-once", user_id=user_id, expires_at=clock.now() + INTENT_TTL)

    first = await repo.consume_valid(user_id, now=clock.now())
    second = await repo.consume_valid(user_id, now=clock.now())

    assert first is not None
    assert second is None


async def test_only_the_bound_admin_can_consume_an_intent(db_session: AsyncSession) -> None:
    clock = FakeClock()
    await LinkIntentRepository(db_session).create(
        token="tok-bound", user_id=100500, expires_at=clock.now() + INTENT_TTL
    )

    stranger = await LinkIntentRepository(db_session).consume_valid(100501, now=clock.now())

    assert stranger is None
