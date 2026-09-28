"""Unit: the Admin Alert texts and entities (§8, §9, §15).

An alert is private-chat text, so it renders in the Admin's language. The
flagged message is quoted with its stored entities, which must move by the
length of the header in UTF-16 code units — the units Telegram counts.
"""

from app.alerts.rendering import (
    message_link,
    render_appeal_alert,
    render_suspicion_alert,
    render_violation_alert,
)
from app.i18n import translator_for

from tests.support.i18n import started_core


def utf16(text: str) -> int:
    """The length of `text` in UTF-16 code units, as Telegram counts it."""
    return len(text.encode("utf-16-le")) // 2


HEADER = "⚠️ My Chat\nMember: Ann\nSpam, 97%\nRestriction: 1 hour"
FOREVER_HEADER = "⚠️ My Chat\nMember: Ann\nSpam, 97%\nRestriction: forever"
QUOTE = "Buy cheap crypto now, DM me"


async def test_the_alert_names_the_chat_member_category_step_and_quotes_the_message() -> None:
    core = await started_core()

    text, entities = render_violation_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        member_name="Ann",
        category="spam",
        confidence=0.97,
        step_seconds=3600,
        flagged_text=QUOTE,
        flagged_entities=[{"offset": 0, "length": 3, "type": "bold"}],
    )

    assert text == f"{HEADER}\n\n{QUOTE}"
    shift = utf16(HEADER + "\n\n")
    assert {(e["type"], e["offset"], e["length"]) for e in entities} == {
        ("bold", shift, 3),  # the stored entity, moved past the header
        ("blockquote", shift, utf16(QUOTE)),  # the quote itself
    }


async def test_stored_entity_offsets_shift_in_utf16_units_not_characters() -> None:
    core = await started_core()
    # The chat title carries an astral-plane emoji: one Python character,
    # two UTF-16 code units.
    title = "Chat 🚀"

    text, entities = render_violation_alert(
        translator_for(core, "en"),
        chat_title=title,
        member_name="Ann",
        category="ads",
        confidence=0.9,
        step_seconds=3600,
        flagged_text=QUOTE,
        flagged_entities=[{"offset": 0, "length": 3, "type": "bold"}],
    )

    shift = utf16(text[: -len(QUOTE)])
    bold = next(e for e in entities if e["type"] == "bold")
    assert bold["offset"] == shift
    assert bold["offset"] > len(text[: -len(QUOTE)])  # UTF-16 counts the rocket twice


async def test_a_purged_message_says_so_instead_of_the_quote() -> None:
    core = await started_core()

    text, entities = render_violation_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        member_name="Ann",
        category="spam",
        confidence=0.97,
        step_seconds=0,
        flagged_text=None,
        flagged_entities=None,
    )

    assert text == f"{FOREVER_HEADER}\n\nThe message text is no longer stored."
    assert entities == []


async def test_the_alert_renders_in_the_admins_language() -> None:
    core = await started_core()

    text, _ = render_violation_alert(
        translator_for(core, "ru"),
        chat_title="Мой чат",
        member_name="Аня",
        category="spam",
        confidence=0.97,
        step_seconds=3600,
        flagged_text="Купи дёшево",
        flagged_entities=[],
    )

    assert text.startswith(
        "\n".join(["⚠️ Мой чат", "Участник: Аня", "Спам, 97 %", "Ограничение: 1 час", ""])
    )


async def test_the_appeal_alert_adds_the_appeal_line_and_keeps_the_entities() -> None:
    core = await started_core()

    text, entities = render_appeal_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        member_name="Ann",
        category="spam",
        confidence=0.97,
        step_seconds=3600,
        flagged_text=QUOTE,
        flagged_entities=[{"offset": 0, "length": 3, "type": "bold"}],
    )

    assert text == f"{HEADER}\n🙋 The Member has appealed\n\n{QUOTE}"
    shift = utf16(text[: -len(QUOTE)])
    assert {(e["type"], e["offset"], e["length"]) for e in entities} == {
        ("bold", shift, 3),  # the stored entity survives, moved past the header
        ("blockquote", shift, utf16(QUOTE)),
    }


async def test_a_purged_appeal_says_so_instead_of_the_quote() -> None:
    core = await started_core()

    text, entities = render_appeal_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        member_name="Ann",
        category="spam",
        confidence=0.97,
        step_seconds=3600,
        flagged_text=None,
        flagged_entities=None,
    )

    assert text == f"{HEADER}\n🙋 The Member has appealed\n\nThe message text is no longer stored."
    assert entities == []


async def test_a_message_link_points_into_the_private_supergroup() -> None:
    assert message_link(chat_id=-1004501234567, message_id=77) == "https://t.me/c/4501234567/77"


async def test_the_suspicion_alert_names_the_facts_links_the_message_and_quotes_it() -> None:
    core = await started_core()
    url = message_link(chat_id=-1004501234567, message_id=77)

    text, entities = render_suspicion_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        member_name="Ann",
        category="spam",
        confidence=0.70,
        url=url,
        flagged_text=QUOTE,
        flagged_entities=[{"offset": 0, "length": 3, "type": "bold"}],
    )

    assert text == f"🔍 My Chat\nMember: Ann\nSpam, 70%\n{url}\n\n{QUOTE}"
    # The link is clickable: a text_link entity over the URL line itself.
    url_offset = utf16("🔍 My Chat\nMember: Ann\nSpam, 70%\n")
    shift = utf16(text[: -len(QUOTE)])
    assert {(e["type"], e["offset"], e["length"]) for e in entities} == {
        ("text_link", url_offset, utf16(url)),
        ("bold", shift, 3),  # the stored entity, moved past the header
        ("blockquote", shift, utf16(QUOTE)),
    }


async def test_the_suspicion_alert_link_offset_counts_utf16_units() -> None:
    core = await started_core()
    # The chat title carries an astral-plane emoji: one Python character,
    # two UTF-16 code units.
    url = message_link(chat_id=-1004501234567, message_id=77)

    _text, entities = render_suspicion_alert(
        translator_for(core, "en"),
        chat_title="Chat 🚀",
        member_name="Ann",
        category="ads",
        confidence=0.70,
        url=url,
        flagged_text=QUOTE,
        flagged_entities=[],
    )

    url_offset = utf16("🔍 Chat 🚀\nMember: Ann\nAdvertising, 70%\n")
    link = next(e for e in entities if e["type"] == "text_link")
    assert link["offset"] == url_offset
    assert link["offset"] < len("🔍 Chat 🚀\nMember: Ann\nAdvertising, 70%\n") + len(url) - 1


async def test_a_purged_suspicion_says_so_but_keeps_the_link() -> None:
    core = await started_core()
    url = message_link(chat_id=-100450, message_id=77)

    text, entities = render_suspicion_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        member_name="Ann",
        category="spam",
        confidence=0.70,
        url=url,
        flagged_text=None,
        flagged_entities=None,
    )

    assert "The message text is no longer stored." in text
    # The link is part of the header, so it survives the purged quote (§9).
    assert entities == [
        {
            "type": "text_link",
            "offset": utf16("🔍 My Chat\nMember: Ann\nSpam, 70%\n"),
            "length": utf16(url),
            "url": url,
        }
    ]
