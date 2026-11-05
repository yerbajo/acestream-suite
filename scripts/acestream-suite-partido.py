#!/usr/bin/env python3
"""Adds the channels airing a Real Madrid match, live, to acestream-suite — an hour before kick-off.

WHY THIS EXISTS
    The owner asked for the channels airing Real Madrid matches — football and basketball — to be
    searched in acesearch and imported into acestream-suite on match day, one hour before kick-off, so the
    sports and HLS playlists can carry a `Real Madrid` folder with them. "Pero quiero que sean en
    directo": the folder and `madrid.m3u` must be the match that is on air, never a replay and never a
    category that accumulates. This script is that job.

WHERE THE FIXTURES COME FROM
    Two keyless sources, both measured on 2026-11-04 from this workstation, and the guide is used only
    to know WHICH CHANNEL airs them:

    - football: `eventsnext.php?id=133738` (Real Madrid CF) on thesportsdb.com — `strTimestamp` in UTC
      and `strLeague`;
    - basketball: the official EuroLeague API,
      `api-live.euroleague.net/v2/competitions/E/seasons/E2026/games` — `utcDate`, `local`/`road`.

    Liga Endesa (ACB) has no keyless source that could be verified, so those matches are NOT added and
    this job says so in its log. The old test key `3` of TheSportsDB is permanently disabled; `123` is
    the current free one, so a non-200 there means "unknown": it is logged and nothing is added.

WHY THE TITLE OF THE GUIDE IS NOT THE SIGNAL
    Measured on 2026-11-04: the guide airs `Euroliga de baloncesto: Real Madrid - Panathinaikos` at
    23:30 on `M+ Deportes`, and the real tip-off was 19:45Z — that programme is the DELAYED airing,
    while the live one is `Movistar Plus+` at 19:45 titled just `Euroliga`, which does not name the
    club at all. In the same guide, `DAZN LALIGA` at 18:00 carries `R. Racing Club - Real Madrid`, a
    replay of the match played on 2026-11-01. A rule that reads the title picks both wrong programmes;
    the real kick-off picks both right ones.

THE LIVE RULE
    A guide programme is the live airing of a fixture when all three hold: its `start` falls between
    `--live-tolerance-before` (20 minutes) before the real kick-off and `--live-tolerance-after`
    (5 minutes) after it — an airing may open its pre-match show early, it never starts a quarter of an
    hour late —; its channel is not the club's own (`Real Madrid TV`, `RMTV`, `realmadridtv`); and its
    title names the fixture's competition (`COMPETICIONES` below). A replay starts at another hour and
    never satisfies the first one: with a symmetric 20 minutes, `DAZN LALIGA` at 18:00 for a 17:45
    kick-off slipped through and only the competition keyword rejected it.

THE TRIGGER WINDOW
    From 60 minutes before the real kick-off (`--window-before`) to the `stop` of the matched programme
    in the guide. The cron runs every 10 minutes, so the search starts inside the hour before and keeps
    running while the match is on air — new publishers appear, others die. A match of three hours ago
    or a replay tomorrow falls outside it and never triggers.

WHAT IT IMPORTS
    For every live airing, the search terms are the guide channel's display name (`DAZN LALIGA`) and,
    when `/epg.json` maps that guide id to a catalogue channel, that channel's title as well.
    `GET /search.m3u?query=<term>&json=1` answers with the streams the engine knows, and of those it
    keeps the ones that are THAT channel — names compared after normalising them (platform prefix,
    publisher tail, punctuation and case dropped), so `M+ LIGA DE CAMPEONES FHD --> NEW ERA` is the
    channel searched for and `M+ LIGA DE CAMPEONES 2 FHD --> NEW ERA` is its neighbour —, that are not
    already in the catalogue, and at most `--max-per-channel` (8) per term. They go in with
    `POST /import` as an acestream-suite export document (`format: "acelist"` v1 — el marcador de la
    app, que no cambia con la marca —, `category: "Real Madrid"`, `language: "es"`, `country: "ES"`);
    the app queues its own availability checks and drops duplicates by content id.

THE STATE FILE
    `/var/lib/acestream-suite-partido/state.json` records every `(kick-off, guide id, term)` that was
    actually imported, with the content ids it brought, so the 10-minute cron does not import the same
    match again and again. It never deletes anything. A term whose search found nothing is NOT
    recorded: that is the whole point of the retry inside the window — the channel may only get peers
    a few minutes later. Mientras el fichero viejo (`/var/lib/acelist-partido/state.json`) sea el unico
    que existe, se sigue leyendo de ahi y se escribe ahi (S4): perderlo seria reimportar medio partido.

THE LIVE SET
    `/var/lib/acestream-suite-partido/live.json` (`--live-file`) is rewritten after every run with the
    content ids airing the match now or in the hour before, each one with the `stop` of its programme.
    It carries the catalogue channels the guide maps to that channel AND the ones this job imported
    (they have no guide mapping until the app matches them again).
    `scripts/acestream-suite-playlist.py` reads it: the `Real Madrid` folder in
    `deportes.m3u`/`hls.m3u` and the `madrid.m3u` list are that set and nothing else, so they appear
    with the match and go away when it ends, while the channels stay in the catalogue for good.

WHAT THIS CANNOT DO (measured limitation)
    The fixture sources say when the match is played, the guide says which channel airs it at that
    hour, the engine says which streams that channel has, and the app's check says the stream is alive.
    None of them proves what is on the screen. The rule is the one in
    `suite-aar/docs/acestream-manual.md`: acesearch does not search for an event, it searches for the
    channel that airs it, and the frame is the owner's eyes.

INSTALL (on dev.albertucho.home, the .245 host)
    install -m 0755 scripts/acestream-suite-partido.py /usr/local/bin/acestream-suite-partido
    mkdir -p /var/lib/acestream-suite-partido
    /usr/local/bin/acestream-suite-partido --dry-run     # what it would do right now, touching nothing
    # The cron entry comes from scripts/acestream-suite-partido.cron, through scripts/crons-install.sh

USAGE
    acestream-suite-partido                  # search and import what the window has open
    acestream-suite-partido --dry-run        # print what it would import, write nothing
    acestream-suite-partido --self-test      # the checks below, no network, no engine and no files
    acestream-suite-partido --engine http://otro:8081 --acestream-suite http://otro:8083 \
        --state /tmp/state.json

    Everything has a default and every default is overridable by flag and by environment
    (`ACESTREAM_SUITE_URL`, `ACESTREAM_SUITE_PARTIDO_*`; las viejas `ACELIST_*` se siguen leyendo si
    las nuevas no estan: S4), which is what the cron job uses. Runs on the host's Python 3.6: no
    dataclasses, no `datetime.fromisoformat`, no walrus, standard library only.
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
import unicodedata
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET


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


# La app: 8083, el mismo acestream-suite que sirven las otras listas. Loopback porque el trabajo corre en el
# host, al lado de su propio contenedor.
ACESTREAM_SUITE_URL = env("ACESTREAM_SUITE_URL", "ACELIST_URL", "http://127.0.0.1:8083")
# El motor de acesearch: el nginx del stack de .28, el mismo que consultan las listas.
ENGINE_URL = env("ACESTREAM_SUITE_PARTIDO_ENGINE", "ACELIST_PARTIDO_ENGINE",
                 "http://192.168.1.28:8081")
STATE_PATH = env("ACESTREAM_SUITE_PARTIDO_STATE", "ACELIST_PARTIDO_STATE",
                 "/var/lib/acestream-suite-partido/state.json")
# S4: el estado viejo se sigue leyendo mientras sea el unico que existe.
STATE_PATH_LEGACY = "/var/lib/acelist-partido/state.json"
# S11: el conjunto en directo que lee `scripts/acestream-suite-playlist.py` para decidir la carpeta. Se
# escribe tras cada pasada; el mismo nombre de variable de entorno lo mueve en los dos scripts.
LIVE_PATH = env("ACESTREAM_SUITE_PARTIDO_LIVE", "ACELIST_PARTIDO_LIVE",
                "/var/lib/acestream-suite-partido/live.json")
LOG_PATH = env("ACESTREAM_SUITE_PARTIDO_LOG", "ACELIST_PARTIDO_LOG",
               "/var/log/acestream-suite-partido.log")
LOCK_PATH = env("ACESTREAM_SUITE_PARTIDO_LOCK", "ACELIST_PARTIDO_LOCK",
                "/var/lock/acestream-suite-partido.lock")
TIMEOUT = float(env("ACESTREAM_SUITE_PARTIDO_TIMEOUT", "ACELIST_PARTIDO_TIMEOUT", "30"))
# S1: la API de la Euroliga contesta 403 al User-Agent por defecto de urllib (`Python-urllib/3.6`) y 200 a
# cualquier otro (medido el 2026-11-04 desde el propio .245). Se identifica el cliente, que es lo que
# pide una API cuando distingue por agente.
USER_AGENT = env("ACESTREAM_SUITE_PARTIDO_UA", "ACELIST_PARTIDO_UA", "acestream-suite-partido/1.0")
WINDOW_BEFORE = int(env("ACESTREAM_SUITE_PARTIDO_WINDOW_BEFORE",
                        "ACELIST_PARTIDO_WINDOW_BEFORE", "60"))
MAX_PER_CHANNEL = int(env("ACESTREAM_SUITE_PARTIDO_MAX_PER_CHANNEL",
                          "ACELIST_PARTIDO_MAX_PER_CHANNEL", "8"))
# S2: cuántos minutos puede separar el `start` del programa en la guía del saque real, por delante y por
# detras. NO es simetrico, y la razon esta medida: una emision en directo puede abrir su previa antes
# (el `AEK Atenas - Real Madrid` de las 17:38 para un saque a las 17:45 son 7 minutos antes), pero no
# puede empezar un cuarto de hora tarde —con ±20 el `Racing Club - Real Madrid` de las 18:00, repetido
# del 1 de noviembre, pasaba el filtro de hora y solo lo rechazaba la competicion.
LIVE_TOLERANCE_BEFORE = int(env("ACESTREAM_SUITE_PARTIDO_LIVE_TOLERANCE_BEFORE",
                               "ACELIST_PARTIDO_LIVE_TOLERANCE_BEFORE", "20"))
LIVE_TOLERANCE_AFTER = int(env("ACESTREAM_SUITE_PARTIDO_LIVE_TOLERANCE_AFTER",
                              "ACELIST_PARTIDO_LIVE_TOLERANCE_AFTER", "5"))
# El `Origin` que exige el POST /import de la app (su comprobacion CSRF, ver docs/acestream-suite.md): la
# direccion LAN de la casa, no la de loopback con la que este script lee.
ORIGIN = env("ACESTREAM_SUITE_PARTIDO_ORIGIN", "ACELIST_PARTIDO_ORIGIN",
             "http://192.168.1.245:8083")
# S1: los partidos y su hora real de saque. Ninguna de las dos fuentes pide clave.
FOOTBALL_URL = env(
    "ACESTREAM_SUITE_PARTIDO_FOOTBALL_URL",
    "ACELIST_PARTIDO_FOOTBALL_URL",
    "https://www.thesportsdb.com/api/v1/json/123/eventsnext.php?id=133738",
)
BASKETBALL_URL = env(
    "ACESTREAM_SUITE_PARTIDO_BASKETBALL_URL",
    "ACELIST_PARTIDO_BASKETBALL_URL",
    "https://api-live.euroleague.net/v2/competitions/E/seasons/E2026/games",
)

# La categoria con la que se importan: es la que `scripts/acestream-suite-playlist.py` convierte en la
# carpeta `Real Madrid` de `deportes.m3u` y `hls.m3u` mientras el partido esta en directo.
CATEGORIA = "Real Madrid"
# El filtro de disponibilidad del catalogo. VACIO a proposito: los duplicados se descartan contra el
# catalogo ENTERO (un canal caido sigue estando en el catalogo y volver a importarlo no aporta nada),
# no solo contra la parte viva que publican las listas.
STATUS = env("ACESTREAM_SUITE_PARTIDO_STATUS", "ACELIST_PARTIDO_STATUS", "")

# Los marcadores de formato son el contrato con la app y con los ficheros que ya existen, no la marca
# del producto: `format: "acelist"` lo escribe la app en su export, `/epg.json` marca
# `format: "acelist-epg"`, y el estado y el conjunto en directo viejos solo se siguen leyendo si su
# marca no cambia (S4).
STATE_FORMAT = "acelist-partido"
LIVE_FORMAT = "acelist-partido-live"
MAPPING_FORMAT = "acelist-epg"
EXPORT_FORMAT = "acelist"

# S2, y la tabla que separa el directo de la repeticion. El titulo del programa no basta: el directo
# del Madrid-Panathinaikos se llama solo `Euroliga` a las 19:45 y la repeticion se llama `Euroliga de
# baloncesto: Real Madrid - Panathinaikos` a las 23:30. La primera condicion (el saque real) es la que
# decide; esto es el segundo filtro, para no casar cualquier programa que caiga a esa hora.
# `(competicion que da la fuente, titulo que tiene que nombrarla en la guia)`.
COMPETICIONES = (
    (re.compile(r"champions", re.IGNORECASE),
     # `champions\s+league` y no `champions`: el torneo de golf de veteranos se llama
     # `PGA Tour Champions` y emite a la misma hora (visto en la guia el 2026-11-04).
     re.compile(r"champions\s+league|liga\s+de\s+campeones", re.IGNORECASE)),
    (re.compile(r"laliga|la liga|primera divisi[oó]n", re.IGNORECASE),
     re.compile(r"laliga|la liga", re.IGNORECASE)),
    (re.compile(r"euroleague|euroliga", re.IGNORECASE),
     re.compile(r"euroliga|baloncesto", re.IGNORECASE)),
    # `copa del rey` y no `copa`: `Copa del mundo de escalada` emite en Eurosport.
    (re.compile(r"copa", re.IGNORECASE),
     re.compile(r"copa\s+del\s+rey", re.IGNORECASE)),
)

# S2: los canales del club no cuentan, aunque el programa sea el partido. Se compara contra una forma
# compacta (solo letras y digitos, en minusculas), porque el id de guia es `RealMadrid.TV.es`; `\s?`
# cubre tanto `Real Madrid TV` como `realmadridtv`.
CANAL_DEL_CLUB = re.compile(r"\b(?:real\s?madrid\s?tv|rmtv)\b")
# S1: el club en el nombre de un partido o de un equipo, sin confundirlo con "realmadridistas".
CLUB = re.compile(r"\breal madrid\b", re.IGNORECASE)

# S5: lo que separa un canal de su vecino. El prefijo de plataforma no distingue un canal de otro
# (`M+ Liga de Campeones` y `M. Liga de Campeones` son el mismo), y la cola del publicador tampoco
# (`--> NEW ERA`, `elcano` / `elcano.top`). Se quitan los dos antes de comparar nombres.
PREFIJOS = ("m", "movistar")
COLA_PUBLICADOR = re.compile(r"-+>\s*new\s*era|elcano(?:\.top)?", re.IGNORECASE)

# La linea con la que la app contesta al POST /import: `N canales creados, M duplicados y K canales
# invalidos`. Se busca con "inv" para no depender de la tilde.
IMPORT_RESULT = re.compile(
    r"(\d+)\s+canales creados,\s*(\d+)\s+duplicados y\s*(\d+)\s+canales inv", re.IGNORECASE
)

HASH = re.compile(r"^[0-9a-f]{40}$")


def log(message, path=LOG_PATH):
    """One line with a timestamp, to the log file and to stdout (a manual run is worth reading)."""
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line)
    if path:
        try:
            with open(path, "a") as handle:
                handle.write(line + "\n")
        except OSError as exc:  # un /var/log de solo lectura no puede tumbar el trabajo
            print("acestream-suite-partido: cannot write %s: %s" % (path, exc), file=sys.stderr)


def now_utc():
    """Ahora en UTC y SIN zona: las fuentes y la guia vienen en UTC, y comparar aware con naive falla."""
    return datetime.datetime(*time.gmtime()[:6])


def parse_utc(value):
    """S1: una fecha de las dos fuentes a datetime UTC sin zona, o None si no se entiende.

    Las dos formas medidas: la Euroliga da `2026-11-04T19:45:00Z` y TheSportsDB `2026-11-04T17:45:00`,
    que ya viene en UTC y sin sufijo. `datetime.fromisoformat` es de Python 3.7 y el host tiene 3.6.8,
    asi que se parsea con `strptime`.
    """
    stamp = " ".join(str(value or "").split())
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(stamp, fmt)
        except ValueError:
            continue
    return None


def names_club(*names):
    """S1: True si alguno de estos nombres lleva al club (un equipo, no una mencion)."""
    for name in names:
        compacto = " ".join(re.sub(r"[^0-9a-z]+", " ", str(name or "").lower()).split())
        if CLUB.search(compacto):
            return True
    return False


# Palabras que no distinguen un equipo de otro en un titulo de la guia. `athens` esta aqui porque la
# guia traduce al rival: `AEK Athens` en la fuente es `AEK Atenas` en la guia, y de ese nombre solo
# sirve `aek`.
PALABRAS_VACIAS = frozenset((
    "real", "madrid", "club", "fc", "cf", "cd", "de", "del", "la", "el", "the",
    "baloncesto", "basket", "basketball", "athens", "ateneas",
))


def words(text):
    """Las palabras de un nombre, sin acentos y en minusculas (3.6: `unicodedata`)."""
    plano = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^0-9a-z]+", " ", plano.lower()).split()


def rival_of(event, teams=()):
    """El rival del club en ese partido, para poder buscarlo en el titulo del programa de la guia.

    `AEK Athens vs Real Madrid` da `AEK Athens`; en baloncesto se pasan los dos equipos ya separados.
    Sin rival conocido devuelve la cadena vacia, y entonces solo cuenta el nombre del club.
    """
    equipo = list(teams)
    if not equipo:
        equipo = [parte for parte in re.split(r"\s+vs\.?\s+|\s+-\s+", str(event or "")) if parte.strip()]
    rivales = [nombre for nombre in equipo if nombre.strip() and not names_club(nombre)]
    return " ".join(rivales[0].split()) if rivales else ""


def names_fixture_teams(programme, fixture):
    """S2: True si ese programa nombra al Real Madrid o al rival de ESTE partido.

    Es lo que separa el programa correcto de sus vecinos del mismo saque: en un dia de Champions hay
    varios canales emitiendo partidos distintos a la misma hora (`UEFA Champions League: Fenerbahçe -
    Liverpool` en `M+ Liga de Campeones  3`) y el torneo de golf de veteranos se llama `PGA Tour
    Champions`, asi que la competicion sola no basta. Cuando el titulo no nombra a nadie (`Euroliga`),
    decide `live_programmes`: ese programa solo se acepta si no hay ninguno que si los nombre.
    """
    titulo = words(programme.get("title"))
    if names_club(" ".join(titulo)):
        return True
    for palabra in words(fixture.get("rival")):
        if len(palabra) >= 3 and palabra not in PALABRAS_VACIAS and palabra in titulo:
            return True
    return False


def parse_football_fixtures(payload):
    """S1: `eventsnext.php` de TheSportsDB a partidos `{kickoff, competition, event}`.

    El endpoint ya es del club (`id=133738`), asi que no se filtra por nombre. Una clave desactivada
    contesta `{"events": null}`: eso es una lista vacia, no un error de formato.
    """
    fixtures = []
    for event in (payload or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        kickoff = parse_utc(event.get("strTimestamp"))
        nombre = " ".join(str(event.get("strEvent") or "").split())
        if kickoff is None or not nombre:
            continue
        fixtures.append(
            {
                "kickoff": kickoff,
                "competition": " ".join(str(event.get("strLeague") or "").split()),
                "event": nombre,
                "rival": rival_of(nombre, ()),
                "source": "football",
            }
        )
    return fixtures


def team_name(game, side):
    """El nombre del equipo de un lado de la API de la Euroliga.

    La API lo mete en `local.club.name` —con `tvCode`, `editorialName` y los escudos al lado—, y una
    respuesta resumida lo trae plano en `local.name`. Se aceptan las dos formas: leer solo `name` fue
    el fallo que dejo la Euroliga en cero partidos en la primera pasada real (2026-11-04).
    """
    lado = game.get(side) or {}
    if not isinstance(lado, dict):
        return ""
    club = lado.get("club") or {}
    nombre = (club.get("name") if isinstance(club, dict) else None) or lado.get("name") or ""
    return " ".join(str(nombre).split())


def parse_basketball_fixtures(payload):
    """S1: la API de la Euroliga a partidos `{kickoff, competition, event}`, filtrando por el club.

    El endpoint es de una competicion sola, asi que la competicion es la constante `EuroLeague` y no
    se lee de cada partido: es lo que hace que el titulo del directo (`Euroliga`) case.
    """
    fixtures = []
    for game in ((payload or {}).get("data")) or []:
        if not isinstance(game, dict):
            continue
        local = team_name(game, "local")
        visitante = team_name(game, "road")
        if not names_club(local, visitante):
            continue
        kickoff = parse_utc(game.get("utcDate"))
        if kickoff is None:
            continue
        fixtures.append(
            {
                "kickoff": kickoff,
                "competition": "EuroLeague",
                "event": "%s - %s" % (local, visitante),
                "rival": rival_of("%s - %s" % (local, visitante), (local, visitante)),
                "source": "basketball",
            }
        )
    return fixtures


def competition_pattern(competition):
    """S2: el titulo que tiene que llevar el programa de la guia para esa competicion, o None.

    None significa "competicion que no sabemos reconocer" (una Liga Endesa, por ejemplo): sin patron
    no se casa ningun programa y el partido no se añade. Inventar el patron seria adivinar.
    """
    for fuente, titulo in COMPETICIONES:
        if fuente.search(str(competition or "")):
            return titulo
    return None


def element_tag(element):
    """El nombre del elemento sin el namespace, que XMLTV a veces trae (`{...}programme`)."""
    return element.tag.rsplit("}", 1)[-1]


def parse_guide(text):
    """S1: la guia XMLTV a una lista de programas `{start, stop, guide_id, title, channel_name}`.

    Solo se leen `<programme>` y `<channel>`; lo que no se entiende se descarta en silencio, que es lo
    unico razonable con un documento de 550 KB y ~25.000 programas de fuentes que no controlamos.
    """
    root = ET.fromstring(text)
    nombres = {}
    for element in root.iter():
        if element_tag(element) != "channel":
            continue
        guide_id = element.get("id") or ""
        for child in element:
            if element_tag(child) != "display-name":
                continue
            nombre = " ".join((child.text or "").split())
            if nombre:
                nombres[guide_id] = nombre
            break
    programas = []
    for element in root.iter():
        if element_tag(element) != "programme":
            continue
        title = ""
        for child in element:
            if element_tag(child) == "title":
                title = " ".join((child.text or "").split())
                break
        start = parse_guide_time(element.get("start"))
        guide_id = element.get("channel") or ""
        if start is None or not title or not guide_id:
            continue
        programas.append(
            {
                "start": start,
                "stop": parse_guide_time(element.get("stop")),
                "guide_id": guide_id,
                "title": title,
                "channel_name": nombres.get(guide_id, guide_id),
            }
        )
    return programas


def parse_guide_time(value):
    """Un `<programme start>` de XMLTV (`20261104180000 +0000`) a datetime UTC sin zona, o None."""
    stamp = " ".join(str(value or "").split())
    for fmt in ("%Y%m%d%H%M%S %z", "%Y%m%d%H%M%S"):
        try:
            momento = datetime.datetime.strptime(stamp, fmt)
        except ValueError:
            continue
        if momento.tzinfo is not None:
            momento = momento.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return momento
    return None


def is_club_channel(*names):
    """S2: True si alguno de esos nombres es un canal del propio club."""
    for name in names:
        compacto = " ".join(re.sub(r"[^0-9a-z]+", " ", str(name or "").lower()).split())
        if CANAL_DEL_CLUB.search(compacto):
            return True
    return False


def live_programmes(fixture, programmes, before=LIVE_TOLERANCE_BEFORE, after=LIVE_TOLERANCE_AFTER):
    """S2: los programas de la guia que son la emision EN DIRECTO de ese partido.

    Tres condiciones, y las tres a la vez: el `start` cae entre `before` minutos antes del saque real y
    `after` despues —una previa puede abrir antes, una emision no empieza un cuarto de hora tarde—, el
    canal no es del club, y el titulo nombra la competicion del partido. La primera es la que separa el
    directo de la repeticion —`M+ Liga de Campeones` 17:38 y `Movistar Plus+` 19:45 si; `DAZN LALIGA`
    18:00 y `M+ Deportes` 23:30 no— y la tercera evita quedarse con cualquier programa que caiga a esa
    hora.
    """
    titulo = competition_pattern(fixture.get("competition"))
    if titulo is None:
        return []
    desde = fixture["kickoff"] - datetime.timedelta(minutes=before)
    hasta = fixture["kickoff"] + datetime.timedelta(minutes=after)
    encontrados = []
    for programa in programmes:
        if not desde <= programa["start"] <= hasta:
            continue
        if is_club_channel(programa.get("channel_name"), programa.get("guide_id")):
            continue
        if not titulo.search(programa.get("title") or ""):
            continue
        encontrados.append(programa)
    # S2: si hay un programa que nombra a los equipos, los demas son vecinos (otro partido al mismo
    # saque, el golf de veteranos, un magazine) y se descartan. Si no lo hay, vale el que nombra solo la
    # competicion, que es como emite Movistar Plus+ la Euroliga (`Euroliga`, sin equipos).
    con_equipos = [programa for programa in encontrados if names_fixture_teams(programa, fixture)]
    return con_equipos or encontrados


def live_airings(fixtures, programmes, before=LIVE_TOLERANCE_BEFORE, after=LIVE_TOLERANCE_AFTER):
    """S2: cada partido con los programas de la guia que lo emiten en directo, ya emparejados."""
    airings = []
    for fixture in fixtures:
        for programa in live_programmes(fixture, programmes, before, after):
            airings.append(
                {
                    "kickoff": fixture["kickoff"],
                    "competition": fixture.get("competition"),
                    "event": fixture.get("event"),
                    "programme": programa,
                }
            )
    return airings


def programme_end(programme):
    """S3: el final del programa en la guía, que es donde acaba la ventana de disparo.

    # Sin `<stop>` se estiman dos horas: ningún partido dura más, y así la ventana no se queda
    # abierta para siempre con una guía a la que le falta el atributo.
    """
    return programme.get("stop") or programme["start"] + datetime.timedelta(hours=2)


def in_window(airing, now, before=WINDOW_BEFORE):
    """S3: ahora cae entre `before` minutos antes del saque real y el final de la emisión.

    Mientras el partido está en directo la ventana sigue abierta, para añadir publicadores nuevos y
    tapar los que se caigan; una repetición empieza a otra hora y no cae aquí nunca.
    """
    return (
        airing["kickoff"] - datetime.timedelta(minutes=before)
        <= now
        <= programme_end(airing["programme"])
    )


def start_key(airing):
    """El saque real del partido como texto, que es lo que se guarda en el estado."""
    return airing["kickoff"].strftime("%Y-%m-%dT%H:%M:%SZ")


def guide_titles(mapping):
    """S4: `{guide_id: [titulos del catalogo]}` a partir de `/epg.json`.

    El documento mapea `content_id -> {guide_id, guide_name}`; aqui se da la vuelta para poder
    preguntar "que canales del catalogo dicen ser este canal de la guia".
    """
    titulos = {}
    for datos in (mapping or {}).values():
        if not isinstance(datos, dict):
            continue
        guide_id = datos.get("guide_id")
        nombre = " ".join(str(datos.get("guide_name") or "").split())
        if not guide_id or not nombre:
            continue
        lista = titulos.setdefault(guide_id, [])
        if nombre.lower() not in [otro.lower() for otro in lista]:
            lista.append(nombre)
    return titulos


def search_terms(airing, extra_titles=()):
    """S4: el nombre del canal de la guia y, si el mapeo lo da, el titulo del canal del catalogo."""
    terminos = []
    for termino in [airing["programme"].get("channel_name")] + list(extra_titles):
        termino = " ".join(str(termino or "").split())
        if termino and termino.lower() not in [otro.lower() for otro in terminos]:
            terminos.append(termino)
    return terminos


def channel_identity(name):
    """S5: el nombre de un canal, normalizado para compararlo consigo mismo y no por tokens.

    Se quita la cola del publicador (`--> NEW ERA`, `elcano.top …`), el prefijo de plataforma (`M+`,
    `M.`, `Movistar`, que no distingue un canal de otro), la puntuacion y las mayusculas. Es lo que
    separa `M+ LIGA DE CAMPEONES FHD --> NEW ERA` (el canal que emite) de
    `M+ LIGA DE CAMPEONES 2 FHD --> NEW ERA` (el canal 2, que no emite): los dos llevan los mismos
    tokens, y solo el segundo lleva un numero detras del nombre. `lower` y no `casefold` por 3.6.
    """
    texto = COLA_PUBLICADOR.split(str(name or "").lower())[0]
    tokens = re.sub(r"[^0-9a-z]+", " ", texto).split()
    while tokens and tokens[0] in PREFIJOS:
        tokens.pop(0)
    return " ".join(tokens)


def same_channel(name, target):
    """S5: True si ese nombre es el canal buscado, y no un vecino suyo.

    Tras normalizar, o son iguales, o el nombre empieza por el buscado y lo que sigue no es un numero:
    `… campeones hd` es el mismo canal con otra calidad, `… campeones 2` es el canal 2.
    """
    identidad = channel_identity(name)
    objetivo = channel_identity(target)
    if not identidad or not objetivo:
        return False
    if identidad == objetivo:
        return True
    if not identidad.startswith(objetivo + " "):
        return False
    resto = identidad[len(objetivo) + 1:]
    return bool(resto) and not resto[0].isdigit()


def search_entries(value, found=None):
    """S5: los objetos con `infohash` de la respuesta del motor, recorrida entera.

    La respuesta es una lista cuyos ultimos elementos son objetos sueltos (el ultimo, `{}`), asi que
    se camina el JSON entero en vez de leer solo el primer nivel.
    """
    if found is None:
        found = []
    if isinstance(value, dict):
        if value.get("infohash"):
            found.append(value)
            return found
        for item in value.values():
            search_entries(item, found)
    elif isinstance(value, list):
        for item in value:
            search_entries(item, found)
    return found


def candidates(entries, term, known_ids, limit=MAX_PER_CHANNEL):
    """S5: los resultados que valen, como mucho `limit` por termino.

    Un resultado vale si es ESE canal (`same_channel`) y no un vecino suyo, su infohash es un content
    id de verdad (40 hex) y ese content id no esta ya en el catalogo. Comparar por nombre normalizado
    y no por tokens es lo que deja fuera al `M+ LIGA DE CAMPEONES 2` cuando lo que emite es el canal sin
    numero. El tope es por termino porque es el motor el que ordena por lo que el conoce, y un solo
    canal puede traer decenas de publishers.
    """
    chosen = []
    seen = set()
    for entry in entries:
        infohash = str(entry.get("infohash") or "").strip().lower()
        if not infohash or not HASH.match(infohash) or infohash in seen or infohash in known_ids:
            continue
        if not same_channel(entry.get("name"), term):
            continue
        seen.add(infohash)
        chosen.append(
            {
                "infohash": infohash,
                "name": " ".join(str(entry.get("name") or "").split()),
                "bitrate": entry.get("bitrate"),
                "availability": entry.get("availability"),
            }
        )
        if len(chosen) >= limit:
            break
    return chosen


def export_document(entries, category=CATEGORIA):
    """S5: el documento de exportacion de acestream-suite que acepta su propio *Importar*."""
    channels = []
    for entry in entries:
        channels.append(
            {
                "title": entry.get("name") or entry["infohash"],
                "title_pending": False,
                "content_id": entry["infohash"],
                "link": "acestream://" + entry["infohash"],
                "category": category,
                "language": "es",
                "country": "ES",
            }
        )
    return {"format": EXPORT_FORMAT, "version": 1, "channels": channels}


def import_summary(html):
    """Los tres numeros de la linea con la que contesta `/import`, o None si no esta."""
    resultado = IMPORT_RESULT.search(html or "")
    if not resultado:
        return None
    return tuple(int(grupo) for grupo in resultado.groups())


def state_empty():
    return {"format": STATE_FORMAT, "version": 1, "done": []}


def state_document(text):
    """El documento de estado a partir de su texto; un documento ajeno se rechaza, no se lee."""
    document = json.loads(text)
    if document.get("format") != STATE_FORMAT:
        raise ValueError("not an %s state (format=%r)" % (STATE_FORMAT, document.get("format")))
    if not isinstance(document.get("done"), list):
        raise ValueError("state document has no 'done' list")
    return document


def state_text(state):
    return json.dumps(state, indent=2, sort_keys=True) + "\n"


def processed_key(programme_start, guide_id, term):
    return "%s|%s|%s" % (programme_start, guide_id, term)


def already_processed(state, programme_start, guide_id, term):
    """S6: ese `(saque, guide id, termino)` ya se importo, asi que no se repite."""
    buscada = processed_key(programme_start, guide_id, term)
    for item in state.get("done") or []:
        if processed_key(item.get("start"), item.get("guide_id"), item.get("term")) == buscada:
            return True
    return False


def mark_processed(state, programme_start, guide_id, term, imported, ids=(), now=None):
    """S6: anota el `(saque, guide id, termino)` importado. Nunca borra nada.

    Los content ids se guardan con el: son los unicos que S11 no puede sacar del mapeo de la guia,
    porque un canal recien importado no esta mapeado hasta que la app lo vuelve a emparejar.
    """
    if already_processed(state, programme_start, guide_id, term):
        return state
    state.setdefault("done", []).append(
        {
            "start": programme_start,
            "guide_id": guide_id,
            "term": term,
            "imported": imported,
            "ids": sorted(str(content_id or "").lower() for content_id in ids),
            "at": (now or now_utc()).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    return state


def imported_ids(state, programme_start, guide_id):
    """S11: los content ids que este trabajo importo para ese partido y ese canal de guia."""
    ids = []
    for item in state.get("done") or []:
        if item.get("start") != programme_start or item.get("guide_id") != guide_id:
            continue
        for content_id in item.get("ids") or []:
            if content_id not in ids:
                ids.append(content_id)
    return ids


def live_document(airings, mapping, state, now=None):
    """S11: los content ids en directo ahora o en la hora previa, con el `stop` de su programa.

    Salen de dos sitios: los canales del catalogo que la guia mapea a ese canal de guia, y los que
    importo el propio trabajo (un canal recien importado no tiene mapeo todavia). Si dos programas
    comparten un canal gana el `stop` mas tardio: la carpeta dura hasta que acabe el ultimo.
    """
    por_guia = {}
    for content_id, datos in (mapping or {}).items():
        if not isinstance(datos, dict):
            continue
        guide_id = datos.get("guide_id")
        if guide_id:
            por_guia.setdefault(guide_id, []).append(str(content_id or "").lower())
    canales = {}
    for airing in airings:
        programa = airing["programme"]
        clave = start_key(airing)
        fin = programme_end(programa).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids = list(por_guia.get(programa["guide_id"], []))
        ids.extend(imported_ids(state, clave, programa["guide_id"]))
        for content_id in ids:
            if content_id and (content_id not in canales or fin > canales[content_id]):
                canales[content_id] = fin
    return {
        "format": LIVE_FORMAT,
        "version": 1,
        "generated": (now or now_utc()).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channels": canales,
    }


def load_state(path):
    """El estado del disco; un fichero que no existe es un estado vacio."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return state_document(handle.read())
    except IOError as exc:
        if getattr(exc, "errno", None) == 2:  # ENOENT: primera vez
            return state_empty()
        raise


