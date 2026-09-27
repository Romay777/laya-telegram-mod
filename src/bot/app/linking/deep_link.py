"""The startgroup deep link of the primary Linking path (§10 step 1).

`<token>` is a one-time `link_intent` code, valid for one hour and tied to the
Admin who pressed 🟢 Add to chat. The `admin` parameter pre-fills the required
rights in Telegram's group picker.
"""

from datetime import timedelta

INTENT_TTL = timedelta(hours=1)

ADMIN_RIGHTS_PARAM = "delete_messages+restrict_members"


def startgroup_url(bot_username: str, token: str) -> str:
    """The `t.me` link that opens Telegram's group picker for `bot_username`."""

    return f"https://t.me/{bot_username}?startgroup={token}&admin={ADMIN_RIGHTS_PARAM}"
