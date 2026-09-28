"""Chat Notice rate-limit maths (§7), as pure domain logic.

The queue asks one question here: given when this chat's recent notices
went out, when may the next one start? The §7 pair of limits answers it —
at most `per_second` messages per second and `per_minute` per rolling
minute. No Telegram, no DB, no I/O.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

_MINUTE = timedelta(minutes=1)


def next_send_at(
    now: datetime,
    sent_at: Iterable[datetime],
    *,
    per_second: float,
    per_minute: int,
) -> datetime:
    """The earliest moment at or after `now` when a further send may start (§7).

    `sent_at` holds the recent send times of one chat, in sending order —
    only the last minute of them matters here. Two rules bind:

    - `per_second`: a full `1 / per_second` gap since the last send;
    - `per_minute`: while a rolling minute already holds `per_minute`
      sends, the queue waits for the oldest of them to age out.
    """
    earliest = now
    sent = list(sent_at)
    if not sent:
        return earliest

    if per_second > 0:
        gap = timedelta(seconds=1 / per_second)
        earliest = max(earliest, sent[-1] + gap)

    if per_minute > 0:
        window = [t for t in sent if now - _MINUTE < t <= now]
        if len(window) >= per_minute:
            earliest = max(earliest, window[0] + _MINUTE)

    return earliest
