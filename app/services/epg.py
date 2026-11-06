import gzip
import io
import json
import logging
import os
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

# The XMLTV sources, as a JSON list of URLs:
#   ACESTREAM_SUITE_EPG_SOURCES='["https://www.tdtchannels.com/epg/TV.xml.gz"]'
# A JSON list and not a comma-separated one, because a URL carries a query string easily enough
# that the separator would be a trap. Empty by default: no sources is a state the rest of the
# layer has to survive anyway, since a source is a cache and never a dependency.
SOURCES_ENV = "EPG_SOURCES"
SOURCES_VAR = SUITE_PREFIX + SOURCES_ENV

# Hours of guide kept ahead of `now`. 72 is what a TV grid shows, and the pruning it implies is what
# stops a 24 MB refresh every 12 h from piling up on a host with 20 G free.
WINDOW_HOURS = float(env("EPG_WINDOW_HOURS", "72"))

# A source answers in seconds; one that never answers must cost one source and not the run.
TIMEOUT_SECONDS = float(env("EPG_TIMEOUT_SECONDS", "120"))

# The aggregator's all-countries file is 196 MB gzipped, so a URL pasted in wrong can still be
# enormous. A source that goes past this is refused with a reason instead of filling the memory of
# a container that shares its host with the database it writes to.
MAX_BYTES = int(env("EPG_MAX_MB", "64")) * 1024 * 1024

GZIP_MAGIC = b"\x1f\x8b"

# A programme that ended seconds ago is what a TV with a lagging clock is still showing: the window
# drops it from the intake, and this keeps it in the table a little longer.
PRUNE_GRACE = timedelta(seconds=float(env("EPG_GRACE_SECONDS", "300")))


def xmltv_sources(raw: str | None = None) -> list[str]:
    """The XMLTV URLs to fetch, from `ACESTREAM_SUITE_EPG_SOURCES`.

    A value that is not a JSON list of strings is logged and read as "no sources": a typo in a
    compose file must not stop the app from starting, because the guide is an addition to a
    catalogue that works without it.
    """
    if raw is None:
        raw = env(SOURCES_ENV, "")
    try:
        values = json.loads(raw or "[]")
    except ValueError as exc:
        log.warning("%s is not JSON, reading it as no sources: %s", SOURCES_VAR, exc)
        return []
    if not isinstance(values, list):
        log.warning("%s is not a JSON list, reading it as no sources", SOURCES_VAR)
        return []
    return [url.strip() for url in values if isinstance(url, str) and url.strip()]


@dataclass(frozen=True)
class Channel:
    """One `<channel>` of a document. `id` is the handle a programme and a mapping refer to."""

    id: str
    name: str
    icon: str | None = None


@dataclass(frozen=True)
class Programme:
    """One `<programme>`, both bounds in UTC whatever offset its source wrote."""

    channel_id: str
    start: datetime
    stop: datetime
    title: str
    description: str | None = None


def parse_timestamp(value: str) -> datetime:
    """`20261013120000 +0200` -> the same instant, in UTC.

    XMLTV gives an offset and nothing else: no zone name, no epoch. UTC is the form the app's own
    datetime columns already take, it is what a window can be compared against, and it is what lets
    the reader's browser render the guide in its own zone.
    """
    try:
        parsed = datetime.strptime(value.strip(), "%Y%m%d%H%M%S %z")
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"not an XMLTV timestamp: {value!r}") from exc
    return parsed.astimezone(UTC)


def parse_xmltv(document: str | bytes | BinaryIO) -> tuple[list[Channel], list[Programme]]:
    """The channels and programmes of one XMLTV document, in a single streaming pass.

    `iterparse` and `element.clear()` because the sources are 5-24 MB of XML each: reading one
    whole would hold several copies of it — the bytes, the tree, the result — for the sake of the
    few hours of it that `in_window` keeps. Every source is treated as unreliable, so a truncated
    download or a maintenance page is not an exception to the caller: whatever parsed before the
    break is returned and the rest is dropped.
    """
    channels: list[Channel] = []
    programmes: list[Programme] = []
    root: ET.Element | None = None
    try:
        for event, element in ET.iterparse(_as_stream(document), events=("start", "end")):
            if root is None:
                root = element
            if event == "start":
                continue
            if element.tag == "channel":
                channel = _channel(element)
                if channel is not None:
                    channels.append(channel)
            elif element.tag == "programme":
                programme = _programme(element)
                if programme is not None:
                    programmes.append(programme)
            else:
                continue
            element.clear()
            # `clear()` frees the text, but the document's own root keeps the element itself in its
            # children list, and the pass would end up holding one cleared shell per programme:
            # 3 MiB of live memory on the 24 MB epgshare01 feed, measured as 99 MiB of RSS after
            # the pass against 96 MiB with the root emptied (the process's own peak does not move —
            # that one is set by the imports and the document, well before the first element).
            # Emptying the root is safe: the parser appends the next child to it.
            if root is not None:
                root.clear()
    except ET.ParseError as exc:
        log.warning("XMLTV document is not well formed, keeping what parsed: %s", exc)
    return channels, programmes