def save_state(state, path):
    """Atomico: un estado a medias solo cuesta reimportar duplicados, pero no se deja por escribir."""
    write_text(state_text(state), path, ".acestream-suite-partido-")


def write_text(text, path, prefix):
    """Atomico y solo cuando cambia: un fichero igual conserva su mtime, que es lo que miran los
    reproductores que refrescan por su cuenta."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            if handle.read() == text:
                return False
    except (IOError, OSError):
        pass
    directory = os.path.dirname(path) or "."
    if not os.path.isdir(directory):
        os.makedirs(directory)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=prefix)
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


def save_live(document, path=LIVE_PATH):
    """S11: el conjunto en directo, atomico y solo cuando cambia."""
    return write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", path,
                      ".acestream-suite-partido-live-")


def guide_url(base_url=ACESTREAM_SUITE_URL):
    """`/epg.xml`, la guia XMLTV que sirve la propia app."""
    return base_url.rstrip("/") + "/epg.xml"


def mapping_url(base_url=ACESTREAM_SUITE_URL):
    """`/epg.json`: el mapeo `content_id -> {guide_id, guide_name}` del catalogo."""
    return base_url.rstrip("/") + "/epg.json"


def export_url(base_url=ACESTREAM_SUITE_URL, status=STATUS):
    """`/export`, con el filtro de disponibilidad solo cuando se pide uno."""
    url = base_url.rstrip("/") + "/export"
    return url + ("?status=" + urllib.parse.quote(status) if status else "")


def import_url(base_url=ACESTREAM_SUITE_URL):
    return base_url.rstrip("/") + "/import"


def search_url(term, engine=ENGINE_URL):
    """`/search.m3u` del motor, en su variante JSON."""
    return "%s/search.m3u?query=%s&json=1" % (
        engine.rstrip("/"),
        urllib.parse.quote(str(term or "")),
    )


def fetch_text(url, timeout=TIMEOUT):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def fetch_json(url, timeout=TIMEOUT):
    return json.loads(fetch_text(url, timeout))


def fetch_guide(base_url=ACESTREAM_SUITE_URL, timeout=TIMEOUT):
    """S1: el texto crudo de la guia, que `parse_guide` convierte en programas."""
    return fetch_text(guide_url(base_url), timeout)


def fetch_catalog_ids(base_url=ACESTREAM_SUITE_URL, timeout=TIMEOUT, status=STATUS):
    """Los content ids que ya estan en el catalogo, para no importarlos otra vez."""
    url = export_url(base_url, status)
    document = fetch_json(url, timeout)
    if document.get("format") != EXPORT_FORMAT:
        raise ValueError("%s is not an acestream-suite export (format=%r)"
                         % (url, document.get("format")))
    return set((channel.get("content_id") or "").lower() for channel in document["channels"])


def fetch_mapping(base_url=ACESTREAM_SUITE_URL, timeout=TIMEOUT):
    """El mapeo de la guia. Que no exista es normal: `main` sigue solo con los nombres de la guia."""
    url = mapping_url(base_url)
    document = fetch_json(url, timeout)
    if document.get("format") != MAPPING_FORMAT:
        raise ValueError("%s is not an EPG mapping (format=%r)" % (url, document.get("format")))
    return document.get("mapping") or {}


def fetch_football_fixtures(url=FOOTBALL_URL, timeout=TIMEOUT):
    """S1: los proximos partidos de futbol del club, con su hora real de saque."""
    return parse_football_fixtures(fetch_json(url, timeout))


def fetch_basketball_fixtures(url=BASKETBALL_URL, timeout=TIMEOUT):
    """S1: los partidos de la Euroliga, filtrados por el club."""
    return parse_basketball_fixtures(fetch_json(url, timeout))


def fetch_search(term, engine=ENGINE_URL, timeout=TIMEOUT):
    """Las entradas del motor que llevan `infohash`, para un termino."""
    return search_entries(fetch_json(search_url(term, engine), timeout))


def post_import(document, base_url=ACESTREAM_SUITE_URL, timeout=TIMEOUT, origin=ORIGIN):
    """`POST /import` con el documento como fichero, que es como lo manda la propia pagina."""
    cuerpo = json.dumps(document).encode("utf-8")
    frontera = uuid.uuid4().hex
    partes = [
        ("--%s\r\n" % frontera).encode("utf-8"),
        b'Content-Disposition: form-data; name="file"; filename="acestream-suite-partido.json"\r\n',
        b"Content-Type: application/json\r\n\r\n",
        cuerpo,
        ("\r\n--%s--\r\n" % frontera).encode("utf-8"),
    ]
    request = urllib.request.Request(
        import_url(base_url),
        data=b"".join(partes),
        headers={
            "Content-Type": "multipart/form-data; boundary=%s" % frontera,
            "Origin": origin,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


# Las cuatro guias y los dos partes con los que se prueba la regla del directo (S2). Son los casos
# medidos el 2026-11-04, literales: si la regla vuelve a mirar el titulo, esta prueba se cae.
GUIA_MEDIDA = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="DAZN.LALIGA.es"><display-name>DAZN LALIGA</display-name></channel>
  <channel id="M.Champions.es"><display-name>M+ Liga de Campeones</display-name></channel>
  <channel id="M.Deportes.es"><display-name>M+ Deportes</display-name></channel>
  <channel id="Movistar.Plus.es"><display-name>Movistar Plus+</display-name></channel>
  <channel id="RealMadrid.TV.es"><display-name>Real Madrid TV</display-name></channel>
  <programme start="20261104173800 +0000" stop="20261104193800 +0000" channel="M.Champions.es">
    <title>UEFA Champions League: AEK Atenas - Real Madrid</title>
  </programme>
  <programme start="20261104180000 +0000" stop="20261104200000 +0000" channel="DAZN.LALIGA.es">
    <title>LALIGA EA SPORTS: R. Racing Club - Real Madrid</title>
  </programme>
  <programme start="20261104194500 +0000" stop="20261104214500 +0000" channel="Movistar.Plus.es">
    <title>Euroliga</title>
  </programme>
  <programme start="20261104233000 +0000" stop="20261105013000 +0000" channel="M.Deportes.es">
    <title>Euroliga de baloncesto: Real Madrid - Panathinaikos</title>
  </programme>
</tv>
"""

