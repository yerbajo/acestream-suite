"""The files the app ships for the browser: the favicon and the name every page shows.

The Windows packaging (`windows/`) is not part of the vendored app, so the checks of its spec,
its build script and its `.ico` are not here; these two read `app/web/static` and the pages.
"""

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def icon_sizes(path: Path) -> set[tuple[int, int]]:
    """Sizes in an .ico file, read from its directory (0 means 256)."""
    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), "not an icon file"
    sizes = set()
    for index in range(count):
        width, height = data[6 + index * 16], data[7 + index * 16]
        sizes.add((width or 256, height or 256))
    return sizes


def test_the_favicon_has_the_browser_sizes():
    assert icon_sizes(ROOT / "app" / "web" / "static" / "favicon.ico") >= {(16, 16), (32, 32)}


def test_pages_use_the_favicon(web):
    page = web.get("/").text

    # El `.ico` sigue siendo el respaldo; el navegador moderno usa el SVG, que es el mismo dibujo.
    assert re.search(r'<link rel="icon" type="image/svg\+xml" href="[^"]*/static/logo-mark\.svg', page)
    assert re.search(r'<link rel="icon" href="[^"]*/static/favicon\.ico\?v=[0-9a-f]{10}"[^>]*>', page)
    assert web.get("/static/favicon.ico").status_code == 200


def test_the_suite_names_itself_on_every_page(web):
    """S2: lo visible dice acestream-suite (rename, 2026-11-05)."""
    for path in ("/", "/import", "/settings", "/search"):
        page = web.get(path).text
        assert 'class="brand-logo"' in page, path
        assert "/static/logo-wordmark.png" in page, path
        # El nombre va en el `alt` del logotipo.
        assert 'alt="acestream-suite"' in page, path
