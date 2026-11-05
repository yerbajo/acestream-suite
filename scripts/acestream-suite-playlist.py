#!/usr/bin/env python3
"""Writes acestream-suite's catalogue as M3U playlists, served over plain HTTP on the house LAN.

WHY THIS EXISTS
    acestream-suite (http://192.168.1.245:8083) catalogs AceStream links, but a catalog in a web page is
    only useful to whoever is sitting in front of that web page. Every other player in the house —
    VLC on a phone, Kodi on the TV box, Dispatcharr behind Jellyfin's Live TV — wants a playlist:
    a URL that answers with `#EXTM3U` and a stream URL per channel. This script is the source of
    truth for those URLs: it asks acestream-suite for its own export and drops the files in a directory
    Caddy already serves, so the links never change even though the catalog does.

    Five playlists, all from the same catalog, plus the one that is not a filter of the catalog:

    | file                | what it holds                                                   |
    |---------------------|-----------------------------------------------------------------|
    | acestream-suite.m3u | everything (this is the one Dispatcharr consumes)                |
    | deportes.m3u        | sports: the `Deportes*` categories, plus sports names elsewhere  |
    | espana.m3u          | from Spain: `ES`, the Spanish categories, or a Spanish name       |
    | hls.m3u             | sports or Spanish, with a `Campeones` folder of its own          |
    | direct.m3u          | everything, as the engine's own MPEG-TS                          |
    | madrid.m3u          | the Real Madrid match that is on air, and nothing else (S12)     |

    `acelist.m3u` is written too, as an exact copy of `acestream-suite.m3u`: Dispatcharr account 4
    reads it by that name today, and this version keeps both so the rename breaks nothing (S4). The
    other five names do not change: the TV reads `hls.m3u`.

    Every display name ends with the quality the catalog detected (`Canal 1080p`), so searching the
    list for "1080p" works; `tvg-name` keeps the bare name, because guide matching reads it.

    Most of them are filters over the catalog, not separate collections: the names rule matters
    because the engine tags a lot of Spanish and sports channels as `Amateur`, `Otros` or `TV`
    (a re-stream of La 1 is "amateur" to it), and because it leaves the language and the country
    empty for most of them.

THE LIVE SET: THE MATCH ON AIR, NOT A CATEGORY
    `scripts/acestream-suite-partido.py` writes `/var/lib/acestream-suite-partido/live.json`
    (`--live-file`) after every run: the content ids airing a Real Madrid match now or in the hour
    before, each one with the `stop` of its programme. This script reads it — the new path first and,
    while the host still keeps the old one, `/var/lib/acelist-partido/live.json` (S4) — and only while
    `now < stop`:

    - `deportes.m3u` and `hls.m3u` give those channels `group-title="Real Madrid"`;
    - `madrid.m3u` carries those channels and nobody else.

    A missing file, an unreadable one or an expired entry is not an error: the channel simply has no
    a missing file, an unreadable one or an expired entry is not an error: the channel simply has no
    folder and is not in `madrid.m3u`, and it stays in `acestream-suite.m3u` with its own category.
    Nothing is ever deleted from the catalog, and the folder appears with the match and goes away when
    it ends.
    The `Real Madrid` category still counts as sport on its own, which is what puts those channels in
    the two sports lists in the first place; it is not what decides the folder.

WHERE THE LINKS POINT
    The stream URLs in the playlists are NOT acestream-suite's. Five of the six point at the HLS bridge
    (`${ACESTREAM_SUITE_HLS_BASE}`, `:8084/<hash>/index.m3u8` on this deployment), and only
    `direct.m3u` carries the engine's own MPEG-TS URL — `http://192.168.1.28:8081/ace/getstream`
    `?infohash=<hash>`, the tunneled engine behind its nginx. The bridge is the default for a reason
    measured on 2026-10-18: the engine serves ONE reader per content, so a direct reader and the
    bridge asking for the same channel leave both without it. One list, one reader: the bridge.
    `infohash` is the engine's parameter (the Linux 3.2.11 build answers `failed to load content`
    to `id`).

INSTALL (on dev.albertucho.home, the .245 host)
    install -m 0755 scripts/acestream-suite-playlist.py /usr/local/bin/acestream-suite-playlist
    install -m 0644 scripts/acestream-suite-playlist.cron /etc/cron.d/acestream-suite-playlist
    /usr/local/bin/acestream-suite-playlist          # first run, so the files exist before anything reads them
    # The playlists are then at http://192.168.1.245/varios/{acestream-suite,deportes,espana}.m3u
    # (`acelist.m3u` is the same file under its old name, for Dispatcharr: S4)

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
import datetime
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


def env(name, legacy, default):
    """S4: la variable nueva y, mientras el host solo exporte la vieja, la vieja.

    Se lee `ACESTREAM_SUITE_*` primero; `ACELIST_*` sigue valiendo en esta version para que un host
    que aun la exporte (el crontab viejo, un docker-compose sin actualizar) no cambie de
    comportamiento el dia del renombrado.
    """
    value = os.environ.get(name)
    if value is None:
        value = os.environ.get(legacy)
    return default if value is None else value


def with_legacy(path, current, legacy):
    """S4: la ruta nueva y, mientras la vieja sea la unica que existe, la vieja.

    Solo se cae a la ruta vieja cuando no se ha pedido otra cosa (`path == current`) y el fichero
    nuevo aun no esta: es la ventana entre este cambio y la primera pasada que escribe el nuevo.
    """
    if path == current and not os.path.exists(path) and legacy and os.path.exists(legacy):
        return legacy
    return path


ACESTREAM_SUITE_URL = env("ACESTREAM_SUITE_URL", "ACELIST_URL", "http://127.0.0.1:8083")
ENGINE_URL = env("ACESTREAM_SUITE_PLAYLIST_ENGINE", "ACELIST_PLAYLIST_ENGINE",
                 "http://192.168.1.28:8081")
OUTPUT_DIR = env("ACESTREAM_SUITE_PLAYLIST_DIR", "ACELIST_PLAYLIST_DIR", "/srv/www/varios")
# El puente HLS (acestream-suite/hls-bridge.py): la misma lista, pero cada canal como manifiesto.
HLS_BASE = env("ACESTREAM_SUITE_HLS_BASE", "ACELIST_HLS_BASE", "http://192.168.1.245:8084")
LOG_PATH = env("ACESTREAM_SUITE_PLAYLIST_LOG", "ACELIST_PLAYLIST_LOG",
               "/var/log/acestream-suite-playlist.log")
LOCK_PATH = env("ACESTREAM_SUITE_PLAYLIST_LOCK", "ACELIST_PLAYLIST_LOCK",
                "/var/lock/acestream-suite-playlist.lock")
# El conjunto en directo de `scripts/acestream-suite-partido.py`: la misma variable de entorno en los
# dos scripts, para que mover el fichero sea un solo cambio. Ausente = sin carpeta y sin `madrid.m3u`.
LIVE_FILE = env("ACESTREAM_SUITE_PARTIDO_LIVE", "ACELIST_PARTIDO_LIVE",
                "/var/lib/acestream-suite-partido/live.json")
# S4: mientras el trabajo de los partidos aun deje el conjunto en la ruta vieja, se lee de ahi.
LIVE_FILE_LEGACY = "/var/lib/acelist-partido/live.json"
LIVE_FORMAT = "acelist-partido-live"
TIMEOUT = float(env("ACESTREAM_SUITE_PLAYLIST_TIMEOUT", "ACELIST_PLAYLIST_TIMEOUT", "30"))
# The engine's own name for the stream parameter, per the finding in odd/tasks/acelist.md.
STREAM_PARAM = env("ACESTREAM_SUITE_STREAM_PARAM", "ACELIST_STREAM_PARAM", "infohash")

# Which channels are worth putting in a playlist: only the ones acestream-suite has verified as online.
# The app's own `alive` is exactly "the engine reached dl with data downloaded and at least one
# peer", so it already means "online with peers" — no dead link reaches a player, and a channel
# that stops streaming drops out on the next run. Empty the value to publish the whole catalog.
STATUS = env("ACESTREAM_SUITE_PLAYLIST_STATUS", "ACELIST_PLAYLIST_STATUS", "alive")

# Sports. The categories are the engine's own tags (and the three Spanish ones this deployment
# made); the names catch what the engine filed elsewhere. `premier` is `Premier Sports` and not the
# movie channels called "Premiere", and `bein` is `beIN Sports` and not `beIN Movies`.
#
# `Real Madrid` is the category `scripts/acestream-suite-partido.py` gives the channels it imports on match
# day: it counts as sport on its own, and it is also the folder `deportes.m3u` and `hls.m3u` ask for.
CATEGORIA_PARTIDO = "Real Madrid"
CATEGORIAS_DEPORTES = ("Deportes", "Deportes ES", "Deportes int.", CATEGORIA_PARTIDO)
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


def carpeta_campeones(canal, live=None):
    """La carpeta propia del canal en `hls.m3u`, o None para dejarlo en la de su categoría."""
    return "Campeones" if CAMPEONES.search(canal.get("title") or "") else None


def now_utc():
    """Ahora en UTC y sin zona: es lo que se compara con los `stop` del conjunto en directo."""
    return datetime.datetime(*time.gmtime()[:6])


def parse_stop(value):
    """El `stop` del conjunto en directo (`2026-11-04T20:00:00Z`), o None si no se entiende.

    `datetime.fromisoformat` es de Python 3.7 y el host tiene 3.6.8: se parsea con `strptime`.
    """
    try:
        return datetime.datetime.strptime(str(value or "").strip(), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def live_entries(document, now=None):
    """S11: el conjunto en directo ya sin las entradas caducadas; un documento ajeno es vacio.

    La condición es `now < stop`, que es lo que hace que la carpeta dure lo que dura el partido. Una
    entrada caducada o una fecha que no se entiende no son un error: no dan carpeta y ya está.
    """
    if not isinstance(document, dict) or document.get("format") != LIVE_FORMAT:
        return {}
    ahora = now or now_utc()
    live = {}
    for content_id, stop in (document.get("channels") or {}).items():
        momento = parse_stop(stop)
        if momento is None or momento <= ahora:
            continue
        live[str(content_id or "").lower()] = momento
    return live


def load_live(path=LIVE_FILE, now=None):
    """S11: el conjunto en directo del disco. Un fichero ausente o ilegible es conjunto vacio.

    Nunca lanza: el partido es un extra de la lista, y una lista que depende de un fichero que puede
    no estar no se cae por ello.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (IOError, OSError, ValueError):
        return {}
    return live_entries(document, now)


