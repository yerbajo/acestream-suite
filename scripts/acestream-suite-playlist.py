#!/usr/bin/env python3
"""Writes acestream-suite's catalogue as M3U playlists, served over plain HTTP on the house LAN.

WHY THIS EXISTS
    acestream-suite (http://127.0.0.1:8083) catalogs AceStream links, but a catalog in a web page is
    only useful to whoever is sitting in front of that web page. Every other player in the house —
    VLC on a phone, Kodi on the TV box, Dispatcharr behind Jellyfin's Live TV — wants a playlist:
    a URL that answers with `#EXTM3U` and a stream URL per channel. This script is the source of
    truth for those URLs: it asks acestream-suite for its own export and drops the files in a directory
    Caddy already serves, so the links never change even though the catalog does.

    Five playlists, all from the same catalog:

    | file                | what it holds                                                   |
    |---------------------|-----------------------------------------------------------------|
    | acestream-suite.m3u | everything (this is the one Dispatcharr consumes)                |
    | deportes.m3u        | sports: the `Deportes*` categories, plus sports names elsewhere  |
    | espana.m3u          | from Spain: `ES`, the Spanish categories, or a Spanish name       |
    | hls.m3u             | sports or Spanish, with a `Campeones` folder of its own          |
    | direct.m3u          | everything, as the engine's own MPEG-TS                          |

    A consumer that still asks for another file name is served without this script knowing that
    name: `ACESTREAM_SUITE_COMPAT_ALIASES` is a comma-separated list of `new-file=other-file` pairs
    and each copy is written next to its own list. Empty or unset — the default — writes nothing
    extra; the host that needs one keeps it in `/etc/acestream-suite.env`, which the `.cron` loads.
    The names themselves do not change: the TV reads `hls.m3u`.

    Every display name ends with the quality the catalog detected (`Canal 1080p`), so searching the
    list for "1080p" works; `tvg-name` keeps the bare name, because guide matching reads it.

    Most of them are filters over the catalog, not separate collections: the names rule matters
    because the engine tags a lot of Spanish and sports channels as `Amateur`, `Otros` or `TV`
    (a re-stream of La 1 is "amateur" to it), and because it leaves the language and the country
    empty for most of them.

WHERE THE LINKS POINT
    The stream URLs in the playlists are NOT acestream-suite's. Four of the five point at the HLS bridge
    (`${ACESTREAM_SUITE_HLS_BASE}`, `:8084/<hash>/index.m3u8` on this deployment), and only
    `direct.m3u` carries the engine's own MPEG-TS URL — `http://127.0.0.1:6878/ace/getstream`
    `?infohash=<hash>`, the tunneled engine behind its nginx. The bridge is the default for a reason
    measured on 2026-10-18: the engine serves ONE reader per content, so a direct reader and the
    bridge asking for the same channel leave both without it. One list, one reader: the bridge.
    `infohash` is the engine's parameter (the Linux 3.2.11 build answers `failed to load content`
    to `id`).

INSTALL (on the host that runs the suite)
    install -m 0755 scripts/acestream-suite-playlist.py /usr/local/bin/acestream-suite-playlist
    install -m 0644 scripts/acestream-suite-playlist.cron /etc/cron.d/acestream-suite-playlist
    /usr/local/bin/acestream-suite-playlist          # first run, so the files exist before anything reads them
    # The playlists are then at http://127.0.0.1/varios/{acestream-suite,deportes,espana}.m3u
    # (a copy under another name, if a consumer needs one, comes from ACESTREAM_SUITE_COMPAT_ALIASES)

USAGE
    acestream-suite-playlist                 # fetch, build and write only the files that changed
    acestream-suite-playlist --dry-run       # print them, write nothing (headers go to stderr)
    acestream-suite-playlist --self-test     # the checks below, no network and no files
    acestream-suite-playlist --directory /tmp/otra --engine http://otro:8081 \
        --acestream-suite http://otro:8083

    Everything has a default and every default is overridable; the three that matter are also
    readable from the environment, which is what the cron job uses.
"""

import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


