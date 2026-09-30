"""Unit: the Statistics, Journal and Violation card screens (§13).

Pure screen functions over a Fluent core: the numbers and the quote on the
text, the toggle and pager wiring on the keyboard, and the `disabled` marks
at either end of the pager and on a lifted card's button.
"""

from datetime import UTC, datetime, timedelta

from app.db.repositories.journal import ChatStatistics, JournalEntry, ViolationCard
from app.i18n import translator_for
from app.menu.screens.chat import chat_screen
from app.menu.screens.journal import journal_screen
from app.menu.screens.statistics import statistics_screen
from app.menu.screens.violation_card import violation_card_screen

from tests.support.i18n import started_core

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def buttons_of(screen):
    return [button for row in screen.reply_markup.inline_keyboard for button in row]


def by_text(screen):
    return {button.text: button for button in buttons_of(screen)}


async def test_the_statistics_screen_shows_the_counts_and_marks_the_window() -> None:
    core = await started_core()
    t = translator_for(core, "en")

    screen = statistics_screen(
        t,
        "My Chat",
        chat_id=-100200,
        stats=ChatStatistics(
            checked=9,
            violations_by_category={"spam": 2, "ads": 1},
            suspicions=2,
            appeals=1,
            false_positives=1,
        ),
        days=7,
    )

    assert "Messages checked: 9" in screen.text
    assert "Violations: 3" in screen.text
    assert "Spam: 2" in screen.text
    assert "Advertising: 1" in screen.text
    assert "Suspicions: 2" in screen.text
    assert "Appeals: 1" in screen.text
    assert "False positives: 1" in screen.text

    buttons = by_text(screen)
    assert buttons["Last 7 days"].style == "primary"  # the window shown
    assert buttons["Last 7 days"].callback_data == "chat-statistics:-100200:7"
    assert buttons["Last 30 days"].style is None
    assert buttons["Last 30 days"].callback_data == "chat-statistics:-100200:30"
    assert buttons["Back"].callback_data == "chat:-100200"


async def test_a_window_without_violations_shows_no_category_lines() -> None:
    core = await started_core()
    t = translator_for(core, "en")

    screen = statistics_screen(
        t,
        "My Chat",
        chat_id=-100200,
        stats=ChatStatistics(
            checked=4, violations_by_category={}, suspicions=0, appeals=0, false_positives=0
        ),
        days=30,
    )

    assert "Violations: 0" in screen.text
    assert "Spam" not in screen.text
    assert by_text(screen)["Last 30 days"].style == "primary"


async def test_the_journal_screen_lists_entries_and_disables_the_first_arrow() -> None:
    core = await started_core()
    t = translator_for(core, "en")
    entries = [
        JournalEntry(
            violation_id=vid, category="spam", user_id=500, created_at=NOW - timedelta(hours=vid)
        )
        for vid in (6, 5, 4, 3, 2)  # newest first
    ]

    screen = journal_screen(t, "My Chat", chat_id=-100200, entries=entries, total=7, page=0)

    assert "Page 1 of 2" in screen.text
    entry_buttons = [
        button
        for button in buttons_of(screen)
        if button.callback_data.startswith("violation-card:")
    ]
    assert [button.callback_data for button in entry_buttons] == [
        f"violation-card:-100200:{vid}:0" for vid in (6, 5, 4, 3, 2)
    ]
    pager = {button.text: button for button in buttons_of(screen) if button.text in ("◀", "▶")}
    assert pager["◀"].disabled is not None  # the first page has nothing before it
    assert pager["▶"].disabled is None
    assert pager["▶"].callback_data == "journal:-100200:1"
    assert by_text(screen)["Back"].callback_data == "chat:-100200"


