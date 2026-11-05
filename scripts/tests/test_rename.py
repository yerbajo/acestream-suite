#!/usr/bin/env python3
"""Checks the acestream-suite naming of scripts/ and its one-version compatibility (S1, S3, S4).

WHAT IT CHECKS
    - Los ficheros renombrados existen con su nombre nuevo y los viejos ya no estan.
    - Cada script lee primero su variable `ACESTREAM_SUITE_*` y despues la vieja `ACELIST_*` (S4).
    - Las rutas de log, lock y estado por defecto son las nuevas, y las viejas se siguen leyendo
      mientras sean las unicas que existen (S4).
    - Las listas publicadas llevan el nombre nuevo y dejan una copia con el viejo, mientras
      `hls.m3u`, `deportes.m3u`, `espana.m3u`, `direct.m3u` y `madrid.m3u` conservan el suyo (S4).
    - Los datos no se mueven: la base sigue en `/opt/acelist/data` (S5), y la historia no se
      reescribe: `odd/tasks/acelist*.md` conserva su nombre (S6).
    - Lo unico que queda con el nombre viejo es lo que esta lista de arriba permite (S4).

    Sin red, sin host y sin ficheros fuera de un directorio temporal que el test borra.

Usage: python3 scripts/tests/test_rename.py
"""

import importlib.util
import os
import re
import shutil
import sys
import tempfile

# Cargar los scripts no debe dejar `__pycache__` en el repositorio.
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

GRUPOS = []


def grupo(nombre):
    """Registra un bloque de comprobaciones: se ejecuta al final, y su fallo no oculta los demas."""
    def registrar(fn):
        GRUPOS.append((nombre, fn))
        return fn
    return registrar