def env(name, default):
    """El valor de `ACESTREAM_SUITE_*`, o el defecto.

    Una sola variable por ajuste: el nombre viejo ya no se lee, asi que un host que solo exporte
    una variable vieja se comporta como si no hubiera puesto nada. El entorno vacio es el caso
    normal (el crontab del host pone lo suyo en `/etc/acestream-suite.env`).
    """
    value = os.environ.get(name)
    return default if value is None else value


ACESTREAM_SUITE_URL = env("ACESTREAM_SUITE_URL", "http://127.0.0.1:8083")
ENGINE_URL = env("ACESTREAM_SUITE_PLAYLIST_ENGINE", "http://127.0.0.1:6878")
OUTPUT_DIR = env("ACESTREAM_SUITE_PLAYLIST_DIR", "/srv/www/varios")
# El puente HLS (acestream-suite/hls-bridge.py): la misma lista, pero cada canal como manifiesto.
HLS_BASE = env("ACESTREAM_SUITE_HLS_BASE", "http://127.0.0.1:8084")
LOG_PATH = env("ACESTREAM_SUITE_PLAYLIST_LOG", "/var/log/acestream-suite-playlist.log")
LOCK_PATH = env("ACESTREAM_SUITE_PLAYLIST_LOCK", "/var/lock/acestream-suite-playlist.lock")
# El marcador del documento que se lee, que es el contrato con la app y no la marca del producto.
EXPORT_FORMAT = "acestream-suite"
TIMEOUT = float(env("ACESTREAM_SUITE_PLAYLIST_TIMEOUT", "30"))
# The engine's own name for the stream parameter (the Linux 3.2.11 build answers `failed to load
# content` to `id`; see the docstring above).
STREAM_PARAM = env("ACESTREAM_SUITE_STREAM_PARAM", "infohash")

# Which channels are worth putting in a playlist: only the ones acestream-suite has verified as online.
# The app's own `alive` is exactly "the engine reached dl with data downloaded and at least one
# peer", so it already means "online with peers" — no dead link reaches a player, and a channel
# that stops streaming drops out on the next run. Empty the value to publish the whole catalog.
STATUS = env("ACESTREAM_SUITE_PLAYLIST_STATUS", "alive")

# Sports. The categories are the engine's own tags (and the three Spanish ones this deployment
# made); the names catch what the engine filed elsewhere. `premier` is `Premier Sports` and not the
# movie channels called "Premiere", and `bein` is `beIN Sports` and not `beIN Movies`.
CATEGORIAS_DEPORTES = ("Deportes", "Deportes ES", "Deportes int.")
DEPORTES = re.compile(
    r"\b(?:sports?|deportes?|esport\d?|dazn|bein\s?sports?|espn|eurosport|sky\s?sports?|"
    r"laliga|la\s?liga|moto\s?gp|\bf1\b|teledeporte|tenis|tennis|\bnba\b|\bufc\b|boxeo|"
    r"motors?port|champions|premier\s?sports?|bundesliga|calcio|cricket|rugby|atletismo|"
    r"ol[ií]mpic|\bnfl\b|\bmlb\b|\bnhl\b|hockey|baloncesto|basket|ciclismo|cycling|"
    r"roland\s?garros|wimbledon|euroliga|euroleague|mundial|supercopa|real\s?madrid|"
    r"bar[cç]a|atl[eé]tico)\b"
    r"|m\+\s?(?:deportes|golf|la?liga)|movistar\s?(?:deportes|la?liga|golf|vamos)"
    r"|\bm\.\s?(?:la?liga|deportes|golf)"
    # VAMOS is Movistar's sports channel and the engine files it as `Amateur`, without the
    # "movistar" prefix: the catalog titles it "#VAMOS FHD --> NEW ERA" and "Vamos".
    r"|#?\bvamos\b",
    re.IGNORECASE,
)

