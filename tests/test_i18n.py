"""The i18n machinery: which language is in force, what a catalog holds, and what happens when it
does not hold anything.

The two guards at the end are the point of the module: a language whose catalog is missing a string
shows Spanish in the middle of its own page, and `translate` is called from templates, where an
exception is a blank page.
"""

import json
import logging
import re

import pytest

from app import i18n
from app.services.settings import AppSettings
from app.web.templating import templates

REFERENCE_FILE = i18n.CATALOG_DIR / "catalog.reference.json"

# Where the app's own texts live: the pages and the code that answers them.
APP_DIR = i18n.CATALOG_DIR.parent
TEMPLATES_DIR = APP_DIR / "web" / "templates"

# `t("…")` in a template and `translate("…", language)` in the app. Both read the key as a quoted
# literal on one line: a key written with escapes or built at run time cannot be checked by reading
# the file, and the guard says so rather than pretending it saw it.
_TEMPLATE_KEY = re.compile(r"(?<![\w.])t\(\s*(?:\"([^\"\\\n]*)\"|'([^'\\\n]*)')")
_APP_KEY = re.compile(r"(?<![\w.])translate\(\s*(?:\"([^\"\\\n]*)\"|'([^'\\\n]*)')")


def reference() -> set[str]:
    """The strings every translated language has to know."""
    return set(json.loads(REFERENCE_FILE.read_text(encoding="utf-8")))


def keys_used(pattern: re.Pattern[str], path) -> set[str]:
    """Every key a file names in a call the pattern recognises."""
    found = set()
    for match in pattern.finditer(path.read_text(encoding="utf-8")):
        found.add(match.group(1) if match.group(1) is not None else match.group(2))
    return found


def reference_entries() -> dict[str, str]:
    """The reference catalog as written: each key with the translation that says where to go."""
    return json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))


def placeholders(text: str) -> set[str]:
    """The `{name}` slots a text carries, which a translation has to keep."""
    return set(re.findall(r"\{(\w+)\}", text))


def assert_all_listed(used: dict[str, set[str]]) -> None:
    """Fail with the keys no catalog knows, and the files that use them.

    A key used but unlisted is the failure this exists for: the page would show Spanish in the
    middle of a translation, and nothing else would notice.
    """
    known = reference()
    missing = {
        key: sorted(str(path) for path in paths) for key, paths in used.items() if key not in known
    }
    assert not missing, "keys used but not in catalog.reference.json: " + repr(missing)


# The settings page's own hint. The reference catalog names it, so only one copy of that long
# sentence exists.
HINT = next(text for text in reference() if "ACESTREAM_SUITE_LANGUAGE" in text)

SETTINGS_PAGE_TEXTS = ("Ajustes", "Idioma", "Idioma de la interfaz", HINT, "Guardar")

# A settings form that is accepted as it stands, so a test can break exactly one box.
SETTINGS_FORM = {
    "engine_url": "http://127.0.0.1:6878",
    "engine_timeout": "15",
    "min_peers": "1",
    "check_timeout": "20",
    "screenshot_timeout": "30",
    "check_concurrency": "2",
    "vlc_path": "",
    "ffmpeg_path": "",
    "acestream_path": "",
}


def test_the_eight_languages_are_the_ones_of_the_readme():
    assert list(i18n.LANGUAGES) == ["es", "en", "fr", "it", "de", "ru", "zh", "ja"]
    assert i18n.LANGUAGES["es"] == "Español"
    assert i18n.LANGUAGES["zh"] == "中文"
    assert i18n.DEFAULT_LANGUAGE == "es"


def test_with_nothing_saved_and_nothing_exported_the_language_is_spanish(monkeypatch):
    monkeypatch.delenv("ACESTREAM_SUITE_LANGUAGE", raising=False)

    assert i18n.resolve() == "es"
    assert i18n.resolve(None) == "es"


def test_the_environment_names_the_language_while_nothing_is_saved(monkeypatch):
    monkeypatch.setenv("ACESTREAM_SUITE_LANGUAGE", "de")

    assert i18n.resolve() == "de"


def test_the_saved_language_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("ACESTREAM_SUITE_LANGUAGE", "de")

    assert i18n.resolve("fr") == "fr"


def test_an_unknown_saved_language_falls_through_to_the_environment(monkeypatch):
    monkeypatch.setenv("ACESTREAM_SUITE_LANGUAGE", "de")

    assert i18n.resolve("pt") == "de"
    assert i18n.resolve("") == "de"


def test_an_unknown_environment_falls_back_to_spanish(monkeypatch):
    monkeypatch.setenv("ACESTREAM_SUITE_LANGUAGE", "klingon")

    assert i18n.resolve() == "es"


def test_the_settings_default_to_the_language_in_force(monkeypatch):
    monkeypatch.setenv("ACESTREAM_SUITE_LANGUAGE", "it")

    assert AppSettings().language == "it"


def test_spanish_is_the_source_and_its_catalog_says_nothing():
    assert (i18n.CATALOG_DIR / "catalog.es.json").is_file()
    assert i18n.catalog("es") == {}
    assert i18n.translate("Guardar", "es") == "Guardar"


def test_a_language_nobody_translated_translates_nothing(catalogs):
    assert i18n.catalog("ja") == {}
    assert i18n.catalog("klingon") == {}
    assert i18n.translate("Guardar", "ja") == "Guardar"


