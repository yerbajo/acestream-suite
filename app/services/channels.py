"""The catalog's channels: one unit of work per function, each committing on success."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.db.models import Category, Channel
from app.services.errors import DuplicateError, NotFoundError, ValidationError
from app.services.iso import normalize_country, normalize_language
from app.services.resolution import normalize_resolution

# The fields a caller may change one at a time. The rest are derived (the timestamps) or
# only meaningful when the whole channel is created.
_EDITABLE = frozenset({"title", "content_id", "category_id", "language", "country", "resolution"})
# Fields stored as a code of the application's own, with the function that reads them.
_CODED = {
    "language": normalize_language,
    "country": normalize_country,
    "resolution": normalize_resolution,
}


def _clean_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise ValidationError("title must not be empty")
    return title


def check_category(session: Session, category_id: int | None) -> None:
    """Raises `NotFoundError` when the category a channel points at does not exist."""
    if category_id is not None and session.get(Category, category_id) is None:
        raise NotFoundError(f"category not found: {category_id}")


def create_channel(
    session: Session,
    *,
    title: str,
    content_id: str,
    category_id: int | None = None,
    language: str | None = None,
    country: str | None = None,
    resolution: str | None = None,
    title_pending: bool = False,
) -> Channel:
    """Stores a channel. `content_id` arrives already normalized: 40 lowercase hex digits.

    `title_pending` marks a title of ours that a check may later replace with the name the
    stream publishes.
    """
    check_category(session, category_id)
    channel = Channel(
        title=_clean_title(title),
        content_id=content_id,
        category_id=category_id,
        language=normalize_language(language),
        country=normalize_country(country),
        resolution=normalize_resolution(resolution),
        title_pending=title_pending,
    )
    session.add(channel)
    _commit(session, content_id)
    return channel


def get_channel(session: Session, channel_id: int) -> Channel:
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise NotFoundError(f"channel not found: {channel_id}")
    return channel


def find_channel_by_content_id(session: Session, content_id: str) -> Channel | None:
    return session.scalars(select(Channel).where(Channel.content_id == content_id)).first()


def list_channels(session: Session) -> list[Channel]:
    return list(session.scalars(select(Channel).order_by(Channel.title, Channel.id)))


def update_channel(session: Session, channel_id: int, **changes) -> Channel:
    """Changes only the fields given; `category_id=None` clears the category."""
    unknown = changes.keys() - _EDITABLE
    if unknown:
        raise ValidationError(f"cannot update: {', '.join(sorted(unknown))}")
    channel = get_channel(session, channel_id)

    prepared = _prepared(session, channel, changes)
    for field, value in prepared.items():
        setattr(channel, field, value)
    _commit(session, prepared.get("content_id", channel.content_id))
    return channel


def _prepared(session: Session, channel: Channel, changes: dict) -> dict:
    """Every change cleaned and validated before one of them is applied."""
    prepared = dict(changes)
    if "title" in prepared:
        prepared["title"] = _clean_title(prepared["title"])
        if prepared["title"] != channel.title:
            # A title somebody typed is a decision; the stream does not get to overwrite it.
            channel.title_pending = False
    if "category_id" in prepared:
        check_category(session, prepared["category_id"])
    for field, normalize in _CODED.items():
        if field in prepared:
            prepared[field] = normalize(prepared[field])
    return prepared


def delete_channel(session: Session, channel_id: int, data_dir: Path = DATA_DIR) -> None:
    """Removes the channel with its check history and the screenshots stored under `data_dir`."""
    channel = get_channel(session, channel_id)
    pictures = [check.screenshot_path for check in channel.checks if check.screenshot_path]
    session.delete(channel)
    session.commit()
    # Only once the row is gone: a delete that fails must not take the files with it.
    for relative in pictures:
        _discard(data_dir, relative)


def _commit(session: Session, content_id: str) -> None:
    """Commits, translating the database's own refusals into the service layer's errors."""
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if "UNIQUE" in str(exc.orig):
            raise DuplicateError(f"channel already exists: {content_id}") from exc
        raise ValidationError(f"invalid channel data: {exc.orig}") from exc


def _discard(data_dir: Path, relative: str) -> None:
    """Deletes one screenshot, refusing any stored path that leads out of `data_dir`."""
    root = data_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        return
    target.unlink(missing_ok=True)