async def test_the_last_journal_page_disables_the_second_arrow() -> None:
    core = await started_core()
    t = translator_for(core, "en")
    entries = [
        JournalEntry(
            violation_id=1, category="spam", user_id=500, created_at=NOW - timedelta(hours=9)
        ),
        JournalEntry(
            violation_id=1, category="ads", user_id=501, created_at=NOW - timedelta(hours=8)
        ),
    ]

    screen = journal_screen(t, "My Chat", chat_id=-100200, entries=entries, total=7, page=1)

    assert "Page 2 of 2" in screen.text
    pager = {button.text: button for button in buttons_of(screen) if button.text in ("◀", "▶")}
    assert pager["◀"].callback_data == "journal:-100200:0"
    assert pager["▶"].disabled is not None  # the last page has nothing after it


async def test_an_empty_journal_has_no_pager_and_says_so() -> None:
    core = await started_core()
    t = translator_for(core, "en")

    screen = journal_screen(t, "My Chat", chat_id=-100200, entries=[], total=0, page=0)

    assert "No violations yet" in screen.text
    assert not [button for button in buttons_of(screen) if button.text in ("◀", "▶")]
    assert by_text(screen)["Back"].callback_data == "chat:-100200"


async def test_the_card_shows_the_facts_the_state_and_the_quote() -> None:
    core = await started_core()
    t = translator_for(core, "en")
    card = ViolationCard(
        violation_id=3,
        chat_id=-100200,
        user_id=500,
        category="ads",
        confidence=0.97,
        step_seconds=3600,
        state="active",
        flagged_text="Buy my product",
        flagged_entities=[{"type": "bold", "offset": 0, "length": 3}],
        created_at=NOW - timedelta(hours=2),
    )

    screen = violation_card_screen(t, "My Chat", chat_id=-100200, card=card, page=1)

    assert "Member: 500" in screen.text
    assert "Advertising" in screen.text
    assert "97%" in screen.text
    assert "1 hour" in screen.text
    assert "2025-12-31 22:00" in screen.text  # when it happened
    assert "Active" in screen.text
    assert screen.text.endswith("Buy my product")  # the quoted text
    kinds = [entity["type"] for entity in screen.entities]
    assert "blockquote" in kinds and "bold" in kinds

    buttons = by_text(screen)
    lift = buttons["🟢 Lift restriction"]
    assert lift.callback_data == "lift:-100200:3"
    assert lift.style == "success"
    assert lift.disabled is None  # an Active Violation can be lifted
    assert buttons["Back"].callback_data == "journal:-100200:1"  # the page it came from


async def test_a_purged_card_says_the_text_is_gone() -> None:
    core = await started_core()
    t = translator_for(core, "en")
    card = ViolationCard(
        violation_id=3,
        chat_id=-100200,
        user_id=500,
        category="spam",
        confidence=0.97,
        step_seconds=3600,
        state="active",
        flagged_text=None,
        flagged_entities=None,
        created_at=NOW - timedelta(hours=2),
    )

    screen = violation_card_screen(t, "My Chat", chat_id=-100200, card=card, page=0)

    assert "no longer stored" in screen.text
    assert not [entity for entity in screen.entities if entity["type"] == "blockquote"]


async def test_a_false_positive_card_disables_the_lift_button() -> None:
    core = await started_core()
    t = translator_for(core, "en")
    card = ViolationCard(
        violation_id=3,
        chat_id=-100200,
        user_id=500,
        category="spam",
        confidence=0.97,
        step_seconds=3600,
        state="false_positive",
        flagged_text=None,
        flagged_entities=None,
        created_at=NOW - timedelta(hours=2),
    )

    screen = violation_card_screen(t, "My Chat", chat_id=-100200, card=card, page=0)

    assert "False Positive" in screen.text
    assert by_text(screen)["🟢 Lift restriction"].disabled is not None


async def test_the_chat_screen_carries_the_statistics_and_journal_buttons() -> None:
    core = await started_core()
    t = translator_for(core, "en")

    screen = chat_screen(
        t,
        "My Chat",
        chat_id=-100200,
        mode="observation",
        backend="laya",
        sensitivity="balanced",
    )

    buttons = by_text(screen)
    assert buttons["Statistics"].callback_data == "chat-statistics:-100200:"
    assert buttons["Journal"].callback_data == "journal:-100200:0"
