"""Notice Template validation and rendering (§14, §2).

A Notice Template is an Admin-defined Chat Notice text with Telegram
formatting and `{placeholders}`. The stored `text` and `entities` are the
Admin's message as received. Rendering replaces the placeholders left to
right and moves every entity offset by the length difference in UTF-16
code units — the offsets Telegram counts. An entity that fully contains a
placeholder stretches over the new value. No Telegram, no DB, no I/O.

`{{` and `}}` are literal braces, so a template can still talk about braces.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: The placeholders a template may name (§14).
PLACEHOLDERS: tuple[str, ...] = ("user", "reason", "duration", "strike")

#: The worst-case render must fit in this many characters (§14), counted the
#: way Telegram counts: UTF-16 code units.
MAX_RENDER_U16 = 1024


def utf16_len(text: str) -> int:
    """The length of `text` in UTF-16 code units — the offsets Telegram counts.

    Pure string math, so it lives in the domain: both the template renderer
    and the alert renderer need it, and the domain imports nothing (§2).
    """
    return len(text.encode("utf-16-le")) // 2


class TemplateError(Exception):
    """A template that names a placeholder outside the §14 table."""

    def __init__(self, unknown: str) -> None:
        super().__init__(unknown)
        self.unknown = unknown


@dataclass(frozen=True, slots=True)
class Replacement:
    """The value one placeholder renders as.

    `entity` is the optional entity that comes with the value, without its
    position — `{user}` renders as a `text_mention` of the Member (§14);
    rendering works out where the value landed and how long it is.
    """

    text: str
    entity: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Validation:
    """What a template must pass before the preview is shown (§14).

    An unknown placeholder is an error; a missing `{user}` only warns —
    the Admin may still save; the worst-case render must fit 1024.
    """

    unknown: str | None
    missing_user: bool
    fits: bool

    @property
    def ok(self) -> bool:
        return self.unknown is None and self.fits


def validate(
    text: str,
    entities: Sequence[dict[str, Any]],
    worst: dict[str, Replacement],
) -> Validation:
    """Check a template before the preview is shown (§14).

    `worst` holds the longest value each placeholder can take in the Chat
    Language — the longest Member name, reason phrase and duration text —
    so `fits` is the worst-case render length, not one sample's.
    """
    _, placeholders, _ = _scan(text)
    names = [name for name, _, _ in placeholders]
    unknown = next((name for name in names if name not in PLACEHOLDERS), None)
    if unknown is not None:
        return Validation(unknown=unknown, missing_user="user" not in names, fits=False)
    fits = utf16_len(render(text, entities, worst)[0]) <= MAX_RENDER_U16
    return Validation(unknown=unknown, missing_user="user" not in names, fits=fits)


def render(
    text: str,
    entities: Sequence[dict[str, Any]],
    values: dict[str, Replacement],
) -> tuple[str, list[dict[str, Any]]]:
    """Replace the placeholders of `text` left to right, entities following (§14).

    Returns the rendered text and its entities, offsets in UTF-16 code
    units. An entity that fully contains a placeholder stretches over the
    new value; one that only reaches into it is clamped to the edge, so no
    offset lands mid-replacement. Raises `TemplateError` on a placeholder
    outside the §14 table — the caller validates first (§14).
    """
    working, placeholders, removed = _scan(text)
    current = _without_removed(entities, removed)
    for name, _, _ in placeholders:
        if name not in values:
            raise TemplateError(name)

    # Placeholders never overlap, so each span's place in the result is its
    # place in the scanned text plus the replacements before it — tracked in
    # UTF-16 units, the offsets the entities speak.
    parts: list[str] = []
    cursor = 0
    shift = 0
    for name, start, end in placeholders:
        value = values[name]
        parts.append(working[cursor:start])
        span_start = start + shift
        span_len = end - start
        delta = utf16_len(value.text) - span_len
        current = [
            moved
            for entity in current
            if (moved := _over_replacement(entity, span_start, span_start + span_len, delta))
            is not None
        ]
        parts.append(value.text)
        if value.entity is not None:
            current.append({**value.entity, "offset": span_start, "length": utf16_len(value.text)})
        cursor = end
        shift += delta
    parts.append(working[cursor:])
    return "".join(parts), current


def _scan(
    text: str,
) -> tuple[str, list[tuple[str, int, int]], list[int]]:
    """One pass over the template: escapes collapsed, placeholders found.

    Returns the scanned text with `{{`/`}}` turned into single braces, the
    `{name}` placeholders as (name, start, end) spans of that scanned text,
    and the UTF-16 offsets the collapsing removed from the original — the
    difference between the entities' frame and the scanned text's.

    A lone `{` that opens no placeholder is literal text, and so is any
    other `}`, so nothing an Admin writes is lost.
    """
    pieces: list[str] = []
    placeholders: list[tuple[str, int, int]] = []
    removed: list[int] = []
    out = 0  # UTF-16 length of the scanned text so far
    index = 0
    while index < len(text):
        char = text[index]
        if char == "{":
            if text[index : index + 2] == "{{":
                removed.append(out)
                pieces.append("{")
                out += 1
                index += 2
                continue
            end = text.find("}", index + 1)
            if end == -1:
                pieces.append(text[index:])
                break
            name = text[index + 1 : end]
            if "{" in name:
                pieces.append("{")
                out += 1
                index += 1
                continue
            placeholders.append((name, out, out + end + 1 - index))
            pieces.append(text[index : end + 1])
            out += end + 1 - index
            index = end + 1
            continue
        if char == "}" and text[index : index + 2] == "}}":
            removed.append(out)
            pieces.append("}")
            out += 1
            index += 2
            continue
        pieces.append(char)
        out += utf16_len(char)
        index += 1
    return "".join(pieces), placeholders, removed


def _without_removed(
    entities: Sequence[dict[str, Any]], removed: Sequence[int]
) -> list[dict[str, Any]]:
    """Entities re-based onto the scanned text: shifted past each removed
    character, a unit shorter per removed character inside, gone when that
    leaves nothing."""

    def adjusted(entity: dict[str, Any]) -> dict[str, Any] | None:
        offset, length = entity["offset"], entity["length"]
        before = sum(1 for at in removed if at < offset)
        inside = sum(1 for at in removed if offset <= at < offset + length)
        new_offset, new_length = offset - before, length - inside
        return {**entity, "offset": new_offset, "length": new_length} if new_length > 0 else None

    return [kept for kept in (adjusted(entity) for entity in entities) if kept is not None]


def _over_replacement(
    entity: dict[str, Any], span_start: int, span_end: int, delta: int
) -> dict[str, Any] | None:
    """One entity around the replaced span `[span_start, span_end)` (§14).

    The text after the span moves by `delta` UTF-16 units. An entity that
    fully contains the span stretches over the new value (§14); one that
    only reaches into it is clamped to the span's edge. An entity left
    covering nothing — a format over only the braces of a placeholder —
    is dropped.
    """
    offset, length = entity["offset"], entity["length"]
    end = offset + length
    if end <= span_start:  # entirely before the span
        return entity
    if offset >= span_end:  # entirely after: follow the text
        return {**entity, "offset": offset + delta}
    if offset <= span_start and end >= span_end:  # fully contains: stretch (§14)
        return {**entity, "length": length + delta}
    if offset < span_start:  # reaches in from the left: stop at the span
        return {**entity, "length": span_start - offset}
    if end > span_end:  # reaches in from the right: start after the span
        return {**entity, "offset": span_end + delta, "length": end - span_end}
    return None  # inside the span: only the braces were there
