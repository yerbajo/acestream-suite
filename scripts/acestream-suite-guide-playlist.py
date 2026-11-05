#!/usr/bin/env python3
"""Writes acestream-suite's catalogue as a playlist with a guide: `acestream-suite-guide.m3u`.

WHY THIS EXISTS
    acestream-suite (http://192.168.1.245:8083) carries the catalogue AND a channel-to-guide mapping:
    every channel it could match to an XMLTV source has the source's channel id on it. A player
    cannot see that mapping from an M3U alone, and it cannot see the guide either, because the M3U
    it consumes names no XMLTV document. This script is what closes that gap: a second playlist,
    next to the one the TV already reads, whose `#EXTM3U` line announces the guide and whose
    `#EXTINF` lines carry the guide channel id as `tvg-id`. Dispatcharr and Jellyfin then show
    now/next and a grid for the channels that have a guide.

WHY A SECOND LIST AND NOT AN EDIT
    `http://192.168.1.245/varios/acelist.m3u` is what Dispatcharr account 4 consumes today, with
    `tvg-id` = the 40-hex AceStream content id on every line. Changing that id in place re-maps the
    live line-up (the account has ~1,072 streams marked stale), which the owner deferred. So this
    script writes a NEW file and never touches the two lists `scripts/acestream-suite-playlist.py`
    owns: the TV keeps reading exactly what it read before, and the owner decides when to cut over.
    `acelist-epg.m3u` is written too, as a copy of `acestream-suite-guide.m3u`, exactly as its new
    sibling does: the name that exists today keeps existing while this version lasts (S4).

WHERE THE GUIDE IDS COME FROM
    `/epg.json` on acestream-suite, which maps `content_id -> guide id -> display name` for the mapped
    channels only. The xmltv at `/epg.xml` carries the same guide ids as `<channel id>`, so a
    `tvg-id` here and a `<channel>` there are the same handle. An UNMAPPED channel gets no
    `tvg-id` attribute at all: it has no guide channel, and the content id it would otherwise carry
    is not a guide id any provider knows, so a `tvg-id` on that line points at nothing.

WHERE THE LINKS POINT
    At the HLS bridge, exactly like the other four lists: `http://192.168.1.245:8084/<hash>/index.m3u8`
    (`acestream-suite/hls-bridge.py`). NOT at the engine. The engine serves one reader per content, so a list
    that calls `:8081/ace/getstream` directly competes with the bridge for the same channel and both
    lose it (measured 2026-10-18: 0 manifests and 23 failures in a minute). `direct.m3u`, written by
    `scripts/acestream-suite-playlist.py`, is the engine's own list, for Dispatcharr and diagnosis.

WHY THE CATALOGUE IS FILTERED BY AVAILABILITY
    The same rule as the other four lists: `?status=alive`. The old reason for publishing everything
    is gone — it was that the retired duplicate service queued no live checks, so `check_result` was
    empty. The guide now lives inside acestream-suite itself (`epg_channel` and `programme` tables in the same
    SQLite) and every channel in the catalogue has a `check_result` row, so the filter returns the
    whole live catalogue. `--status ""` exists if a caller ever wants the unfiltered list back.

INSTALL (on dev.albertucho.home, the .245 host)
    install -m 0755 scripts/acestream-suite-guide-playlist.py /usr/local/bin/acestream-suite-guide-playlist
    /usr/local/bin/acestream-suite-guide-playlist          # first run, so the file exists before anything reads it
    # Served at http://192.168.1.245/varios/acestream-suite-guide.m3u (and `acelist-epg.m3u`, its
    # old name, while this version lasts); the guide at ...:8083/epg.xml

USAGE
    acestream-suite-guide-playlist                 # fetch, build and write only when the content changed
    acestream-suite-guide-playlist --dry-run       # print it, write nothing (the header goes to stderr)
    acestream-suite-guide-playlist --self-test     # the checks below, no network and no files
    acestream-suite-guide-playlist --output /otro/acestream-suite-guide.m3u --acestream-suite \
        http://otra:8083 --hls-base http://puente:8084

    Everything has a default and every default is overridable; the ones a host may need are also
    readable from the environment.
"""

