"""The suite's own export format: every channel with the data stored for it, as JSON.

What is left out is deliberate. Check history and screenshots describe the machine that
made them, so they do not travel; the receiving machine checks the channels itself.
"""

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.acestream.content_id import InvalidContentIdError, parse_content_id
from app.db.base import utcnow
from app.db.models import Channel
from app.services import categories, channels
from app.services.errors import ServiceError, ValidationError
from app.services.importer import MAX_LINES, ImportResult, Reason, Rejected
from app.services.iso import normalize_country, normalize_language
from app.services.naming import placeholder_title
from app.services.resolution import normalize_resolution

FORMAT = "acestream-suite"
VERSION = 1
_TITLE_LENGTH = 200  # the column length
_SHOWN = 120  # how much of a rejected entry is quoted back to the user


def export_channels(rows: Iterable[Channel], *, exported_at: datetime | None = None) -> dict:
    """The export document for these channels, ready to be handed to `json.dumps`."""
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": (exported_at or utcnow()).isoformat(),
        "channels": [_entry_for(channel) for channel in rows],
    }


def _entry_for(channel: Channel) -> dict[str, Any]:
    return {
        "title": channel.title,
        "title_pending": channel.title_pending,
        "content_id": channel.content_id,
        "link": f"acestream://{channel.content_id}",
        "category": channel.category.name if channel.category else None,
        "language": channel.language,
        "country": channel.country,
        "resolution": channel.resolution.value if channel.resolution else None,
        "created_at": channel.created_at.isoformat(),
    }


def is_export(text: str) -> bool:
    """Whether the text opens as a JSON object: an export does, a list of links does not."""
    return text.lstrip().startswith("{")


def parse_export(text: str) -> list[Any]:
    """The `channels` of an export, or a `ValidationError` explaining why this is not one."""
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ValidationError(f"not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ValidationError('not an acestream-suite export (missing "format": "acestream-suite")')
    version = document.get("version")
    if not isinstance(version, int) or version > VERSION:
        raise ValidationError(f"unsupported export version: {version!r}")
    entries = document.get("channels")
    if not isinstance(entries, list):
        raise ValidationError('the export has no "channels" list')
    if len(entries) > MAX_LINES:
        raise ValidationError(f"too many channels: {len(entries)} (at most {MAX_LINES})")
    return entries


def import_export(
    session: Session,
    text: str,
    *,
    category_id: int | None = None,
    language: str | None = None,
    country: str | None = None,
) -> ImportResult:
    """Creates the channels of an export that this catalog does not have yet.

    A channel keeps what the file says about it, and the category, language and country
    given here only fill in what it lacks. One unusable channel is reported and skipped
    without stopping the rest; the checks are left for the caller to queue.
    """
    entries = parse_export(text)
    language = normalize_language(language)
    country = normalize_country(country)
    channels.check_category(session, category_id)

    result = ImportResult(unit="channel")
    seen: dict[str, int] = {}
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            result.invalid.append(
                Rejected(position, str(entry)[:_SHOWN], Reason.INVALID, detail="not a channel")
            )
            continue
        raw_link = str(entry.get("content_id") or entry.get("link") or "")
        content_id = _parsed_link(raw_link)
        if content_id is None:
            result.invalid.append(Rejected(position, raw_link[:_SHOWN], Reason.NO_LINK))
            continue
        if content_id in seen:
            result.duplicates.append(
                Rejected(position, content_id, Reason.REPEATED, first_line=seen[content_id])
            )
            continue
        seen[content_id] = position
        existing = channels.find_channel_by_content_id(session, content_id)
        if existing is not None:
            result.duplicates.append(
                Rejected(position, content_id, Reason.EXISTING, existing=existing)
            )
            continue
        try:
            created = _create(session, entry, content_id, category_id, language, country)
        except ServiceError as exc:
            result.invalid.append(Rejected(position, content_id, Reason.INVALID, detail=str(exc)))
            continue
        result.created.append(created)
    return result


def _parsed_link(raw: str) -> str | None:
    """The Content ID held by an entry's `content_id` or `link`, when either is valid."""
    try:
        return parse_content_id(raw)
    except InvalidContentIdError:
        return None


def _create(
    session: Session,
    entry: dict,
    content_id: str,
    default_category_id: int | None,
    default_language: str | None,
    default_country: str | None,
) -> Channel:
    # Every field is read and accepted before anything is written, so a channel rejected
    # half way leaves no trace: not a row and not the category it asked for.
    language = normalize_language(_text(entry, "language")) or default_language
    country = normalize_country(_text(entry, "country")) or default_country
    resolution = normalize_resolution(_text(entry, "resolution"))
    title = (_text(entry, "title") or "").strip()[:_TITLE_LENGTH]
    channel = channels.create_channel(
        session,
        title=title or placeholder_title(content_id),
        title_pending=bool(entry.get("title_pending")) or not title,
        content_id=content_id,
        category_id=_category_for(session, entry, default_category_id),
        language=language,
        country=country,
        resolution=resolution.value if resolution else None,
    )
    _restore_added_at(session, channel, entry.get("created_at"))
    return channel


def _category_for(session: Session, entry: dict, fallback: int | None) -> int | None:
    """The category the entry names, matched by name; the fallback when it names none."""
    name = (_text(entry, "category") or "").strip()
    if not name:
        return fallback
    return categories.find_or_create(session, name).id


def _restore_added_at(session: Session, channel: Channel, value: Any) -> None:
    """Keeps the moment the channel was added on the machine that exported it."""
    moment = _datetime(value)
    if moment is not None:
        channel.created_at = moment
        session.commit()


def _text(entry: dict, key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be text, not {type(value).__name__}")
    return value


def _datetime(value: Any) -> datetime | None:
    """An ISO date from the export as UTC; a value that cannot be read is simply ignored.

    A date without a zone is taken to be UTC, since that is the only zone the app stores.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