def carpeta_real_madrid(canal, live=None):
    """La carpeta `Real Madrid`: solo los canales del conjunto en directo (S7 y S11).

    La categoría `Real Madrid` NO decide la carpeta — solo hace que el canal entre en las dos listas
    de deporte —; si se decidiera por categoría, la carpeta se quedaría para siempre con los canales
    de todos los partidos pasados, y el dueño quiere que sean en directo. `live` viene ya filtrado
    por `now < stop`.
    """
    return CATEGORIA_PARTIDO if (canal.get("content_id") or "").lower() in (live or {}) else None


def carpeta_hls(canal, live=None):
    """Las dos carpetas propias de `hls.m3u`, y el conjunto en directo gana el empate.

    Precedencia, decidida y no accidental: un canal que es a la vez de Movistar Liga de Campeones y
    del conjunto en directo cae en UNA sola carpeta, y esa es `Real Madrid`. Lo pidió el dueño para
    encontrar el partido, y la medición del 2026-11-04 lo dejó claro: con `Campeones` ganando, los dos
    canales que emitían el AEK Atenas - Real Madrid (`M. Liga de Campeones`, `M+ LIGA DE CAMPEONES FHD
    (SAT)`) se iban a `Campeones` y la carpeta `Real Madrid` se quedaba solo con el canal de la
    Euroliga. Al revés no se pierde nada: en cuanto el partido acaba, esos canales salen del conjunto
    en directo y vuelven solos a `Campeones`.
    """
    return carpeta_real_madrid(canal, live) or carpeta_campeones(canal)


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
# URL, and `None` as folder means "group by the catalog's category", which is what `acestream-suite.m3u`,
# `espana.m3u` and `direct.m3u` do. Two lists ask for a folder of their own: `deportes.m3u` puts the
# `Real Madrid` category in the folder of that name, and `hls.m3u` gives the Movistar Liga de
# Campeones channels `Campeones` and the imported match channels `Real Madrid`.
#
# TODAS menos `direct.m3u` apuntan al puente HLS, y es a proposito: el motor sirve UN solo lector por
# contenido, asi que dos reproductores de la casa pidiendo el mismo canal -la tele por una URL directa
# y el puente a la vez- se quedan los dos sin canal (500 y respuestas vacias). Medido el 2026-10-18:
# con el puente peleando contra un lector directo, 0 manifiestos y 23 fallos en un minuto. Una lista,
# un lector: el puente. `direct.m3u` se queda para Dispatcharr (que no proxya HLS) y para diagnosticar.
PLAYLISTS = (
    ("acestream-suite.m3u", "the whole catalog, as HLS", lambda canal: True, hls_url, None),
    ("deportes.m3u", "sports, as HLS", es_deporte, hls_url, carpeta_real_madrid),
    ("espana.m3u", "Spanish channels, as HLS", es_espanol, hls_url, None),
    ("hls.m3u", "sports and Spanish, as HLS", lambda canal: es_deporte(canal) or es_espanol(canal),
     hls_url, carpeta_hls),
    ("direct.m3u", "the whole catalog, engine MPEG-TS (Dispatcharr, debugging)", lambda canal: True, None,
     None),
)

