"""Unit: the startgroup deep link of the primary Linking path (§10)."""

from datetime import timedelta

from app.linking.deep_link import INTENT_TTL, startgroup_url


def test_url_opens_the_group_picker_with_the_admin_rights_prefilled() -> None:
    url = startgroup_url("laya_moderator_bot", "tok-123")

    assert (
        url
        == "https://t.me/laya_moderator_bot?startgroup=tok-123&admin=delete_messages+restrict_members"
    )


def test_the_intent_is_valid_for_one_hour() -> None:
    assert INTENT_TTL == timedelta(hours=1)
