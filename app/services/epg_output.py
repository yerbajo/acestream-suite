"""The guide as a machine reads it: one XMLTV document and one JSON mapping.

`epg_guide.py` reads the store for the screens; this module is the output side. Two artefacts, both
computed from stored rows and neither of them writing one:

    /epg.xml   the mapped guide as XMLTV — the document a playlist's `url-tvg` points at
    /epg.json  `content_id -> guide id -> display name` — the link to the catalogue, which the
               XMLTV does not carry and a playlist builder cannot guess

The `<channel id>` is the guide channel id as stored (`La1.TV`, `M+ Deportes 2`). That is the handle
a playlist's `tvg-id` will carry, so it is the id the XMLTV has to match; it is never the
catalogue's 40-hex `content_id`, which no guide provider has ever heard of.

An unmapped catalogue channel is simply absent from both documents, and a mapped guide channel with
nothing to show is absent as well: both empty states read as "nothing to publish" instead of as a
`<channel>` element claiming a guide that has no programmes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.services import epg

# The JSON mapping's own format marker, read by `scripts/acestream-suite-guide-playlist.py` so a
# wrong document (an `/export`, an error page) is refused instead of silently read as an empty
# mapping.
MAPPING_FORMAT = "acestream-suite-guide"
MAPPING_VERSION = 1

# The two lines every XMLTV document opens with. The store keeps UTC, so a stamp's offset is always
# the same one.
_PROLOGUE = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
_STAMP = "%Y%m%d%H%M%S +0000"


def mapped(session: Session) -> Iterator[tuple[str, models.EpgChannel]]:
    """Every (catalogue `content_id`, its guide channel), for the channels that have one.

    Ordered by guide id and then content id, so two requests produce the same bytes and a player
    that diffs the document only sees a change when the data changed.
    """
    rows = session.execute(
        select(models.Channel.content_id, models.EpgChannel)
        .join(models.EpgChannel, models.Channel.guide_channel_id == models.EpgChannel.id)
        .order_by(models.EpgChannel.source_channel_id, models.Channel.content_id)
    ).all()
    return ((content_id, guide) for content_id, guide in rows)


def build_mapping(session: Session) -> dict:
    """The mapping document: one entry per mapped catalogue channel, keyed by `content_id`.

    This is the only place the two sides are joined. The XMLTV carries guide ids and names but
    nothing that ties them to the catalogue, and a playlist builder needs exactly that: which of its
    `content_id` rows gets which `tvg-id`.
    """
    entries = {
        content_id: {"guide_id": guide.source_channel_id, "guide_name": guide.name}
        for content_id, guide in mapped(session)
    }
    return {"format": MAPPING_FORMAT, "version": MAPPING_VERSION, "mapping": entries}


def _xmltv_time(value: datetime) -> str:
    """XMLTV's own stamp for one instant: `YYYYMMDDHHMMSS +0000`, the offset always UTC."""
    return value.astimezone(UTC).strftime(_STAMP)


def build_xmltv(
    session: Session, *, now: datetime | None = None, hours: float | None = None
) -> str:
    """The mapped guide as an XMLTV document: channels first, then their programmes.

    Only the window is published, the same bound the store is pruned to. The grid cannot show more,
    and a source that suddenly ships a year of listings must not turn into a year of XMLTV.
    """
    now = now or datetime.now(UTC)
    horizon = now + timedelta(hours=epg.WINDOW_HOURS if hours is None else hours)
    return _document(_published(session, now, horizon))


def _published(
    session: Session, now: datetime, horizon: datetime
) -> list[tuple[models.EpgChannel, list[models.Programme]]]:
    """The guide channels worth publishing, each with its listings, in a stable order.

    Several catalogue channels routinely share one guide channel (five AceStream sources for the
    same `DAZN 2`), so the guide appears once and never once per catalogue row. A channel whose
    window is empty is left out entirely: an `<channel>` with no `<programme>` would tell a player
    that the guide exists and is empty, when the truth is that nothing is on today.
    """
    guides = {guide.id: guide for _, guide in mapped(session)}
    listings = _listings(session, guides, now, horizon)
    return [
        (guides[guide_id], listings[guide_id])
        for guide_id in sorted(guides, key=lambda key: guides[key].source_channel_id)
        if listings[guide_id]
    ]


def _listings(
    session: Session, guides: dict[int, models.EpgChannel], now: datetime, horizon: datetime
) -> dict[int, list[models.Programme]]:
    """The listings of these guide channels inside the window, grouped and earliest first."""
    grouped: dict[int, list[models.Programme]] = {guide_id: [] for guide_id in guides}
    if not grouped:
        return grouped
    rows = session.scalars(
        select(models.Programme)
        .where(
            models.Programme.epg_channel_id.in_(list(grouped)),
            models.Programme.stop > now,
            models.Programme.start < horizon,
        )
        .order_by(models.Programme.epg_channel_id, models.Programme.start)
    )
    for programme in rows:
        grouped[programme.epg_channel_id].append(programme)
    return grouped


def _document(published: list[tuple[models.EpgChannel, list[models.Programme]]]) -> str:
    """The XMLTV text for those channels and listings, channels first."""
    root = ET.Element("tv")
    for guide, _ in published:
        _channel_element(root, guide)
    for guide, programmes in published:
        for programme in programmes:
            _programme_element(root, guide.source_channel_id, programme)
    # `indent` gives one element per line, which is what makes the document readable when a person
    # opens it; it changes nothing about what a parser reads.
    ET.indent(root)
    return _PROLOGUE + ET.tostring(root, encoding="unicode") + "\n"


def _channel_element(root: ET.Element, guide: models.EpgChannel) -> None:
    """One `<channel>`: its id, its name, and an icon only when the source gave one.

    An `<icon>` with an empty `src` is a broken image in a player's grid, not a missing icon.
    """
    element = ET.SubElement(root, "channel", {"id": guide.source_channel_id})
    ET.SubElement(element, "display-name").text = guide.name
    if guide.icon:
        ET.SubElement(element, "icon", {"src": guide.icon})


def _programme_element(
    root: ET.Element, source_channel_id: str, programme: models.Programme
) -> None:
    """One `<programme>` of the guide channel it is published under."""
    element = ET.SubElement(
        root,
        "programme",
        {
            "start": _xmltv_time(programme.start),
            "stop": _xmltv_time(programme.stop),
            "channel": source_channel_id,
        },
    )
    ET.SubElement(element, "title").text = programme.title
    if programme.description:
        ET.SubElement(element, "desc").text = programme.description
