#!/usr/bin/env python3
"""Checks that the old product name is gone from scripts/ and that the new identifiers hold (S1, S3).

WHAT IT CHECKS
    - El nombre viejo no aparece en NINGUN fichero de `scripts/`, ni en este mismo test. La
      compatibilidad de una version (S4) ya se retiro: el producto se llama acestream-suite y lo
      unico que puede seguir nombrando el fichero viejo de una lista es un alias de DATOS, fuera del
      repositorio. Las dos mitades del nombre se unen aqui al ejecutar, no en el compilador: si el
      nombre estuviera entero en este fichero, la auditoria se dispararia contra si misma, y una
      suma de cadenas se pliega en una constante que acaba dentro del `.pyc`.
    - Los ficheros renombrados existen con su nombre nuevo, y con el viejo no queda ninguno.
    - Cada script lee solo su variable `ACESTREAM_SUITE_*`: puesta la vieja y no la nueva, manda el
      defecto (la vieja se ignora). Con el entorno vacio todo tiene un defecto utilizable.
    - Las rutas de log, lock, estado y respaldo son las nuevas, y no queda ningun respaldo por ruta
      vieja (ni `with_legacy`, ni constantes `*_LEGACY`).
    - Las copias de una lista se piden como DATOS (`ACESTREAM_SUITE_COMPAT_ALIASES`, pares
      `nuevo=otro` separados por comas): sin la variable no se escribe ninguna copia, la copia se
      escribe solo cuando la lista principal cambio, y siempre dentro del directorio de salida.
    - Los marcadores de los documentos (export, mapeo de guia y estado) llevan el nombre nuevo, que
      es lo que la app escribe.

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

# El nombre viejo, en dos trozos: ver el docstring. `VIEJO` es con lo que se compone un nombre de
# fichero o de variable que ya no debe existir; `PATRON` es lo que se busca en el texto.
VIEJO = "".join(("ace", "list"))
PATRON = re.compile(VIEJO, re.IGNORECASE)

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


def recorrer(directorio):
    """Todos los ficheros de un directorio, sin `__pycache__` ni directorios ocultos."""
    for raiz, carpetas, ficheros in os.walk(directorio):
        carpetas[:] = sorted(n for n in carpetas if not n.startswith((".", "__")))
        for nombre in sorted(ficheros):
            yield os.path.join(raiz, nombre)


FICHEROS = (
    "acestream-suite-playlist.py",
    "acestream-suite-prune.py",
    "acestream-suite-guide-playlist.py",
    "acestream-suite-playlist.cron",
    "acestream-suite-prune.cron",
    "acestream-suite-guide-playlist.cron",
    "acestream-suite-guide-refresh.cron",
    "crons-install.sh",
)

# Lo que el renombrado elimino: ni un fichero con estos nombres, ni una constante que los nombre.
VIEJOS = tuple(VIEJO + resto for resto in (
    "-playlist.py", "-playlist.cron", "-prune.py",
    "-prune.cron", "-epg-playlist.py", "-epg-playlist.cron", "-epg-refresh.cron",
))

SIMBOLOS_VIEJOS = (
    "with_legacy", "STATE_PATH_LEGACY", "GUARD_PATH_LEGACY",
    "PLAYLIST_SCRIPT_LEGACY", "PLAYLIST_FILE_LEGACY", "COMPAT_NAMES", "COMPAT_NAME",
)


@grupo("S3: el nombre viejo no aparece en scripts/, ni en este mismo test")
def sin_restos():
    for ruta in recorrer(SCRIPTS):
        with open(ruta, "r", encoding="utf-8", errors="replace") as fichero:
            for numero, linea in enumerate(fichero, 1):
                check(
                    not PATRON.search(linea),
                    "%s:%d: el nombre viejo sigue aqui: %r"
                    % (os.path.relpath(ruta, SCRIPTS), numero, linea.strip()),
                )


@grupo("S3: los ficheros llevan el nombre nuevo y los viejos ya no estan")
def ficheros():
    for nombre in FICHEROS:
        check(os.path.isfile(os.path.join(SCRIPTS, nombre)), "falta %s" % nombre)
    for viejo in VIEJOS:
        check(not os.path.exists(os.path.join(SCRIPTS, viejo)), "sigue estando %s" % viejo)


@grupo("S3: cada script lee solo su ACESTREAM_SUITE_*, y la vieja se ignora")
def entorno():
    # Lista: la app y el puente.
    modulo = cargar("acestream-suite-playlist.py", {})
    check(modulo.ACESTREAM_SUITE_URL == "http://127.0.0.1:8083", modulo.ACESTREAM_SUITE_URL)
    check(modulo.LOG_PATH == "/var/log/acestream-suite-playlist.log", modulo.LOG_PATH)
    check(modulo.LOCK_PATH == "/var/lock/acestream-suite-playlist.lock", modulo.LOCK_PATH)
    check(modulo.EXPORT_FORMAT == "acestream-suite", modulo.EXPORT_FORMAT)
    # Con la nueva puesta, manda la nueva.
    modulo = cargar("acestream-suite-playlist.py", {"ACESTREAM_SUITE_URL": "http://nuevo:8083"})
    check(modulo.ACESTREAM_SUITE_URL == "http://nuevo:8083", modulo.ACESTREAM_SUITE_URL)
    # Con SOLO la vieja puesta, manda el defecto: el nombre viejo ya no es una entrada.
    modulo = cargar("acestream-suite-playlist.py", {VIEJO + "_URL": "http://old:8083"})
    check(modulo.ACESTREAM_SUITE_URL == "http://127.0.0.1:8083", modulo.ACESTREAM_SUITE_URL)

    # Guia.
    modulo = cargar("acestream-suite-guide-playlist.py", {})
    check(modulo.OUTPUT == "/srv/www/varios/acestream-suite-guide.m3u", modulo.OUTPUT)
    check(modulo.LOG_PATH == "/var/log/acestream-suite-guide-playlist.log", modulo.LOG_PATH)
    check(modulo.LOCK_PATH == "/var/lock/acestream-suite-guide-playlist.lock", modulo.LOCK_PATH)
    check(modulo.MAPPING_FORMAT == "acestream-suite-guide", modulo.MAPPING_FORMAT)
    modulo = cargar("acestream-suite-guide-playlist.py", {"ACESTREAM_SUITE_GUIDE_URL": "http://n:8083"})
    check(modulo.ACESTREAM_SUITE_GUIDE_URL == "http://n:8083", modulo.ACESTREAM_SUITE_GUIDE_URL)
    modulo = cargar("acestream-suite-guide-playlist.py", {VIEJO + "_EPG_URL": "http://old:8083"})
    check(modulo.ACESTREAM_SUITE_GUIDE_URL == "http://127.0.0.1:8083",
          modulo.ACESTREAM_SUITE_GUIDE_URL)

    # Poda: guarda, log, lock y respaldo nuevos, y la copia de la base con el nombre nuevo.
    modulo = cargar("acestream-suite-prune.py", {})
    check(modulo.GUARD_PATH == "/var/lib/acestream-suite-prune/done", modulo.GUARD_PATH)
    check(modulo.LOG_PATH == "/var/log/acestream-suite-prune.log", modulo.LOG_PATH)
    check(modulo.LOCK_PATH == "/var/lock/acestream-suite-prune.lock", modulo.LOCK_PATH)
    check(modulo.BACKUP_DIR == "/backup/acestream-suite", modulo.BACKUP_DIR)
    check(modulo.BACKUP_DB_NAME == "acestream-suite.db", modulo.BACKUP_DB_NAME)
    check(modulo.DB_PATH == "/opt/acestream-suite/data/acestream-suite.db", modulo.DB_PATH)
    check(modulo.PLAYLIST_SCRIPT == "/usr/local/bin/acestream-suite-playlist",
          modulo.PLAYLIST_SCRIPT)


@grupo("S4: ninguna ruta se respalda con el nombre viejo (no quedan ayudantes de compatibilidad)")
def sin_respaldos():
    for fichero in ("acestream-suite-playlist.py", "acestream-suite-guide-playlist.py",
                    "acestream-suite-prune.py"):
        modulo = cargar(fichero)
        for simbolo in SIMBOLOS_VIEJOS:
            check(not hasattr(modulo, simbolo), "%s: sigue existiendo %s" % (fichero, simbolo))
        # `env` recibe la variable nueva y el defecto, y nada mas.
        check(modulo.env("ACESTREAM_SUITE_NO_EXISTE", "d") == "d", fichero)


@grupo("S4: las copias de una lista se piden como datos y no salen del directorio de salida")
def alias_copias():
    lista = cargar("acestream-suite-playlist.py", {})
    check(lista.COMPAT_ALIASES == {}, lista.COMPAT_ALIASES)   # entorno vacio: ninguna copia
    check(lista.compat_aliases("") == {}, lista.compat_aliases(""))
    check(lista.compat_aliases("acestream-suite.m3u=otro.m3u")
          == {"acestream-suite.m3u": ["otro.m3u"]}, lista.compat_aliases("acestream-suite.m3u=x"))
    check(lista.compat_aliases(" acestream-suite.m3u = otro.m3u , ")
          == {"acestream-suite.m3u": ["otro.m3u"]}, lista.compat_aliases(" con espacios "))
    check(lista.compat_aliases("a.m3u=b.m3u,c.m3u=d.m3u")
          == {"a.m3u": ["b.m3u"], "c.m3u": ["d.m3u"]}, lista.compat_aliases("dos pares"))
    check(lista.compat_aliases("sin-igual") == {}, lista.compat_aliases("sin-igual"))
    check(lista.compat_aliases("a.m3u=") == {}, lista.compat_aliases("a.m3u="))
    # La regla de seguridad: una copia nunca escribe fuera del directorio de salida.
    check(lista.compat_aliases("a.m3u=../fuera.m3u") == {}, lista.compat_aliases(".."))
    check(lista.compat_aliases("a.m3u=/tmp/fuera.m3u") == {}, lista.compat_aliases("absoluta"))

    # Con la variable puesta, el modulo la lee al importarse: es lo que hace el cron del host.
    modulo = cargar("acestream-suite-playlist.py",
                    {"ACESTREAM_SUITE_COMPAT_ALIASES": "acestream-suite.m3u=otro.m3u"})
    check(modulo.COMPAT_ALIASES == {"acestream-suite.m3u": ["otro.m3u"]}, modulo.COMPAT_ALIASES)
    temporal = tempfile.mkdtemp()
    try:
        built = [(nombre, "#EXTM3U\n", 0) for nombre, _, _, _, _ in modulo.PLAYLISTS]
        escrito = modulo.save_all(built, temporal)
        check(sorted(os.listdir(temporal)) == sorted(
            [nombre for nombre, _, _, _, _ in modulo.PLAYLISTS] + ["otro.m3u"]
        ), sorted(os.listdir(temporal)))
        with open(os.path.join(temporal, "otro.m3u")) as fichero:
            check(fichero.read() == "#EXTM3U\n", "la copia no lleva el texto de la lista")
        # Solo cuando la lista principal cambio: un fichero igual no reescribe la copia.
        os.unlink(os.path.join(temporal, "otro.m3u"))
        check(escrito["acestream-suite.m3u"] is True, escrito)
        check(modulo.save_all(built, temporal)["acestream-suite.m3u"] is False, "igual, no se escribe")
        check(not os.path.exists(os.path.join(temporal, "otro.m3u")), "sin cambio no hay copia")
    finally:
        shutil.rmtree(temporal)

    # La guia: el mismo mecanismo, y un destino ajeno no lleva copias al lado.
    guia = cargar("acestream-suite-guide-playlist.py",
                  {"ACESTREAM_SUITE_COMPAT_ALIASES": "acestream-suite-guide.m3u=otro-guia.m3u"})
    check(guia.compat_copies(guia.OUTPUT) == ("otro-guia.m3u",), guia.compat_copies(guia.OUTPUT))
    check(guia.compat_copies("/tmp/otra.m3u") == (), guia.compat_copies("/tmp/otra.m3u"))
    temporal = tempfile.mkdtemp()
    try:
        destino = os.path.join(temporal, "acestream-suite-guide.m3u")
        check(guia.save_output("#EXTM3U\n", destino) is True, "el primer guardado escribe")
        for nombre in ("acestream-suite-guide.m3u", "otro-guia.m3u"):
            check(os.path.isfile(os.path.join(temporal, nombre)), "no se escribio %s" % nombre)
        os.unlink(os.path.join(temporal, "otro-guia.m3u"))
        check(guia.save_output("#EXTM3U\n", destino) is False, "un fichero igual no se reescribe")
        check(not os.path.exists(os.path.join(temporal, "otro-guia.m3u")), "sin cambio no hay copia")
        otro = tempfile.mkdtemp()
        try:
            guia.save_output("#EXTM3U\n", os.path.join(otro, "otra.m3u"))
            check(os.listdir(otro) == ["otra.m3u"], os.listdir(otro))
        finally:
            shutil.rmtree(otro)
    finally:
        shutil.rmtree(temporal)


@grupo("S3: los marcadores de los documentos llevan el nombre nuevo")
def formatos():
    guia = cargar("acestream-suite-guide-playlist.py")
    check(guia.MAPPING_FORMAT == "acestream-suite-guide", guia.MAPPING_FORMAT)
    check(guia.EXPORT_FORMAT == "acestream-suite", guia.EXPORT_FORMAT)
    lista = cargar("acestream-suite-playlist.py")
    check(lista.EXPORT_FORMAT == guia.EXPORT_FORMAT, lista.EXPORT_FORMAT)


@grupo("S3: crons-install.sh instala los jobs nuevos")
def cron_install():
    with open(os.path.join(SCRIPTS, "crons-install.sh"), "r", encoding="utf-8") as fichero:
        texto = fichero.read()
    for trabajo in ("acestream-suite-playlist", "acestream-suite-prune",
                    "acestream-suite-guide-playlist", "acestream-suite-guide-refresh"):
        check(re.search(r"^JOBS=\(.*\b%s\b" % re.escape(trabajo), texto, re.M | re.S) is not None,
              "JOBS no lleva %s" % trabajo)


@grupo("S1: los self-test de los scripts siguen pasando")
def self_tests():
    for fichero in ("acestream-suite-playlist.py",
                    "acestream-suite-guide-playlist.py", "acestream-suite-prune.py"):
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