# Los dos documentos de S1, con la forma medida: `strTimestamp` en UTC sin sufijo y `utcDate` con `Z`.
PARTIDOS_FUTBOL = {
    "events": [
        {"strTimestamp": "2026-11-04T17:45:00", "strLeague": "UEFA Champions League",
         "strEvent": "AEK Athens vs Real Madrid", "idEvent": "1"},
        {"strTimestamp": "2026-11-08T20:00:00", "strLeague": "Spanish La Liga",
         "strEvent": "Real Madrid vs Rayo Vallecano", "idEvent": "2"},
    ]
}
PARTIDOS_BALONCESTO = {
    "data": [
        {"utcDate": "2026-11-04T19:45:00Z", "played": False, "gameStatus": 1,
         "local": {"club": {"code": "MAD", "name": "Real Madrid"}},
         "road": {"club": {"code": "PAO", "name": "Panathinaikos AKTOR Athens"}}},
        {"utcDate": "2026-11-05T19:45:00Z", "played": False, "gameStatus": 1,
         "local": {"club": {"name": "Fenerbahce Beko Istanbul"}},
         "road": {"club": {"name": "Olympiacos"}}},
        # Una respuesta resumida trae el nombre plano, no bajo `club`: las dos formas valen.
        {"utcDate": "2026-11-06T19:45:00Z", "played": False, "gameStatus": 1,
         "local": {"name": "Barcelona"}, "road": {"name": "Real Madrid Baloncesto"}},
    ]
}


