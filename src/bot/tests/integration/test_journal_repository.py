"""Integration: the Statistics and Journal queries against real Postgres (§13).

Seam: `JournalRepository`. Statistics must return the right numbers for
seeded data across both windows — 7 and 30 days — and the Journal must list
Violations newest first, 5 per page, with a total to steer the ◀ ▶ ends.
The card data joins the Violation with its check (the confidence) and its
stored text, and names its current state (§6).
"""

from datetime import timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import Chat
from app.db.repositories.appeals import AppealRepository
from app.db.repositories.chats import ChatRepository
from app.db.repositories.journal import JournalRepository
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.statistics import StatisticsRepository
from app.db.repositories.suspicions import SuspicionRepository
from app.domain.ladder import violation_state
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared, so every test gets its own chat and people.
_chat_ids = count(-100750, -1)
_user_ids = count(5000, 1)

SPAM_PROBABILITIES = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}


async def a_chat(db_session: AsyncSession, clock: FakeClock) -> Chat:
    return await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=77,
        linked_at=clock.now(),
        chat_language="en",
    )


class Seeder:
    """Plants checks, violations, suspicions and appeals at controlled times."""

    def __init__(self, db_session: AsyncSession, chat: Chat, clock: FakeClock) -> None:
        self.repo = ModerationRepository(db_session)
        self.appeals = AppealRepository(db_session)
        self.suspicions = SuspicionRepository(db_session)
        self.chat = chat
        self.clock = clock
        self.user_id = next(_user_ids)
        self._message_id = count(1)

    async def check(self, outcome: str, *, at: timedelta, category: str | None = None):
        """One check at `at` before the clock's fixed now."""
        return await self.repo.record_check(
            chat_id=self.chat.chat_id,
            user_id=self.user_id,
            message_id=next(self._message_id),
            backend=self.chat.backend,
            model="multilingual",
            spec_version=1,
            outcome=outcome,
            category=category,
            confidence=0.97 if category else None,
            probabilities=SPAM_PROBABILITIES if category else None,
            created_at=self.clock.now() - at,
        )

    async def violation(self, *, at: timedelta, category: str = "spam"):
        check = await self.check("violation", at=at, category=category)
        # The violation row's own clock: recorded when the message was checked.
        return await self.repo.record_violation(
            chat=self.chat,
            user_id=self.user_id,
            check_id=check.id,
            category=category,
            now=self.clock.now() - at,
        )

    async def suspicion(self, *, at: timedelta):
        """One Suspicion as the pipeline plants it: check, kept text, row (§4)."""
        check = await self.check("suspicion", at=at, category="ads")
        await self.repo.store_flagged(
            check.id,
            text="middle-band text",
            entities=[],
            purge_at=self.clock.now() + timedelta(days=30),
        )
        await self.suspicions.create(
            check_id=check.id,
            chat_id=self.chat.chat_id,
            user_id=self.user_id,
            message_id=check.message_id,
            created_at=self.clock.now() - at,
        )
        return check

    async def appeal(self, violation_id: int, *, at: timedelta):
        return await self.appeals.create(violation_id, created_at=self.clock.now() - at)


