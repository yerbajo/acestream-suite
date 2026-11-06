"""Reading a pasted list of links, and turning each new one into a channel."""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.orm import Session

from app.db.models import Channel
from app.services import channels
from app.services.errors import ValidationError
from app.services.iso import normalize_country, normalize_language
from app.services.naming import placeholder_title

MAX_LINES = 2000
_TITLE_LENGTH = 200  # the column length

# A Content ID anywhere in the line: bare or as a link, any case. The lookarounds keep a
# longer run of hex characters from being read as an id that happens to sit inside it.
_LINK = re.compile(r"(?:acestream://)?(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])", re.IGNORECASE)
# What usually sits between a title and its link: "Title - acestream://...", "hash | Title".
_SEPARATORS = " \t-|,;:"


@dataclass(frozen=True)
class Entry:
    line: int  # 1-based, counted the way the user counts lines
    content_id: str
    title: str | None  # None: the stream's own name is used instead


class Reason(StrEnum):
    NO_LINK = "no_link"  # the line holds no Content ID
    INVALID = "invalid"  # an exported channel whose data does not validate
    REPEATED = "repeated"  # that Content ID was already on an earlier line
    EXISTING = "existing"  # that Content ID is already in the catalog


@dataclass(frozen=True)
class Rejected:
    line: int
    text: str
    reason: Reason
    first_line: int | None = None  # REPEATED: where the id appeared before
    existing: Channel | None = None  # EXISTING: the channel already there
    detail: str | None = None  # INVALID: what was wrong with it


@dataclass
class ImportResult:
    unit: str = "line"  # what `Rejected.line` counts: a line of text or an exported channel
    created: list[Channel] = field(default_factory=list)
    duplicates: list[Rejected] = field(default_factory=list)
    invalid: list[Rejected] = field(default_factory=list)


def parse_links(text: str) -> tuple[list[Entry], list[Rejected]]:
    """Reads one link per line, skipping comments and blank lines.

    An `#EXTINF` line names the link below it, the way an M3U list does. When no such
    line gave a name, the text around the link on its own line is used as the title; when
    there is none either, the entry has no title and the stream's name is used later.
    """
    entries: list[Entry] = []
    rejected: list[Rejected] = []
    extinf_title: str | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            extinf_title = _extinf_title(line)
            continue
        if line.startswith("#"):
            continue
        match = _LINK.search(line)
        if match is None:
            rejected.append(Rejected(number, line, Reason.NO_LINK))
            extinf_title = None
            continue
        around = (line[: match.start()] + " " + line[match.end() :]).strip(_SEPARATORS)
        title = extinf_title or around or None
        entries.append(
            Entry(number, match.group(1).lower(), title[:_TITLE_LENGTH] if title else None)
        )
        extinf_title = None
    return entries, rejected


def import_links(
    session: Session,
    text: str,
    *,
    category_id: int | None = None,
    language: str | None = None,
    country: str | None = None,
) -> ImportResult:
    """Creates a channel for every link that is new, without checking any (callers queue that).

    A link already in the catalog, or one that appeared earlier in the same text, comes
    back as a duplicate instead of a second channel.
    """
    line_count = len(text.splitlines())
    if line_count > MAX_LINES:
        raise ValidationError(f"too many lines: {line_count} (at most {MAX_LINES})")
    # The shared values are read once, before anything is written, so a bad one fails the
    # whole import instead of failing channels one at a time.
    language = normalize_language(language)
    country = normalize_country(country)
    channels.check_category(session, category_id)

    entries, invalid = parse_links(text)
    result = ImportResult(invalid=invalid)
    seen: dict[str, int] = {}
    for entry in entries:
        if entry.content_id in seen:
            result.duplicates.append(
                Rejected(
                    entry.line, entry.content_id, Reason.REPEATED, first_line=seen[entry.content_id]
                )
            )
            continue
        seen[entry.content_id] = entry.line
        existing = channels.find_channel_by_content_id(session, entry.content_id)
        if existing is not None:
            result.duplicates.append(
                Rejected(entry.line, entry.content_id, Reason.EXISTING, existing=existing)
            )
            continue
        result.created.append(
            channels.create_channel(
                session,
                title=entry.title or placeholder_title(entry.content_id),
                title_pending=entry.title is None,
                content_id=entry.content_id,
                category_id=category_id,
                language=language,
                country=country,
            )
        )
    return result


def _extinf_title(line: str) -> str | None:
    """The name an `#EXTINF` line carries: what follows its last attribute, after a comma.

    The attributes can hold commas of their own inside quotes, so the scan has to know
    when it is inside one.
    """
    in_quotes = False
    for position, character in enumerate(line):
        if character == '"':
            in_quotes = not in_quotes
        elif character == "," and not in_quotes:
            return line[position + 1 :].strip() or None
    return None
