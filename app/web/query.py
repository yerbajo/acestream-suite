"""Reading the list's filters and its order out of a URL.

Every value here was typed by somebody, bookmarked months ago or left over from another version,
so none of it is taken on trust: what the catalogue cannot honour is dropped and the page is drawn
as if it had not been asked. A list that still works under a wrong address bar is worth more than a
400 over `?sort=bogus`, and the filter bar then shows what is really applied.
"""

from starlette.datastructures import QueryParams

from app.db.models import CheckStatus, Resolution
from app.services.iso import country_name, language_name
from app.services.listing import DEFAULT_DESCENDING, UNCHECKED, UNKNOWN, ChannelQuery, SortKey

# What the dropdowns can ask for: every stored status and resolution, plus the one state each has
# that no row stores — never checked, and picture never measured.
_STATUSES = frozenset([*(status.value for status in CheckStatus), UNCHECKED])
_RESOLUTIONS = frozenset([*(resolution.value for resolution in Resolution), UNKNOWN])

_DIRECTIONS = ("asc", "desc")


def _number(raw: str) -> int | None:
    """A filter that names a row by id: anything that is not digits is not an id."""
    return int(raw) if raw.isdigit() else None


def _language(raw: str) -> str | None:
    """The code in the casing the catalogue keeps, or None when no language has it."""
    code = raw.strip().lower()
    return code if language_name(code) else None


def _country(raw: str) -> str | None:
    """The code in the casing the catalogue keeps, or None when no country has it."""
    code = raw.strip().upper()
    return code if country_name(code) else None


def _one_of(raw: str, allowed: frozenset[str]) -> str | None:
    return raw if raw in allowed else None


def _sort(raw: str) -> SortKey:
    try:
        return SortKey(raw)
    except ValueError:
        return SortKey.TITLE


def _descending(raw: str | None, sort: SortKey) -> bool:
    """`?dir=` wins when it names a direction; otherwise the column's own first click."""
    if raw in _DIRECTIONS:
        return raw == "desc"
    return DEFAULT_DESCENDING[sort]


def parse_query(params: QueryParams) -> ChannelQuery:
    """The filters and the order the URL asks for, without the parts that make no sense."""
    sort = _sort(params.get("sort", ""))
    return ChannelQuery(
        text=params.get("q", "").strip() or None,
        category_id=_number(params.get("category", "")),
        language=_language(params.get("language", "")),
        country=_country(params.get("country", "")),
        status=_one_of(params.get("status", ""), _STATUSES),
        resolution=_one_of(params.get("resolution", "").strip().lower(), _RESOLUTIONS),
        sort=sort,
        descending=_descending(params.get("dir"), sort),
    )