async def test_statistics_counts_seed_data_across_both_windows(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    seed = Seeder(db_session, chat, clock)

    # Inside the 7-day window: 9 checks (4 clean, 3 violations, 2
    # suspicions), 3 violations (2 spam, 1 ads — one of them revoked),
    # 2 suspicions, 1 appeal.
    for _ in range(4):
        await seed.check("clean", at=timedelta(hours=1))
    spam_one = await seed.violation(at=timedelta(hours=2), category="spam")
    await seed.violation(at=timedelta(hours=3), category="spam")
    ads_one = await seed.violation(at=timedelta(hours=4), category="ads")
    await seed.repo.revoke_violation(ads_one.id, by=77, at=clock.now())
    await seed.suspicion(at=timedelta(hours=5))
    await seed.suspicion(at=timedelta(hours=6))
    await seed.appeal(spam_one.id, at=timedelta(hours=7))

    # Between the windows (8-30 days): 3 clean checks, 1 insult violation,
    # 1 suspicion, 1 more spam violation, 1 appeal — all invisible to the
    # 7-day window. Each violation and suspicion carries its own check.
    for _ in range(3):
        await seed.check("clean", at=timedelta(days=20))
    await seed.violation(at=timedelta(days=21), category="insult")
    await seed.suspicion(at=timedelta(days=22))
    old = await seed.violation(at=timedelta(days=23), category="spam")
    await seed.appeal(old.id, at=timedelta(days=24))

    # Beyond both windows: counted by nothing.
    await seed.check("clean", at=timedelta(days=40))
    await seed.violation(at=timedelta(days=41), category="spam")

    repo = StatisticsRepository(db_session)
    week = await repo.statistics(chat.chat_id, since=clock.now() - timedelta(days=7))
    month = await repo.statistics(chat.chat_id, since=clock.now() - timedelta(days=30))

    assert week.checked == 9
    assert week.violations_by_category == {"spam": 2, "ads": 1}
    assert week.suspicions == 2
    assert week.appeals == 1
    assert week.false_positives == 1

    assert month.checked == 15
    assert month.violations_by_category == {"spam": 3, "ads": 1, "insult": 1}
    assert month.suspicions == 3
    assert month.appeals == 2
    assert month.false_positives == 1


async def test_a_false_positive_counts_when_it_was_revoked(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    seed = Seeder(db_session, chat, clock)
    await seed.violation(at=timedelta(days=40), category="spam")
    lifted_today = await seed.violation(at=timedelta(days=20), category="spam")
    await seed.repo.revoke_violation(lifted_today.id, by=77, at=clock.now())

    repo = StatisticsRepository(db_session)
    week = await repo.statistics(chat.chat_id, since=clock.now() - timedelta(days=7))
    month = await repo.statistics(chat.chat_id, since=clock.now() - timedelta(days=30))

    # The lift is when the False Positive happened: a Violation lifted today
    # counts today, whatever its age — while the 40-day-old Violation, never
    # lifted, shows up in neither window's False Positives (§13).
    assert week.false_positives == 1
    assert month.false_positives == 1
    assert week.violations_by_category == {}
    assert month.violations_by_category == {"spam": 1}  # still counted by created_at


async def test_the_journal_lists_violations_newest_first_five_per_page(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    seed = Seeder(db_session, chat, clock)
    recorded: list[int] = []
    for hours in range(7, 0, -1):  # oldest first, so ids climb with age backwards
        violation = await seed.violation(at=timedelta(hours=hours))
        recorded.append(violation.id)

    repo = JournalRepository(db_session)
    first, total = await repo.page(chat.chat_id, page=0)
    second, total_again = await repo.page(chat.chat_id, page=1)

    assert total == total_again == 7
    assert [entry.violation_id for entry in first] == list(reversed(recorded))[:5]
    assert [entry.violation_id for entry in second] == list(reversed(recorded))[5:]
    assert all(entry.category == "spam" for entry in first)


async def test_an_empty_journal_is_one_empty_page(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)

    entries, total = await JournalRepository(db_session).page(chat.chat_id, page=0)

    assert entries == []
    assert total == 0


async def test_the_card_joins_the_check_the_text_and_the_state(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    seed = Seeder(db_session, chat, clock)
    violation = await seed.violation(at=timedelta(hours=2), category="ads")
    await seed.repo.store_flagged(
        violation.check_id,
        text="Buy my product",
        entities=[{"type": "bold", "offset": 0, "length": 3}],
        purge_at=clock.now() + timedelta(days=30),
    )

    repo = JournalRepository(db_session)
    card = await repo.card(violation.id, now=clock.now())

    assert card is not None
    assert card.violation_id == violation.id
    assert card.chat_id == chat.chat_id
    assert card.user_id == seed.user_id
    assert card.category == "ads"
    assert card.confidence == 0.97
    assert card.step_seconds == 3600  # Step 1 of the default ladder
    assert (
        card.state
        == violation_state(revoked_at=None, expires_at=violation.expires_at, now=clock.now())
        == "active"
    )
    assert card.flagged_text == "Buy my product"
    assert card.flagged_entities == [{"type": "bold", "offset": 0, "length": 3}]


async def test_a_purged_card_has_no_text_left(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    seed = Seeder(db_session, chat, clock)
    violation = await seed.violation(at=timedelta(hours=2))
    await seed.repo.store_flagged(
        violation.check_id, text="soon gone", entities=[], purge_at=clock.now()
    )
    await seed.repo.purge_flagged(violation.check_id)

    card = await JournalRepository(db_session).card(violation.id, now=clock.now())

    assert card is not None
    assert card.flagged_text is None
    assert card.flagged_entities is None


async def test_a_card_of_a_missing_violation_is_none(db_session: AsyncSession) -> None:
    assert await JournalRepository(db_session).card(999_999, now=FakeClock().now()) is None