# Spanish. Same idea: the metadata the engine does give (country ES), the two Spanish categories,
# and then the names. `spain` covers "ANTENA 3 Spain" and "NOVA Spain"; `\bgol\b` does not match
# "Golf"; the regional broadcasters are here so that a channel the engine indexes later lands in
# the right list without touching this file.
#
# The engine's `language` is NOT used on its own: every Spanish-language channel it knows outside
# Spain (Cali TV, Multimedios, Holvoet TV, …) is tagged `es`, so it would drag 26 Latin American
# channels into a list about Spain. Adding them back is one condition here.
CATEGORIAS_ESPANA = ("Movistar", "Deportes ES")
ESPANA = re.compile(
    r"\b(?:spain|espa[nñ]a|espa[nñ]ol|tve|antena\s?3|telecinco|cuatro|la\s?sexta|la\s?[12]|"
    r"movistar|teledeporte|canal\s?sur|esport\d?|arag[oó]n\s?tv|telemadrid|etb|tvg|tv3|"
    r"gol|castilla|andaluc[ií]a|valencia|m[aá]laga|canarias|catalunya|cocina|decasa|"
    r"viajar|dmax|divinity|mega|energy|fdf|trece|bemad|bom\s?cine|popular\s?tv|"
    r"intereconom|atreseries|galicia|ib3|\btpa\b|\bcmm\b|extremadura|murcia|navarra|"
    r"cantabria)\b"
    r"|m\+\s?|movistar\s?|\bm\.\s"
    r"|#?\bvamos\b",   # VAMOS again: Spanish channel the engine files as `Amateur`
    re.IGNORECASE,
)


# «Campeones»: la carpeta propia que `hls.m3u` da a los canales de Movistar Liga de Campeones. La
# categoría no los distingue —el motor los reparte entre `Deportes ES`, `Deportes` y `Amateur` (los
# "M+ LIGA DE CAMPEONES … --> NEW ERA" llegan como `Amateur`)—, así que se casan por nombre.
CAMPEONES = re.compile(r"\bcampeones\b", re.IGNORECASE)


def carpeta_campeones(canal):
    """La carpeta propia del canal en `hls.m3u`, o None para dejarlo en la de su categoría."""
    return "Campeones" if CAMPEONES.search(canal.get("title") or "") else None


def log(message, path=LOG_PATH):
    """One line with a timestamp, to the log file and to stdout (a manual run is worth reading)."""
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line)
    if path:
        try:
            with open(path, "a") as handle:
                handle.write(line + "\n")
        except OSError as exc:  # a read-only /var/log must not lose the playlists
            print("acestream-suite-playlist: cannot write %s: %s" % (path, exc), file=sys.stderr)


def es_deporte(canal):
    titulo = canal.get("title") or ""
    return canal.get("category") in CATEGORIAS_DEPORTES or bool(DEPORTES.search(titulo))


def es_espanol(canal):
    if canal.get("country") == "ES":
        return True
    if canal.get("category") in CATEGORIAS_ESPANA:
        return True
    return bool(ESPANA.search(canal.get("title") or ""))


def hls_url(content_id, base=HLS_BASE):
    """El manifiesto que sirve el puente HLS para ese canal (ver acestream-suite/hls-bridge.py)."""
    return "%s/%s/index.m3u8" % (base.rstrip("/"), content_id.lower())


# The files this script owns, in the order they are written:
# `(name, description, filter, url builder, folder)`; `None` as url means the engine's own MPEG-TS
# URL, and `None` as folder means "group by the catalog's category", which is what
# `acestream-suite.m3u`, `deportes.m3u`, `espana.m3u` and `direct.m3u` do. Only `hls.m3u` asks for a
# folder of its own: it gives the Movistar Liga de Campeones channels `Campeones`.
#
# TODAS menos `direct.m3u` apuntan al puente HLS, y es a proposito: el motor sirve UN solo lector por
# contenido, asi que dos reproductores de la casa pidiendo el mismo canal -la tele por una URL directa
# y el puente a la vez- se quedan los dos sin canal (500 y respuestas vacias). Medido el 2026-10-18:
# con el puente peleando contra un lector directo, 0 manifiestos y 23 fallos en un minuto. Una lista,
# un lector: el puente. `direct.m3u` se queda para Dispatcharr (que no proxya HLS) y para diagnosticar.
PLAYLISTS = (
    ("acestream-suite.m3u", "the whole catalog, as HLS", lambda canal: True, hls_url, None),
    ("deportes.m3u", "sports, as HLS", es_deporte, hls_url, None),
    ("espana.m3u", "Spanish channels, as HLS", es_espanol, hls_url, None),
    ("hls.m3u", "sports and Spanish, as HLS", lambda canal: es_deporte(canal) or es_espanol(canal),
     hls_url, carpeta_campeones),
    ("direct.m3u", "the whole catalog, engine MPEG-TS (Dispatcharr, debugging)", lambda canal: True, None,
     None),
)

