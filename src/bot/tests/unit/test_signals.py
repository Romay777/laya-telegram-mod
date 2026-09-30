"""The cheap pre-classifier text filters (§4 steps 3-4).

`urls_in` slices by UTF-16 code units — the offsets Telegram sends — so an
astral character (an emoji) before a URL must not drift the slice.
"""

from app.moderation.signals import extract_state, has_link_invite_or_mention, is_short


def test_a_url_entity_is_collected_from_after_an_emoji() -> None:
    text = "🔥 https://example.com"
    # The emoji takes 2 UTF-16 units, then the space, then 19 URL characters;
    # a naive Python slice would start mid-text.
    entities = [{"type": "url", "offset": 3, "length": 19}]

    urls = extract_state(text, entities)["urls"]

    assert urls == ["https://example.com"]


def test_a_text_link_contributes_its_target() -> None:
    entities = [{"type": "text_link", "offset": 0, "length": 6, "url": "https://example.com"}]

    state = extract_state("a shop", entities)

    assert state == {"message": "a shop", "urls": ["https://example.com"]}


def test_an_invite_link_in_the_text_counts_even_without_entities() -> None:
    assert has_link_invite_or_mention("join https://t.me/+abc123", [])
    assert has_link_invite_or_mention("join https://t.me/joinchat/AAAA", [])


def test_a_mention_entity_counts() -> None:
    entities = [{"type": "mention", "offset": 5, "length": 8}]
    assert has_link_invite_or_mention("ping @spammer", entities)


def test_plain_text_counts_as_nothing() -> None:
    assert not has_link_invite_or_mention("just talking here", [])


def test_is_short_follows_min_chars_and_the_link_exception() -> None:
    assert is_short(text="hi there", entities=[], min_chars=10)
    assert is_short(text="hi", entities=[], min_chars=10)
    assert not is_short(text="hi there friends", entities=[], min_chars=10)
    # Under the limit, but the invite link keeps it in the pipeline (§4 step 4).
    assert not is_short(text="t.me/+abc", entities=[], min_chars=10)


def test_is_short_counts_characters_like_telegram_does() -> None:
    # Six emoji are 6 Python code points but 12 UTF-16 units, the count
    # Telegram shows — enough to clear a limit of 10 (§4 step 4).
    assert not is_short(text="😡" * 6, entities=[], min_chars=10)


def test_an_invite_link_is_detected_in_text_and_in_text_link_targets() -> None:
    from app.moderation.signals import has_invite_link

    assert has_invite_link("join https://t.me/+abc123", [])
    assert has_invite_link("https://t.me/joinchat/AAAA", [])
    assert has_invite_link(
        "come", [{"type": "text_link", "offset": 0, "length": 4, "url": "https://t.me/+xyz"}]
    )
    assert not has_invite_link("a plain https://example.com", [])
    assert not has_invite_link("https://t.me/durov", [])  # a public profile link is not an invite