def self_test():
    """The checks this script leaves behind: they fail if a fixture source, the live rule, the
    window, the search filter, the import document, the state file or the live set break.

    Sin red y sin motor; de ficheros, solo los de un directorio temporal que se borra al terminar.
    """
    # S1: las dos formas de fecha de las dos fuentes.
    assert parse_utc("2026-11-04T17:45:00") == datetime.datetime(2026, 11, 4, 17, 45), "sin sufijo"
    assert parse_utc("2026-11-04T19:45:00Z") == datetime.datetime(2026, 11, 4, 19, 45), "con Z"
    assert parse_utc("2026-11-04 19:45:00") is None
    assert parse_utc("") is None
    assert parse_utc(None) is None

    # S1: los dos documentos medidos, cada uno a su forma.
    futbol = parse_football_fixtures(PARTIDOS_FUTBOL)
    assert len(futbol) == 2, futbol
    assert futbol[0]["kickoff"] == datetime.datetime(2026, 11, 4, 17, 45), futbol[0]
    assert futbol[0]["competition"] == "UEFA Champions League", futbol[0]
    assert futbol[0]["event"] == "AEK Athens vs Real Madrid", futbol[0]
    assert futbol[1]["kickoff"] == datetime.datetime(2026, 11, 8, 20, 0), futbol[1]
    # Una clave desactivada contesta `{"events": null}`: lista vacia, no error.
    assert parse_football_fixtures({"events": None}) == []
    assert parse_football_fixtures({}) == []
    baloncesto = parse_basketball_fixtures(PARTIDOS_BALONCESTO)
    assert len(baloncesto) == 2, baloncesto   # solo los dos del club
    assert baloncesto[0]["kickoff"] == datetime.datetime(2026, 11, 4, 19, 45), baloncesto[0]
    assert baloncesto[0]["competition"] == "EuroLeague", baloncesto[0]
    assert baloncesto[0]["event"] == "Real Madrid - Panathinaikos AKTOR Athens", baloncesto[0]
    # Y el rival, que es lo que se busca en el titulo del programa (S2): `club.name` en las dos formas.
    assert baloncesto[0]["rival"] == "Panathinaikos AKTOR Athens", baloncesto[0]
    assert futbol[0]["rival"] == "AEK Athens", futbol[0]
    assert baloncesto[1]["event"] == "Barcelona - Real Madrid Baloncesto", baloncesto[1]
    assert parse_basketball_fixtures({"data": []}) == []
    assert parse_basketball_fixtures({}) == []
    # La Liga Endesa no tiene fuente: sin competicion reconocible no se casa nada, y no se inventa.
    assert competition_pattern("Liga Endesa") is None
    assert competition_pattern("UEFA Champions League") is not None
    assert competition_pattern("Spanish La Liga") is not None
    assert competition_pattern("EuroLeague") is not None
    assert competition_pattern("Copa del Rey") is not None

    programas = parse_guide(GUIA_MEDIDA)
    assert len(programas) == 4, programas
    assert programas[0]["start"] == datetime.datetime(2026, 11, 4, 17, 38), programas[0]
    assert programas[0]["stop"] == datetime.datetime(2026, 11, 4, 19, 38), programas[0]
    assert programas[0]["channel_name"] == "M+ Liga de Campeones", programas[0]
    euroliga_1945 = programas[2]
    repeticion_2330 = programas[3]

    # S2, el caso 1: con el partido de Champions a las 17:45, casa el programa de las 17:38 y NO el
    # `Racing - Real Madrid` de las 18:00, que es la repeticion del 1 de noviembre (aunque su hora
    # caiga dentro de la tolerancia, su titulo no nombra la Champions).
    champions = futbol[0]
    en_directo = live_programmes(champions, programas)
    assert [p["guide_id"] for p in en_directo] == ["M.Champions.es"], en_directo
    assert en_directo[0]["title"] == "UEFA Champions League: AEK Atenas - Real Madrid"

    # S2, el caso 1b: al mismo saque y con la misma competicion hay otro partido en `M+ Liga de
    # Campeones  3` (`Fenerbahçe - Liverpool`) y el golf de veteranos se llama `PGA Tour Champions`.
    # Ninguno emite al Madrid y el programa que si lo nombra existe, asi que los dos se quedan fuera.
    vecinos = list(programas) + [
        {"start": en_directo[0]["start"], "stop": en_directo[0]["stop"],
         "guide_id": "M.Champions.3.es", "channel_name": "M+ Liga de Campeones  3",
         "title": "UEFA Champions League: Fenerbahçe - Liverpool"},
        {"start": en_directo[0]["start"], "stop": en_directo[0]["stop"],
         "guide_id": "M.Golf.es", "channel_name": "M+ Golf",
         "title": "PGA Tour Champions: Simmons Bank Championship. Jornada 3"},
    ]
    assert [p["guide_id"] for p in live_programmes(champions, vecinos)] == ["M.Champions.es"], (
        live_programmes(champions, vecinos)
    )
    # Y los dos patrones que se apretaron por esos mismos casos: el golf no es la Champions League ni
    # la escalada es la Copa del Rey.
    assert competition_pattern("UEFA Champions League").search("PGA Tour Champions: Jornada 3") is None
    assert competition_pattern("Copa del Rey").search("Copa del mundo de escalada") is None
    assert competition_pattern("Copa del Rey").search("Copa del Rey: Real Madrid - X") is not None

    # S2, el caso 2: con el partido de Euroliga a las 19:45, casa el `Euroliga` de las 19:45 en
    # `Movistar Plus+` (que no nombra al Madrid) y NO la repeticion de las 23:30 en `M+ Deportes`.
    euroliga = baloncesto[0]
    assert [p["guide_id"] for p in live_programmes(euroliga, programas)] == ["Movistar.Plus.es"], (
        live_programmes(euroliga, programas)
    )
    assert euroliga_1945["title"] == "Euroliga"
    assert not live_programmes(euroliga, [repeticion_2330]), "la repeticion de las 23:30 no es directo"
    # Y el mismo documento de Euroliga no casa el partido de Champions ni el de LaLiga.
    assert not live_programmes(euroliga, [programas[0]]), "17:38 no es el saque de la Euroliga"

    # S2: la tolerancia es asimetrica y es lo que rechaza la repeticion del mismo dia. Una emision
    # puede abrir su previa antes (el 17:38 del partido de las 17:45 son 7 minutos antes) pero no
    # empezar un cuarto de hora tarde (el `Racing - Real Madrid` de las 18:00 son 15 despues).
    def programa(start, title="UEFA Champions League: AEK Atenas - Real Madrid",
                 guide_id="M.Champions.es", name="M+ Liga de Campeones"):
        return {"start": start, "stop": start + datetime.timedelta(hours=2),
                "guide_id": guide_id, "title": title, "channel_name": name}

    def minutos(delta):
        return champions["kickoff"] + datetime.timedelta(minutes=delta)

    assert live_programmes(champions, [programa(minutos(-20))]), "20 minutos antes si"
    assert not live_programmes(champions, [programa(minutos(-21))]), "21 minutos antes no"
    assert live_programmes(champions, [programa(minutos(0))]), "la hora del saque si"
    assert live_programmes(champions, [programa(minutos(5))]), "5 minutos despues si"
    assert not live_programmes(champions, [programa(minutos(6))]), "6 minutos despues no"
    # El caso medido: 18:00 para un saque a las 17:45, con el titulo de LaLiga. Lo rechazan las dos
    # condiciones, y la de la hora basta por si sola.
    assert not live_programmes(champions, [
        programa(minutos(15), "LALIGA EA SPORTS: R. Racing Club - Real Madrid",
                 "DAZN.LALIGA.es", "DAZN LALIGA")
    ])
    assert not live_programmes(champions, [programa(minutos(15))]), "ni con el titulo correcto"
    # La tolerancia se puede mover por flag, y con 20 despues el de las 18:00 si entra: por eso el
    # valor por defecto es 5 y no 20.
    assert live_programmes(champions, [programa(minutos(15))], 20, 20)

    # S2: el canal del club no emite en directo para este trabajo, ni con la hora y el titulo exactos.
    club = {"start": datetime.datetime(2026, 11, 4, 19, 45), "stop": None,
            "guide_id": "RealMadrid.TV.es", "title": "Euroliga", "channel_name": "Real Madrid TV"}
    assert is_club_channel("Real Madrid TV"), "el nombre del canal del club"
    assert is_club_channel("RealMadrid.TV.es"), "el id de guia del club"
    assert is_club_channel("RMTV")
    assert not is_club_channel("Movistar Plus+")
    assert not is_club_channel("M+ Deportes")
    assert live_programmes(euroliga, [club]) == [], "el canal del club no cuenta"
    # Y una competicion sin patron no se casa con nada, por mucho que la hora cuadre.
    endesa = {"kickoff": euroliga["kickoff"], "competition": "Liga Endesa", "event": "Real Madrid - Barca"}
    assert live_programmes(endesa, [euroliga_1945]) == []

    # S3: la ventana va del saque real al final de la emision, y una repeticion no cae en ella.
    airings = live_airings([champions, euroliga], programas)
    assert len(airings) == 2, airings
    assert [a["programme"]["guide_id"] for a in airings] == ["M.Champions.es", "Movistar.Plus.es"], airings
    airing = airings[0]
    saque = airing["kickoff"]
    fin = airing["programme"]["stop"]
    assert start_key(airing) == "2026-11-04T17:45:00Z", start_key(airing)
    assert in_window(airing, saque - datetime.timedelta(minutes=60))
    assert not in_window(airing, saque - datetime.timedelta(minutes=61))
    assert in_window(airing, saque)
    assert in_window(airing, saque + datetime.timedelta(minutes=90))
    assert in_window(airing, fin)
    assert not in_window(airing, fin + datetime.timedelta(minutes=1))
    assert not in_window(airing, saque + datetime.timedelta(days=1))
    assert not in_window(airing, saque - datetime.timedelta(days=1))
    # La ventana se puede ensanchar hacia atras; el final lo pone la guia, no una opcion.
    assert in_window(airing, saque - datetime.timedelta(minutes=120), 180)
    assert not in_window(airing, saque - datetime.timedelta(minutes=120), 60)
    # Sin `<stop>` la ventana se cierra a las dos horas, que es lo que dura un partido como mucho.
    sin_fin = {"kickoff": saque, "programme": {"start": saque, "stop": None}}
    assert programme_end(sin_fin["programme"]) == saque + datetime.timedelta(hours=2)
    assert in_window(sin_fin, saque + datetime.timedelta(minutes=90))
    assert not in_window(sin_fin, saque + datetime.timedelta(minutes=121))

    # S4: los terminos son el nombre del canal de la guia y el titulo del catalogo cuando el mapeo lo da.
    mapping = {
        "aa" * 20: {"guide_id": "Movistar.Plus.es", "guide_name": "Movistar Plus+ 2"},
        "bb" * 20: {"guide_id": "M.Deportes.es", "guide_name": "M+ Deportes 2"},
    }
    por_guia = guide_titles(mapping)
    assert sorted(por_guia) == sorted(["Movistar.Plus.es", "M.Deportes.es"]), por_guia
    assert search_terms(airing, por_guia.get(airing["programme"]["guide_id"], ())) == [
        "M+ Liga de Campeones"
    ], search_terms(airing)
    assert search_terms(airings[1], por_guia.get(airings[1]["programme"]["guide_id"], ())) == [
        "Movistar Plus+", "Movistar Plus+ 2"
    ]
    assert search_terms({"programme": {"channel_name": "DAZN LALIGA"}}, ["DAZN  LALIGA"]) == [
        "DAZN LALIGA"
    ]
    assert search_terms({"programme": {"channel_name": ""}}) == []
    assert search_url("DAZN LALIGA", "http://motor:8081/") == (
        "http://motor:8081/search.m3u?query=DAZN%20LALIGA&json=1"
    )
    assert "query=M%2B%20Deportes" in search_url("M+ Deportes")

    # S5: del JSON del motor se leen los objetos con infohash, en cualquier nivel.
    respuesta = [
        {"name": "DAZN LALIGA HD", "infohash": "aa" * 20, "bitrate": 6000, "availability": True},
        {},
        [{"name": "DAZN LALIGA 2", "infohash": "bb" * 20}],
        {"otra": {"name": "DAZN LALIGA 3", "infohash": "cc" * 20, "categories": ["Deportes"]}},
    ]
    assert [e["infohash"] for e in search_entries(respuesta)] == [
        "aa" * 20, "bb" * 20, "cc" * 20
    ], search_entries(respuesta)
    assert search_entries([]) == []
    assert search_entries([{}, {"name": "sin hash"}]) == []

    # S5: solo el canal buscado, comparando su NOMBRE normalizado y no sus tokens. El caso medido: el
    # motor devuelve el canal 2 con todos los tokens del buscado, y no es el que emite.
    assert channel_identity("M+ Liga de Campeones") == "liga de campeones", (
        channel_identity("M+ Liga de Campeones")
    )
    assert channel_identity("M. Liga de Campeones 2") == "liga de campeones 2"
    assert channel_identity("M+ LIGA DE CAMPEONES FHD --> NEW ERA") == "liga de campeones fhd"
    # La cola del publicador se corta entera, tambien lo que venga detras de la marca.
    assert channel_identity("Campeones elcano FHD") == "campeones", (
        channel_identity("Campeones elcano FHD")
    )
    assert channel_identity("Campeones elcano") == "campeones"
    assert channel_identity("Movistar Plus+") == "plus"
    assert channel_identity("M+") == ""
    assert same_channel("M+ LIGA DE CAMPEONES FHD --> NEW ERA", "M+ Liga de Campeones")
    assert same_channel("M+ Liga de Campeones HD", "M+ Liga de Campeones")
    assert same_channel("M. Liga de Campeones", "M+ Liga de Campeones")
    assert not same_channel("M+ LIGA DE CAMPEONES 2 FHD --> NEW ERA", "M+ Liga de Campeones"), (
        "el canal 2 no es el canal"
    )
    assert not same_channel("M+ LIGA DE CAMPEONES 3 --> NEW ERA", "M+ Liga de Campeones")
    assert not same_channel("M+ Liga de Campeones 2", "M+ Liga de Campeones")
    assert not same_channel("DAZN 1", "M+ Liga de Campeones")
    assert not same_channel("M+", "M+ Liga de Campeones")
    # El numero forma parte del nombre cuando lo lleva el propio objetivo.
    assert same_channel("M+ Deportes 2", "M+ Deportes 2")
    assert not same_channel("M+ Deportes 1", "M+ Deportes 2")

    # S5: el filtro de resultados completo, con el caso medido del canal 2 (86a071ec… es el vecino).
    conocidos = set(["ff" * 20])
    entradas = [
        {"name": "M+ LIGA DE CAMPEONES FHD --> NEW ERA", "infohash": "aa" * 20},
        {"name": "M. Liga de Campeones", "infohash": "bb" * 20},
        {"name": "M+ LIGA DE CAMPEONES 2 FHD --> NEW ERA", "infohash": "86a071ec" + "0" * 32},
        {"name": "M+ LIGA DE CAMPEONES 3 --> NEW ERA", "infohash": "dd" * 20},
        {"name": "M+ Liga de Campeones", "infohash": "ff" * 20},
        {"name": "M+ Liga de Campeones roto", "infohash": "no es un content id"},
    ]
    elegidos = candidates(entradas, "M+ Liga de Campeones", conocidos)
    assert [e["infohash"] for e in elegidos] == ["aa" * 20, "bb" * 20], elegidos
    assert "86a071ec" + "0" * 32 not in [e["infohash"] for e in elegidos], "el canal 2 no entra"
    assert [candidates(entradas, "M+ Liga de Campeones", conocidos, 1)[0]["infohash"]] == ["aa" * 20]
    muchos = [{"name": "M+ LIGA DE CAMPEONES HD (publisher %d)" % i, "infohash": "%040x" % (i + 1)}
              for i in range(20)]
    assert len(candidates(muchos, "M+ Liga de Campeones", set())) == 8
    assert len(candidates(muchos, "M+ Liga de Campeones", set(), 3)) == 3
    # Un termino sin nombre de canal no se lleva nada.
    assert candidates(entradas, "+ -", set()) == []

    # S5: el documento que se manda a /import es el de exportacion de acestream-suite, v1.
    documento = export_document(elegidos)
    assert documento["format"] == "acelist", documento
    assert documento["version"] == 1, documento
    assert documento["channels"][0] == {
        "title": "M+ LIGA DE CAMPEONES FHD --> NEW ERA",
        "title_pending": False,
        "content_id": "aa" * 20,
        "link": "acestream://" + "aa" * 20,
        "category": "Real Madrid",
        "language": "es",
        "country": "ES",
    }, documento["channels"][0]
    assert documento["channels"][1]["title"] == "M. Liga de Campeones", documento["channels"][1]
    # La respuesta HTML de /import: se leen sus tres numeros, y no se inventan si no estan.
    assert import_summary("<p>3 canales creados, 1 duplicados y 0 canales inválidos</p>") == (3, 1, 0)
    assert import_summary("<p>nada</p>") is None
    assert import_summary(None) is None

    # S6: el estado guarda cada (saque, guide id, termino) y no repite la importacion.
    estado = state_empty()
    clave = start_key(airing)
    assert not already_processed(estado, clave, "M.Champions.es", "M+ Liga de Campeones")
    mark_processed(estado, clave, "M.Champions.es", "M+ Liga de Campeones", 2,
                   ids=["bb" * 20, "aa" * 20], now=datetime.datetime(2026, 11, 4, 17, 5))
    assert already_processed(estado, clave, "M.Champions.es", "M+ Liga de Campeones")
    assert len(estado["done"]) == 1, estado
    assert estado["done"][0]["imported"] == 2, estado
    assert estado["done"][0]["at"] == "2026-11-04T17:05:00Z", estado
    # Los content ids van con la entrada: son los que S11 no puede sacar del mapeo de la guia.
    assert estado["done"][0]["ids"] == ["aa" * 20, "bb" * 20], estado
    assert imported_ids(estado, clave, "M.Champions.es") == ["aa" * 20, "bb" * 20], estado
    assert imported_ids(estado, clave, "M.Deportes.es") == [], estado
    # Otra hora, otro canal o otro termino son otra clave.
    assert not already_processed(estado, "2026-11-11T17:45:00Z", "M.Champions.es",
                                 "M+ Liga de Campeones")
    assert not already_processed(estado, clave, "M.Deportes.es", "M+ Liga de Campeones")
    assert not already_processed(estado, clave, "M.Champions.es", "M+ Liga de Campeones 2")
    # Anotar dos veces lo mismo no duplica, y anotar otro partido no borra el anterior.
    mark_processed(estado, clave, "M.Champions.es", "M+ Liga de Campeones", 2)
    mark_processed(estado, "2026-11-04T19:45:00Z", "Movistar.Plus.es", "Movistar Plus+", 1,
                   now=datetime.datetime(2026, 11, 4, 19, 0))
    assert len(estado["done"]) == 2, estado
    assert estado["done"][1]["start"] == "2026-11-04T19:45:00Z", estado
    assert estado["done"][1]["ids"] == [], estado

    # S11: el conjunto en directo lleva el `stop` de cada programa en directo, y sale de los canales
    # del catalogo mapeados a ese canal de guia y de los que importo este trabajo.
    ahora = datetime.datetime(2026, 11, 4, 17, 30)
    en_ventana = [a for a in airings if in_window(a, ahora)]
    assert [a["programme"]["guide_id"] for a in en_ventana] == ["M.Champions.es"], en_ventana
    mapping_en_directo = {
        "cc" * 20: {"guide_id": "M.Champions.es", "guide_name": "M+ Liga de Campeones"},
        "dd" * 20: {"guide_id": "Movistar.Plus.es", "guide_name": "Movistar Plus+"},
    }
    vivo = live_document(en_ventana, mapping_en_directo, estado, ahora)
    assert vivo["format"] == "acelist-partido-live", vivo
    assert vivo["version"] == 1, vivo
    assert vivo["generated"] == "2026-11-04T17:30:00Z", vivo
    assert vivo["channels"]["cc" * 20] == "2026-11-04T19:38:00Z", vivo
    # Los importados por este trabajo tambien, que no tienen mapeo todavia.
    assert vivo["channels"]["aa" * 20] == "2026-11-04T19:38:00Z", vivo
    assert vivo["channels"]["bb" * 20] == "2026-11-04T19:38:00Z", vivo
    # El canal de la Euroliga no esta en la ventana: no entra en el conjunto.
    assert "dd" * 20 not in vivo["channels"], vivo
    # Sin partido en la ventana el conjunto esta vacio: la carpeta y `madrid.m3u` se vacian solas.
    assert live_document([], mapping_en_directo, estado, ahora)["channels"] == {}
    # El estado se lee y se escribe sin perder nada, y un documento ajeno se rechaza.
    assert state_document(state_text(estado))["done"] == estado["done"]
    try:
        state_document('{"format": "otra-cosa", "done": []}')
        raise AssertionError("un estado ajeno se ha aceptado")
    except ValueError:
        pass
    try:
        state_document("{}")
        raise AssertionError("un estado sin 'done' se ha aceptado")
    except ValueError:
        pass

    # S3/S4: los nombres nuevos del estado y del cliente, y las dos compatibilidades: la ruta vieja
    # mientras sea la unica que existe (perderla seria reimportar medio partido) y la variable vieja
    # mientras el host solo exporte esa.
    assert STATE_PATH == "/var/lib/acestream-suite-partido/state.json", STATE_PATH
    assert STATE_PATH_LEGACY == "/var/lib/acelist-partido/state.json", STATE_PATH_LEGACY
    assert LIVE_PATH == "/var/lib/acestream-suite-partido/live.json", LIVE_PATH
    assert USER_AGENT.startswith("acestream-suite-partido/"), USER_AGENT
    temporal = tempfile.mkdtemp()
    try:
        nuevo = os.path.join(temporal, "state.json")
        viejo = os.path.join(temporal, "state-viejo.json")
        assert with_legacy(nuevo, nuevo, viejo) == nuevo, "sin ficheros manda la nueva"
        save_state(state_empty(), viejo)
        assert with_legacy(nuevo, nuevo, viejo) == viejo, "solo la vieja: se lee la vieja"
        assert load_state(with_legacy(nuevo, nuevo, viejo))["done"] == [], "el estado viejo no se leyo"
        save_state(state_empty(), nuevo)
        assert with_legacy(nuevo, nuevo, viejo) == nuevo, "las dos: manda la nueva"
        assert with_legacy("/tmp/pedido.json", nuevo, viejo) == "/tmp/pedido.json", "una ruta pedida"
    finally:
        shutil.rmtree(temporal)
    respaldo = dict(os.environ)
    try:
        os.environ.pop("ACESTREAM_SUITE_URL", None)
        os.environ["ACELIST_URL"] = "http://viejo:8083"
        assert env("ACESTREAM_SUITE_URL", "ACELIST_URL", "d") == "http://viejo:8083"
        os.environ["ACESTREAM_SUITE_URL"] = "http://nuevo:8083"
        assert env("ACESTREAM_SUITE_URL", "ACELIST_URL", "d") == "http://nuevo:8083"
    finally:
        os.environ.clear()
        os.environ.update(respaldo)
    print("self-test OK")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Imports the channels airing a Real Madrid match into acestream-suite, live"
    )
    parser.add_argument("--acestream-suite", default=ACESTREAM_SUITE_URL,
                        help="acestream-suite base URL")
    parser.add_argument("--engine", default=ENGINE_URL, help="AceStream search engine base URL")
    parser.add_argument("--state", default=STATE_PATH, help="the state file, one entry per import")
    parser.add_argument("--live-file", default=LIVE_PATH,
                        help="the live set the playlists read (S11)")
    parser.add_argument("--log", default=LOG_PATH, help="the log file")
    parser.add_argument("--lock", default=LOCK_PATH, help="the flock file")
    parser.add_argument("--football-url", default=FOOTBALL_URL,
                        help="the football fixture source (TheSportsDB)")
    parser.add_argument("--basketball-url", default=BASKETBALL_URL,
                        help="the basketball fixture source (EuroLeague)")
    parser.add_argument("--status", default=STATUS,
                        help="availability filter for the catalogue (empty: the whole catalogue)")
    parser.add_argument("--window-before", type=int, default=WINDOW_BEFORE,
                        help="minutes before kick-off the search starts (it ends at the guide's stop)")
    parser.add_argument("--live-tolerance-before", type=int, default=LIVE_TOLERANCE_BEFORE,
                        help="minutes a guide programme may open before the real kick-off")
    parser.add_argument("--live-tolerance-after", type=int, default=LIVE_TOLERANCE_AFTER,
                        help="minutes a guide programme may open after the real kick-off")
    parser.add_argument("--max-per-channel", type=int, default=MAX_PER_CHANNEL,
                        help="how many streams to keep per search term")
    parser.add_argument("--origin", default=ORIGIN, help="Origin header /import checks")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what it would import, write nothing")
    parser.add_argument("--self-test", action="store_true", help="run the checks and exit")
    return parser.parse_args(argv)


