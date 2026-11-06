"""Taking the guide in: what the XMLTV sources say, and only the hours a grid can show.

A refresh does three things per source. Download the feed, keep the listings that are still worth
a row, and make the store say what this source says now. The rest of the module is about keeping a
bad source cheap: one that never answers, serves a maintenance page, ships a 196 MB file or one
malformed row costs that source alone, because the guide is an addition to a catalogue that works
without it.

Every instant is stored in UTC. XMLTV gives an offset and no zone at all, so the offset is applied
once, in `parse_timestamp`, and nothing further up has to think about it.
"""

import gzip
import io
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import SUITE_PREFIX, env
from app.db import models

log = logging.getLogger(__name__)

# The feeds to read, as a JSON list of URLs:
#   ACESTREAM_SUITE_EPG_SOURCES='["https://www.tdtchannels.com/epg/TV.xml.gz"]'
# JSON because a URL brings `?`, `&` and commas of its own, so a comma-separated list would split
# one URL into two. Empty by default: no sources is a state the layer below has to survive anyway,
# since a source is a cache and never a dependency.
SOURCES_ENV = "EPG_SOURCES"
SOURCES_VAR = SUITE_PREFIX + SOURCES_ENV

# The horizon of the store: the hours of listings kept ahead of `now`. It is the span a TV grid
# shows, and it is also what stops a 24 MB download every 12 h from piling up on a host with 20 G
# to spare.
WINDOW_HOURS = float(env("EPG_WINDOW_HOURS", "72"))

# A source answers in seconds; one that never answers has to cost one timeout, not the run.
TIMEOUT_SECONDS = float(env("EPG_TIMEOUT_SECONDS", "120"))

# The all-countries aggregator is 196 MB gzipped, so a mistyped URL can point at something
# enormous. The body is measured as it arrives and refused once it goes past this: one source's
# memory, not the container's.
MAX_BYTES = int(env("EPG_MAX_MB", "64")) * 1024 * 1024

GZIP_MAGIC = b"\x1f\x8b"

# A listing that ended a moment ago is still what a player with a slow clock is showing. The window
# stops storing it the second it ends; this grace keeps it readable a little longer.
PRUNE_GRACE = timedelta(seconds=float(env("EPG_GRACE_SECONDS", "300")))

# Which fields a stored listing carries from its source, in the order they are compared and copied.
_LISTING_FIELDS = ("stop", "title", "description")


def xmltv_sources(raw: str | None = None) -> list[str]:
    """The feed URLs to read, in the order they were configured.

    A value that is not a JSON list of strings is logged and read as an empty list. A typo in a
    compose file has to cost the guide and nothing else: the catalogue is browsable without one.
    """
    text = env(SOURCES_ENV, "") if raw is None else raw
    try:
        configured = json.loads(text or "[]")
    except ValueError as exc:
        log.warning("%s is not JSON, reading it as no sources: %s", SOURCES_VAR, exc)
        return []
    if not isinstance(configured, list):
        log.warning("%s is not a JSON list, reading it as no sources", SOURCES_VAR)
        return []
    return [url.strip() for url in configured if isinstance(url, str) and url.strip()]


@dataclass(frozen=True)
class Channel:
    """One `<channel>`: the id a programme and a mapping point at, and what a grid prints.

    `icon` is None when the source gave none, which is common and never a reason to drop the rest.
    """

    id: str
    name: str
    icon: str | None = None


@dataclass(frozen=True)
class Programme:
    """One `<programme>`, both bounds in UTC whatever offset its source wrote them with."""

    channel_id: str
    start: datetime
    stop: datetime
    title: str
    description: str | None = None


def parse_timestamp(value: str) -> datetime:
    """An XMLTV stamp (`20261013120000 +0200`) as the instant it names, in UTC.

    The offset in the stamp is all a source ever says about its zone, and UTC is the form every
    stored window is compared against and every reader's zone is rendered from.
    """
    try:
        parsed = datetime.strptime(value.strip(), "%Y%m%d%H%M%S %z")
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"not an XMLTV timestamp: {value!r}") from exc
    return parsed.astimezone(UTC)


