"""The two forms the app is written with: one channel, and the settings.

A form here holds what was typed, and what is wrong with it. The rules belong to the services — a
Content ID, an ISO code, a file that exists — and this layer only turns a refusal into one Spanish
sentence placed next to the field that caused it.

Both forms check every field before answering. The page shows all of its problems at once on
purpose: reporting one error per round trip makes somebody with three mistakes correct them three
times.
"""

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.db.models import Category, Channel
from app.services import categories, channels
from app.services.catalog import parse_link
from app.services.errors import ValidationError
from app.services.iso import normalize_country, normalize_language
from app.services.resolution import normalize_resolution
from app.services.settings import AppSettings


@dataclass(frozen=True)
class ChannelData:
    """What a channel form leaves behind once every field has been accepted.

    `new_category` is the name that was typed, not an id: it is created on save, so the write does
    not have to happen for the form to be valid.
    """

    title: str
    content_id: str
    category_id: int | None
    new_category: str | None  # created on save; wins over `category_id`
    language: str | None
    country: str | None
    resolution: str | None


@dataclass
class ChannelForm:
    """A channel as typed, so a rejected form comes back with everything still in it."""

    title: str = ""
    link: str = ""
    category_id: str = ""
    new_category: str = ""
    language: str = ""
    country: str = ""
    resolution: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_channel(cls, channel: Channel) -> "ChannelForm":
        """The form filled with what is stored, so an edit starts from the channel."""
        return cls(
            title=channel.title,
            link=channel.content_id,
            category_id=str(channel.category_id or ""),
            language=channel.language or "",
            country=channel.country or "",
            resolution=channel.resolution.value if channel.resolution else "",
        )

    def validate(self, session: Session, *, channel_id: int | None = None) -> ChannelData | None:
        """The accepted values, or None with `errors` filled in.

        `channel_id` is the channel being edited: its own Content ID is not a duplicate of itself.
        """
        self.errors = {}
        data = ChannelData(
            title=self._checked_title(channel_id),
            content_id=self._checked_content_id(session, channel_id),
            category_id=self._checked_category(session),
            new_category=self.new_category.strip() or None,
            language=self._checked_language(),
            country=self._checked_country(),
            resolution=self._checked_resolution(),
        )
        return None if self.errors else data

    def _checked_title(self, channel_id: int | None) -> str:
        """The title as typed; only an edit needs one, a new channel takes the stream's own name."""
        title = self.title.strip()
        if not title and channel_id is not None:
            self.errors["title"] = "El título es obligatorio."
        return title

    def _checked_content_id(self, session: Session, channel_id: int | None) -> str:
        """The Content ID inside what was pasted, provided no other channel already has it."""
        try:
            content_id = parse_link(self.link)
        except ValidationError:
            self.errors["link"] = (
                "No es un Content ID válido: se esperan 40 caracteres hexadecimales "
                "o un enlace acestream://."
            )
            return ""
        owner = channels.find_channel_by_content_id(session, content_id)
        if owner is not None and owner.id != channel_id:
            self.errors["link"] = f"Este Content ID ya está registrado como «{owner.title}»."
        return content_id

    def _checked_category(self, session: Session) -> int | None:
        """The category picked from the list, or None when it names no row.

        A name typed by hand wins over the list: it is the more specific of the two answers, and it
        is created on save. That is why a typed name means the select is never read, and a bad one
        is never reported while a good name is sitting next to it.
        """
        if self.new_category.strip() or not self.category_id:
            return None
        chosen = int(self.category_id) if self.category_id.isdigit() else None
        if chosen is None or session.get(Category, chosen) is None:
            self.errors["category_id"] = "La categoría elegida no existe."
            return None
        return chosen

    def _checked_language(self) -> str | None:
        try:
            return normalize_language(self.language)
        except ValidationError:
            self.errors["language"] = "Idioma desconocido: usa un código ISO 639-1, como «es»."
            return None

    def _checked_country(self) -> str | None:
        try:
            return normalize_country(self.country)
        except ValidationError:
            self.errors["country"] = "País desconocido: usa un código ISO 3166-1, como «AR»."
            return None

    def _checked_resolution(self) -> str | None:
        """What was chosen from the list, or None for a channel nobody has measured yet."""
        try:
            chosen = normalize_resolution(self.resolution)
        except ValidationError:
            self.errors["resolution"] = "Resolución desconocida."
            return None
        return chosen.value if chosen else None


