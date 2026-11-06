"""The check history: writing one result and reading a channel's back."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Check, CheckStatus
from app.services.channels import get_channel
from app.services.verification import VerificationResult

# `error_message` is a String(500), and engine messages are longer than that.
_MESSAGE_LIMIT = 500
# Newest first. Two checks recorded in the same instant are ordered by insertion, which is
# the order they were really made in.
_NEWEST_FIRST = (Check.checked_at.desc(), Check.id.desc())


def add_check(
    session: Session,
    channel_id: int,
    *,
    status: CheckStatus,
    peers: int | None = None,
    speed_down: int | None = None,
    infohash: str | None = None,
    screenshot_path: str | None = None,
    error_message: str | None = None,
    width: int | None = None,
    height: int | None = None,
    checked_at: datetime | None = None,
) -> Check:
    """Files one check. `screenshot_path` is relative to the data directory, and width and
    height are the size of that screenshot."""
    get_channel(session, channel_id)
    check = Check(
        channel_id=channel_id,
        status=status,
        peers=peers,
        speed_down=speed_down,
        infohash=infohash,
        screenshot_path=screenshot_path,
        error_message=error_message,
        width=width,
        height=height,
    )
    if checked_at is not None:
        check.checked_at = checked_at
    session.add(check)
    session.commit()
    return check


def record_verification(
    session: Session,
    channel_id: int,
    result: VerificationResult,
    *,
    screenshot_path: str | None = None,
    frame: tuple[int, int] | None = None,
    checked_at: datetime | None = None,
) -> Check:
    """Appends a verification outcome to the channel's history."""
    return add_check(
        session,
        channel_id,
        status=result.status,
        peers=result.peers,
        speed_down=result.speed_down,
        infohash=result.infohash,
        screenshot_path=screenshot_path,
        error_message=_fitted(result.error_message),
        width=frame[0] if frame else None,
        height=frame[1] if frame else None,
        checked_at=checked_at,
    )


def _fitted(message: str | None) -> str | None:
    """An engine message cut to the column's length, so storing it cannot fail."""
    return message[:_MESSAGE_LIMIT] if message else None


def list_checks(session: Session, channel_id: int) -> list[Check]:
    """Everything known about the channel, newest first."""
    get_channel(session, channel_id)
    return list(session.scalars(_history(channel_id)))


def get_latest_check(session: Session, channel_id: int) -> Check | None:
    """The channel's current state, or None when it was never checked."""
    get_channel(session, channel_id)
    return session.scalars(_history(channel_id).limit(1)).first()


def _history(channel_id: int):
    return select(Check).where(Check.channel_id == channel_id).order_by(*_NEWEST_FIRST)


def _ranked(*columns):
    """The given columns plus each row's position in its channel's history, newest first."""
    return select(
        *columns,
        func.row_number().over(partition_by=Check.channel_id, order_by=_NEWEST_FIRST).label("rank"),
    )


def latest_screenshots(session: Session) -> dict[int, str]:
    """The most recent screenshot of every channel that has one, keyed by channel id.

    It is not necessarily the screenshot of the latest check: most checks take none.
    """
    ranked = (
        _ranked(Check.channel_id.label("channel_id"), Check.screenshot_path.label("path"))
        .where(Check.screenshot_path.is_not(None))
        .subquery()
    )
    rows = session.execute(select(ranked.c.channel_id, ranked.c.path).where(ranked.c.rank == 1))
    return {channel_id: path for channel_id, path in rows}


def latest_checks(session: Session) -> dict[int, Check]:
    """The newest check of each channel that has one, keyed by channel id."""
    ranked = _ranked(Check.id.label("id")).subquery()
    newest = select(Check).join(ranked, Check.id == ranked.c.id).where(ranked.c.rank == 1)
    return {check.channel_id: check for check in session.scalars(newest)}
