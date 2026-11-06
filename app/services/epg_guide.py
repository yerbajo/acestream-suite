"""Reading the stored guide: what is on a channel now, what follows, and its next day.

Fetching and storing the feed belongs to `app/services/epg.py`, and deciding which catalogue channel
owns which guide channel belongs to `app/services/epg_matching.py`. What is left is reads: the guide
cell of the list page and the day grid behind one channel.

Every bound in the store is UTC, and a guide is read against this house's clock, which is
Europe/Madrid. So a value leaves this module already converted and a template never handles a zone.
The conversion is written once, below, because a second copy of it would drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

MADRID = ZoneInfo("Europe/Madrid")

# The day the grid shows. The store's own horizon is three days, so this is always inside it.
GRID_HOURS = 24

# A grid prints the hour and the minute and nothing else: the date is the page's, not the row's.
CLOCK = "%H:%M"
STAMP = "%Y-%m-%d %H:%M"


def local(value: datetime, fmt: str = STAMP) -> str:
    """One stored UTC datetime as Madrid time; `local(t, '%H:%M')` for the hour alone."""
    return value.astimezone(MADRID).strftime(fmt)


def time_range(programme: models.Programme) -> str:
    """A listing's span the way a grid shows it — `19:30–20:15`, both ends Madrid time."""
    return f"{local(programme.start, CLOCK)}–{local(programme.stop, CLOCK)}"


@dataclass(frozen=True)
class NowNext:
    """One guide channel's current and following listing; either may be missing.

    Missing is the ordinary state of a guide that has not been refreshed yet, and a blank cell is
    what it looks like — not an error.
    """

    current: models.Programme | None = None
    following: models.Programme | None = None


def now_next(
    session: Session, guide_ids: set[int] | list[int], *, now: datetime | None = None
) -> dict[int, NowNext]:
    """What is on and what follows, per guide channel, keyed by guide channel id.

    One query for the whole page rather than one per row: the list shows hundreds of channels and
    every mapped one would otherwise repeat the same lookup.
    """
    ids = set(guide_ids)
    if not ids:
        return {}
    now = now or datetime.now(UTC)
    return _split(_listings_ahead(session, ids, now), now)


def _listings_ahead(session: Session, ids: set[int], now: datetime) -> list[models.Programme]:
    """Every listing of these channels that has not finished yet, earliest first."""
    return list(
        session.scalars(
            select(models.Programme)
            .where(models.Programme.epg_channel_id.in_(ids), models.Programme.stop > now)
            .order_by(models.Programme.start)
        )
    )


def _split(programmes: list[models.Programme], now: datetime) -> dict[int, NowNext]:
    """Sort each channel's listings into the one on now and the first one after it.

    A started listing replaces whatever the channel had as current, so the latest start wins: two
    sources sometimes carry the same slot twice, and the one a television would be showing is the
    one that started later.
    """
    result: dict[int, NowNext] = {}
    for programme in programmes:
        state = result.get(programme.epg_channel_id, NowNext())
        if programme.start <= now:
            result[programme.epg_channel_id] = NowNext(programme, state.following)
        elif state.following is None:
            result[programme.epg_channel_id] = NowNext(state.current, programme)
    return result


def day_grid(
    session: Session, guide_id: int, *, now: datetime | None = None, hours: int = GRID_HOURS
) -> list[models.Programme]:
    """One guide channel's listings from now through `hours` ahead, earliest first.

    The listing already on is part of the grid, because it started before `now` and the page has to
    mark it; only what has finished is left out.
    """
    now = now or datetime.now(UTC)
    return list(session.scalars(_grid_query(guide_id, now, hours)))


def _grid_query(guide_id: int, now: datetime, hours: int):
    """The statement behind one channel's grid: everything not finished, inside the horizon."""
    return (
        select(models.Programme)
        .where(
            models.Programme.epg_channel_id == guide_id,
            models.Programme.stop > now,
            models.Programme.start < now + timedelta(hours=hours),
        )
        .order_by(models.Programme.start)
    )
