"""The write side of the catalog: adding and editing channels, and checking them."""

import logging
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acestream.client import EngineClient, StreamSession
from app.acestream.content_id import InvalidContentIdError, parse_content_id
from app.acestream.errors import EngineError, EngineUnavailableError
from app.config import DATA_DIR
from app.db.models import Channel, Check
from app.services import channels
from app.services.checks import record_verification
from app.services.errors import DuplicateError, ValidationError
from app.services.naming import clean_stream_name, placeholder_title
from app.services.resolution import classify, frame_size
from app.services.screenshots import CaptureSettings, ScreenshotError, capture_screenshot
from app.services.verification import VerificationSettings, verify

log = logging.getLogger(__name__)


class Screenshots(StrEnum):
    """Whether a check of a live channel also captures a frame of it."""

    NEVER = "never"  # availability and peers, nothing else
    IF_MISSING = "if_missing"  # the channel has no picture yet
    ALWAYS = "always"  # a different stream, or pictures being rebuilt


def parse_link(link: str) -> str:
    """The Content ID inside what the user pasted, refused as a `ValidationError` when unusable."""
    try:
        return parse_content_id(link)
    except InvalidContentIdError as exc:
        raise ValidationError(str(exc)) from exc


def check_channel(
    session: Session,
    client: EngineClient,
    channel_id: int,
    *,
    settings: VerificationSettings | None = None,
    capture: CaptureSettings | None = None,
    screenshots: Screenshots = Screenshots.NEVER,
    data_dir: Path = DATA_DIR,
) -> Check:
    """Checks one channel and files the outcome in its history.

    Availability and peers are always checked; `screenshots` decides whether a channel
    that turns out to be alive also gets a picture, which costs seconds per channel.
    Engine and ffmpeg trouble becomes the stored check, with one exception: an engine
    that cannot be reached says nothing about the channel, so it raises
    `EngineUnavailableError` and writes nothing at all.

    Polls the engine while it waits, so run it in a worker thread.
    """
    channel = channels.get_channel(session, channel_id)
    content_id = channel.content_id
    if channel.title_pending:
        _name_from_stream(session, client, channel)

    wanted = _wants_picture(session, channel_id, screenshots)
    picture: str | None = None

    def grab(stream: StreamSession) -> None:
        nonlocal picture
        picture = _capture(stream, content_id, capture or CaptureSettings(), data_dir)

    result = verify(client, content_id, settings, on_alive=grab if wanted else None)

    frame = frame_size(data_dir / picture) if picture else None
    check = record_verification(session, channel_id, result, screenshot_path=picture, frame=frame)
    if frame is not None:
        # What the stream shows is evidence; it overrides a resolution chosen by hand.
        channels.update_channel(session, channel_id, resolution=classify(*frame).value)
    return check


def _wants_picture(session: Session, channel_id: int, mode: Screenshots) -> bool:
    if mode is Screenshots.ALWAYS:
        return True
    if mode is Screenshots.IF_MISSING:
        return not has_screenshot(session, channel_id)
    return False


def _capture(
    stream: StreamSession, content_id: str, capture: CaptureSettings, data_dir: Path
) -> str | None:
    """The relative path of the new screenshot, or None when no frame could be taken.

    A channel that is alive without a picture is still alive, so ffmpeg failing is
    logged and not turned into a failed check.
    """
    try:
        return capture_screenshot(
            stream.playback_url,
            content_id,
            data_dir=data_dir,
            ffmpeg_path=capture.ffmpeg_path,
            timeout=capture.timeout,
        )
    except ScreenshotError as exc:
        log.warning("no screenshot for %s: %s", content_id, exc)
        return None


def _name_from_stream(session: Session, client: EngineClient, channel: Channel) -> None:
    """Gives a provisional title the stream's own name, when the engine can supply one.

    Any engine failure leaves the provisional title alone; the next check tries again.
    """
    try:
        name = clean_stream_name(client.media_name(channel.content_id))
    except EngineError as exc:
        log.warning("no stream name for %s: %s", channel.content_id, exc)
        return
    if name is not None:
        channels.update_channel(session, channel.id, title=name)