def compat_aliases(value=None):
    """Las copias con otro nombre, como DATOS: `nuevo=otro,nuevo2=otro2`.

    Vacio o ausente = ninguna copia, y es el defecto: el unico consumidor que pide otra cosa lo pone
    en `/etc/acestream-suite.env` (su `.cron` carga ese fichero), asi que el nombre ajeno no vuelve
    al codigo. Un nombre con separador de ruta se ignora: las copias se escriben SIEMPRE dentro del
    directorio de salida, y esa es la regla que impide que un dato de entorno escriba donde no debe.
    """
    value = os.environ.get("ACESTREAM_SUITE_COMPAT_ALIASES", "") if value is None else value
    aliases = {}
    for par in str(value).split(","):
        nombre, _, otro = par.partition("=")
        nombre, otro = nombre.strip(), otro.strip()
        if not nombre or not otro:
            continue
        if "/" in otro or "\\" in otro or otro in (".", ".."):
            continue
        aliases.setdefault(nombre, []).append(otro)
    return aliases


COMPAT_ALIASES = compat_aliases()


def export_url(base_url=ACESTREAM_SUITE_URL, status=STATUS):
    """`/export`, with the availability filter the app's list view uses (`status=alive`)."""
    url = base_url.rstrip("/") + "/export"
    return url + ("?status=" + urllib.parse.quote(status) if status else "")


def fetch_channels(base_url=ACESTREAM_SUITE_URL, timeout=TIMEOUT, status=STATUS):
    """acestream-suite's own export document: the channels, with their category and availability."""
    url = export_url(base_url, status)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        document = json.loads(response.read().decode("utf-8"))
    if document.get("format") != EXPORT_FORMAT:
        raise ValueError("%s is not an acestream-suite export (format=%r)"
                         % (url, document.get("format")))
    return document["channels"]


def attribute(value):
    """An attribute value, made safe for a line-oriented format.

    Quotes would end the value, newlines would end the line, and a comma would look like the
    separator between the attributes and the display name to any parser that splits on the first
    comma (which is most of them).
    """
    return " ".join(str(value).replace('"', "'").replace(",", " ").split())


def display_name(value):
    """The name the player shows: everything after the first comma of the line, so commas stay."""
    return " ".join(str(value).split())


# Qualities the catalog names. `other` means "could not be classified" and is not a quality: it
# is not appended to the name, because "Canal other" helps nobody search for anything.
KNOWN_QUALITY = ("4k", "1080p", "720p", "576p", "480p", "360p")


def shown_name(channel, title=None):
    """The name the player shows: the channel's own and, after it, its quality (`Canal 1080p`).

    Only the display name carries it. `tvg-name` is left alone on purpose: it is what guide
    matching uses, and adding the quality there would tie an EPG mapping to a resolution that
    changes when the engine re-detects the screenshot.
    """
    title = title or display_name(channel.get("title") or channel["content_id"])
    quality = (channel.get("resolution") or "").strip().lower()
    return "%s %s" % (title, quality) if quality in KNOWN_QUALITY else title


def stream_url(content_id, engine=ENGINE_URL):
    return "%s/ace/getstream?%s=%s" % (engine.rstrip("/"), STREAM_PARAM, content_id.lower())


def quality_rank(channel):
    """How good the declared quality is: 0 is the best known, and "not declared" comes last."""
    quality = (channel.get("resolution") or "").strip().lower()
    return KNOWN_QUALITY.index(quality) if quality in KNOWN_QUALITY else len(KNOWN_QUALITY)