# S4: las copias con el nombre viejo que se siguen escribiendo mientras dure esta version.
# Dispatcharr (cuenta 4) lee `acelist.m3u` hoy; la tele lee `hls.m3u`, que no cambia de nombre.
COMPAT_NAMES = {"acestream-suite.m3u": "acelist.m3u"}


def export_url(base_url=ACESTREAM_SUITE_URL, status=STATUS):
    """`/export`, with the availability filter the app's list view uses (`status=alive`)."""
    url = base_url.rstrip("/") + "/export"
    return url + ("?status=" + urllib.parse.quote(status) if status else "")


def fetch_channels(base_url=ACESTREAM_SUITE_URL, timeout=TIMEOUT, status=STATUS):
    """acestream-suite's own export document: the channels, with their category and availability."""
    url = export_url(base_url, status)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        document = json.loads(response.read().decode("utf-8"))
    if document.get("format") != "acelist":  # el marcador de la app, no la marca del producto
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


def folder_of(channel, group=None, live=None):
    """El nombre de la carpeta del canal: la que le da la lista, o la de su categoría."""
    return (group(channel, live) if group else None) or channel.get("category") or ""


def build_playlist(channels, engine=ENGINE_URL, url=None, group=None, live=None):
    """The whole playlist as text. One entry per channel name, ordered by folder and then by title.

    `group` lets one list put a channel in a folder of its own instead of its catalog category:
    `deportes.m3u` and `hls.m3u` do that with the channels of the live set (`Real Madrid`), and
    `hls.m3u` also with the Movistar Liga de Campeones channels (`Campeones`). `live` is that set,
    already filtered by `now < stop`.
    """
    lines = ["#EXTM3U"]
    url_for = url or (lambda content_id: stream_url(content_id, engine))
    for channel in sorted(
        without_duplicates(channels),
        key=lambda c: (folder_of(c, group, live).lower(), (c.get("title") or "").lower()),
    ):
        title = display_name(channel.get("title") or channel["content_id"])
        attributes = [
            'tvg-id="%s"' % channel["content_id"].lower(),
            'tvg-name="%s"' % attribute(title),
        ]
        folder = folder_of(channel, group, live)
        if folder:
            attributes.append('group-title="%s"' % attribute(folder))
        lines.append("#EXTINF:-1 %s,%s" % (" ".join(attributes), shown_name(channel, title)))
        lines.append(url_for(channel["content_id"]))
    return "\n".join(lines) + "\n"