def parse_xmltv(document: str | bytes | BinaryIO) -> tuple[list[Channel], list[Programme]]:
    """The channels and listings of one document, read in a single pass that never holds it whole.

    A feed is 5 to 24 MB of XML while only a few hours of it survive the window, so each element is
    released as soon as it has been read. Sources are the least reliable thing the app talks to:
    whatever parsed before a truncated download or a maintenance page is returned, and the rest is
    data lost, not an exception raised.
    """
    found = _Found()
    root: ET.Element | None = None
    try:
        for event, element in ET.iterparse(_byte_stream(document), events=("start", "end")):
            if root is None:
                root = element
            if event == "end" and _collected(element, found):
                element.clear()
                # Clearing the element is not enough: the document's root keeps every child it has
                # seen, so the pass would end up holding one emptied shell per listing (99 MiB of
                # RSS after the 24 MB epgshare01 feed, against 96 with the root emptied). Emptying
                # the root is safe because the parser appends the next child to it.
                if root is not None:
                    root.clear()
    except ET.ParseError as exc:
        log.warning("XMLTV document is not well formed, keeping what parsed: %s", exc)
    return found.channels, found.programmes


@dataclass
class _Found:
    """What the pass over one document has collected so far."""

    channels: list[Channel] = field(default_factory=list)
    programmes: list[Programme] = field(default_factory=list)


def _collected(element: ET.Element, found: _Found) -> bool:
    """File one finished element as a channel or a listing, and say whether it was either.

    Everything else is left untouched on purpose: a `<title>` is what the listing around it is made
    of, so it has to still be there when the listing itself ends.
    """
    if element.tag == "channel":
        channel = _channel_of(element)
        if channel is not None:
            found.channels.append(channel)
        return True
    if element.tag == "programme":
        programme = _listing_of(element)
        if programme is not None:
            found.programmes.append(programme)
        return True
    return False


def in_window(
    programme: Programme, *, now: datetime | None = None, hours: float = WINDOW_HOURS
) -> bool:
    """Whether this listing earns a row: it is on now, or it starts before the horizon.

    Two bounds, two reasons. Dropping what has finished is what makes a refresh an update instead
    of a table that only grows, because the guide is only ever asked what is on. The far bound
    belongs to the sources and not to the grid: a feed that suddenly ships a year of listings must
    not be stored whole.
    """
    now = now or datetime.now(UTC)
    return programme.stop > now and programme.start < now + timedelta(hours=hours)


def _byte_stream(document: str | bytes | BinaryIO) -> BinaryIO:
    """The document as something the XML parser can pull bytes from."""
    if isinstance(document, (bytes, bytearray)):
        return io.BytesIO(document)
    if isinstance(document, str):
        return io.BytesIO(document.encode("utf-8"))
    return document


def _child_text(element: ET.Element, tag: str) -> str:
    """The stripped text of one child, or `''` when the child or its text is missing."""
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _channel_of(element: ET.Element) -> Channel | None:
    """One `<channel>`, or None when it carries no id.

    The id is the only handle a listing or a mapping can use, so a channel without one can never be
    matched to anything and is dropped. The id is also the display name when the source gives none:
    something has to be printed where the name goes.
    """
    channel_id = (element.get("id") or "").strip()
    if not channel_id:
        return None
    icon = element.find("icon")
    icon_src = (icon.get("src") or "").strip() if icon is not None else ""
    return Channel(
        id=channel_id,
        name=_child_text(element, "display-name") or channel_id,
        icon=icon_src or None,
    )


def _listing_of(element: ET.Element) -> Programme | None:
    """One `<programme>`, or None for a row the store could not place.

    A listing needs a channel to hang on and two readable bounds. One bad row in a 24 MB community
    feed is that row lost, never the day's guide.
    """
    channel_id = (element.get("channel") or "").strip()
    if not channel_id:
        return None
    try:
        start = parse_timestamp(element.get("start", ""))
        stop = parse_timestamp(element.get("stop", ""))
    except ValueError as exc:
        log.warning("skipping an XMLTV programme of %s: %s", channel_id, exc)
        return None
    return Programme(
        channel_id=channel_id,
        start=start,
        stop=stop,
        title=_child_text(element, "title"),
        description=_child_text(element, "desc") or None,
    )


def fetch(source: str, *, timeout: float = TIMEOUT_SECONDS) -> bytes:
    """The XML of one source, decompressed when it arrived compressed.

    Gzip is decided by the first two bytes of the body and never by the URL: tdtchannels redirects
    its `.gz` path to a file whose content-type says nothing, and epgshare01 serves gzip from a path
    that never mentions it. A source that fails here raises, and the caller is the one that decides
    a source is disposable.
    """
    return _gunzipped(_download(source, timeout))


