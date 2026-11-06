"""Searching the channels acesearch is carrying, in front of the engine.

The searcher is the service the .28 deployment puts next to the engine: `GET /search.m3u?query=…&json=1`
answers a JSON list whose entries carry `name`, `infohash`, `bitrate`, `availability`, `categories`
and `languages`. The last element of that list is an empty object, not a channel, so the answer is
walked and every object that has an `infohash` is taken.

It is a different service from the engine, so it has its own URL in the settings and its own test: an
engine that answers `get_version` says nothing about whether the search is up.
"""

import json
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from app.acestream.content_id import InvalidContentIdError, parse_content_id

# A query is a search, not a harvest: the searcher is somebody else's session on somebody else's box.
MAX_QUERY_LENGTH = 100
DEFAULT_LIMIT = 60
DEFAULT_TIMEOUT = 15.0


class SearchError(Exception):
    """The searcher could not be reached, or did not answer what it promises."""


@dataclass(frozen=True)
class SearchHit:
    """One channel acesearch knows about, with what it says about it."""

    name: str
    content_id: str
    bitrate: int | None = None
    availability: float | None = None
    categories: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()

    @property
    def bitrate_text(self) -> str:
        """KB/s, which is how the channel's own catalog talks about bitrate."""
        return "" if self.bitrate is None else f"{self.bitrate // 1024} KB/s"

    @property
    def availability_text(self) -> str:
        """How often the searcher has found it up, 0-100 %; blank when it does not say."""
        return "" if self.availability is None else f"{self.availability * 100:.0f} %"

    @property
    def tags_text(self) -> str:
        """Categories and countries in one column, which is all the table has room for."""
        return " · ".join(self.categories + self.countries)


def search_url(base_url: str, query: str) -> str:
    """The searcher's playlist endpoint with `json=1`, which is the documented shape."""
    base = (base_url or "").strip().rstrip("/")
    return f"{base}/search.m3u?{urllib.parse.urlencode({'query': query, 'json': 1})}"


def _walk(value: Any, found: list[dict]) -> None:
    """Every object with an `infohash`, in the order it appears."""
    if isinstance(value, dict):
        if value.get("infohash"):
            found.append(value)
            return
        for item in value.values():
            _walk(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk(item, found)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _texts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(" ".join(str(item).split()) for item in value if str(item).strip())


def parse_hits(payload: Any, limit: int = DEFAULT_LIMIT) -> list[SearchHit]:
    """The answer of the searcher as hits: no repeated content id, no fake ones."""
    entries: list[dict] = []
    _walk(payload, entries)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for entry in entries:
        try:
            content_id = parse_content_id(str(entry.get("infohash")))
        except InvalidContentIdError:
            continue
        if content_id in seen:
            continue
        seen.add(content_id)
        hits.append(
            SearchHit(
                name=" ".join(str(entry.get("name") or "").split()) or content_id,
                content_id=content_id,
                bitrate=_int_or_none(entry.get("bitrate")),
                availability=_float_or_none(entry.get("availability")),
                categories=_texts(entry.get("categories")),
                languages=_texts(entry.get("languages")),
                countries=_texts(entry.get("countries")),
            )
        )
        if len(hits) >= limit:
            break
    return hits


class SearchClient:
    """A searcher's `/search.m3u`, with a timeout and one error type.

    Habla con `httpx`, que es lo que usa el cliente del engine: una sola forma de hablar HTTP en la
    app, y las pruebas de la página pueden interceptar la búsqueda como ya interceptan el engine.
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)

    def __enter__(self) -> "SearchClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False

    def close(self) -> None:
        self._http.close()

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list[SearchHit]:
        query = " ".join(str(query or "").split())[:MAX_QUERY_LENGTH]
        if not query:
            return []
        if not self.base_url:
            raise SearchError("no hay URL de búsqueda configurada")
        url = search_url(self.base_url, query)
        try:
            response = self._http.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchError(f"no responde en {self.base_url}: {exc}") from exc
        try:
            document = response.json()
        except ValueError as exc:
            raise SearchError(f"{url} no contestó JSON (¿es la URL de la búsqueda?)") from exc
        return parse_hits(document, limit)
