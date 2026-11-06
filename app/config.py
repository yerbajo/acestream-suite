"""Every setting the suite reads from the environment, resolved once at import time."""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

# The repository (or the unpacked program) this module lives in.
ROOT_DIR = Path(__file__).resolve().parent.parent

# One prefix for all of them, so the suite can never pick up a variable belonging to
# something else that happens to be exported on the same machine.
SUITE_PREFIX = "ACESTREAM_SUITE_"


def env(name: str, default: str | None = None) -> str | None:
    """The suite's variable `ACESTREAM_SUITE_<name>`, or `default` when it is not set.

    `name` is the part after the prefix, which is why every setting below can be read
    through this one function.
    """
    value = os.environ.get(SUITE_PREFIX + name)
    return default if value is None else value


# True when running as the Windows executable PyInstaller builds (docs/packaging.md).
FROZEN = bool(getattr(sys, "frozen", False))


def resource_dir(frozen: bool = FROZEN) -> Path:
    """Where the read-only files ship: templates, static files and migrations.

    Inside the executable that is the bundle's `_internal` folder; from source it is the
    repository the application was started from.
    """
    return Path(getattr(sys, "_MEIPASS", ROOT_DIR)) if frozen else ROOT_DIR


def _user_local_folder(environ: Mapping[str, str]) -> Path:
    """The account's Local folder, or where Windows keeps it when the variable is missing."""
    local = environ.get("LOCALAPPDATA")
    return Path(local) if local else Path.home() / "AppData" / "Local"


def default_data_dir(frozen: bool = FROZEN, environ: Mapping[str, str] = os.environ) -> Path:
    """Where the database and the screenshots go when `ACESTREAM_SUITE_DATA_DIR` says nothing.

    The executable writes into the user's profile and not next to itself: an update
    replaces the program folder, and the program folder may well be read-only.
    """
    if not frozen:
        return ROOT_DIR / "data"
    return _user_local_folder(environ) / "acestream-suite"


RESOURCE_DIR = resource_dir()
DATA_DIR = Path(env("DATA_DIR") or default_data_dir())
DB_PATH = DATA_DIR / "acestream-suite.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

ENGINE_URL = env("ENGINE_URL", "http://127.0.0.1:6878")
# A Content ID the engine cannot resolve takes around four seconds to fail, so the
# default timeout is set well above that instead of just above a healthy answer.
ENGINE_TIMEOUT = float(env("ENGINE_TIMEOUT", "15"))
# Address of the `acesearch` service that answers `/search.m3u`. Empty means "same address
# as the engine", which is how the LAN deployment serves it, so moving the engine moves
# the search with it unless a different one is configured.
SEARCH_URL = env("SEARCH_URL", "")

FFMPEG_PATH = env("FFMPEG_PATH")  # None: look it up on PATH
SCREENSHOT_TIMEOUT = float(env("SCREENSHOT_TIMEOUT", "30"))

# Channels checked in parallel in the background. Each one holds a live engine session,
# so this is a load limit and not a speed dial.
CHECK_CONCURRENCY = int(env("CHECK_CONCURRENCY", "2"))

VLC_PATH = env("VLC_PATH")  # None: usual install folders, then PATH

# Host headers and write origins that are accepted. Empty is the desktop case, where the
# app is only ever reached on loopback. A LAN deployment has to list the address the
# browser types: the CSRF check compares a write's origin against this same list, so
# without it every write coming from the LAN would be answered with a 403.
ALLOWED_HOSTS = [host.strip() for host in env("ALLOWED_HOSTS", "").split(",") if host.strip()]

# "vlc" starts the stream and opens the local player, which is what a desktop expects.
# "url" starts it and hands the URL back, for a host that has no player to open.
PLAYBACK_MODE = env("PLAYBACK_MODE", "vlc").strip().lower()

# Name of the query parameter that starts a stream. The Windows engine build reads `id`
# and that is the default. The Linux 3.2.11 build answers `failed to load content` to it
# and only streams with `infohash`, even when both parameters are sent, so this replaces
# the name rather than adding a second one.
STREAM_PARAM = env("STREAM_PARAM", "id").strip()