import argparse
import fcntl
import json
import os
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
    """S4: la ruta nueva y, mientras la vieja sea la unica que existe, la vieja."""
    if path == current and not os.path.exists(path) and legacy and os.path.exists(legacy):
        return legacy
    return path


# The service that carries the catalogue and the guide: 8083, the live acestream-suite. The default is
# loopback because the script runs on the host next to its own container.
ACESTREAM_SUITE_GUIDE_URL = env("ACESTREAM_SUITE_GUIDE_URL", "ACELIST_EPG_URL",
                               "http://127.0.0.1:8083")
# El puente HLS, igual que en las otras cuatro listas: el motor solo sirve un lector por contenido.
HLS_BASE = env("ACESTREAM_SUITE_HLS_BASE", "ACELIST_HLS_BASE", "http://192.168.1.245:8084")
OUTPUT = env("ACESTREAM_SUITE_GUIDE_PLAYLIST_OUTPUT", "ACELIST_EPG_PLAYLIST_OUTPUT",
             "/srv/www/varios/acestream-suite-guide.m3u")
# S4: la copia con el nombre viejo, al lado de la lista nueva.
COMPAT_NAME = "acelist-epg.m3u"
# The URL the PLAYER fetches the guide from, not the localhost one this script reads. A player on
# the LAN cannot reach 127.0.0.1:8083, so the header carries the host's own address.
URL_TVG = env("ACESTREAM_SUITE_GUIDE_URL_TVG", "ACELIST_EPG_URL_TVG",
              "http://192.168.1.245:8083/epg.xml")
LOG_PATH = env("ACESTREAM_SUITE_GUIDE_PLAYLIST_LOG", "ACELIST_EPG_PLAYLIST_LOG",
               "/var/log/acestream-suite-guide-playlist.log")
LOCK_PATH = env("ACESTREAM_SUITE_GUIDE_PLAYLIST_LOCK", "ACELIST_EPG_PLAYLIST_LOCK",
                "/var/lock/acestream-suite-guide-playlist.lock")
TIMEOUT = float(env("ACESTREAM_SUITE_GUIDE_PLAYLIST_TIMEOUT", "ACELIST_EPG_PLAYLIST_TIMEOUT", "30"))
# `alive`, la misma politica de frescura que las otras cuatro listas: la app ya comprueba todos los
# canales, y publicarlo todo metia en el reproductor enlaces sin pares (los cuatro DAZN Baloncesto
# del 2026-10-25 salieron ahi publicados y sin un solo par).
STATUS = env("ACESTREAM_SUITE_GUIDE_PLAYLIST_STATUS", "ACELIST_EPG_PLAYLIST_STATUS", "alive")

# The format marker `/epg.json` carries; a wrong document is refused, not read as an empty mapping.
MAPPING_FORMAT = "acelist-epg"


def log(message, path=LOG_PATH):
    """One line with a timestamp, to the log file and to stdout (a manual run is worth reading)."""
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line)
    if path:
        try:
            with open(path, "a") as handle:
                handle.write(line + "\n")
        except OSError as exc:  # a read-only /var/log must not lose the playlist
            print("acestream-suite-guide-playlist: cannot write %s: %s" % (path, exc), file=sys.stderr)


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

# Las categorias que son deportes por si mismas: desempate del dedupe, y el mismo criterio que usa
# `scripts/acestream-suite-playlist.py` para sus filtros.
CATEGORIAS_DEPORTES = ("Deportes", "Deportes ES", "Deportes int.")


