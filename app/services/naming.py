"""Channel titles taken from the engine's stream names, which are mostly advertising."""

import re

# `Channel.title` is a String(200): a longer name would be refused by the database.
_TITLE_LENGTH = 200
# Streams are published to be found, and the publisher pays per line:
# "DAZN 1 1080 elcano.top https://x.com/ace_stream_ https://t.me/acestreamf1".
_LINK = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# What is left behind when a link goes: "Partido - www.example.com/x" ends in "Partido -".
# Blanks are not in the set: collapsing already left every word separated by one space.
_DANGLING = "-|,;:"


def placeholder_title(content_id: str) -> str:
    """An unnamed channel's title until a check learns the stream's own name."""
    return f"Canal {content_id[:8]}"


def clean_stream_name(raw: str | None) -> str | None:
    """The engine's name for a stream as a title, or None when nothing usable is left."""
    if not raw:
        return None
    words = _LINK.sub(" ", raw).split()
    title = " ".join(words).strip(_DANGLING)[:_TITLE_LENGTH].rstrip()
    return title or None
