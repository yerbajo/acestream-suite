"""Reading the stored guide: what is on now and what follows, and one channel's next day.

`app/services/epg.py` fetches and stores the feed and `epg_matching.py` decides which catalogue
channel points at which guide channel. This module is only reads: the list page's `Ahora` and
`Después` cells, and the day grid behind one channel.

The store keeps every bound in UTC. A guide is read against a wall clock, and this house's clock is
Europe/Madrid, so every value leaves here already converted and a template never has to think
about zones. The conversion lives in one function because a second one would drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

MADRID = ZoneInfo("Europe/Madrid")

# The grid shows the day ahead, the same horizon the feed's 72 h window is windowed for.
GRID_HOURS = 24


def local(value: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """One stored UTC datetime written in Madrid time; `local(t, '%H:%M')` for the hour alone."""
    return value.astimezone(MADRID).strftime(fmt)


def time_range(programme: models.Programme) -> str:
    """The programme's span as a person reads a grid: `19:30–20:15`, both ends Madrid time."""
    return f"{local(programme.start, '%H:%M')}–{local(programme.stop, '%H:%M')}"


@dataclass(frozen=True)
class NowNext:
    """One guide channel's current and following programme; either can be missing.

    Empty is the normal state of a guide that has not been refreshed yet, and a blank cell is what
    it looks like, not an error.
    """

    current: models.Programme | None = None
    following: models.Programme | None = None


def now_next(
    session: Session, guide_ids: set[int] | list[int], *, now: datetime | None = None
) -> dict[int, NowNext]:
    """Current and next programme per guide channel, keyed by the guide channel id.

    One query for every channel on the page and not one per row: the list shows hundreds of rows,
    and a mapped one would otherwise be a separate lookup for the same screen.
    """
    ids = set(guide_ids)
    if not ids:
        return {}
    now = now or datetime.now(UTC)
    rows = session.scalars(
        select(models.Programme)
        .where(models.Programme.epg_channel_id.in_(ids), models.Programme.stop > now)
        .order_by(models.Programme.start)
    ).all()
    result: dict[int, NowNext] = {}
    for programme in rows:
        state = result.get(programme.epg_channel_id, NowNext())
        if programme.start <= now:
            # Sources sometimes list two programmes for one slot; the later start still running is
            # the one a television would be showing.
            result[programme.epg_channel_id] = NowNext(programme, state.following)
        elif state.following is None:
            result[programme.epg_channel_id] = NowNext(state.current, programme)
    return result


def day_grid(
    session: Session, guide_id: int, *, now: datetime | None = None, hours: int = GRID_HOURS
) -> list[models.Programme]:
    """One guide channel's programmes from now through `hours` ahead, earliest first.

    The programme already on is included, because it started before `now` and the grid has to mark
    it; only what has already finished is left out.
    """
    now = now or datetime.now(UTC)
    return list(
        session.scalars(
            select(models.Programme)
            .where(
                models.Programme.epg_channel_id == guide_id,
                models.Programme.stop > now,
                models.Programme.start < now + timedelta(hours=hours),
            )
            .order_by(models.Programme.start)
        )
    )