def shown_name(channel, title=None):
    """The name the player shows: the channel's own and, after it, its quality (`Canal 1080p`).

    Only the display name carries it. `tvg-name` is left alone on purpose: it is what guide
    matching uses, and adding the quality there would tie an EPG mapping to a resolution that
    changes when the engine re-detects the screenshot.
    """
    title = title or display_name(channel.get("title") or channel["content_id"])
    quality = (channel.get("resolution") or "").strip().lower()
    return "%s %s" % (title, quality) if quality in KNOWN_QUALITY else title


def stream_url(content_id, base=HLS_BASE):
    """The HLS bridge's manifest for that channel, as in `scripts/acestream-suite-playlist.py`."""
    return "%s/%s/index.m3u8" % (base.rstrip("/"), content_id.lower())


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


def export_url(base_url=ACESTREAM_SUITE_GUIDE_URL, status=STATUS):
    """`/export` on acestream-suite, with the availability filter only when one is asked for."""
    url = base_url.rstrip("/") + "/export"
    return url + ("?status=" + urllib.parse.quote(status) if status else "")


def mapping_url(base_url=ACESTREAM_SUITE_GUIDE_URL):
    """`/epg.json` on acestream-suite: the `content_id -> guide id -> name` mapping."""
    return base_url.rstrip("/") + "/epg.json"


def _get_json(url, timeout=TIMEOUT):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_channels(base_url=ACESTREAM_SUITE_GUIDE_URL, timeout=TIMEOUT, status=STATUS):
    """acestream-suite's own export document: the channels, with their category."""
    url = export_url(base_url, status)
    document = _get_json(url, timeout)
    if document.get("format") != "acelist":  # el marcador de la app, no la marca del producto
        raise ValueError("%s is not an acestream-suite export (format=%r)"
                         % (url, document.get("format")))
    return document["channels"]


def fetch_mapping(base_url=ACESTREAM_SUITE_GUIDE_URL, timeout=TIMEOUT):
    """The guide mapping: `{content_id: {guide_id, guide_name}}` for the mapped channels only."""
    url = mapping_url(base_url)
    document = _get_json(url, timeout)
    if document.get("format") != MAPPING_FORMAT:
        raise ValueError("%s is not an EPG mapping (format=%r)" % (url, document.get("format")))
    return document["mapping"]


def build_playlist(channels, mapping, base=HLS_BASE, url_tvg=URL_TVG):
    """The whole playlist as text. One entry per channel name, ordered by category and title.

    `mapping` is keyed by content id; a channel that is not in it is unmapped and gets no `tvg-id`.
    The guide id, when there is one, is used verbatim and NOT lowercased: it is an XMLTV channel id
    (`La1.TV`, `M+ Deportes 2`) and the `<channel id>` in `/epg.xml` is case-sensitive.
    """
    lines = ['#EXTM3U url-tvg="%s"' % attribute(url_tvg)]
    for channel in sorted(
        without_duplicates(channels),
        key=lambda c: ((c.get("category") or "").lower(), (c.get("title") or "").lower()),
    ):
        title = display_name(channel.get("title") or channel["content_id"])
        guide = mapping.get((channel.get("content_id") or "").lower())
        attributes = []
        # No `tvg-id` for an unmapped channel: the attribute would name a guide channel that does
        # not exist in the XMLTV, and the content id it would carry is not a guide id at all.
        if guide:
            attributes.append('tvg-id="%s"' % attribute(guide["guide_id"]))
        attributes.append('tvg-name="%s"' % attribute(title))
        if channel.get("category"):
            attributes.append('group-title="%s"' % attribute(channel["category"]))
        lines.append("#EXTINF:-1 %s,%s" % (" ".join(attributes), shown_name(channel, title)))
        lines.append(stream_url(channel["content_id"], base))
    return "\n".join(lines) + "\n"


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
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".acestream-suite-guide-playlist-")
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


