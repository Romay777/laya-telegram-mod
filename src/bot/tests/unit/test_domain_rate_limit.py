"""The §7 rate-limit maths, as pure domain logic.

`next_send_at` answers one question for the Chat Notice queue: given when
this chat's recent notices went out, when may the next one start? The
limits are the §7 pair — at most `per_second` messages per second and
`per_minute` per rolling minute, per chat.
"""

from datetime import UTC, datetime, timedelta

from app.domain.rate_limit import next_send_at

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_a_chat_that_has_sent_nothing_sends_at_once() -> None:
    assert next_send_at(T0, [], per_second=1, per_minute=18) == T0


def test_per_second_keeps_a_full_second_between_sends() -> None:
    sent = [T0]
    # Half a second after the last send, the next one waits out the rest.
    assert next_send_at(T0 + timedelta(milliseconds=400), sent, per_second=1, per_minute=18) == (
        T0 + timedelta(seconds=1)
    )
    # A full second later the send may start immediately.
    assert next_send_at(T0 + timedelta(seconds=1), sent, per_second=1, per_minute=18) == (
        T0 + timedelta(seconds=1)
    )


def test_per_minute_closes_the_rolling_window() -> None:
    # Eighteen sends, one per second across the first minute: the window is full.
    sent = [T0 + timedelta(seconds=i) for i in range(18)]
    now = T0 + timedelta(seconds=17)
    # The next send waits for the oldest one to age out of the window (§7).
    assert next_send_at(now, sent, per_second=1, per_minute=18) == T0 + timedelta(seconds=60)


def test_the_rolling_window_opens_as_sends_age_out() -> None:
    sent = [T0 + timedelta(seconds=i) for i in range(18)]
    # One minute on, the first send has left the window: 17 remain, so a send
    # may start at once.
    assert next_send_at(T0 + timedelta(seconds=60), sent, per_second=1, per_minute=18) == (
        T0 + timedelta(seconds=60)
    )


def test_per_minute_beats_per_second_when_it_binds_first() -> None:
    # Two sends 10 s apart: the per-second gap has long passed, but a third
    # send inside the same minute crosses `per_minute`.
    sent = [T0, T0 + timedelta(seconds=10)]
    now = T0 + timedelta(seconds=10)
    assert next_send_at(now, sent, per_second=1, per_minute=2) == T0 + timedelta(seconds=60)


def test_the_wait_is_never_negative_even_for_a_fast_limit() -> None:
    # A fast per_second with a send long ago: the gap is already satisfied.
    sent = [T0]
    assert next_send_at(T0 + timedelta(seconds=5), sent, per_second=10, per_minute=18) == (
        T0 + timedelta(seconds=5)
    )
