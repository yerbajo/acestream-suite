"""The settings the user can change, and how they are read from and written to the database."""

from collections.abc import Callable
from dataclasses import dataclass, fields, replace

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import (
    CHECK_CONCURRENCY,
    ENGINE_TIMEOUT,
    ENGINE_URL,
    FFMPEG_PATH,
    SCREENSHOT_TIMEOUT,
    SEARCH_URL,
    VLC_PATH,
)
from app.db.models import Setting
from app.services.screenshots import CaptureSettings
from app.services.verification import VerificationSettings


@dataclass(frozen=True)
class AppSettings:
    """Everything the user can adjust.

    The defaults are the environment's (`ACESTREAM_SUITE_*`); a saved value replaces one.
    A path of None means "find it yourself": the usual install folders, then PATH.
    """

    engine_url: str = ENGINE_URL
    engine_timeout: float = ENGINE_TIMEOUT
    # Empty means the searcher answers on the engine's address, so moving the engine takes
    # the search along. Pointing it somewhere else is a saved value in its own right.
    search_url: str = SEARCH_URL
    min_peers: int = VerificationSettings.min_peers
    check_timeout: float = VerificationSettings.timeout
    screenshot_timeout: float = SCREENSHOT_TIMEOUT
    check_concurrency: int = CHECK_CONCURRENCY
    vlc_path: str | None = VLC_PATH
    ffmpeg_path: str | None = FFMPEG_PATH
    acestream_path: str | None = None  # the installed engine, kept for reference

    @property
    def resolved_search_url(self) -> str:
        """Where searches go: the searcher's own URL, or the engine's when it has none."""
        return self.search_url or self.engine_url

    def verification(self) -> VerificationSettings:
        return VerificationSettings(min_peers=self.min_peers, timeout=self.check_timeout)

    def capture(self) -> CaptureSettings:
        return CaptureSettings(ffmpeg_path=self.ffmpeg_path, timeout=self.screenshot_timeout)


# How each stored value is read back. The assert below keeps this table and the dataclass
# from drifting apart: a new field has to be given a way to be stored.
_READERS: dict[str, Callable[[str], object]] = {
    "engine_url": str,
    "engine_timeout": float,
    "search_url": str,
    "min_peers": int,
    "check_timeout": float,
    "screenshot_timeout": float,
    "check_concurrency": int,
    "vlc_path": str,
    "ffmpeg_path": str,
    "acestream_path": str,
}
assert _READERS.keys() == {f.name for f in fields(AppSettings)}


def load_settings(session: Session) -> AppSettings:
    """The saved settings over the defaults.

    A stored value that no longer parses is skipped, so one bad row written by an older
    version costs that setting and not every page that reads it.
    """
    stored = {row.key: row.value for row in session.scalars(select(Setting))}
    return replace(AppSettings(), **_readable(stored))


def _readable(stored: dict[str, str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for name, read in _READERS.items():
        if name not in stored:
            continue
        try:
            values[name] = read(stored[name])
        except ValueError:
            continue
    return values


def save_settings(session: Session, settings: AppSettings) -> None:
    """Stores every setting; a path left as None is removed so it gets detected again.

    Values are validated before they get here: this function only persists them.
    """
    for name in _READERS:
        value = getattr(settings, name)
        if value is None:
            session.execute(delete(Setting).where(Setting.key == name))
        else:
            session.merge(Setting(key=name, value=str(value)))
    session.commit()
