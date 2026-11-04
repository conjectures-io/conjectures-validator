"""Names other people read: account display names and signed public credit.

Both are chosen by whoever holds the account and rendered to everyone else — next to results, in
search, on the dashboard, in exports and notifications. The frontend escapes text, but not every
place a name ends up does, and auditing every sink is a worse bet than refusing at the door what no
real name needs. One display name already in the database is `<img src=x onerror=alert(...)>`.

So this is an allowlist, not a blocklist of known-bad strings:

* **letters, digits and combining marks** in any script — a mathematician's name is not ASCII;
* **the plain space**, one at a time;
* **the punctuation names and handles actually use**: `. , ' ’ - _ ( ) &`.

Everything else is refused, which is what removes, without having to enumerate them: markup and
template syntax (angle brackets, double quotes, backticks, braces, square brackets, backslashes,
`$`, `*`), `@` (mentions, and an e-mail address published by mistake), and every control,
format, bidirectional-override and zero-width character — the
`Cc`/`Cf` categories that make one name render as another. Combining marks are capped per base
character so a name cannot be stacked into a block that draws over its neighbours.
"""

from __future__ import annotations

import unicodedata

MAX_DISPLAY_NAME_CHARS = 64  # accounts.display_name_length
NAME_PUNCTUATION = frozenset(".,'’-_()&")
MAX_COMBINING_MARKS = 3
ALLOWED_DESCRIPTION = "letters, digits, single spaces and . , ' ’ - _ ( ) &"


def name_problem(value: str) -> str | None:
    """Why `value` cannot be shown as a name, or None if it can.

    Only the characters. Length and outer whitespace are each caller's own rule, because a display
    name is trimmed and a signed credit name is refused instead.
    """
    marks = 0
    spaces = 0
    has_letter_or_digit = False
    for character in value:
        category = unicodedata.category(character)
        if category.startswith("M"):
            marks += 1
            if marks > MAX_COMBINING_MARKS:
                return f"more than {MAX_COMBINING_MARKS} combining marks on one character"
            continue
        marks = 0
        if character == " ":
            spaces += 1
            if spaces > 1:
                return "consecutive spaces"
            continue
        spaces = 0
        if category[0] in "LN":
            has_letter_or_digit = True
            continue
        if character in NAME_PUNCTUATION:
            continue
        return f"may only contain {ALLOWED_DESCRIPTION}; U+{ord(character):04X} is not allowed"
    if not has_letter_or_digit:
        return "must contain at least one letter or digit"
    return None


def display_name(value: str) -> str:
    """The display name to store for `value`, or ValueError saying why there is none.

    Trimmed and NFC-normalised first: nothing signs a display name, so tidying it is a kindness
    rather than a change to something a person attested to. A signed credit name is never tidied —
    see `attribution.public_credit`.
    """
    normalised = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(normalised) <= MAX_DISPLAY_NAME_CHARS:
        raise ValueError(f"display name must be 1-{MAX_DISPLAY_NAME_CHARS} characters")
    problem = name_problem(normalised)
    if problem is not None:
        raise ValueError(f"display name {problem}")
    return normalised


__all__ = [
    "ALLOWED_DESCRIPTION",
    "MAX_COMBINING_MARKS",
    "MAX_DISPLAY_NAME_CHARS",
    "NAME_PUNCTUATION",
    "display_name",
    "name_problem",
]