def in_window(
    programme: Programme, *, now: datetime | None = None, hours: float = WINDOW_HOURS
) -> bool:
    """Whether a programme is still worth a row: on now, or starting inside the window.

    A finished programme is dropped, which is what makes a refresh an update instead of a file
    that only grows — the guide is only ever asked what is on. The far future is dropped for a
    different reason: that bound belongs to the sources, not to the grid, and a feed that suddenly
    ships a year of listings must not be stored whole.
    """
    now = now or datetime.now(UTC)
    return programme.stop > now and programme.start < now + timedelta(hours=hours)


def _as_stream(document: str | bytes | BinaryIO) -> BinaryIO:
    """A readable of bytes: a file (a gunzipped source included), a str or raw bytes."""
    if isinstance(document, (bytes, bytearray)):
        return io.BytesIO(document)
    if isinstance(document, str):
        return io.BytesIO(document.encode("utf-8"))
    return document


def _text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _channel(element: ET.Element) -> Channel | None:
    channel_id = (element.get("id") or "").strip()
    if not channel_id:
        # The id is the only handle on a guide entry, so a channel without one can never be matched.
        return None
    icon = element.find("icon")
    icon_src = (icon.get("src") or "").strip() if icon is not None else ""
    # A source that ships no display-name still needs one, and the id is the only other thing there.
    return Channel(
        id=channel_id,
        name=_text(element, "display-name") or channel_id,
        icon=icon_src or None,
    )


def _programme(element: ET.Element) -> Programme | None:
    channel_id = (element.get("channel") or "").strip()
    if not channel_id:
        return None
    try:
        start = parse_timestamp(element.get("start", ""))
        stop = parse_timestamp(element.get("stop", ""))
    except ValueError as exc:
        # One bad row in a 24 MB community feed is dropped, not raised: the rest of the file is
        # still the day's guide, and one malformed entry cannot be allowed to lose all of it.
        log.warning("skipping an XMLTV programme of %s: %s", channel_id, exc)
        return None
    return Programme(
        channel_id=channel_id,
        start=start,
        stop=stop,
        title=_text(element, "title"),
        description=_text(element, "desc") or None,
    )