def compat_copy(path):
    """S4: la copia con el nombre viejo al lado de la lista nueva.

    `None` cuando el destino no es el de casa (`--output /tmp/otra.m3u`): ahi no hay nada que
    mantener vivo con el nombre viejo, y dejar `acelist-epg.m3u` en un directorio ajeno seria un
    efecto de lado que nadie pidio.
    """
    if os.path.basename(path) != os.path.basename(OUTPUT):
        return None
    return os.path.join(os.path.dirname(path), COMPAT_NAME)


def save_output(text, path=OUTPUT):
    """Escribe la lista nueva y su copia con el nombre viejo (S4). Devuelve si la nueva cambio."""
    changed = write_if_changed(text, path)
    alias = compat_copy(path)
    if alias:
        write_if_changed(text, alias)
    return changed


def self_test():
    """The checks this script leaves behind: they fail if the format or the mapping rule breaks."""
    canales = [
        {"title": 'El "Canal", raro', "content_id": "aa" * 20, "category": "Deportes ES"},
        {"title": "Con guía", "content_id": "bb" * 20, "category": "Movistar", "resolution": "720p"},
    ]
    mapping = {"bb" * 20: {"guide_id": "M+ Deportes 2", "guide_name": "M+ Deportes 2"}}
    text = build_playlist(canales, mapping, base="http://puente:8084")
    lines = text.splitlines()

    # The header advertises the guide the player has to fetch; it is the first line, always.
    assert lines[0] == '#EXTM3U url-tvg="http://192.168.1.245:8083/epg.xml"', lines[0]
    assert len(lines) == 5, "2 channels are 1 + 2 lines each, plus the header: %d" % len(lines)
    # Ordered by category (Deportes ES first): the unmapped row is first and has NO tvg-id.
    assert "tvg-id" not in lines[1], lines[1]
    assert lines[1] == (
        '#EXTINF:-1 tvg-name="El \'Canal\' raro" group-title="Deportes ES",El "Canal", raro'
    ), lines[1]
    # Las URLs van al puente HLS y no al motor: el motor sirve UN lector por contenido, asi que
    # una lista que lo llame directo se pelea con las otras cuatro por el mismo canal (medido el
    # 2026-10-18: 0 manifiestos y 23 fallos en un minuto con el puente peleando contra un lector
    # directo). La lista de diagnostico del motor es `direct.m3u`, no esta.
    assert lines[2] == "http://puente:8084/%s/index.m3u8" % ("aa" * 20), lines[2]
    # The mapped row carries the GUIDE id, not the 40-hex content id, and the display name keeps
    # its commas: the first comma of the line is the one that separates attributes from the name.
    assert lines[3] == (
        '#EXTINF:-1 tvg-id="M+ Deportes 2" tvg-name="Con guía" group-title="Movistar",Con guía 720p'
    ), lines[3]
    assert lines[4] == "http://puente:8084/%s/index.m3u8" % ("bb" * 20), lines[4]
    # Misma politica de frescura que las otras cuatro listas: `alive`, que es lo que la app ya
    # sabe de cada canal (`check_result` cubre el catalogo entero desde que la guia vive dentro
    # del propio servicio). Publicarlo todo metia en el reproductor enlaces sin pares.
    assert STATUS == "alive", STATUS
    # Y un canal por nombre visible, igual que en `acestream-suite-playlist.py`.
    repetidos = [
        {"title": "M. Liga de Campeones", "content_id": "ff" * 20, "category": "Deportes",
         "resolution": "720p"},
        {"title": "M. Liga de Campeones", "content_id": "11" * 20, "category": "Deportes ES",
         "resolution": "1080p"},
        {"title": "M. Liga de Campeones", "content_id": "22" * 20, "category": "Regionales"},
    ]
    texto_repetido = build_playlist(repetidos, {})
    assert texto_repetido.count("#EXTINF") == 1, texto_repetido
    assert 'tvg-name="M. Liga de Campeones"' in texto_repetido, texto_repetido
    assert ("11" * 20) in texto_repetido, texto_repetido
    # An empty catalogue is still a valid playlist, not an empty file, and the header survives.
    assert build_playlist([], {}) == '#EXTM3U url-tvg="http://192.168.1.245:8083/epg.xml"\n'
    # The guide URL is overridable and sanitised like every other attribute.
    assert (
        build_playlist([], {}, url_tvg='http://otra/epg.xml"x').splitlines()[0]
        == '#EXTM3U url-tvg="http://otra/epg.xml\'x"'
    )
    # The two fetchers build the URLs they claim to.
    assert export_url("http://caja:8087/", "alive") == "http://caja:8087/export?status=alive"
    assert export_url("http://caja:8087", "") == "http://caja:8087/export"
    assert mapping_url("http://caja:8087/") == "http://caja:8087/epg.json"

    # S3/S4: el nombre nuevo, la copia con el viejo al lado, y la variable nueva ganando a la vieja.
    assert OUTPUT == "/srv/www/varios/acestream-suite-guide.m3u", OUTPUT
    assert compat_copy(OUTPUT) == "/srv/www/varios/acelist-epg.m3u", compat_copy(OUTPUT)
    assert compat_copy("/tmp/otra.m3u") is None, compat_copy("/tmp/otra.m3u")
    temporal = tempfile.mkdtemp()
    try:
        guardado = os.path.join(temporal, "acestream-suite-guide.m3u")
        assert save_output("#EXTM3U\n", guardado) is True
        for nombre in ("acestream-suite-guide.m3u", "acelist-epg.m3u"):
            with open(os.path.join(temporal, nombre)) as fichero:
                assert fichero.read() == "#EXTM3U\n", nombre
        assert save_output("#EXTM3U\n", guardado) is False, "un fichero igual no se reescribe"
    finally:
        shutil.rmtree(temporal)
    respaldo = dict(os.environ)
    try:
        os.environ.pop("ACESTREAM_SUITE_GUIDE_URL", None)
        os.environ["ACELIST_EPG_URL"] = "http://viejo:8083"
        assert env("ACESTREAM_SUITE_GUIDE_URL", "ACELIST_EPG_URL", "d") == "http://viejo:8083"
        os.environ["ACESTREAM_SUITE_GUIDE_URL"] = "http://nuevo:8083"
        assert env("ACESTREAM_SUITE_GUIDE_URL", "ACELIST_EPG_URL", "d") == "http://nuevo:8083"
    finally:
        os.environ.clear()
        os.environ.update(respaldo)
    print("self-test OK")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="acestream-suite's catalogue as a playlist with a guide")
    parser.add_argument("--acestream-suite", default=ACESTREAM_SUITE_GUIDE_URL,
                        help="acestream-suite base URL")
    parser.add_argument("--hls-base", default=HLS_BASE, help="HLS bridge base URL")
    parser.add_argument("--output", default=OUTPUT, help="the .m3u file to write")
    parser.add_argument("--url-tvg", default=URL_TVG, help="the guide URL on the #EXTM3U line")
    parser.add_argument("--status", default=STATUS,
                        help="availability filter, as the list view takes it (empty: all)")
    parser.add_argument("--dry-run", action="store_true", help="print it, write nothing")
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
        mapping = fetch_mapping(args.acestream_suite)
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        log("cannot read the catalogue or the mapping from %s: %s" % (args.acestream_suite, exc))
        return 1

    text = build_playlist(channels, mapping, args.hls_base, args.url_tvg)
    if args.dry_run:
        print("--- %d channels, %d mapped ---" % (len(channels), len(mapping)), file=sys.stderr)
        sys.stdout.write(text)
        return 0

    changed = save_output(text, args.output)
    alias = compat_copy(args.output)
    log(
        "%d channels, %d mapped, %s%s: %s"
        % (len(channels), len(mapping), args.output,
           " (+ %s)" % os.path.basename(alias) if alias else "",
           "written" if changed else "unchanged")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