def check(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


def cargar(fichero, entorno=None):
    """Carga un script del repositorio por su nombre de fichero (llevan guiones) y su entorno.

    `entorno` se aplica ANTES de ejecutar el modulo, porque los scripts leen sus variables al
    importarse: es la misma ruta que sigue el cron en el host.
    """
    ruta = os.path.join(SCRIPTS, fichero)
    respaldo = dict(os.environ)
    if entorno:
        os.environ.update(entorno)
    try:
        spec = importlib.util.spec_from_file_location(fichero[:-3].replace("-", "_"), ruta)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
    finally:
        os.environ.clear()
        os.environ.update(respaldo)
    return modulo


# Referencias al nombre viejo que SI se quedan en esta version, cada una con su razon: si una
# aparicion no encaja aqui, es un resto del renombrado y el test falla.
LEGADO = re.compile(
    r"ACELIST_[A-Z_]+"                                  # variables de entorno viejas (S4)
    r"|acelist(?:-epg)?\.m3u"                           # listas viejas, como copia (S4)
    r"|/var/(?:lib|log|lock)/acelist-[a-z-]+"           # estado, log y lock viejos (S4)
    r"|/usr/local/bin/acelist-[a-z-]+"                  # binarios viejos, como envoltorio (S4)
    r"|/opt/acelist/data"                               # los datos no se mueven (S5)
    r"|acelist\.db"                                     # la base y su copia en el respaldo (S5)
    r"|/backup/acelist"                                 # los respaldos que ya existen
    r"|odd/tasks/acelist(?:-epg)?\.md"                  # la historia no se reescribe (S6)
    r'|"(?:acelist|acelist-partido|acelist-partido-live|acelist-epg)"'  # marcadores de la app
)

FICHEROS = (
    "acestream-suite-playlist.py",
    "acestream-suite-partido.py",
    "acestream-suite-prune.py",
    "acestream-suite-guide-playlist.py",
    "acestream-suite-playlist.cron",
    "acestream-suite-partido.cron",
    "acestream-suite-prune.cron",
    "acestream-suite-guide-playlist.cron",
    "acestream-suite-guide-refresh.cron",
    "crons-install.sh",
)


@grupo("S3: los ficheros llevan el nombre nuevo y los viejos ya no estan")
def ficheros():
    for nombre in FICHEROS:
        check(os.path.isfile(os.path.join(SCRIPTS, nombre)), "falta %s" % nombre)
    for viejo in (
        "acelist-playlist.py", "acelist-playlist.cron",
        "acelist-partido.py", "acelist-partido.cron",
        "acelist-prune.py", "acelist-prune.cron",
        "acelist-epg-playlist.py", "acelist-epg-playlist.cron",
        "acelist-epg-refresh.cron",
    ):
        check(not os.path.exists(os.path.join(SCRIPTS, viejo)), "sigue estando %s" % viejo)


@grupo("S3/S4: solo quedan referencias viejas declaradas como compatibilidad")
def restos():
    for nombre in FICHEROS:
        with open(os.path.join(SCRIPTS, nombre), "r", encoding="utf-8") as fichero:
            texto = fichero.read()
        check("AceList" not in texto, "%s: la marca vieja sigue en el texto" % nombre)
        cubiertas = set()
        for encontrado in LEGADO.finditer(texto):
            cubiertas.update(range(encontrado.start(), encontrado.end()))
        for posicion in range(len(texto)):
            if texto.startswith("acelist", posicion) and posicion not in cubiertas:
                linea = texto.count("\n", 0, posicion) + 1
                check(False, "%s:%d: «acelist» sin compatibilidad declarada: %r"
                      % (nombre, linea, texto[posicion:posicion + 60].splitlines()[0]))


@grupo("S4: cada script lee ACESTREAM_SUITE_* y, si no esta, la vieja ACELIST_*")
def entorno():
    # Lista: la app, el conjunto en directo y el puente.
    modulo = cargar("acestream-suite-playlist.py", {"ACELIST_URL": "http://viejo:8083"})
    check(modulo.ACESTREAM_SUITE_URL == "http://viejo:8083", modulo.ACESTREAM_SUITE_URL)
    modulo = cargar("acestream-suite-playlist.py",
                    {"ACELIST_URL": "http://viejo:8083", "ACESTREAM_SUITE_URL": "http://nuevo:8083"})
    check(modulo.ACESTREAM_SUITE_URL == "http://nuevo:8083", modulo.ACESTREAM_SUITE_URL)
    modulo = cargar("acestream-suite-playlist.py",
                    {"ACELIST_PARTIDO_LIVE": "/tmp/viejo/live.json"})
    check(modulo.LIVE_FILE == "/tmp/viejo/live.json", modulo.LIVE_FILE)
    modulo = cargar("acestream-suite-playlist.py", {})
    check(modulo.ACESTREAM_SUITE_URL == "http://127.0.0.1:8083", modulo.ACESTREAM_SUITE_URL)
    check(modulo.LOG_PATH == "/var/log/acestream-suite-playlist.log", modulo.LOG_PATH)
    check(modulo.LOCK_PATH == "/var/lock/acestream-suite-playlist.lock", modulo.LOCK_PATH)
    check(modulo.LIVE_FILE == "/var/lib/acestream-suite-partido/live.json", modulo.LIVE_FILE)
    check(modulo.LIVE_FILE_LEGACY == "/var/lib/acelist-partido/live.json", modulo.LIVE_FILE_LEGACY)

    # Guia.
    modulo = cargar("acestream-suite-guide-playlist.py", {"ACELIST_EPG_URL": "http://viejo:8083"})
    check(modulo.ACESTREAM_SUITE_GUIDE_URL == "http://viejo:8083", modulo.ACESTREAM_SUITE_GUIDE_URL)
    modulo = cargar("acestream-suite-guide-playlist.py",
                    {"ACELIST_EPG_URL": "http://viejo:8083",
                     "ACESTREAM_SUITE_GUIDE_URL": "http://nuevo:8083"})
    check(modulo.ACESTREAM_SUITE_GUIDE_URL == "http://nuevo:8083", modulo.ACESTREAM_SUITE_GUIDE_URL)
    modulo = cargar("acestream-suite-guide-playlist.py", {})
    check(modulo.OUTPUT == "/srv/www/varios/acestream-suite-guide.m3u", modulo.OUTPUT)
    check(modulo.LOG_PATH == "/var/log/acestream-suite-guide-playlist.log", modulo.LOG_PATH)
    check(modulo.LOCK_PATH == "/var/lock/acestream-suite-guide-playlist.lock", modulo.LOCK_PATH)

    # Partido: estado, log y lock nuevos, y el estado viejo aun legible.
    modulo = cargar("acestream-suite-partido.py", {"ACELIST_PARTIDO_STATE": "/tmp/viejo/state.json"})
    check(modulo.STATE_PATH == "/tmp/viejo/state.json", modulo.STATE_PATH)
    modulo = cargar("acestream-suite-partido.py", {})
    check(modulo.STATE_PATH == "/var/lib/acestream-suite-partido/state.json", modulo.STATE_PATH)
    check(modulo.STATE_PATH_LEGACY == "/var/lib/acelist-partido/state.json",
          modulo.STATE_PATH_LEGACY)
    check(modulo.LIVE_PATH == "/var/lib/acestream-suite-partido/live.json", modulo.LIVE_PATH)
    check(modulo.LOG_PATH == "/var/log/acestream-suite-partido.log", modulo.LOG_PATH)
    check(modulo.LOCK_PATH == "/var/lock/acestream-suite-partido.lock", modulo.LOCK_PATH)
    check(modulo.USER_AGENT.startswith("acestream-suite"), modulo.USER_AGENT)

    # Poda: guarda, log y lock nuevos; la base NO se mueve (S5).
    modulo = cargar("acestream-suite-prune.py", {"ACELIST_PRUNE_GUARD": "/tmp/viejo/done"})
    check(modulo.GUARD_PATH == "/tmp/viejo/done", modulo.GUARD_PATH)
    modulo = cargar("acestream-suite-prune.py", {})
    check(modulo.DB_PATH == "/opt/acelist/data/acelist.db", modulo.DB_PATH)
    check(modulo.GUARD_PATH == "/var/lib/acestream-suite-prune/done", modulo.GUARD_PATH)
    check(modulo.GUARD_PATH_LEGACY == "/var/lib/acelist-prune/done", modulo.GUARD_PATH_LEGACY)
    check(modulo.LOG_PATH == "/var/log/acestream-suite-prune.log", modulo.LOG_PATH)
    check(modulo.LOCK_PATH == "/var/lock/acestream-suite-prune.lock", modulo.LOCK_PATH)
    check(modulo.PLAYLIST_SCRIPT == "/usr/local/bin/acestream-suite-playlist",
          modulo.PLAYLIST_SCRIPT)
    check(modulo.PLAYLIST_SCRIPT_LEGACY == "/usr/local/bin/acelist-playlist",
          modulo.PLAYLIST_SCRIPT_LEGACY)


@grupo("S4: la ruta vieja de estado se sigue leyendo mientras sea la unica que existe")
def rutas_viejas():
    temporal = tempfile.mkdtemp()
    try:
        nuevo = os.path.join(temporal, "nuevo.json")
        viejo = os.path.join(temporal, "viejo.json")
        for modulo in (cargar("acestream-suite-playlist.py"), cargar("acestream-suite-partido.py"),
                       cargar("acestream-suite-prune.py")):
            check(modulo.with_legacy(nuevo, nuevo, viejo) == nuevo, "sin ficheros: la nueva")
            with open(viejo, "w") as fichero:
                fichero.write("x")
            check(modulo.with_legacy(nuevo, nuevo, viejo) == viejo, "solo la vieja: la vieja")
            with open(nuevo, "w") as fichero:
                fichero.write("x")
            check(modulo.with_legacy(nuevo, nuevo, viejo) == nuevo, "las dos: la nueva")
            check(modulo.with_legacy("/tmp/otra.json", nuevo, viejo) == "/tmp/otra.json",
                  "una ruta pedida a mano no se cambia por la vieja")
            os.unlink(nuevo)
            os.unlink(viejo)
    finally:
        shutil.rmtree(temporal)


@grupo("S4: la lista nueva se escribe y deja la copia con el nombre viejo")
def listas():
    modulo = cargar("acestream-suite-playlist.py")
    check([nombre for nombre, _, _, _, _ in modulo.PLAYLISTS] == [
        "acestream-suite.m3u", "deportes.m3u", "espana.m3u", "hls.m3u", "direct.m3u",
    ], [nombre for nombre, _, _, _, _ in modulo.PLAYLISTS])
    check(modulo.COMPAT_NAMES == {"acestream-suite.m3u": "acelist.m3u"}, modulo.COMPAT_NAMES)

    temporal = tempfile.mkdtemp()
    try:
        built = [(nombre, "#EXTM3U\n", 0) for nombre, _, _, _, _ in modulo.PLAYLISTS]
        built.append(("madrid.m3u", "#EXTM3U\n", 0))
        modulo.save_all(built, temporal)
        for nombre in ("acestream-suite.m3u", "acelist.m3u", "deportes.m3u", "espana.m3u",
                       "hls.m3u", "direct.m3u", "madrid.m3u"):
            check(os.path.isfile(os.path.join(temporal, nombre)), "no se escribio %s" % nombre)
        with open(os.path.join(temporal, "acelist.m3u")) as fichero:
            copia = fichero.read()
        check(copia == "#EXTM3U\n", copia)
    finally:
        shutil.rmtree(temporal)

    guia = cargar("acestream-suite-guide-playlist.py")
    check(guia.compat_copy("/srv/www/varios/acestream-suite-guide.m3u")
          == "/srv/www/varios/acelist-epg.m3u", guia.compat_copy("/srv/www/varios/x.m3u"))
    check(guia.compat_copy("/tmp/otra.m3u") is None, guia.compat_copy("/tmp/otra.m3u"))
    temporal = tempfile.mkdtemp()
    try:
        destino = os.path.join(temporal, "acestream-suite-guide.m3u")
        guia.save_output("#EXTM3U\n", destino)
        for nombre in ("acestream-suite-guide.m3u", "acelist-epg.m3u"):
            check(os.path.isfile(os.path.join(temporal, nombre)), "no se escribio %s" % nombre)
        otro = tempfile.mkdtemp()
        try:
            guia.save_output("#EXTM3U\n", os.path.join(otro, "otra.m3u"))
            check(os.listdir(otro) == ["otra.m3u"], os.listdir(otro))
        finally:
            shutil.rmtree(otro)
    finally:
        shutil.rmtree(temporal)


@grupo("S4: los marcadores de formato de la app no se renombran")
def formatos():
    # `format: "acelist"` lo escribe la app en su export y `/epg.json` marca `acelist-epg`: son el
    # contrato con el contenedor, no la marca del producto, y renombrarlos rompe el par.
    guia = cargar("acestream-suite-guide-playlist.py")
    check(guia.MAPPING_FORMAT == "acelist-epg", guia.MAPPING_FORMAT)
    partido = cargar("acestream-suite-partido.py")
    check(partido.EXPORT_FORMAT == "acelist", partido.EXPORT_FORMAT)
    check(partido.MAPPING_FORMAT == "acelist-epg", partido.MAPPING_FORMAT)
    # El estado y el conjunto en directo viejos tienen que seguir leyendose (S4).
    check(partido.STATE_FORMAT == "acelist-partido", partido.STATE_FORMAT)
    check(partido.LIVE_FORMAT == "acelist-partido-live", partido.LIVE_FORMAT)
    lista = cargar("acestream-suite-playlist.py")
    check(lista.LIVE_FORMAT == "acelist-partido-live", lista.LIVE_FORMAT)


@grupo("S3: crons-install.sh instala los jobs nuevos")
def cron_install():
    with open(os.path.join(SCRIPTS, "crons-install.sh"), "r", encoding="utf-8") as fichero:
        texto = fichero.read()
    for trabajo in ("acestream-suite-playlist", "acestream-suite-partido",
                    "acestream-suite-prune", "acestream-suite-guide-playlist",
                    "acestream-suite-guide-refresh"):
        check(re.search(r"^JOBS=\(.*\b%s\b" % re.escape(trabajo), texto, re.M | re.S) is not None,
              "JOBS no lleva %s" % trabajo)


@grupo("S1: los self-test de los scripts siguen pasando")
def self_tests():
    for fichero in ("acestream-suite-playlist.py", "acestream-suite-partido.py",
                    "acestream-suite-guide-playlist.py"):
        modulo = cargar(fichero)
        modulo.self_test()


def main():
    fallos = 0
    for nombre, fn in GRUPOS:
        try:
            fn()
        except AssertionError as exc:
            fallos += 1
            print("FAIL: %s: %s" % (nombre, exc))
        except Exception as exc:  # un script que no se puede cargar tambien es un fallo
            fallos += 1
            print("FAIL: %s: %s: %s" % (nombre, type(exc).__name__, exc))
        else:
            print("PASS: %s" % nombre)
    print()
    if fallos:
        print("FAILED: %d grupos de %d" % (fallos, len(GRUPOS)))
        return 1
    print("OK: %d grupos, todos pasan" % len(GRUPOS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
