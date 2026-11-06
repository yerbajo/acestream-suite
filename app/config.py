import os
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# The suite's own variables: every setting is `ACESTREAM_SUITE_<name>`, and nothing else is read.
SUITE_PREFIX = "ACESTREAM_SUITE_"


def env(name: str, default: str | None = None) -> str | None:
    """The suite's variable (`ACESTREAM_SUITE_<name>`), or `default` when it is not set.

    `name` is the part after the prefix, so one helper covers every setting.
    """
    value = os.environ.get(SUITE_PREFIX + name)
    return default if value is None else value


# True inside the Windows executable built with PyInstaller (see docs/packaging.md).
FROZEN = bool(getattr(sys, "frozen", False))


def resource_dir(frozen: bool = FROZEN) -> Path:
    """Where the files that ship with acestream-suite live: templates, static files, migrations.

    Read-only: in the executable it is the bundle's `_internal` folder.
    """
    return Path(getattr(sys, "_MEIPASS", ROOT_DIR)) if frozen else ROOT_DIR


def default_data_dir(frozen: bool = FROZEN, environ: Mapping[str, str] = os.environ) -> Path:
    """Where the database and screenshots go unless `ACESTREAM_SUITE_DATA_DIR` says otherwise.

    The executable keeps them in the user's profile, so replacing the program folder on
    an update, or unpacking it somewhere read-only, never touches them.
    """
    if not frozen:
        return ROOT_DIR / "data"
    local = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "acestream-suite"


RESOURCE_DIR = resource_dir()
DATA_DIR = Path(env("DATA_DIR") or default_data_dir())
DB_PATH = DATA_DIR / "acestream-suite.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

ENGINE_URL = env("ENGINE_URL", "http://127.0.0.1:6878")
# An unknown hash takes ~4 s to fail on the engine, so the default leaves headroom.
ENGINE_TIMEOUT = float(env("ENGINE_TIMEOUT", "15"))
# The acesearch service, the searcher that answers `/search.m3u` next to the engine. Empty means
# "the engine's own address", which is where the .28 deployment serves it, so one URL is enough.
SEARCH_URL = env("SEARCH_URL", "")

FFMPEG_PATH = env("FFMPEG_PATH")  # None: look it up on PATH
SCREENSHOT_TIMEOUT = float(env("SCREENSHOT_TIMEOUT", "30"))

# Channels verified at once in the background; each one is a live engine session.
CHECK_CONCURRENCY = int(env("CHECK_CONCURRENCY", "2"))

VLC_PATH = env("VLC_PATH")  # None: usual install folders, then PATH

# Hosts accepted in the `Host` header and in the `Origin` of a write. Empty on a desktop
# install, which only ever answers on loopback. A LAN deployment lists the address the
# browser types, because the CSRF check compares the request's origin against this same
# list: without it every write from the LAN is refused with a 403.
ALLOWED_HOSTS = [
    host.strip()
    for host in env("ALLOWED_HOSTS", "").split(",")
    if host.strip()
]

# "vlc": start the stream and open the local VLC, the desktop behaviour.
# "url": start the stream and hand its URL to the caller, for a host with no player.
PLAYBACK_MODE = env("PLAYBACK_MODE", "vlc").strip().lower()

# Name of the query parameter that starts a stream on the engine. The Windows engine build
# takes `id`, and that is the default. The Linux 3.2.11 build answers `failed to load
# content` to `id` and only streams with `infohash` — even when both are sent together, so
# this is a replacement and not an addition.
STREAM_PARAM = env("STREAM_PARAM", "id").strip()
