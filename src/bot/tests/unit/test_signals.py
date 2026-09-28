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


def test_is_short_follows_min_words_and_the_link_exception() -> None:
    assert is_short(text="hi there", entities=[], min_words=3)
    assert is_short(text="hi", entities=[], min_words=3)
    assert not is_short(text="hi there friends", entities=[], min_words=3)
    # Short, but the invite link keeps it in the pipeline (§4 step 4).
    assert not is_short(text="join t.me/+abc", entities=[], min_words=3)
