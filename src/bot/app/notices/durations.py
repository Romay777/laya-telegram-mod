"""Step durations as text, in the reader's language (§14, §7).

The largest unit that fits, with correct plural forms from Fluent (`1 час`,
`3 часа`, `5 часов`; "forever" / "навсегда") — without code per case. The
default Chat Notice, the Admin Alerts and the Penalty Ladder screens all
read from here, so a Step always says the same thing everywhere.
"""

from app.i18n import GetText

_MINUTE = 60
_HOUR = 3600
_DAY = 86400


def duration_text(t: GetText, step_seconds: int) -> str:
    """A Step as text: the largest unit that fits, with plural forms (§14)."""
    if step_seconds <= 0:
        return t("notice-duration-forever")
    if step_seconds % _DAY == 0:
        return t("notice-duration-days", days=step_seconds // _DAY)
    if step_seconds % _HOUR == 0:
        return t("notice-duration-hours", hours=step_seconds // _HOUR)
    return t("notice-duration-minutes", minutes=max(step_seconds // _MINUTE, 1))