def fetch_fixtures(args, log_path):
    """S1: los partidos de las dos fuentes. Una fuente caida es "no lo se", no un error de la pasada.

    Devuelve `(fixtures, fuentes)`, donde `fuentes` cuenta cuantas de las dos contestaron.
    """
    fixtures = []
    fuentes = 0
    for nombre, fetcher, url in (
        ("football", fetch_football_fixtures, args.football_url),
        ("basketball", fetch_basketball_fixtures, args.basketball_url),
    ):
        try:
            encontrados = fetcher(url, TIMEOUT)
        except (OSError, ValueError, KeyError) as exc:
            log("cannot read the %s fixtures from %s (%s); unknown, nothing added from it"
                % (nombre, url, exc), path=log_path)
            continue
        fuentes += 1
        fixtures.extend(encontrados)
        log("%d %s fixtures from %s" % (len(encontrados), nombre, url), path=log_path)
    # S1: la ACB no tiene fuente sin clave. Se dice en cada pasada, porque el efecto es que esos
    # partidos no se añaden nunca y no hay otra forma de notarlo.
    log("Liga Endesa (ACB) has no keyless fixture source: its matches are not added",
        path=log_path)
    return fixtures, fuentes


def main(argv=None):
    args = parse_args(argv)
    if args.self_test:
        self_test()
        return 0

    lock = open(args.lock, "a")
    try:  # nunca dos pasadas a la vez: el cron puede arrancar una mientras la anterior busca
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another run is still going; nothing done", path=args.log)
        return 0

    ahora = now_utc()
    # S1: primero los partidos y su hora real de saque; la guia solo dice despues que canal los emite.
    fixtures, fuentes = fetch_fixtures(args, args.log)
    if not fuentes:
        # Sin ninguna fuente no se sabe si hay partido: el conjunto en directo se deja como esta y sus
        # entradas caducan solas por su `stop`. Borrarlo apagaria la carpeta de un partido en directo.
        log("no fixture source answered; the live set is left as it is", path=args.log)
        return 1

    try:
        programmes = parse_guide(fetch_guide(args.acestream_suite, TIMEOUT))
    except (ET.ParseError, ValueError) as exc:
        log("cannot read the guide from %s: %s" % (guide_url(args.acestream_suite), exc), path=args.log)
        return 1
    except OSError as exc:  # URLError, HTTPError, timeout de socket: todos son OSError
        log("cannot fetch the guide from %s: %s" % (guide_url(args.acestream_suite), exc), path=args.log)
        return 1

    airings = [
        airing for airing in live_airings(fixtures, programmes, args.live_tolerance_before,
                                          args.live_tolerance_after)
        if in_window(airing, ahora, args.window_before)
    ]
    # Un mismo canal puede traer dos programas dentro de la ventana (la previa y el partido, como
    # `Previa Euroliga` 19:25 y `Euroliga` 19:45 en Movistar Plus+): se busca una vez por canal y
    # saque, con el `stop` mas tardio, para no repetir la misma busqueda ni la misma linea de log.
    unicos = {}
    for airing in airings:
        clave_unica = (airing["programme"]["guide_id"], airing["kickoff"])
        previo = unicos.get(clave_unica)
        if previo is None or programme_end(airing["programme"]) > programme_end(previo["programme"]):
            unicos[clave_unica] = airing
    airings = [unicos[clave] for clave in sorted(unicos, key=lambda clave: clave[1])]
    if not airings:
        # S11: la pasada se hace igual, y sin partido el conjunto en directo queda vacio para que la
        # carpeta y `madrid.m3u` se vacien solas. Nada se borra del catalogo.
        if args.dry_run:
            print("--- live set would be empty, %s left as it is (dry run) ---" % args.live_file,
                  file=sys.stderr)
        else:
            save_live(live_document([], {}, state_empty(), ahora), args.live_file)
        log("no Real Madrid fixture from %d min before kick-off to the end of its live airing (%s); "
            "nothing done" % (args.window_before, ahora.strftime("%Y-%m-%dT%H:%M:%SZ")),
            path=args.log)
        return 0

    try:
        known_ids = fetch_catalog_ids(args.acestream_suite, TIMEOUT, args.status)
    except (OSError, ValueError, KeyError) as exc:
        log("cannot read the catalogue from %s: %s" % (export_url(args.acestream_suite, args.status), exc),
            path=args.log)
        return 1

    # El mapeo es un extra, no un requisito: sin el se busca solo por el nombre de la guia.
    try:
        mapping = fetch_mapping(args.acestream_suite, TIMEOUT)
    except (OSError, ValueError) as exc:
        log("no guide mapping from %s (%s); searching by the guide's channel name only"
            % (mapping_url(args.acestream_suite), exc), path=args.log)
        mapping = {}
    por_guia = guide_titles(mapping)

    # S4: el estado nuevo y, mientras el viejo sea el unico que existe, el viejo. Se escribe donde se
    # leyo, para no partir el estado en dos ficheros a mitad de la ventana.
    args.state = with_legacy(args.state, STATE_PATH, STATE_PATH_LEGACY)
    try:
        estado = load_state(args.state)
    except (IOError, OSError, ValueError) as exc:
        # Un estado ilegible no se pisa: es el registro que evita reimportar el mismo partido.
        log("cannot read the state file %s: %s" % (args.state, exc), path=args.log)
        return 1

    intentos = fallos = importados = 0
    for airing in airings:
        programa = airing["programme"]
        clave = start_key(airing)
        terminos = search_terms(airing, por_guia.get(programa["guide_id"], ()))
        for termino in terminos:
            if already_processed(estado, clave, programa["guide_id"], termino):
                continue
            intentos += 1
            try:
                entradas = fetch_search(termino, args.engine, TIMEOUT)
            except (OSError, ValueError) as exc:
                fallos += 1
                log("cannot search %r in %s: %s" % (termino, args.engine, exc), path=args.log)
                continue
            elegidos = candidates(entradas, termino, known_ids, args.max_per_channel)
            if not elegidos:
                # Nada se anota: el cron vuelve en 10 minutos, y para entonces el canal puede tener
                # pares. Es el reintento que pide la ventana.
                log("no new stream of %r yet for %s on %s (kick-off %s)"
                    % (termino, airing["event"], programa["channel_name"], clave), path=args.log)
                continue
            documento = export_document(elegidos)
            if args.dry_run:
                print("--- would import %d channels of %r for %s, category %s ---"
                      % (len(elegidos), termino, airing["event"], CATEGORIA), file=sys.stderr)
                sys.stdout.write(json.dumps(documento, indent=2, sort_keys=True) + "\n")
                continue
            try:
                html = post_import(documento, args.acestream_suite, TIMEOUT, args.origin)
            except (OSError, ValueError) as exc:
                fallos += 1
                log("cannot import %d channels of %r into %s: %s"
                    % (len(elegidos), termino, import_url(args.acestream_suite), exc), path=args.log)
                continue
            importados += len(elegidos)
            # Los recien importados tambien estan ya en el catalogo para los terminos que vienen
            # despues en esta misma pasada: dos terminos pueden encontrar el mismo publisher, y la app
            # lo descartaria como duplicado, pero no hace falta pedirselo dos veces.
            known_ids.update(elegido["infohash"] for elegido in elegidos)
            mark_processed(estado, clave, programa["guide_id"], termino, len(elegidos),
                           [elegido["infohash"] for elegido in elegidos])
            resumen = import_summary(html)
            log("%d channels of %r for %s on %s (%s): %s"
                % (len(elegidos), termino, airing["event"], programa["channel_name"], clave,
                   "import answered without a result line" if resumen is None
                   else "%d created, %d duplicates, %d invalid" % resumen), path=args.log)

    # S11: el conjunto en directo sale de las emisiones en directo de la ventana, recien importadas o
    # ya importadas antes (el estado guarda sus content ids).
    vivo = live_document(airings, mapping, estado, ahora)
    if args.dry_run:
        print("--- live set would be %d channels: %s ---"
              % (len(vivo["channels"]), ", ".join(sorted(vivo["channels"]))),
              file=sys.stderr)
    else:
        save_state(estado, args.state)
        cambiado = save_live(vivo, args.live_file)
        log("live set: %d channels with the match on air, %s: %s"
            % (len(vivo["channels"]), args.live_file,
               "written" if cambiado else "unchanged"), path=args.log)
    log("done: %d live airings in the window, %d searches, %d channels imported, %d failures"
        % (len(airings), intentos, importados, fallos), path=args.log)
    # Un motor muerto deja una linea por termino y un codigo de salida, nunca un traceback: si
    # fallaron todas las busquedas, la pasada no ha hecho nada.
    return 1 if intentos and fallos == intentos else 0


if __name__ == "__main__":
    sys.exit(main())
