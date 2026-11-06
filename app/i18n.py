"""The languages the interface speaks, and the catalog that says what each one shows.

A text is written once, in Spanish, exactly as it reads on the page, and a catalog maps that text
to its translation — the key *is* the Spanish sentence, as gettext does it:

    {"Guardar": "Save"}

Spanish is the source, so `es` needs no entries at all: whatever the catalog does not have falls
back to the key, which is already the Spanish sentence, and the app's own tests keep asserting it.
Each language has its catalog in `app/i18n/catalog.<code>.json`, and `catalog.reference.json`
names every string worth translating: a language missing one of those keys shows Spanish in the
middle of its own page, which is why the test suite compares the key sets.

Which language is in force is decided per request (`resolve`), never once at import: a language
saved in the settings page applies to the next page without restarting anything.

Nothing here imports `app.web` or `app.services` on purpose: the services read this module, and a
module that read them back could not be imported by a service.
"""

import json
import logging
from functools import cache
from pathlib import Path

from app.config import env

logger = logging.getLogger(__name__)

# The eight languages of the interface, in the order the selector shows them, each named in its own
# language: somebody who reads only Chinese has to recognise their own row.
LANGUAGES: dict[str, str] = {
    "es": "Español",
    "en": "English",
    "fr": "Français",
    "it": "Italiano",
    "de": "Deutsch",
    "ru": "Русский",
    "zh": "中文",
    "ja": "日本語",
}

DEFAULT_LANGUAGE = "es"

# The catalogs travel with the program, next to this module: they are not something the user edits
# in the data folder, and the frozen executable carries them inside itself.
CATALOG_DIR = Path(__file__).resolve().parent / "i18n"


@cache
def catalog(language: str) -> dict[str, str]:
    """Everything `language` knows how to say; empty when that file exists but says nothing.

    A language nobody has translated yet (no file) and a file that does not parse both cost their
    translations and nothing else: the Spanish text shows through instead of a blank box or a
    traceback. Only a broken file is worth a line in the log — a missing one is a language still
    to come, and Spanish has no entries by design.
    """
    path = CATALOG_DIR / f"catalog.{language}.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("catalog %s is unreadable: %s", path, exc)
        return {}
    if not isinstance(entries, dict):
        logger.warning("catalog %s is not an object of texts", path)
        return {}
    # A value that is not text is a broken entry, not a translation: the key reads better than
    # whatever JSON put there.
    return {key: value for key, value in entries.items() if isinstance(value, str)}


def translate(text: str, language: str) -> str:
    """`text` in `language`, or `text` itself when that language has no entry for it."""
    return catalog(language).get(text) or text


def resolve(saved: str | None = None) -> str:
    """The language in force: the saved one, else the environment, else Spanish.

    An answer that is not one of the eight — a typo, a row written by an older version — is worth
    no error page: it falls through to the next answer, and the last one always works.
    """
    for candidate in (saved, env("LANGUAGE")):
        if candidate in LANGUAGES:
            return candidate
    return DEFAULT_LANGUAGE
