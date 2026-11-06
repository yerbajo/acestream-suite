"""The engine's search index, read as channels this catalogue could take.

`acesearch` stands beside the engine and is the only way to find a channel the database does not
hold yet: `GET /search.m3u?query=…&json=1` answers a JSON list of entries, each carrying a name, an
infohash and whatever else the index measured. This module turns that answer into `SearchHit`s and
keeps the conversation with the index inside one class. It reads no database and knows no catalogue.

Two properties of somebody else's index shape everything here. Its answer is not trusted to be a
clean list — the live one ends with an empty object, and a URL that points at the engine instead
answers something that is not JSON at all — so the payload is walked for entries carrying an
infohash, and a body that does not parse is reported together with the URL that produced it. And a
search costs the other side real work, so a query is normalised, cut to `MAX_QUERY_LENGTH` and
never sent empty.

Only a 40-hex Content ID survives: that is how every page above this module addresses a stream, and
a hash the engine cannot open is not a channel anybody could add.
"""

import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.acestream.content_id import InvalidContentIdError, parse_content_id
from app.i18n import resolve, translate

# One query is one search on a machine somebody else runs for everybody: long text is not a bigger
# harvest, it is a slower answer.
MAX_QUERY_LENGTH = 100
DEFAULT_LIMIT = 60
DEFAULT_TIMEOUT = 15.0


class SearchError(Exception):
    """The index could not be reached, or answered something other than its list."""


@dataclass(frozen=True)
class SearchHit:
    """One channel the index knows, with as much of its measurement as it was willing to give.

    Every measurement is optional. The index has been seen omitting the bitrate and the
    availability, and a hit with neither is still a channel worth listing.
    """

    name: str
    content_id: str
    bitrate: int | None = None
    availability: float | None = None
    categories: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()

    @property
    def bitrate_text(self) -> str:
        """The bitrate in KB/s, the unit the catalogue's own pages print; blank when unmeasured."""
        return "" if self.bitrate is None else f"{self.bitrate // 1024} KB/s"

    @property
    def availability_text(self) -> str:
        """The share of the time the index saw it up, as a whole percentage; blank if silent."""
        return "" if self.availability is None else f"{self.availability * 100:.0f} %"

    @property
    def tags_text(self) -> str:
        """Categories and countries in the single column the results table spares for both."""
        return " · ".join(self.categories + self.countries)


def search_url(base_url: str, query: str) -> str:
    """The index's playlist endpoint asked for JSON, which is the shape this module parses.

    The JSON is a flag of that endpoint rather than a header, so it belongs in the query string next
    to the search itself; a trailing slash on the base would double the one before `search.m3u`.
    """
    base = (base_url or "").strip().rstrip("/")
    asked = urllib.parse.urlencode({"query": query, "json": 1})
    return f"{base}/search.m3u?{asked}"


def _entries(payload: Any) -> Iterator[dict]:
    """Every object of the answer that carries an `infohash`, in the order it appears.

    Walking the whole structure instead of indexing into it is what makes the module indifferent to
    the answer's shape: the empty object the index appends has no infohash and drops out here, and
    so would a wrapper object around the list, without either being a special case below.
    """
    if isinstance(payload, dict):
        if payload.get("infohash"):
            yield payload
            return
        for value in payload.values():
            yield from _entries(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _entries(value)


def _flat(value: Any) -> str:
    """One field as a single line, its runs of whitespace collapsed to one space."""
    return " ".join(str(value).split())


def _tags(value: Any) -> tuple[str, ...]:
    """A list field as one entry per non-blank tag, each on a single line."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(_flat(item) for item in value if _flat(item))


def _as_int(value: Any) -> int | None:
    """The value as a whole number, or None where the index wrote something that is not one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    """The value as a number, or None where the index wrote something that is not one."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hit(entry: dict) -> SearchHit | None:
    """One entry as a hit, or None when its hash is not a Content ID.

    A nameless entry takes its hash as the name: a row nobody can recognise is a row nobody can
    mark, and the hash is at least the thing that would be added.
    """
    try:
        content_id = parse_content_id(str(entry.get("infohash")))
    except InvalidContentIdError:
        return None
    return SearchHit(
        name=_flat(entry.get("name") or "") or content_id,
        content_id=content_id,
        bitrate=_as_int(entry.get("bitrate")),
        availability=_as_float(entry.get("availability")),
        categories=_tags(entry.get("categories")),
        languages=_tags(entry.get("languages")),
        countries=_tags(entry.get("countries")),
    )


def parse_hits(payload: Any, limit: int = DEFAULT_LIMIT) -> list[SearchHit]:
    """The index's answer as at most `limit` hits, one per Content ID.

    A repeated hash is one channel listed twice, and a hash that is not a Content ID is not a
    channel at all, so both are dropped before the limit starts counting. Neither is reported: the
    index's own noise is not something the person searching can act on.
    """
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for entry in _entries(payload):
        hit = _hit(entry)
        if hit is None or hit.content_id in seen:
            continue
        seen.add(hit.content_id)
        hits.append(hit)
        if len(hits) >= limit:
            break
    return hits


class SearchClient:
    """One index endpoint, with a timeout and a single error type for everything that goes wrong.

    It speaks `httpx` because the engine client does: the app has one way of talking HTTP, and a
    test intercepts the index exactly as it already intercepts the engine.
    """

    def __init__(
        self, base_url: str, timeout: float = DEFAULT_TIMEOUT, language: str | None = None
    ):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.timeout = timeout
        # The language its refusals are written in: they end up in the page that asked.
        self.language = language or resolve()
        self._http = httpx.Client(timeout=timeout)

    def __enter__(self) -> "SearchClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False

    def close(self) -> None:
        self._http.close()

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list[SearchHit]:
        """The hits for `query`, or nothing at all when there is nothing to ask.

        A blank query is not an error and never leaves the process: submitting the form with an
        empty box must not turn into a search for everything. A missing base URL is an error, since
        it means the settings pointed the page nowhere.
        """
        query = _flat(query or "")[:MAX_QUERY_LENGTH]
        if not query:
            return []
        if not self.base_url:
            raise SearchError(translate("no hay URL de búsqueda configurada", self.language))
        url = search_url(self.base_url, query)
        return parse_hits(self._answer(url), limit)

    def _answer(self, url: str) -> Any:
        """The index's body, decoded, or the error naming the step that failed.

        Transport and decoding fail differently and for different reasons — one is the index being
        down or slow, the other is the URL in the settings pointing at something that is not the
        index — so each gets its own sentence instead of one "no funciona".
        """
        try:
            response = self._http.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchError(
                translate("no responde en {base}: {detail}", self.language).format(
                    base=self.base_url, detail=exc
                )
            ) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise SearchError(
                translate(
                    "{url} no contestó JSON (¿es la URL de la búsqueda?)", self.language
                ).format(url=url)
            ) from exc
