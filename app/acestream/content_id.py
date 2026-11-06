"""Turning whatever the user pasted into a Content ID the engine will accept."""

import re

_PREFIX = "acestream://"
# ASCII on purpose: other alphabets have digits and letters that only look like these.
_BODY = re.compile(r"[0-9a-f]{40}", re.IGNORECASE | re.ASCII)
# Offending input is quoted back, but a pasted file should not fill an error page.
_QUOTED = 60


class InvalidContentIdError(ValueError):
    """The text is neither a Content ID nor an `acestream://` link to one."""


def parse_content_id(value: str) -> str:
    """The canonical form of `value`: 40 lowercase hex characters.

    A bare Content ID and an `acestream://` link are both accepted, in any case and
    with blanks around them. Anything else is refused rather than guessed at.
    """
    text = value.strip()
    if text[: len(_PREFIX)].lower() == _PREFIX:
        text = text[len(_PREFIX) :]
    if _BODY.fullmatch(text):
        return text.lower()
    raise InvalidContentIdError(_refusal(value.strip()))


def _refusal(shown: str) -> str:
    if len(shown) > _QUOTED:
        shown = shown[: _QUOTED - 3] + "..."
    return (
        f"{shown!r} is not an Acestream Content ID or acestream:// link "
        "(expected 40 hexadecimal characters)"
    )