def fetch(source: str, *, timeout: float = TIMEOUT_SECONDS) -> bytes:
    """The XML of one source. Gzip is decided by the bytes and never by the URL.

    tdtchannels answers the `.gz` path with a 301 to a file whose content-type says nothing, and
    epgshare01 serves gzip from a path that does not mention it: a suffix is not a content type.
    Everything a source can do wrong is left to the caller — a source is a cache, and a broken one
    is skipped, not raised.
    """
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", source) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ValueError(f"la descarga pasa de {MAX_BYTES // 2**20} MB")
    if bytes(body[:2]) == GZIP_MAGIC:
        try:
            body = gzip.decompress(body)
        except OSError as exc:
            raise ValueError(f"no se pudo descomprimir la respuesta: {exc}") from exc
    return bytes(body)


@dataclass
class SourceReport:
    """What one source gave this run, or why it gave nothing.

    `stored` counts the rows written, so a second refresh of an unchanged document reports 0 and
    the idempotence of the storage is visible in the summary instead of only in the database.
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
    """The whole refresh: one entry per source, in the order they were fetched."""

    now: datetime
    sources: list[SourceReport] = field(default_factory=list)
    channels_removed: int = 0

    @property
    def fatal(self) -> bool:
        """Whether every source failed. A guide with one live provider is still a guide."""
        return bool(self.sources) and all(item.error for item in self.sources)


def refresh(
    session: Session,
    *,
    sources: list[str] | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> Report:
    """Fetch every source, store what is on, drop what is over. One dead source never fails it.

    `sources` defaults to `xmltv_sources()`. `now` is a parameter because the window and the
    pruning are both measured from it: a test that wants a second refresh six hours later must not
    have to wait six hours, and the host's clock is not the only one a container can be handed.
    """
    now = now or datetime.now(UTC)
    report = Report(now=now)
    for source in xmltv_sources() if sources is None else sources:
        item = SourceReport(source=source)
        report.sources.append(item)
        try:
            document = fetch(source)
            channels, programmes = parse_xmltv(document)
            if not channels:
                # A 404 page, a maintenance page and an empty feed all answer 200. None of them
                # names a single channel, and a programme can only be hung on a channel, so this is
                # a source that did not answer rather than a source with empty listings.
                raise ValueError("no trae ningún canal, no parece un XMLTV")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            item.error = str(exc) or exc.__class__.__name__
            log.warning("fuente EPG %s descartada: %s", source, exc)
            continue

        item.channels = len(channels)
        item.parsed = len(programmes)
        # A feed that ships a year of listings must not be stored whole: only what the grid can
        # still show goes in, and the rest is dropped before it ever reaches a row.
        kept = [programme for programme in programmes if in_window(programme, now=now)]
        item.windowed = len(kept)
        if dry_run:
            continue
        item.stored = _store(session, source, channels, kept, now=now)
        item.pruned, removed = _prune(session, source, now=now)
        report.channels_removed += removed
        # One commit per source, the posture the importer already takes: a source that breaks half
        # way keeps what the earlier ones gave.
        session.commit()
    return report


def _store(
    session: Session,
    source: str,
    channels: list[Channel],
    programmes: list[Programme],
    *,
    now: datetime,
) -> int:
    """Write this source's channels and the windowed programmes; returns the rows written.

    The two unique constraints are what makes this idempotent: a channel is looked up by
    (source, source_channel_id) and a programme by (epg_channel_id, start), so a second refresh of
    the same document updates what is there instead of adding a second copy of it.
    """
    known = {
        row.source_channel_id: row
        for row in session.scalars(
            select(models.EpgChannel).where(models.EpgChannel.source == source)
        )
    }
    for channel in channels:
        row = known.get(channel.id)
        if row is None:
            row = models.EpgChannel(source=source, source_channel_id=channel.id)
            session.add(row)
            known[channel.id] = row
        row.name, row.icon, row.last_seen = channel.name, channel.icon, now
    # One flush for the whole source: a programme needs its channel's id, and a flush per channel
    # would be one INSERT per channel.
    session.flush()
    ids = {channel.id: known[channel.id].id for channel in channels}

    by_channel: dict[int, list[Programme]] = {}
    for programme in programmes:
        # A programme whose channel the same document does not declare is dropped: there is no row
        # to hang it on, and inventing one would put a made-up name in the guide.
        channel_id = ids.get(programme.channel_id)
        if channel_id is not None:
            by_channel.setdefault(channel_id, []).append(programme)
    return sum(
        _store_programmes(session, channel_id, items) for channel_id, items in by_channel.items()
    )


def _store_programmes(session: Session, channel_id: int, programmes: list[Programme]) -> int:
    """Insert the new listings of one channel and update the ones that changed.

    The lookup is on UNIQUE (epg_channel_id, start): a programme whose end time or title was edited
    by the provider is the same row, not a second one.
    """
    existing = {
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
        row = existing.get(programme.start)
        if row is not None:
            if (row.stop, row.title, row.description) != (
                programme.stop,
                programme.title,
                programme.description,
            ):
                row.stop, row.title, row.description = (
                    programme.stop,
                    programme.title,
                    programme.description,
                )
                written += 1
            continue
        row = models.Programme(
            epg_channel_id=channel_id,
            start=programme.start,
            stop=programme.stop,
            title=programme.title,
            description=programme.description,
        )
        session.add(row)
        # The same document listing a programme twice must not reach the flush as two INSERTs of
        # one key: the second one is an update of the row that is already pending.
        existing[programme.start] = row
        written += 1
    return written


def _prune(session: Session, source: str, *, now: datetime) -> tuple[int, int]:
    """Drop what one source stopped offering: (programmes pruned, channels removed).

    Only the rows of the source that was just read. A source that did not answer keeps every row it
    gave last time — losing a dead provider's guide is not a repair, and the next refresh that
    reaches it writes the rows again anyway.
    """
    pruned = session.execute(
        delete(models.Programme).where(
            models.Programme.stop < now - PRUNE_GRACE,
            models.Programme.epg_channel_id.in_(
                select(models.EpgChannel.id).where(models.EpgChannel.source == source)
            ),
        )
    ).rowcount
    # A channel whose `last_seen` the run above did not touch is one this source has stopped
    # offering. Its programmes go with it: ON DELETE CASCADE, and they are not counted as pruned.
    removed = session.execute(
        delete(models.EpgChannel).where(
            models.EpgChannel.source == source, models.EpgChannel.last_seen < now
        )
    ).rowcount
    return pruned, removed