def _download(source: str, timeout: float) -> bytes:
    """Every byte of one response, refused as soon as it grows past the cap.

    The refusal happens while the body is still arriving, so an enormous source costs a partial
    download and not its whole size in memory.
    """
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", source) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ValueError(f"la descarga pasa de {MAX_BYTES // 2**20} MB")
    return bytes(body)


def _gunzipped(body: bytes) -> bytes:
    """The document inside the body: decompressed only when the magic bytes say it is gzip."""
    if body[:2] != GZIP_MAGIC:
        return body
    try:
        return gzip.decompress(body)
    except OSError as exc:
        raise ValueError(f"no se pudo descomprimir la respuesta: {exc}") from exc


@dataclass
class SourceReport:
    """What one source gave this run, or why it gave nothing.

    `stored` counts the rows that changed, so a second refresh of an unchanged document reports 0
    and the idempotence of the storage is visible in the summary and not only in the database.
    """

    source: str
    channels: int = 0
    parsed: int = 0
    windowed: int = 0
    stored: int = 0
    pruned: int = 0
    error: str | None = None


@dataclass
class Report:
    """The whole refresh: one entry per source, in the order they were read."""

    now: datetime
    sources: list[SourceReport] = field(default_factory=list)
    channels_removed: int = 0

    @property
    def fatal(self) -> bool:
        """Whether no source answered at all. One live provider is still a guide."""
        return bool(self.sources) and all(item.error for item in self.sources)


def refresh(
    session: Session,
    *,
    sources: list[str] | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> Report:
    """Bring the store up to date with every source, one source at a time.

    `sources` defaults to `xmltv_sources()`. `now` is a parameter because the window and the pruning
    are both measured from it: a test that wants the next refresh six hours later must not wait six
    hours, and the host's clock is not the only clock a container can be handed.
    """
    now = now or datetime.now(UTC)
    report = Report(now=now)
    for source in xmltv_sources() if sources is None else sources:
        item = SourceReport(source=source)
        report.sources.append(item)
        report.channels_removed += _refresh_source(session, source, item, now=now, dry_run=dry_run)
    return report


def _refresh_source(
    session: Session, source: str, item: SourceReport, *, now: datetime, dry_run: bool
) -> int:
    """Take one source from download to commit, filling in its entry as it goes.

    Returns the number of guide channels that source stopped offering. A source that fails leaves
    the store exactly as it was, and every row it gave last time still there: losing a dead
    provider's guide is not a repair.
    """
    try:
        channels, programmes = _incoming(source)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        item.error = str(exc) or exc.__class__.__name__
        log.warning("fuente EPG %s descartada: %s", source, exc)
        return 0

    item.channels = len(channels)
    item.parsed = len(programmes)
    # A feed that ships a year of listings is trimmed before it reaches a row: the store keeps the
    # hours the grid can still show and nothing else.
    kept = [programme for programme in programmes if in_window(programme, now=now)]
    item.windowed = len(kept)
    if dry_run:
        return 0

    item.stored = _write_source(session, source, channels, kept, now=now)
    item.pruned, removed = _prune(session, source, now=now)
    # One commit per source, the same posture the importer takes: a source that breaks half way
    # keeps what the ones before it gave.
    session.commit()
    return removed


def _incoming(source: str) -> tuple[list[Channel], list[Programme]]:
    """What one source says, or the reason it says nothing worth storing.

    A 404 page, a maintenance page and an empty feed all answer 200, and none of them declares a
    single channel. A listing needs a channel to hang on, so a document with no channel is a source
    that did not answer rather than a source with empty listings.
    """
    channels, programmes = parse_xmltv(fetch(source))
    if not channels:
        raise ValueError("no trae ningún canal, no parece un XMLTV")
    return channels, programmes


def _write_source(
    session: Session,
    source: str,
    channels: list[Channel],
    programmes: list[Programme],
    *,
    now: datetime,
) -> int:
    """Write what one source declared and the listings that survived the window; rows written.

    Two unique keys carry the idempotence: a channel is the one with its (source, source channel
    id), a listing the one with its (guide channel, start). A second refresh of the same document
    therefore updates the rows that are there instead of adding a second copy of them.
    """
    existing = {
        row.source_channel_id: row
        for row in session.scalars(
            select(models.EpgChannel).where(models.EpgChannel.source == source)
        )
    }
    declared = _declared(session, existing, source, channels, now=now)
    # One flush for the whole source: a listing needs its channel's row id, and a flush per channel
    # would be one INSERT per channel.
    session.flush()
    return _write_listings(session, declared, programmes)


def _declared(
    session: Session,
    existing: dict[str, models.EpgChannel],
    source: str,
    channels: list[Channel],
    *,
    now: datetime,
) -> dict[str, models.EpgChannel]:
    """This document's channels as stored rows, created or refreshed, keyed by the source's id.

    Only what this document declares comes back: a channel the source has dropped keeps its row
    until the pruning takes it, and never counts as a home for a listing.
    """
    for channel in channels:
        row = existing.get(channel.id)
        if row is None:
            row = models.EpgChannel(source=source, source_channel_id=channel.id)
            session.add(row)
            # A document that declares one id twice must not reach the flush as two INSERTs of one
            # key; the second mention lands on the row the first one created.
            existing[channel.id] = row
        row.name, row.icon, row.last_seen = channel.name, channel.icon, now
    return {channel.id: existing[channel.id] for channel in channels}


def _write_listings(
    session: Session, declared: dict[str, models.EpgChannel], programmes: list[Programme]
) -> int:
    """Write the windowed listings of one source, grouped by the channel they belong to.

    A listing for a channel the document never declared has no row to hang on and is dropped: the
    alternative is inventing a guide channel with a name nobody wrote.
    """
    by_channel: dict[int, list[Programme]] = {}
    for programme in programmes:
        row = declared.get(programme.channel_id)
        if row is not None:
            by_channel.setdefault(row.id, []).append(programme)
    return sum(
        _write_channel_listings(session, row_id, items) for row_id, items in by_channel.items()
    )


def _write_channel_listings(session: Session, channel_id: int, programmes: list[Programme]) -> int:
    """Insert one channel's new listings and rewrite the ones the provider edited.

    The start time is the identity of a listing, so an end time or a title the provider fixed is an
    update of the same row and not a second one. What the row keeps beyond that is what `_apply`
    copies, which is also what the count of written rows means.
    """
    stored = {
        row.start: row
        for row in session.scalars(
            select(models.Programme).where(
                models.Programme.epg_channel_id == channel_id,
                models.Programme.start.in_([programme.start for programme in programmes]),
            )
        )
    }
    written = 0
    for programme in programmes:
        row = stored.get(programme.start)
        if row is None:
            row = models.Programme(
                epg_channel_id=channel_id,
                start=programme.start,
                stop=programme.stop,
                title=programme.title,
                description=programme.description,
            )
            session.add(row)
            # The same document listing one start twice must not be two INSERTs of one key either.
            stored[programme.start] = row
            written += 1
        elif _apply(row, programme):
            written += 1
    return written


def _apply(row: models.Programme, programme: Programme) -> bool:
    """Copy the source's fields onto a stored listing, and say whether any of them changed."""
    changed = tuple(getattr(row, name) for name in _LISTING_FIELDS) != tuple(
        getattr(programme, name) for name in _LISTING_FIELDS
    )
    for name in _LISTING_FIELDS:
        setattr(row, name, getattr(programme, name))
    return changed


def _prune(session: Session, source: str, *, now: datetime) -> tuple[int, int]:
    """Drop what one source stopped offering: (listings pruned, guide channels removed)."""
    return _pruned_listings(session, source, now), _removed_channels(session, source, now)


def _pruned_listings(session: Session, source: str, now: datetime) -> int:
    """Remove this source's listings that ended longer ago than the grace period.

    Only the rows of the source that was just read: a provider that did not answer keeps its guide
    untouched.
    """
    return session.execute(
        delete(models.Programme).where(
            models.Programme.stop < now - PRUNE_GRACE,
            models.Programme.epg_channel_id.in_(
                select(models.EpgChannel.id).where(models.EpgChannel.source == source)
            ),
        )
    ).rowcount


def _removed_channels(session: Session, source: str, now: datetime) -> int:
    """Remove the channels this refresh did not see, and with them their listings.

    A channel whose `last_seen` the write above left behind is one the source has stopped listing.
    Its listings leave by ON DELETE CASCADE and are not counted as pruned, which is why the two
    numbers are reported apart.
    """
    return session.execute(
        delete(models.EpgChannel).where(
            models.EpgChannel.source == source, models.EpgChannel.last_seen < now
        )
    ).rowcount
