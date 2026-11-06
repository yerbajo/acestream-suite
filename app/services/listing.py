"""The channel list: which channels it shows, in what order, and what it knows about them.

Everything status-related in the list describes the *latest* check of a channel, so the
same "newest check per channel" query is the base of every filter and every order. The
list is read once per page render, which is why the guide mapping it displays comes with
the rows instead of one lazy query per row.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.db.models import Channel, Check, CheckStatus, Resolution

UNCHECKED = "unchecked"  # status filter: the channel has never been checked
UNKNOWN = "unknown"  # resolution filter: nothing has measured the picture yet


class SortKey(StrEnum):
    TITLE = "title"
    CREATED = "created"
    CHECKED = "checked"
    PEERS = "peers"
    STATUS = "status"


# The direction a column gets the first time it is clicked: titles read A-Z, and
# everything else is a measurement or a date, where "more" and "newer" are the point.
DEFAULT_DESCENDING = {
    SortKey.TITLE: False,
    SortKey.CREATED: True,
    SortKey.CHECKED: True,
    SortKey.PEERS: True,
    SortKey.STATUS: False,
}

# How the statuses compare with each other when sorting: what the user is looking for first.
_BY_USEFULNESS = [CheckStatus.ALIVE, CheckStatus.NO_PEERS, CheckStatus.NOT_FOUND, CheckStatus.ERROR]


@dataclass(frozen=True)
class ChannelQuery:
    """One request for the list; `None` means the filter is not applied at all."""

    text: str | None = None  # part of the title, or of the Content ID
    category_id: int | None = None
    language: str | None = None  # ISO 639-1
    country: str | None = None  # ISO 3166-1 alpha-2
    resolution: str | None = None  # a `Resolution` value, or `UNKNOWN`
    status: str | None = None  # a `CheckStatus` value, or `UNCHECKED`
    sort: SortKey = SortKey.TITLE
    descending: bool = False


def search_channels(session: Session, query: ChannelQuery) -> list[tuple[Channel, Check | None]]:
    """The channels the list shows, each with its latest check or None.

    Channels whose sort column has no value end up last in both directions: a channel
    that was never checked is not "the most recent", and flipping the arrow should not
    move it to the top.
    """
    latest = aliased(Check)
    ranked = _ranked_checks()
    stmt = (
        select(Channel, latest)
        .outerjoin(ranked, and_(ranked.c.channel_id == Channel.id, ranked.c.rank == 1))
        .outerjoin(latest, latest.id == ranked.c.id)
        # The guide name is shown next to every row. One extra query for the page, whatever
        # its length, instead of one per mapped channel as a lazy load would cost.
        .options(selectinload(Channel.guide_channel))
    )
    rows = session.execute(_ordered(_filtered(stmt, query, latest), query, latest))
    return [(channel, check) for channel, check in rows]


def _ranked_checks():
    """Every check with its position in its channel's history, the newest first."""
    return select(
        Check.id.label("id"),
        Check.channel_id.label("channel_id"),
        func.row_number()
        .over(
            partition_by=Check.channel_id,
            order_by=(Check.checked_at.desc(), Check.id.desc()),
        )
        .label("rank"),
    ).subquery()


def _filtered(stmt, query: ChannelQuery, latest):
    """The statement with every filter of the query applied."""
    if query.text and query.text.strip():
        stmt = stmt.where(_matching_text(query.text.strip()))
    if query.category_id is not None:
        stmt = stmt.where(Channel.category_id == query.category_id)
    if query.language:
        stmt = stmt.where(Channel.language == query.language.lower())
    if query.country:
        stmt = stmt.where(Channel.country == query.country.upper())
    if query.resolution == UNKNOWN:
        stmt = stmt.where(Channel.resolution.is_(None))
    elif query.resolution:
        stmt = stmt.where(Channel.resolution == Resolution(query.resolution))
    # Statuses are only ever read from the latest check, never from the ones before it.
    if query.status == UNCHECKED:
        stmt = stmt.where(latest.id.is_(None))
    elif query.status:
        stmt = stmt.where(latest.status == CheckStatus(query.status))
    return stmt


def _ordered(stmt, query: ChannelQuery, latest):
    """The statement sorted as the query asks, with a stable tie-break."""
    column = _sort_column(query.sort, latest)
    direction = column.desc() if query.descending else column.asc()
    return stmt.order_by(
        column.is_(None),  # missing values last, whichever direction was asked for
        direction,
        func.lower(Channel.title),
        Channel.id,
    )


def _sort_column(key: SortKey, latest):
    """What each column of the list is sorted on."""
    return {
        SortKey.TITLE: func.lower(Channel.title),
        SortKey.CREATED: Channel.created_at,
        SortKey.CHECKED: latest.checked_at,
        SortKey.PEERS: latest.peers,
        # Lower is better here, so the enum's list becomes the rank: alive is 0.
        SortKey.STATUS: case(
            *((latest.status == status, rank) for rank, status in enumerate(_BY_USEFULNESS)),
            else_=None,
        ),
    }[key]


def _matching_text(text: str):
    """Title or Content ID containing `text`; LIKE's own wildcards typed by the user are
    characters to look for, not patterns that would widen the search."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return or_(
        Channel.title.ilike(f"%{escaped}%", escape="\\"),
        Channel.content_id.contains(text.lower().removeprefix("acestream://"), autoescape=True),
    )


def used_languages(session: Session) -> list[str]:
    """The languages the catalog actually uses, for the filter options."""
    return _used_values(session, Channel.language)


def used_countries(session: Session) -> list[str]:
    """The countries the catalog actually uses, for the filter options."""
    return _used_values(session, Channel.country)


def _used_values(session: Session, column) -> list[str]:
    """The distinct values of a channel column, leaving out the channels that have none."""
    return list(session.scalars(select(column).where(column.is_not(None)).distinct()))