def has_screenshot(session: Session, channel_id: int) -> bool:
    """Whether any check of this channel has stored a screenshot."""
    return (
        session.scalar(
            select(Check.id)
            .where(Check.channel_id == channel_id, Check.screenshot_path.is_not(None))
            .limit(1)
        )
        is not None
    )


def add_channel(
    session: Session,
    client: EngineClient,
    *,
    title: str = "",
    link: str,
    category_id: int | None = None,
    language: str | None = None,
    country: str | None = None,
    resolution: str | None = None,
    settings: VerificationSettings | None = None,
    capture: CaptureSettings | None = None,
    verify: bool = True,
    data_dir: Path = DATA_DIR,
) -> Channel:
    """Registers a channel from a Content ID or link and checks it.

    The row is committed before the engine is asked, so a link typed while the engine is
    off is stored rather than lost; `verify=False` skips the check for a caller that
    already knows there is nothing to ask. A channel without a title takes the stream's
    name, or a provisional one until a later check finds that name.
    """
    content_id = parse_link(link)
    _ensure_free(session, content_id)
    unnamed = not title.strip()
    channel = channels.create_channel(
        session,
        title=placeholder_title(content_id) if unnamed else title,
        title_pending=unnamed,
        content_id=content_id,
        category_id=category_id,
        language=language,
        country=country,
        resolution=resolution,
    )
    if verify:
        _first_check(session, client, channel, settings, capture, data_dir)
    return channel


def _first_check(
    session: Session,
    client: EngineClient,
    channel: Channel,
    settings: VerificationSettings | None,
    capture: CaptureSettings | None,
    data_dir: Path,
) -> None:
    """Checks a channel just added, leaving it stored and unchecked when the engine is off."""
    try:
        check_channel(
            session,
            client,
            channel.id,
            settings=settings,
            capture=capture,
            screenshots=Screenshots.IF_MISSING,
            data_dir=data_dir,
        )
    except EngineUnavailableError as exc:
        log.warning("engine unavailable, %s saved unchecked: %s", channel.content_id, exc)


def edit_channel(
    session: Session,
    client: EngineClient,
    channel_id: int,
    *,
    link: str | None = None,
    settings: VerificationSettings | None = None,
    capture: CaptureSettings | None = None,
    verify: bool = True,
    data_dir: Path = DATA_DIR,
    **changes,
) -> Channel:
    """Applies edits to a channel, checking it again only when its Content ID changed.

    `changes` takes `title`, `category_id`, `language`, `country` and `resolution`; a new
    Content ID travels through `link` so it goes through the same parsing as any other.
    """
    if "content_id" in changes:
        raise ValidationError("change the Content ID through `link` so it is validated")
    channel = channels.get_channel(session, channel_id)

    replacement = parse_link(link) if link is not None else None
    other_stream = replacement is not None and replacement != channel.content_id
    if other_stream:
        _ensure_free(session, replacement, ignore=channel_id)
        changes["content_id"] = replacement

    channels.update_channel(session, channel_id, **changes)
    if other_stream and verify:
        _recheck_new_stream(session, client, channel, settings, capture, data_dir)
    return channel


def _recheck_new_stream(
    session: Session,
    client: EngineClient,
    channel: Channel,
    settings: VerificationSettings | None,
    capture: CaptureSettings | None,
    data_dir: Path,
) -> None:
    """Checks a channel whose Content ID was replaced, keeping the history of the old one.

    That history is how the user sees the previous link stopped working, so it stays; the
    pictures go, because they show a stream that is no longer this channel's.
    """
    try:
        check_channel(
            session,
            client,
            channel.id,
            settings=settings,
            capture=capture,
            screenshots=Screenshots.ALWAYS,
            data_dir=data_dir,
        )
    except EngineUnavailableError as exc:
        log.warning("engine unavailable, %s left unchecked: %s", channel.content_id, exc)


def _ensure_free(session: Session, content_id: str, *, ignore: int | None = None) -> None:
    """Refuses a Content ID that another channel already uses."""
    existing = channels.find_channel_by_content_id(session, content_id)
    if existing is not None and existing.id != ignore:
        raise DuplicateError(f"this Content ID is already registered as '{existing.title}'")