def resolve_category(
    session: Session, category_id: int | None, new_category: str | None
) -> int | None:
    """The category id to store, creating the typed name unless one is already called that."""
    if new_category is None:
        return category_id
    return categories.find_or_create(session, new_category).id


# Every number the settings page collects: its accepted range, and whether a fraction means
# anything. Concurrency and peers are counted; the rest are seconds.
_NUMBER_RULES = {
    "engine_timeout": (1, 120, False),
    "min_peers": (0, 1000, True),
    "check_timeout": (5, 600, False),
    "screenshot_timeout": (5, 300, False),
    "check_concurrency": (1, 8, True),
}

# Programs the app can call, each saved as an absolute path or left empty for autodetection.
_PATH_FIELDS = ("vlc_path", "ffmpeg_path", "acestream_path")


def _is_http_url(value: str) -> bool:
    """Whether that is an address the HTTP clients can be pointed at."""
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.hostname)


@dataclass
class SettingsForm:
    """The settings as typed, so a rejected form comes back with everything still in it."""

    engine_url: str = ""
    engine_timeout: str = ""
    search_url: str = ""
    min_peers: str = ""
    check_timeout: str = ""
    screenshot_timeout: str = ""
    check_concurrency: str = ""
    vlc_path: str = ""
    ffmpeg_path: str = ""
    acestream_path: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "SettingsForm":
        """The form filled with what is saved; numbers read the way they will be typed back."""
        return cls(
            engine_url=settings.engine_url,
            engine_timeout=f"{settings.engine_timeout:g}",
            search_url=settings.search_url or "",
            min_peers=str(settings.min_peers),
            check_timeout=f"{settings.check_timeout:g}",
            screenshot_timeout=f"{settings.screenshot_timeout:g}",
            check_concurrency=str(settings.check_concurrency),
            vlc_path=settings.vlc_path or "",
            ffmpeg_path=settings.ffmpeg_path or "",
            acestream_path=settings.acestream_path or "",
        )

    def validate(self) -> AppSettings | None:
        """The settings to store, or None with `errors` filled in."""
        self.errors = {}
        values: dict[str, object] = {
            "engine_url": self._checked_engine_url(),
            "search_url": self._checked_search_url(),
            **self._checked_numbers(),
            **self._checked_paths(),
        }
        return None if self.errors else AppSettings(**values)

    def _checked_engine_url(self) -> str:
        """The engine's address as the client wants it, without its trailing slash."""
        url = self.engine_url.strip().rstrip("/")
        if not _is_http_url(url):
            self.errors["engine_url"] = (
                "Debe ser una URL http:// o https://, como http://127.0.0.1:6878."
            )
        return url

    def _checked_search_url(self) -> str | None:
        """The searcher's address, or None to mean "wherever the engine serves it"."""
        url = self.search_url.strip().rstrip("/")
        if url and not _is_http_url(url):
            self.errors["search_url"] = (
                "Debe ser una URL http:// o https://, o quedar vacío para usar la del engine."
            )
        return url or None

    def _checked_numbers(self) -> dict[str, float | int | None]:
        """Each typed number, or None where it is not one the app can work with."""
        numbers: dict[str, float | int | None] = {}
        for name, (low, high, whole) in _NUMBER_RULES.items():
            # 7,5 is how this keyboard writes a decimal point, so both are read as the same value.
            raw = getattr(self, name).strip().replace(",", ".")
            try:
                number: float | int | None = int(raw) if whole else float(raw)
            except ValueError:
                number = None
            if number is None or not low <= number <= high:
                kind = "un número entero" if whole else "un número"
                self.errors[name] = f"Debe ser {kind} entre {low} y {high}."
            numbers[name] = number
        return numbers

    def _checked_paths(self) -> dict[str, str | None]:
        """Each program's path, or None for a box somebody left empty for autodetection."""
        paths: dict[str, str | None] = {}
        for name in _PATH_FIELDS:
            # "Copy as path" on Windows wraps the path in quotes, and people paste them back.
            path = getattr(self, name).strip().strip('"')
            if path and not Path(path).is_file():
                self.errors[name] = "No existe ese archivo."
            paths[name] = path or None
        return paths