def live_channels(channels, live):
    """S12: los canales del catálogo que están en el conjunto en directo, y solo esos."""
    return [canal for canal in channels if (canal.get("content_id") or "").lower() in (live or {})]


def build_all(channels, engine=ENGINE_URL, live=None):
    """`(name, text, how many channels)` for every playlist, built from the same fetch.

    `madrid.m3u` goes last and does not come from the catalog filters: it is the live set of S11, so
    with no match on air it is a valid empty playlist, never an absent file and never a 404.
    """
    built = []
    for name, _, selected, url, group in PLAYLISTS:
        chosen = [canal for canal in channels if selected(canal)]
        built.append((name, build_playlist(chosen, engine, url, group, live), len(chosen)))
    en_directo = live_channels(channels, live)
    # Con la URL del puente, igual que las otras cinco listas del catálogo: el motor solo sirve un
    # lector por contenido, y `direct.m3u` ya es la lista del motor para quien la quiera.
    built.append(("madrid.m3u", build_playlist(en_directo, engine, hls_url), len(en_directo)))
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
    """Escribe cada lista y, si la tiene, su copia con el nombre viejo (S4).

    Devuelve `{nombre: escrito}` para que `main` registre lo mismo que antes.
    """
    changed = {}
    for name, text, _ in built:
        changed[name] = write_if_changed(text, os.path.join(directory, name))
        alias = COMPAT_NAMES.get(name)
        if alias:
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

    # La carpeta «Real Madrid» (S7 y S11): la categoría que importa el trabajo de los partidos cuenta
    # como deporte —es lo que hace que esos canales entren en las dos listas de deporte— pero NO
    # decide la carpeta; la decide el conjunto en directo, y solo mientras `now < stop`.
    assert es_deporte({"title": "DAZN LaLiga 2", "category": "Real Madrid"})
    partido = {"title": "DAZN LaLiga 2", "content_id": "99" * 20, "category": "Real Madrid"}
    # Un partido en directo sí lleva carpeta, y fuera de las dos listas de deporte no se toca nada.
    vivo = {partido["content_id"]: datetime.datetime(2026, 11, 4, 20, 0)}
    assert carpeta_real_madrid(partido, vivo) == "Real Madrid"
    assert carpeta_real_madrid(partido) is None
    assert carpeta_real_madrid(la_liga, vivo) is None
    # La carpeta va cableada lista por lista, no a todas: se comprueba el cableado, no el texto.
    carpetas_por_lista = dict((n, g) for n, _, _, _, g in PLAYLISTS)
    assert carpetas_por_lista["deportes.m3u"] is carpeta_real_madrid, carpetas_por_lista
    assert carpetas_por_lista["hls.m3u"] is carpeta_hls, carpetas_por_lista
    for nombre in ("acestream-suite.m3u", "espana.m3u", "direct.m3u"):
        assert carpetas_por_lista[nombre] is None, nombre
    listas = dict((n, tx) for n, tx, _ in build_all([partido, la_liga], live=vivo))
    assert 'group-title="Real Madrid"' in listas["deportes.m3u"], listas["deportes.m3u"]
    assert 'group-title="Real Madrid"' in listas["hls.m3u"], listas["hls.m3u"]
    # Y sin partido en directo la categoría se queda como estaba: el canal no desaparece de ninguna
    # lista, simplemente no hay carpeta especial (la de siempre es su categoría, en las dos).
    sin_partido = dict((n, tx) for n, tx, _ in build_all([partido, la_liga], live={}))
    assert carpeta_real_madrid(partido, {}) is None
    assert 'group-title="Real Madrid"' in sin_partido["deportes.m3u"], sin_partido["deportes.m3u"]
    assert 'group-title="Deportes ES"' in sin_partido["deportes.m3u"], sin_partido["deportes.m3u"]
    # En las demás, la fila aparece (o no) por las reglas de siempre, agrupada por su categoría: la
    # carpeta no ensancha ninguna lista. `DAZN LaLiga 2` no es español por país, ni por categoría, ni
    # por nombre, así que sigue sin estar en `espana.m3u`.
    assert 'group-title="Real Madrid"' in listas["acestream-suite.m3u"], listas["acestream-suite.m3u"]
    assert 'group-title="Real Madrid"' in listas["direct.m3u"], listas["direct.m3u"]
    assert 'group-title="Deportes ES"' in listas["deportes.m3u"], listas["deportes.m3u"]
    assert partido["content_id"] not in listas["espana.m3u"], listas["espana.m3u"]
    # Precedencia en `hls.m3u`: un canal que es las dos cosas —título de Campeones y en el conjunto en
    # directo— cae en UNA sola carpeta, `Campeones` (la razón está en `carpeta_hls`), y en
    # `deportes.m3u`, que no tiene `Campeones`, en `Real Madrid`.
    las_dos = {"title": "M+ LIGA DE CAMPEONES FHD (SAT) --> NEW ERA", "content_id": "88" * 20,
               "category": "Real Madrid"}
    # El conjunto en directo gana el empate: un canal de las dos carpetas, en partido, va a `Real
    # Madrid` (medido: con `Campeones` ganando, la carpeta del partido se quedaba sin los canales que
    # emitían el AEK Atenas - Real Madrid).
    assert carpeta_hls(las_dos, {("88" * 20): datetime.datetime(2026, 11, 4, 20, 0)}) == "Real Madrid"
    assert carpeta_hls(las_dos) == "Campeones"  # sin partido, vuelve a `Campeones`
    assert carpeta_real_madrid(las_dos, {("88" * 20): datetime.datetime(2026, 11, 4, 20, 0)}) == (
        "Real Madrid"
    )
    listas_dos = dict((n, tx) for n, tx, _ in build_all([las_dos], live={("88" * 20):
                                                                    datetime.datetime(2026, 11, 4, 20, 0)}))
    assert listas_dos["hls.m3u"].count("#EXTINF") == 1, listas_dos["hls.m3u"]
    assert 'group-title="Real Madrid"' in listas_dos["hls.m3u"], listas_dos["hls.m3u"]
    assert 'group-title="Campeones"' not in listas_dos["hls.m3u"], listas_dos["hls.m3u"]
    assert 'group-title="Real Madrid"' in listas_dos["deportes.m3u"], listas_dos["deportes.m3u"]
    assert 'group-title="Campeones"' not in listas_dos["deportes.m3u"], listas_dos["deportes.m3u"]
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
    # Sin partido el mismo canal vuelve a su carpeta de siempre: `Campeones` no se queda sin sus
    # canales para siempre.
    sin_partido = dict((n, tx) for n, tx, _ in build_all([las_dos]))
    assert 'group-title="Campeones"' in sin_partido["hls.m3u"], sin_partido["hls.m3u"]
    assert 'group-title="Real Madrid"' not in sin_partido["hls.m3u"], sin_partido["hls.m3u"]

    # S12: `madrid.m3u` lleva SOLO los canales del conjunto en directo, con las URLs del puente, y
    # sin partido es una lista vacía válida: nunca falta, nunca es un 404.
    dos_en_directo = {
        partido["content_id"]: datetime.datetime(2026, 11, 4, 20, 0),
        la_liga["content_id"]: datetime.datetime(2026, 11, 4, 19, 38),
    }
    solo_madrid = dict((n, tx) for n, tx, _ in build_all([partido, la_liga, campeon], live=dos_en_directo))
    assert solo_madrid["madrid.m3u"].count("#EXTINF") == 2, solo_madrid["madrid.m3u"]
    assert 'tvg-id="%s"' % partido["content_id"] in solo_madrid["madrid.m3u"], solo_madrid["madrid.m3u"]
    assert 'tvg-id="%s"' % la_liga["content_id"] in solo_madrid["madrid.m3u"], solo_madrid["madrid.m3u"]
    assert campeon["content_id"] not in solo_madrid["madrid.m3u"], solo_madrid["madrid.m3u"]
    assert solo_madrid["madrid.m3u"].splitlines()[2].startswith("http://192.168.1.245:8084/"), (
        solo_madrid["madrid.m3u"]
    )
    assert solo_madrid["madrid.m3u"].splitlines()[2].endswith("/index.m3u8"), solo_madrid["madrid.m3u"]
    # Ordenada como las demás: por carpeta (aquí la categoría de cada fila), después por título.
    assert solo_madrid["madrid.m3u"].splitlines()[1].endswith(",DAZN LaLiga"), (
        solo_madrid["madrid.m3u"]
    )
    assert solo_madrid["madrid.m3u"].splitlines()[3].endswith(",DAZN LaLiga 2"), (
        solo_madrid["madrid.m3u"]
    )
    # Sin partido en directo, o con el fichero ausente, la lista existe y está vacía.
    vacia = dict((n, tx) for n, tx, _ in build_all([partido, la_liga], live={}))
    assert vacia["madrid.m3u"] == "#EXTM3U\n", vacia["madrid.m3u"]
    assert build_all([partido], live=load_live("/no-existe/para-el-self-test/live.json"))[-1][1] == (
        "#EXTM3U\n"
    )
    # Las demás listas no cambian por el conjunto en directo: mismas filas, mismas carpetas.
    sin = dict((n, tx) for n, tx, _ in build_all([partido, la_liga, campeon], live={}))
    con = dict((n, tx) for n, tx, _ in build_all([partido, la_liga, campeon], live=dos_en_directo))
    for nombre in ("acestream-suite.m3u", "espana.m3u", "direct.m3u"):
        assert sin[nombre] == con[nombre], nombre
    assert sin["hls.m3u"].count("#EXTINF") == con["hls.m3u"].count("#EXTINF"), "hls.m3u"

    # Y el conjunto en directo en sí: una entrada caducada no da carpeta y no es un error; un fichero
    # ausente o un documento ajeno es conjunto vacío, sin excepción.
    assert live_entries({"format": LIVE_FORMAT, "channels": {partido["content_id"]:
                                                              "2026-11-04T20:00:00Z"}},
                         datetime.datetime(2026, 11, 4, 19, 0)) == {
        partido["content_id"]: datetime.datetime(2026, 11, 4, 20, 0)
    }
    assert live_entries({"format": LIVE_FORMAT, "channels": {partido["content_id"]:
                                                              "2026-11-04T20:00:00Z"}},
                         datetime.datetime(2026, 11, 4, 20, 1)) == {}
    # `now == stop` ya no es «en directo»: la condición es `now < stop`.
    assert live_entries({"format": LIVE_FORMAT, "channels": {partido["content_id"]:
                                                              "2026-11-04T20:00:00Z"}},
                         datetime.datetime(2026, 11, 4, 20, 0)) == {}
    assert live_entries({"format": LIVE_FORMAT, "channels": {partido["content_id"]: "sin fecha"}}) == {}
    assert live_entries({"format": "otra-cosa", "channels": {partido["content_id"]:
                                                              "2026-11-04T20:00:00Z"}}) == {}
    assert live_entries({}) == {}
    assert parse_stop("2026-11-04T20:00:00Z") == datetime.datetime(2026, 11, 4, 20, 0)
    assert parse_stop("") is None

    # S4: la ruta vieja del conjunto en directo, mientras sea la unica que existe. Se prueba con
    # ficheros de verdad porque es un fallo silencioso: si no se lee, el partido no da carpeta.
    temporal = tempfile.mkdtemp()
    try:
        nuevo = os.path.join(temporal, "live.json")
        viejo = os.path.join(temporal, "viejo.json")
        assert with_legacy(nuevo, nuevo, viejo) == nuevo, "sin ficheros manda la nueva"
        with open(viejo, "w") as fichero:
            fichero.write("{}")
        assert with_legacy(nuevo, nuevo, viejo) == viejo, "solo la vieja: se lee la vieja"
        with open(nuevo, "w") as fichero:
            fichero.write("{}")
        assert with_legacy(nuevo, nuevo, viejo) == nuevo, "las dos: manda la nueva"
        assert with_legacy("/tmp/pedida.json", nuevo, viejo) == "/tmp/pedida.json", "una ruta pedida a mano"
        # Y la copia con el nombre viejo se escribe con el mismo texto que la nueva.
        assert save_all([("acestream-suite.m3u", "#EXTM3U\n", 0)], temporal)["acestream-suite.m3u"]
        with open(os.path.join(temporal, "acelist.m3u")) as fichero:
            assert fichero.read() == "#EXTM3U\n", "la copia vieja no se escribio"
    finally:
        shutil.rmtree(temporal)

    # And every file is always produced, even when the catalog is empty.
    built = build_all([], engine="http://motor:8081/")
    assert [name for name, _, _ in built] == [
        "acestream-suite.m3u", "deportes.m3u", "espana.m3u", "hls.m3u", "direct.m3u", "madrid.m3u"
    ], built
    assert all(text == "#EXTM3U\n" and n == 0 for _, text, n in built)

    # S3/S4: la lista lleva el nombre nuevo y deja la copia con el viejo; y de las variables de
    # entorno manda la nueva, con la vieja aun valiendo mientras el host solo exporte esa.
    assert COMPAT_NAMES == {"acestream-suite.m3u": "acelist.m3u"}, COMPAT_NAMES
    respaldo = dict(os.environ)
    try:
        os.environ.pop("ACESTREAM_SUITE_STREAM_PARAM", None)
        os.environ["ACELIST_STREAM_PARAM"] = "viejo"
        assert env("ACESTREAM_SUITE_STREAM_PARAM", "ACELIST_STREAM_PARAM", "d") == "viejo"
        os.environ["ACESTREAM_SUITE_STREAM_PARAM"] = "nuevo"
        assert env("ACESTREAM_SUITE_STREAM_PARAM", "ACELIST_STREAM_PARAM", "d") == "nuevo"
    finally:
        os.environ.clear()
        os.environ.update(respaldo)
    assert env("ACESTREAM_SUITE_NO_EXISTE", "ACELIST_TAMPOCO", "d") == "d"
    print("self-test OK")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="acestream-suite's catalogue as M3U playlists")
    parser.add_argument("--acestream-suite", default=ACESTREAM_SUITE_URL,
                        help="acestream-suite base URL")
    parser.add_argument("--engine", default=ENGINE_URL, help="AceStream engine base URL")
    parser.add_argument("--directory", default=OUTPUT_DIR, help="where to write the playlists")
    parser.add_argument("--status", default=STATUS,
                        help="availability filter, as acestream-suite's list view takes it (empty: all)")
    parser.add_argument("--live-file", default=LIVE_FILE,
                        help="the live set that decides the Real Madrid folder and madrid.m3u")
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

    # S11/S12: el conjunto en directo, leido una vez por pasada. Un fichero ausente es conjunto
    # vacio, no un error: sin partido no hay carpeta ni entradas en `madrid.m3u`, y nada mas cambia.
    # S4: la ruta nueva, y la vieja mientras sea la unica que existe.
    live_path = with_legacy(args.live_file, LIVE_FILE, LIVE_FILE_LEGACY)
    live = load_live(live_path)
    log("%d channels in the live set from %s" % (len(live), live_path))

    built = build_all(channels, args.engine, live)
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