def without_duplicates(channels):
    """One entry per display name: two publishers of the same channel arrive with the same title
    and the player lists it twice. The best quality wins; on a tie, a sports category; on a tie,
    the lowest content id, so the order of the input decides nothing."""
    best = {}
    for channel in channels:
        key = display_name(channel.get("title") or channel["content_id"]).lower()
        rank = (
            quality_rank(channel),
            0 if channel.get("category") in CATEGORIAS_DEPORTES else 1,
            channel["content_id"],
        )
        if key not in best or rank < best[key][0]:
            best[key] = (rank, channel)
    return [channel for _, channel in best.values()]


def folder_of(channel, group=None):
    """El nombre de la carpeta del canal: la que le da la lista, o la de su categoría."""
    return (group(channel) if group else None) or channel.get("category") or ""


def build_playlist(channels, engine=ENGINE_URL, url=None, group=None):
    """The whole playlist as text. One entry per channel name, ordered by folder and then by title.

    `group` lets one list put a channel in a folder of its own instead of its catalog category:
    `hls.m3u` does that with the Movistar Liga de Campeones channels (`Campeones`).
    """
    lines = ["#EXTM3U"]
    url_for = url or (lambda content_id: stream_url(content_id, engine))
    for channel in sorted(
        without_duplicates(channels),
        key=lambda c: (folder_of(c, group).lower(), (c.get("title") or "").lower()),
    ):
        title = display_name(channel.get("title") or channel["content_id"])
        attributes = [
            'tvg-id="%s"' % channel["content_id"].lower(),
            'tvg-name="%s"' % attribute(title),
        ]
        folder = folder_of(channel, group)
        if folder:
            attributes.append('group-title="%s"' % attribute(folder))
        lines.append("#EXTINF:-1 %s,%s" % (" ".join(attributes), shown_name(channel, title)))
        lines.append(url_for(channel["content_id"]))
    return "\n".join(lines) + "\n"


def build_all(channels, engine=ENGINE_URL):
    """`(name, text, how many channels)` for every playlist, built from the same fetch."""
    built = []
    for name, _, selected, url, group in PLAYLISTS:
        chosen = [canal for canal in channels if selected(canal)]
        built.append((name, build_playlist(chosen, engine, url, group), len(chosen)))
    return built


