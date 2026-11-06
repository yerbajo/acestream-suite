"""The guide as a machine reads it: the XMLTV document and the JSON mapping.

`epg_guide.py` reads the store for the screens; this module is the output side. Two artefacts,
both computed from what is already stored and neither writing a row:

    /epg.xml   the mapped guide as XMLTV, the document a playlist's `url-tvg` points at
    /epg.json  `content_id -> guide id -> display name`, the mapping a playlist builder needs
               and the XMLTV does not carry

The `<channel id>` is the guide channel id already stored (`La1.TV`, `M+ Deportes 2`): that is
the handle the playlist's `tvg-id` will carry, so it is the id the XMLTV has to match. It is
NEVER the catalogue's 40-hex `content_id`, which no guide provider knows.

An unmapped catalogue channel is simply absent from both documents. A channel with no guide is
visible as absent rather than as an empty `<channel>` that claims a guide exists and has nothing
to show for it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.services import epg

# The JSON mapping's own format marker, read by `scripts/acestream-suite-guide-playlist.py` so a wrong
# document (an `/export`, an error page) is refused instead of silently read as an empty mapping.
MAPPING_FORMAT = "acestream-suite-guide"
MAPPING_VERSION = 1


def mapped(session: Session) -> Iterator[tuple[str, models.EpgChannel]]:
    """Every (catalogue `content_id`, its guide channel) for the channels that have a guide.

    Ordered by guide id and then content id so two requests produce the same bytes: a player that
    diffs the document sees a change only when the data changed.
    """
    rows = session.execute(
        select(models.Channel.content_id, models.EpgChannel)
        .join(models.EpgChannel, models.Channel.guide_channel_id == models.EpgChannel.id)
        .order_by(models.EpgChannel.source_channel_id, models.Channel.content_id)
    ).all()
    return ((content_id, guide) for content_id, guide in rows)


def build_mapping(session: Session) -> dict:
    """The mapping document: one entry per mapped catalogue channel, keyed by `content_id`.

    The XMLTV carries the guide ids and the names, but nothing links them to the catalogue, and a
    playlist builder needs exactly that link: which of its `content_id` rows gets which `tvg-id`.
    """
    mapping = {
        content_id: {"guide_id": guide.source_channel_id, "guide_name": guide.name}
        for content_id, guide in mapped(session)
    }
    return {"format": MAPPING_FORMAT, "version": MAPPING_VERSION, "mapping": mapping}


def _stamp(value: datetime) -> str:
    """XMLTV's `YYYYMMDDHHMMSS +0000`. The store keeps UTC, so the offset is always +0000."""
    return value.astimezone(UTC).strftime("%Y%m%d%H%M%S +0000")


def build_xmltv(
    session: Session, *, now: datetime | None = None, hours: float | None = None
) -> str:
    """The mapped guide as an XMLTV document, channels first and then their programmes.

    Only the programmes in the window are published, the same bound the store is pruned to: the
    grid cannot show more, and a source that suddenly ships a year of listings must not turn into a
    year of XMLTV.

    A mapped guide channel with no programme in the window is left out entirely, and that is a
    decision: an `<channel>` with no `<programme>` would tell a player the guide exists and is
    empty, when what is true is that this channel has nothing to show today. Absent is honest.
    """
    now = now or datetime.now(UTC)
    horizon = now + timedelta(hours=epg.WINDOW_HOURS if hours is None else hours)

    # The distinct guide channels the catalogue points at, each with the programmes in the window.
    # Several catalogue channels routinely share one guide channel (five AceStream sources for the
    # same `DAZN 2`), so the document carries the guide once and not once per catalogue row.
    guides: dict[int, models.EpgChannel] = {}
    listings: dict[int, list[models.Programme]] = {}
    for _, guide in mapped(session):
        guides[guide.id] = guide
        listings.setdefault(guide.id, [])
    for guide_id in guides:
        listings[guide_id] = list(
            session.scalars(
                select(models.Programme)
                .where(
                    models.Programme.epg_channel_id == guide_id,
                    models.Programme.stop > now,
                    models.Programme.start < horizon,
                )
                .order_by(models.Programme.start)
            )
        )

    root = ET.Element("tv")
    published = [
        (guides[guide_id], listings[guide_id])
        for guide_id in sorted(guides, key=lambda key: guides[key].source_channel_id)
        if listings[guide_id]
    ]
    for guide, _ in published:
        channel = ET.SubElement(root, "channel", {"id": guide.source_channel_id})
        ET.SubElement(channel, "display-name").text = guide.name
        # Only when the source gave one: an `<icon>` element with an empty src is a broken image
        # in the player's grid, not a missing icon.
        if guide.icon:
            ET.SubElement(channel, "icon", {"src": guide.icon})
    for guide, programmes in published:
        for programme in programmes:
            element = ET.SubElement(
                root,
                "programme",
                {
                    "start": _stamp(programme.start),
                    "stop": _stamp(programme.stop),
                    "channel": guide.source_channel_id,
                },
            )
            ET.SubElement(element, "title").text = programme.title
            if programme.description:
                ET.SubElement(element, "desc").text = programme.description

    # `indent` puts one element per line, which is what makes the document readable when a person
    # opens it; it also changes nothing about what a parser reads.
    ET.indent(root)
    body = ET.tostring(root, encoding="unicode")
    doctype = '<!DOCTYPE tv SYSTEM "xmltv.dtd">'
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + doctype + "\n" + body + "\n"
