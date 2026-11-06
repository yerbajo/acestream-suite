"""ISO language and country codes: the value lists of the forms and their normalization."""

from functools import cache

import pycountry

from app.services.errors import ValidationError


def _by_name(names: dict[str, str]) -> dict[str, str]:
    """The same mapping with its entries ordered by name, which is how the selects want it."""
    return dict(sorted(names.items(), key=lambda pair: pair[1]))


@cache
def _languages() -> dict[str, str]:
    # Two-letter codes only: that is what a channel column holds.
    two_letter = {
        lang.alpha_2: lang.name for lang in pycountry.languages if hasattr(lang, "alpha_2")
    }
    return _by_name(two_letter)


@cache
def _countries() -> dict[str, str]:
    # "United Kingdom" rather than "United Kingdom of Great Britain and Northern Ireland":
    # the short name is the one people look for in a list.
    names = {c.alpha_2: getattr(c, "common_name", c.name) for c in pycountry.countries}
    return _by_name(names)


def language_choices() -> list[tuple[str, str]]:
    """(code, name) pairs, sorted by name, for a form select."""
    return list(_languages().items())


def country_choices() -> list[tuple[str, str]]:
    return list(_countries().items())


def language_name(code: str | None) -> str | None:
    return _languages().get(code) if code else None


def country_name(code: str | None) -> str | None:
    return _countries().get(code) if code else None


def normalize_language(value: str | None) -> str | None:
    """A lowercase ISO 639-1 code, or None when the value is blank."""
    code = (value or "").strip().lower()
    if code and code not in _languages():
        raise ValidationError(f"unknown language code '{value.strip()}' (use ISO 639-1, e.g. 'es')")
    return code or None


def normalize_country(value: str | None) -> str | None:
    """An uppercase ISO 3166-1 alpha-2 code, or None when the value is blank."""
    code = (value or "").strip().upper()
    if code and code not in _countries():
        raise ValidationError(
            f"unknown country code '{value.strip()}' (use ISO 3166-1 alpha-2, e.g. 'AR')"
        )
    return code or None