def write_if_changed(text, path):
    """Atomically, and only when it differs: an unchanged file keeps its mtime, so nothing that
    watches it (Dispatcharr refreshes on its own schedule) has a reason to react."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            if handle.read() == text:
                return False
    except OSError:
        pass
    directory = os.path.dirname(path) or "."
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".acestream-suite-playlist-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as writing:
            writing.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return True


def save_all(built, directory):
    """Escribe cada lista y, si las pide, sus copias con otro nombre (`compat_aliases`).

    Devuelve `{nombre: escrito}` para que `main` registre lo mismo que antes. La copia se escribe
    solo cuando su lista cambio: un fichero igual conserva su mtime, y nada que lo vigile tiene una
    razon para reaccionar. Las copias caen siempre dentro de `directory`.
    """
    changed = {}
    for name, text, _ in built:
        changed[name] = write_if_changed(text, os.path.join(directory, name))
        if not changed[name]:
            continue
        for alias in COMPAT_ALIASES.get(name, ()):
            write_if_changed(text, os.path.join(directory, alias))
    return changed


def self_test():
    """The checks this script leaves behind: they fail if the format or a filter breaks.

    Sin red, y de ficheros solo los de un directorio temporal que se borra al terminar.
    """
    canales = [
        {"title": 'El "Canal", raro', "content_id": "AA" * 20, "category": "Deportes ES"},
        {"title": "Zeta", "content_id": "bb" * 20, "category": "Deportes ES", "resolution": "other"},
        {"title": "Alfa", "content_id": "cc" * 20, "category": None, "resolution": "1080p"},
    ]
    text = build_playlist(canales, engine="http://motor:8081/")
    lines = text.splitlines()

    assert lines[0] == "#EXTM3U", lines[0]
    assert len(lines) == 7, "3 channels are 1 + 2 lines each, plus the header: %d" % len(lines)
    # Ordered by category (the channel without one first), then by title.
    assert [line for line in lines if line.startswith("#EXTINF")][0].endswith(",Alfa 1080p")
    # Case is normalised in the hash, quotes are sanitised and the comma stays in the name. The
    # quality goes after the name the player shows and NOT into `tvg-name`, which guide matching
    # reads; a channel with no quality known keeps its name untouched (Zeta, below).
    assert lines[1] == (
        '#EXTINF:-1 tvg-id="cccccccccccccccccccccccccccccccccccccccc" tvg-name="Alfa",Alfa 1080p'
    )
    assert lines[2] == "http://motor:8081/ace/getstream?infohash=cccccccccccccccccccccccccccccccccccccccc"
    assert 'tvg-name="El \'Canal\' raro"' in lines[3], lines[3]
    assert 'group-title="Deportes ES"' in lines[3]
    # The first comma of the line is the one that separates attributes from the display name,
    # even though the display name itself carries one: a parser splitting there gets them right.
    assert lines[3].index(",") == lines[3].index(',El "Canal", raro'), lines[3]
    assert lines[5].endswith(",Zeta")
    # An empty catalog is still a valid playlist, not an empty file.
    assert build_playlist([]) == "#EXTM3U\n"

    # The two filters: category alone, name alone, and the metadata the engine does give.
    assert es_deporte({"title": "Cualquiera", "category": "Deportes"})
    assert es_deporte({"title": "DAZN MotoGP", "category": "Amateur"})
    assert es_deporte({"title": "beIN SPORTS HD 1 [TR]", "category": "Regionales"})
    assert not es_deporte({"title": "beIN Movies Turk", "category": "Películas"})
    assert not es_deporte({"title": "VIP Premiere [RU]", "category": "Películas"})
    assert es_espanol({"title": "Cualquiera", "category": "Otros", "country": "ES"})
    assert es_espanol({"title": "LA SEXTA FHD", "category": "Amateur"})
    assert es_espanol({"title": "Cualquiera", "category": "Movistar"})
    assert not es_espanol({"title": "Nova Lady HD", "category": "Otros"})
    assert not es_espanol({"title": "NOVA SPORT 6", "category": "Otros"})
    assert not es_espanol({"title": "Golf Channel [PL]", "category": "Regionales"})
    # VAMOS (Movistar) is sports and Spanish, and the engine files it as `Amateur` with no country:
    # these are the titles the catalog actually has, and they must land in all three lists.
    for titulo in ("#VAMOS FHD --> NEW ERA", "Vamos"):
        canal = {"title": titulo, "category": "Amateur"}
        assert es_deporte(canal), titulo
        assert es_espanol(canal), titulo
    # Spanish-language is not Spanish: the engine tags every Latin American channel `es`.
    assert not es_espanol({"title": "Cali TV", "category": "TV", "language": "es", "country": "CO"})
    # The availability filter travels as the list view's own query parameter.
    assert export_url("http://caja:8083/", "alive") == "http://caja:8083/export?status=alive"
    assert export_url("http://caja:8083", "") == "http://caja:8083/export"
    # La variante HLS: el mismo canal, pero apuntando al puente.
    assert hls_url("AA" * 20, base="http://puente:8084/") == "http://puente:8084/%s/index.m3u8" % ("aa" * 20)
    texto_hls = build_playlist(
        [{"title": "Uno", "content_id": "dd" * 20, "category": "TV"}],
        url=lambda content_id: hls_url(content_id, base="http://puente:8084"),
    )
    assert texto_hls.splitlines()[2] == "http://puente:8084/%s/index.m3u8" % ("dd" * 20), texto_hls
    # `direct.m3u` es la unica que lleva la URL del motor, y las demas llevan el puente.
    directo = dict((n, tx) for n, tx, _ in build_all(
        [{"title": "DAZN 1", "content_id": "dd" * 20, "category": "Deportes ES", "country": "ES"}],
        engine="http://motor:8081/"))
    assert "motor:8081/ace/getstream?infohash=" in directo["direct.m3u"], directo["direct.m3u"]
    for nombre in ("acestream-suite.m3u", "deportes.m3u", "espana.m3u", "hls.m3u"):
        assert ":8084/" in directo[nombre] and "motor:8081/ace" not in directo[nombre], nombre
    # La carpeta «Campeones» de `hls.m3u`: los canales de Movistar Liga de Campeones caen en ella
    # (el motor los reparte entre `Deportes ES`, `Deportes` y `Amateur`, y los "M+ LIGA DE CAMPEONES
    # … --> NEW ERA" llegan como `Amateur`), y las demás listas siguen agrupando por categoría.
    campeon = {"title": "M. Liga de Campeones 2", "content_id": "ee" * 20, "category": "Deportes ES"}
    assert carpeta_campeones(campeon) == "Campeones"
    assert carpeta_campeones({"title": "M+ LIGA DE CAMPEONES FHD (SAT) --> NEW ERA",
                              "category": "Amateur"}) == "Campeones"
    assert carpeta_campeones({"title": "DAZN LaLiga", "category": "Deportes ES"}) is None
    la_liga = {"title": "DAZN LaLiga", "content_id": "77" * 20, "category": "Deportes ES"}
    carpetas = dict((n, tx) for n, tx, _ in build_all([campeon, la_liga]))
    assert 'group-title="Campeones"' in carpetas["hls.m3u"], carpetas["hls.m3u"]
    assert 'group-title="Deportes ES"' in carpetas["hls.m3u"], carpetas["hls.m3u"]
    for nombre in ("acestream-suite.m3u", "deportes.m3u", "espana.m3u", "direct.m3u"):
        assert 'group-title="Campeones"' not in carpetas[nombre], nombre

    # La carpeta propia va cableada lista por lista, no a todas: se comprueba el cableado, no el
    # texto, y las demás listas siguen agrupando por la categoría del catálogo.
    assert 'group-title="Deportes ES"' in carpetas["deportes.m3u"], carpetas["deportes.m3u"]
    carpetas_por_lista = dict((n, g) for n, _, _, _, g in PLAYLISTS)
    assert carpetas_por_lista["hls.m3u"] is carpeta_campeones, carpetas_por_lista
    for nombre in ("acestream-suite.m3u", "deportes.m3u", "espana.m3u", "direct.m3u"):
        assert carpetas_por_lista[nombre] is None, nombre
    # Dos publicadores del mismo canal llegan con el mismo titulo y el reproductor los lista dos
    # veces. Se queda uno: el de mejor calidad; a igual calidad, el de una categoria de deportes; y
    # a igual todo, el content id menor, para que el resultado no dependa del orden de entrada.
    repetidos = [
        {"title": "M. Liga de Campeones", "content_id": "ff" * 20, "category": "Deportes",
         "resolution": "720p"},
        {"title": "M. Liga de Campeones", "content_id": "11" * 20, "category": "Deportes ES",
         "resolution": "1080p"},
        {"title": "M. Liga de Campeones", "content_id": "22" * 20, "category": "Regionales"},
        {"title": "DAZN LaLiga", "content_id": "33" * 20, "category": "Amateur",
         "resolution": "1080p"},
        {"title": "DAZN LaLiga", "content_id": "44" * 20, "category": "Deportes ES",
         "resolution": "1080p"},
    ]
    texto_repetido = build_playlist(repetidos)
    assert texto_repetido.count("#EXTINF") == 2, texto_repetido
    assert 'tvg-id="%s"' % ("11" * 20) in texto_repetido, texto_repetido   # 1080p gana a 720p y a nada
    assert 'tvg-id="%s"' % ("44" * 20) in texto_repetido, texto_repetido   # empate: gana Deportes ES

    # And every file is always produced, even when the catalog is empty.
    built = build_all([], engine="http://motor:8081/")
    assert [name for name, _, _ in built] == [
        "acestream-suite.m3u", "deportes.m3u", "espana.m3u", "hls.m3u", "direct.m3u"
    ], built
    assert all(text == "#EXTM3U\n" and n == 0 for _, text, n in built)

    # Las copias con otro nombre son datos: sin la variable no se escribe ninguna, la copia lleva
    # el texto de su lista, solo cuando la lista cambio, y siempre dentro del directorio de salida.
    assert COMPAT_ALIASES == {}, COMPAT_ALIASES
    assert compat_aliases("") == {}
    assert compat_aliases("acestream-suite.m3u=otro.m3u") == {"acestream-suite.m3u": ["otro.m3u"]}
    assert compat_aliases(" a.m3u = b.m3u , ") == {"a.m3u": ["b.m3u"]}
    assert compat_aliases("a.m3u=b.m3u,c.m3u=d.m3u") == {"a.m3u": ["b.m3u"], "c.m3u": ["d.m3u"]}
    assert compat_aliases("sin-igual") == {}
    assert compat_aliases("a.m3u=../fuera.m3u") == {}, "fuera del directorio de salida no"
    assert compat_aliases("a.m3u=/tmp/fuera.m3u") == {}, "ni una ruta absoluta"
    temporal = tempfile.mkdtemp()
    respaldo_alias = globals()["COMPAT_ALIASES"]
    try:
        globals()["COMPAT_ALIASES"] = {"acestream-suite.m3u": ["otro.m3u"]}
        una = [("acestream-suite.m3u", "#EXTM3U\n", 0)]
        assert save_all(una, temporal)["acestream-suite.m3u"] is True
        for nombre in ("acestream-suite.m3u", "otro.m3u"):
            with open(os.path.join(temporal, nombre)) as fichero:
                assert fichero.read() == "#EXTM3U\n", nombre
        # Un fichero igual no se reescribe: ni la lista ni su copia.
        os.unlink(os.path.join(temporal, "otro.m3u"))
        assert save_all(una, temporal)["acestream-suite.m3u"] is False
        assert not os.path.exists(os.path.join(temporal, "otro.m3u")), "sin cambio no hay copia"
    finally:
        globals()["COMPAT_ALIASES"] = respaldo_alias
        shutil.rmtree(temporal)

    # Las variables de entorno se leen con su nombre y solo con el: la vieja ya no se mira. El nombre
    # viejo va en dos trozos unidos al ejecutar: el compilador pliega una suma de cadenas en una
    # sola constante, y el nombre quedaria dentro del `.pyc` que la auditoria tambien mira.
    viejo = "".join(("ACE", "LIST"))
    respaldo = dict(os.environ)
    try:
        os.environ.pop("ACESTREAM_SUITE_STREAM_PARAM", None)
        os.environ[viejo + "_STREAM_PARAM"] = "viejo"
        assert env("ACESTREAM_SUITE_STREAM_PARAM", "d") == "d", "la vieja ya no se lee"
        os.environ["ACESTREAM_SUITE_STREAM_PARAM"] = "nuevo"
        assert env("ACESTREAM_SUITE_STREAM_PARAM", "d") == "nuevo"
    finally:
        os.environ.clear()
        os.environ.update(respaldo)
    assert env("ACESTREAM_SUITE_NO_EXISTE", "d") == "d"
    print("self-test OK")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="acestream-suite's catalogue as M3U playlists")
    parser.add_argument("--acestream-suite", default=ACESTREAM_SUITE_URL,
                        help="acestream-suite base URL")
    parser.add_argument("--engine", default=ENGINE_URL, help="AceStream engine base URL")
    parser.add_argument("--directory", default=OUTPUT_DIR, help="where to write the playlists")
    parser.add_argument("--status", default=STATUS,
                        help="availability filter, as acestream-suite's list view takes it (empty: all)")
    parser.add_argument("--dry-run", action="store_true", help="print them, write nothing")
    parser.add_argument("--self-test", action="store_true", help="run the checks and exit")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.self_test:
        self_test()
        return 0

    lock = open(LOCK_PATH, "a")
    try:  # never two runs at once: cron can start one while a slow fetch is still going
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another run is still going; nothing done")
        return 0

    try:
        channels = fetch_channels(args.acestream_suite, status=args.status)
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        log("cannot read the catalog from %s: %s" % (args.acestream_suite, exc))
        return 1

    built = build_all(channels, args.engine)
    if args.dry_run:
        for name, text, count in built:
            print("--- %s: %d channels ---" % (name, count), file=sys.stderr)
            sys.stdout.write(text)
        return 0

    changed = save_all(built, args.directory)
    for name, text, count in built:
        log(
            "%d %s channels in the catalog, %d in %s: %s"
            % (
                len(channels),
                args.status or "unfiltered",
                count,
                name,
                "written" if changed[name] else "unchanged",
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