def test_a_catalog_that_does_not_parse_is_empty_and_logged(catalogs, caplog):
    catalogs("en", "{no es json")

    with caplog.at_level(logging.WARNING, logger="app.i18n"):
        assert i18n.catalog("en") == {}

    assert "catalog.en.json" in caplog.text


def test_a_catalog_that_is_not_an_object_is_empty_and_logged(catalogs, caplog):
    catalogs("en", '["Guardar"]')

    with caplog.at_level(logging.WARNING, logger="app.i18n"):
        assert i18n.catalog("en") == {}

    assert "catalog.en.json" in caplog.text


def test_translate_returns_the_key_when_the_catalog_has_no_entry(catalogs):
    catalogs("en", {"Guardar": "Save"})

    assert i18n.translate("Guardar", "en") == "Save"
    assert i18n.translate("Ajustes", "en") == "Ajustes"


@pytest.mark.parametrize(
    "entries",
    [{"Guardar": None}, {"Guardar": 7}, {"Guardar": ""}, {"Guardar": {"a": "b"}}, {}],
)
def test_translate_never_raises_and_never_returns_none(catalogs, entries):
    """A value that is not text is a broken entry: the key reads better than a Python repr."""
    catalogs("en", entries)

    assert i18n.translate("Guardar", "en") == "Guardar"


def test_t_translates_a_template_rendered_without_a_request(catalogs, monkeypatch):
    """The global reads the language from the page's context, and a bare render has none."""
    catalogs("en", {"Guardar": "Save"})
    monkeypatch.setenv("ACESTREAM_SUITE_LANGUAGE", "en")

    assert templates.env.from_string("{{ t('Guardar') }}").render() == "Save"
    assert templates.env.from_string("{{ t('Ajustes') }}").render() == "Ajustes"


def test_the_reference_catalog_names_the_texts_of_the_settings_page():
    page = (i18n.CATALOG_DIR.parent / "web" / "templates" / "settings.html").read_text("utf-8")

    assert set(SETTINGS_PAGE_TEXTS) <= reference()
    assert all(text in page for text in SETTINGS_PAGE_TEXTS)


def test_a_page_draws_its_own_texts_through_the_catalog(web, catalogs, monkeypatch):
    """The templates' half of the seam: the list's heading is a key, not a Spanish literal."""
    catalogs("en", {"Canales": "Channels", "Agregar canal": "Add channel"})
    monkeypatch.setenv("ACESTREAM_SUITE_LANGUAGE", "en")

    page = web.get("/").text

    assert "<h1>Channels</h1>" in page
    assert "Add channel" in page
    assert "<h1>Canales</h1>" not in page


def test_a_rejected_form_is_explained_in_the_language_in_force(web, catalogs, monkeypatch):
    """The Python half: a form has no request, so the route hands it the language it is read in."""
    catalogs(
        "en",
        {
            "Debe ser {kind} entre {low} y {high}.": "{kind} between {low} and {high} required.",
            "un número entero": "a whole number",
        },
    )
    monkeypatch.setenv("ACESTREAM_SUITE_LANGUAGE", "en")

    response = web.post("/settings", data={**SETTINGS_FORM, "min_peers": "x"})

    assert response.status_code == 422
    assert "a whole number between 0 and 1000 required." in response.text


def test_every_translated_language_has_exactly_the_reference_keys():
    """S6: a catalog with a gap, or with a string nobody wrote, fails here and not in production."""
    expected = reference()
    assert expected, "the reference catalog is what every language is measured against"

    for language in i18n.LANGUAGES:
        # Spanish is the source: its catalog is the empty fallback that hands the key back.
        if language == i18n.DEFAULT_LANGUAGE:
            continue
        path = i18n.CATALOG_DIR / f"catalog.{language}.json"
        if not path.is_file():
            continue
        assert set(json.loads(path.read_text(encoding="utf-8"))) == expected, path.name


def test_every_translation_keeps_the_slots_its_key_carries():
    """A translation that loses `{count}` would make `.format()` raise inside a page.

    The templates and the routes fill these slots after translating, so the placeholders are part
    of the contract, not decoration: the same set has to come back, in whatever order.
    """
    catalogs = {"reference": reference_entries()}
    for language in i18n.LANGUAGES:
        path = i18n.CATALOG_DIR / f"catalog.{language}.json"
        if language != i18n.DEFAULT_LANGUAGE and path.is_file():
            catalogs[language] = json.loads(path.read_text(encoding="utf-8"))

    for name, entries in catalogs.items():
        for key, value in entries.items():
            assert placeholders(value) == placeholders(key), (name, key, value)


def test_every_key_a_template_uses_is_in_the_reference_catalog():
    """S6: the other half of the guard — a page cannot ask for a text no catalog lists.

    Read from the files, not from a list kept by hand: a new `t("…")` in any template is measured
    the moment it is written.
    """
    used: dict[str, set[str]] = {}
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        for key in keys_used(_TEMPLATE_KEY, path):
            used.setdefault(key, set()).add(path)

    assert used, "no template translates anything: the scan is looking in the wrong place"
    assert_all_listed(used)


def test_every_key_the_app_translates_is_in_the_reference_catalog():
    """S6 for the Python side: the messages a route or a form hands to a page."""
    used: dict[str, set[str]] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for key in keys_used(_APP_KEY, path):
            used.setdefault(key, set()).add(path.relative_to(APP_DIR))

    assert used, "no module translates anything: the scan is looking in the wrong place"
    assert_all_listed(used)
