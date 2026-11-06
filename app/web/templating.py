"""The template environment: what every page can reach without being handed it.

A page draws what a service decided. What it needs from here are spellings — a moment as "hace 3 h",
a status as a badge, a static file under a URL no cache has seen, a code as a name — and they live
in one environment so the list, the detail page and the guide spell the same value the same way.

Two rules are worth stating because the pages depend on them. Times are stored in UTC and shown in
this machine's zone: the app runs where the person using it lives. And the guide is a different
promise, read in Europe/Madrid, so it gets its own filter that names its zone instead of pretending
to be the machine's.
"""

import hashlib
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.db.base import utcnow
from app.db.models import CheckStatus, Resolution
from app.services import epg_guide
from app.services.iso import country_choices, country_name, language_choices, language_name
from app.web.flash import flash_message

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"

# How each stored value reads on a badge. Every member has a line here on purpose: a status or a
# resolution added to the database without one shows up as a blank badge, not as a crash.
_STATUS_BADGES = {
    CheckStatus.ALIVE: "Activo",
    CheckStatus.NO_PEERS: "Sin peers",
    CheckStatus.NOT_FOUND: "No encontrado",
    CheckStatus.ERROR: "Error",
}

_RESOLUTION_BADGES = {
    Resolution.SD: "480p",
    Resolution.HD: "720p",
    Resolution.FULL_HD: "1080p",
    Resolution.UHD: "4K",
    Resolution.OTHER: "Otra",
}

# A gap is spoken in the widest unit it still fits, with the seconds that unit holds. Past the last
# row the sentence stops helping — "hace 45 d" is arithmetic for the reader — and the date is shown.
_GAP_UNITS = (
    (60 * 60, 60, "min"),
    (24 * 60 * 60, 60 * 60, "h"),
    (31 * 24 * 60 * 60, 24 * 60 * 60, "d"),
)


def _machine_time(value: datetime | None) -> str:
    """The moment where the machine is, or nothing at all for a moment that does not exist."""
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else ""


def _ago(value: datetime, now: datetime | None = None) -> str:
    """How long ago that was, in the widest unit that still reads ("hace 3 h")."""
    seconds = ((now or utcnow()) - value).total_seconds()
    if seconds < 60:
        return "ahora"
    for limit, unit_seconds, unit in _GAP_UNITS:
        if seconds < limit:
            return f"hace {int(seconds // unit_seconds)} {unit}"
    return value.astimezone().strftime("%Y-%m-%d")


@lru_cache(maxsize=32)
def _content_stamp(path: Path, mtime_ns: int) -> str:
    """Ten hex digits of the file's own content.

    The modification time is part of the key, so editing a file gets it digested again without
    restarting the app; the digest is what the browser compares.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def static_url(request: Request, path: str) -> str:
    """A static file's URL with its content in it: a changed file is a URL no cache has seen."""
    file = STATIC_DIR / path
    url = request.url_for("static", path=path)
    try:
        stamp = _content_stamp(file, file.stat().st_mtime_ns)
    except OSError:
        return str(url)
    return f"{url}?v={stamp}"


def _shortened_hash(content_id: str) -> str:
    """Six and four hex digits: two rows can be told apart, and the full value is beside it."""
    return f"{content_id[:6]}…{content_id[-4:]}"


templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.filters["localtime"] = _machine_time
templates.env.filters["madrid"] = epg_guide.local
templates.env.filters["short_hash"] = _shortened_hash
templates.env.filters["ago"] = _ago
templates.env.globals.update(
    status_labels=_STATUS_BADGES,
    resolution_labels=_RESOLUTION_BADGES,
    flash_message=flash_message,
    static_url=static_url,
    language_name=language_name,
    country_name=country_name,
    language_choices=language_choices,
    country_choices=country_choices,
    guide_time_range=epg_guide.time_range,
)
