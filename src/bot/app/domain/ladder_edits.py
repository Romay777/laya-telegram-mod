"""Ladder-edit rules (§6): the shapes a Penalty Ladder edit may take.

The Penalty Ladder screen offers duration presets per Step, + Add step and
🔴 Remove last. The ladder is always 1-10 Steps long; the buttons that would
break that limit are `disabled` instead. No Telegram, no DB, no I/O.
"""

#: The duration presets one Step may take (§6): 5m … 30d, forever (0).
STEP_PRESETS: tuple[int, ...] = (
    300,  # 5m
    900,  # 15m
    3600,  # 1h
    10800,  # 3h
    43200,  # 12h
    86400,  # 1d
    259200,  # 3d
    604800,  # 7d
    2592000,  # 30d
    0,  # forever
)

#: The Expiry presets (§6): 7d … 90d, never (None).
EXPIRY_PRESETS: tuple[int | None, ...] = (
    604800,  # 7d
    1209600,  # 14d
    2592000,  # 30d
    5184000,  # 60d
    7776000,  # 90d
    None,  # never
)

#: The ladder is always 1-10 Steps long (§6).
MIN_STEPS = 1
MAX_STEPS = 10


def can_add(ladder: tuple[int, ...] | list[int]) -> bool:
    """Whether + Add step is enabled: the ladder has room below 10 Steps."""
    return len(ladder) < MAX_STEPS


def can_remove(ladder: tuple[int, ...] | list[int]) -> bool:
    """Whether 🔴 Remove last is enabled: the ladder has more than 1 Step."""
    return len(ladder) > MIN_STEPS


def add_step(ladder: tuple[int, ...] | list[int], duration: int) -> tuple[int, ...]:
    """Append a Step; a full ladder comes back unchanged (§6)."""
    if not can_add(ladder):
        return tuple(ladder)
    return (*ladder, duration)


def remove_last(ladder: tuple[int, ...] | list[int]) -> tuple[int, ...]:
    """Drop the last Step; a single-Step ladder comes back unchanged (§6)."""
    if not can_remove(ladder):
        return tuple(ladder)
    return tuple(ladder[:-1])


def set_step(ladder: tuple[int, ...] | list[int], index: int, duration: int) -> tuple[int, ...]:
    """Replace one Step's duration; anything out of range comes back unchanged.

    Only a preset duration lands: the screen offers buttons, never free text.
    """
    if duration not in STEP_PRESETS or not 0 <= index < len(ladder):
        return tuple(ladder)
    return (*ladder[:index], duration, *ladder[index + 1 :])
