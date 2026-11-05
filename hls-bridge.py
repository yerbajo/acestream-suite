#!/usr/bin/env python3
"""Pasa un canal de AceStream a HLS, para reproductores de TV que lo prefieren.

POR QUÉ EXISTE
    El motor entrega MPEG-TS en directo por HTTP, y el reproductor de la tele (Tessel, AVPlay) lo
    consume tal cual. El motor no sirve HLS (`/ace/manifest.m3u8` no responde en la compilación
    3.2.11, comprobado) y Dispatcharr 0.31.0 tampoco: su módulo `hls_proxy` existe en el código pero
    **no está montado en sus URLs** (solo `ts/`, `vod/`, `catchup/`). Así que el HLS lo hacemos aquí:
    un ffmpeg que lee el TS del motor y lo reempaqueta en segmentos. `-c copy`: no reencodea, solo
    corta y reescribe el contenedor, así que cuesta muy poco CPU — y de paso hace de colchón, porque
    entrega al reproductor al ritmo que este consume en vez de en la ráfaga con la que el motor
    arranca.

CÓMO SE USA
    http://<host>:8084/<infohash>/index.m3u8     el manifiesto; el reproductor hace el resto
    http://<host>:8084/healthz                   qué sesiones hay vivas
    El fichero `hls.m3u` que genera acelist-playlist.py apunta aquí, así que en la tele basta con
    cargar esa lista.

DECISIONES
    - Una sesión por canal (una instancia de ffmpeg por infohash), y el motor admite un solo
      reproductor por sesión: si alguien más abre el mismo canal, el segundo se queda fuera. Es
      deliberado, no un fallo.
    - Colchón largo en disco, ventana corta en el manifiesto: 240 segmentos (8 min) en el tmpfs, y
      al reproductor se le sirven los últimos 30, que acaban 20 segmentos (40 s) por detrás del
      directo. Con `delete_segments` el manifiesto no crece y una bajada del par no se ve.
    - Las sesiones se cierran solas cuando nadie pide durante IDLE segundos, y hay un tope de
      sesiones simultáneas para no dejar al motor sin aire.
    - **Sin `-reconnect`, y con la espera creciendo cuando el motor rechaza.** Medido el 2026-10-15:
      el motor cerró el stream de un canal que se estaba viendo cuatro veces en diez minutos (una con
      salida limpia, ffmpeg código 0) y cada cierre era una sesión nueva con el manifiesto desde 0
      debajo de quien estaba viendo - la tele se quedaba en «cargando contenido». Hoy el bombeo
      reabre la respuesta del motor y alimenta al MISMO ffmpeg, así que ni el muxer ni la secuencia
      se reinician. El motor da **un solo stream por contenido**:
      mientras lo guarda, otro lector recibe una URL que no entrega datos (medido: «Invalid data
      found» con el mismo ffmpeg que sí lee cuando el stream está libre), y el dueño del stream sigue
      siéndolo ~10 min después de desaparecer (medido: 20:07 ocupado, 20:15 ocupado, libre 20:19).
      Por eso el bombeo acota cada lectura (`urlopen(timeout=PUMP_TIMEOUT)`) y cierra la entrada de
      ffmpeg tras PUMP_MAX_MISSES intentos sin datos, y `release_engine` suelta la sesión propia al
      cerrar.
    - **Al cerrar una sesión, el puente la suelta en el motor** (el `command_url` que el propio
      `getstream` devuelve para NUESTRO `pid`, con `?method=stop`). Sin `command_url` no se suelta
      nada: el token por defecto es la sesión sin `pid`, la que comparten todos los clientes de la
      casa, y soltarla pararía el stream de otro lector.
      Sin eso el motor sigue guardando la sesión unos 10 min después de que el consumidor desaparece
      (medido: ocupado a las 20:07, ocupado a las 20:15, libre a las 20:19) y volver a ese canal
      dentro de ese rato contesta 500 — que es como la tele se quedaba en «cargando contenido» tras
      cambiar de canal y volver. Con la sesión soltada, el mismo canal vuelve a arrancar al instante.
    - La sesión **sobrevive al ffmpeg**: si el proceso muere, se suelta la sesión del motor y se
      reintenta en el sitio, en la misma sesión (y el mismo directorio), hasta que el reaper la cierre
      por no haber peticiones. Antes la sesión se tiraba en el hilo que vigila al ffmpeg, y la
      petición siguiente abría otra.
    - El 504 se reserva para cuando de verdad no va a haber manifiesto: la espera se alarga mientras
      haya reintentos en marcha, con un tope (HLS_STARTUP_TOTAL_SECONDS). Antes volvía en cuanto el
      ffmpeg moria, así que un canal ocupado contestaba 504 en 1,5 s (medido).

CÓMO SE COMPRUEBA
    python3 hls-bridge.py --self-test     # decisiones de arriba, sin motor y sin red
    python3 hls-bridge.py                 # sirve (el compose lo llama así)
    curl http://127.0.0.1:8084/healthz    # qué sesiones hay vivas
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8084"))
ENGINE = os.environ.get("ACESTREAM_PUBLIC", "http://192.168.1.28:8081").rstrip("/")
ROOT = os.environ.get("HLS_ROOT", "/hls")
IDLE = float(os.environ.get("HLS_IDLE_SECONDS", "45"))       # sin peticiones: se cierra
STARTUP = float(os.environ.get("HLS_STARTUP_SECONDS", "8"))  # hasta que hay manifiesto
# Tope de la espera total de un manifiesto contando reintentos. El motor contesta 500 a una sesion
# cuyo contenido aun tiene la anterior viva, y en 1-2 s la suelta: el reintento es lo que arranca, asi
# que la espera se alarga con el, pero no sin fin (un reproductor esperando es un reproductor colgado).
STARTUP_TOTAL = float(os.environ.get("HLS_STARTUP_TOTAL_SECONDS", str(STARTUP * 3)))
# Entre el ffmpeg muerto y el siguiente intento, en la misma sesion. Es el reintento corto: el que
# se usa cuando la generacion anterior SI llego a servir manifiesto (un final de stream, que se
# reabre y el reproductor apenas lo nota).
RETRY = float(os.environ.get("HLS_RETRY_SECONDS", "2"))
# Tope del reintento cuando el motor NO acepta la sesion (otro lector tiene el stream): se espera el
# doble cada vez. Medido el 2026-10-18: un canal atascado asi recibio 13 027 peticiones en un dia.
MAX_RETRY = float(os.environ.get("HLS_MAX_RETRY_SECONDS", "30"))
STOP_TIMEOUT = float(os.environ.get("HLS_STOP_TIMEOUT_SECONDS", "5"))
# Aviso de tmpfs: por debajo de estos MB libres el reaper cierra la sesion que mas ocupa. Un tmpfs
# lleno no rompe una sesion, las rompe TODAS a la vez (ffmpeg deja de poder escribir segmentos en
# cualquiera de ellas), y con la ventana de 240 segmentos dos canales de 1080p rozan los 2 GB del
# tmpfs del compose.
MIN_FREE_MB = float(os.environ.get("HLS_MIN_FREE_MB", "256"))
MAX_SESSIONS = int(os.environ.get("HLS_MAX_SESSIONS", "4"))
# Alias de Content ID: `{"<viejo>": "<nuevo>"}`. Sirve para que un enlace ya cargado en un
# reproductor (una lista cacheada en la tele) siga funcionando cuando en el catalogo se cambia la
# fuente de ese canal por otra mejor: la peticion del infohash viejo se atiende con el nuevo.
ALIASES = json.loads(os.environ.get("HLS_ALIASES", "{}") or "{}")
# El bombeo: cuanto se espera a leer del motor antes de dar la conexion por muerta, y cuantos
# rechazos seguidos se aguantan antes de cerrarle la entrada a ffmpeg para que el puente reintente
# la sesion entera. Medido el 2026-10-18: el motor sirve el directo en respuestas finitas (de 3 s a
# 45 s segun lo que tenga por delante), asi que el que lee tiene que volver a pedir sin cortar.
# Tolerancia a un hueco sin datos del motor. Un directo P2P tiene huecos de decenas de segundos; con
# 20 s el bombeo abortaba lecturas buenas (medido el 2026-10-18) y, peor, soltaba la sesion.
PUMP_TIMEOUT = float(os.environ.get("HLS_PUMP_TIMEOUT_SECONDS", "60"))
PUMP_MAX_MISSES = int(os.environ.get("HLS_PUMP_MAX_MISSES", "3"))
SEGMENT_TIME = os.environ.get("HLS_SEGMENT_TIME", "2")
# El PRIMER segmento, mas corto: es lo que espera la tele al cambiar de canal. El resto siguen a 2 s.
INIT_TIME = os.environ.get("HLS_INIT_TIME", "1")
# Ventana de la lista: 20 segmentos de 2 s = 40 s de colchon en el manifiesto, y 10 segmentos mas
# que se quedan en disco antes de borrarse (`-hls_delete_threshold`). Medido el 2026-10-18: con 8
# segmentos (16 s), el reproductor de la tele pedia segmentos que ya se habian borrado (404 en
# cadena: seg00130, seg00131...) y acababa en error. Un reproductor que se queda 20 s atras tiene que
# encontrar todavia lo que pide.
LIST_SIZE = os.environ.get("HLS_LIST_SIZE", "240")
# 240 segmentos de 2 s = 8 min de ventana (a ~1,5 MB por segmento son ~360 MB de tmpfs), y 60
# segmentos mas que se quedan en disco antes de borrarse. Es el COLCHON: el motor entrega a rachas y
# su par puede bajar del bitrate del canal, asi que cuanto mas aguante la ventana, menos se ve.
DELETE_THRESHOLD = os.environ.get("HLS_DELETE_THRESHOLD", "60")
# Cuantos segmentos por detras del directo se sirven. El motor entrega a rachas y su unico par puede
# bajar por debajo del bitrate del canal (medido el 2026-10-18: speed_down cayendo de 1,7 MB/s a
# 0,3 MB/s con 1 par), asi que el reproductor tiene que ir por detras para que esas bajadas no se
# vean: se le esconden los ultimos N segmentos del manifiesto. 10 segmentos = 20 s de colchon.
DELAY_SEGMENTS = int(os.environ.get("HLS_DELAY_SEGMENTS", "20"))
# Y cuantos segmentos se le ensenan al reproductor, contando hacia atras desde el borde retrasado.
# Ventana corta en el manifiesto (arranque rapido, como un directo normal) y colchon grande en disco:
# un reproductor que empieza por el primer segmento de una lista de 240 se pondria 8 minutos atras.
SERVED_SEGMENTS = int(os.environ.get("HLS_SERVED_SEGMENTS", "30"))

INFO = re.compile(r"^/?(?P<infohash>[0-9a-fA-F]{40})/(?P<name>[A-Za-z0-9._-]+)$")
_sessions = {}
_lock = threading.Lock()


def log(message):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message), flush=True)


def _rebase(url):
    """El motor da sus URLs con `localhost:6878`; se cambian por la direccion por la que preguntamos."""
    if not url:
        return ""
    return url.replace("http://localhost:6878", ENGINE).replace("http://127.0.0.1:6878", ENGINE)


def retry_delay(failures):
    """Espera antes del siguiente intento de ffmpeg, segun los fallos seguidos.

    Sin fallos seguidos, la corta: un final de stream se reabre en 2 s. Con fallos seguidos (el motor
    no acepta la sesion porque otro lector tiene el stream), el doble cada vez y con tope: no se
    martillea al motor mientras el reproductor recibe su 504.
    """
    if failures <= 0:
        return RETRY
    return min(RETRY * 2 ** failures, MAX_RETRY)


def _served_manifest(text, served=SERVED_SEGMENTS):
    """Sirve solo los ultimos `served` segmentos del manifiesto y reescribe la secuencia.

    El fichero en disco es el colchon (240 segmentos); lo que se le ensena al reproductor es una
    ventana corta pegada al borde retrasado, para que arranque rapido y no se vaya al principio.
    """
    if served <= 0:
        return text
    lines = text.splitlines(keepends=True)
    blocks = [i for i, line in enumerate(lines) if line.startswith("#EXTINF")]
    if len(blocks) <= served:
        return text
    drop = len(blocks) - served
    sequence = 0
    for line in lines:
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                sequence = int(line.split(":", 1)[1].strip())
            except ValueError:
                sequence = 0
            break
    head = []
    seen_sequence = False
    for line in lines[: blocks[0]]:
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            head.append("#EXT-X-MEDIA-SEQUENCE:%d\n" % (sequence + drop))
            seen_sequence = True
        else:
            head.append(line)
    if not seen_sequence:
        head.append("#EXT-X-MEDIA-SEQUENCE:%d\n" % (sequence + drop))
    return "".join(head) + "".join(lines[blocks[drop]:])


def _delayed_manifest(text, delay=DELAY_SEGMENTS):
    """Quita del final del manifiesto los ultimos `delay` segmentos: el reproductor va por detras.

    El manifiesto que escribe ffmpeg acaba en el directo; si se le sirve tal cual, el reproductor
    vive pegado al borde y cualquier bajada del par se ve como un corte. Recortando el final, el
    reproductor reproduce `delay * 2` segundos por detras y la bajada se absorbe.
    """
    if delay <= 0:
        return text
    lines = text.splitlines(keepends=True)
    blocks = []  # (indice de la linea #EXTINF, indice de la linea del segmento)
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            blocks.append((i, i + 1 if i + 1 < len(lines) else i))
    if len(blocks) <= delay:
        return text
    drop_from = blocks[-delay][0]
    return "".join(lines[:drop_from])


def _newest_segment(directory):
    """El segmento mas reciente del directorio, o None. Para sustituir uno que ya no esta."""
    try:
        files = [os.path.join(directory, n) for n in os.listdir(directory) if n.endswith(".ts")]
    except OSError:
        return None
    return max(files, key=os.path.getmtime) if files else None


class Session:
    def __init__(self, infohash, *, command=None, pump=None):
        self.infohash = infohash
        self.directory = os.path.join(ROOT, infohash)
        # `command` existe para el --self-test: sin motor y sin red se puede arrancar un ffmpeg de
        # mentira que muere al instante, que es la unica forma de provocar el reintento.
        self.command = command
        # `pump` es explicito para el --self-test: con un comando de mentira se puede pedir que el
        # bombeo del motor alimente a ese comando (un `cat` que cuenta bytes, por ejemplo).
        self.pump = (command is None) if pump is None else pump
        self.closed = False  # el reaper la cerro: no se reintenta mas
        self.attempt = 0  # generaciones de ffmpeg de esta sesion
        self.failures = 0  # generaciones seguidas que NO llegaron a servir manifiesto
        self.substituted = 0  # segmentos pedidos que ya no estaban y se sirvio el mas nuevo
        # El pid es lo que hace ESTABLE la sesion del motor: medido el 2026-10-18, con pid fijo el
        # motor entrega un stream continuo (36 MB en 40 s sin cerrar) y devuelve el mismo
        # `playback_session_id` en cada peticion; sin pid, la sesion por defecto se la reparten todos
        # los clientes de la casa y las lecturas acaban en 500.
        self.pid = str(uuid.uuid4())
        self.playback_url = None
        self.command_url = None
        self.session_id = None
        self.stat_url = None
        self.last_seen = time.time()
        # Contadores para el resumen periodico: es lo que dice si la tele va fina o se atasca.
        self.client = None
        self.manifests = 0
        self.segments = 0
        self.failures = 0
        self.last_sequence = None
        self.process = None
        self.start()
        threading.Thread(target=self._watch, daemon=True).start()

    @property
    def manifest(self):
        return os.path.join(self.directory, "index.m3u8")

    def ready(self):
        return os.path.exists(self.manifest)

    def touch(self):
        self.last_seen = time.time()

    def start(self):
        """Arranca - o rearranca - el ffmpeg de la sesion, en el mismo directorio.

        Cada generacion empieza con el directorio limpio: el muxer HLS reescribe el manifiesto desde
        cero, asi que los segmentos de la generacion anterior nadie los pide y solo llenarian el
        tmpfs (512 MB) hasta dejar sin sitio a la que si se ve.

        shortcut: la numeracion vuelve a 0 en cada generacion, asi que un cliente a media
        reproduccion ve saltar MEDIA-SEQUENCE. Continuarla pide renombrar segmentos debajo del
        cliente, que es mas trabajo y mas riesgo que el que justifica un corte que ya casi no pasa:
        la sesion solo se rearranca cuando el ffmpeg muere de verdad.
        """
        shutil.rmtree(self.directory, ignore_errors=True)
        os.makedirs(self.directory, exist_ok=True)
        self.attempt += 1
        self.command_url = None  # esta generacion vuelve a pedir la sesion (con el mismo pid)
        self.playback_url = None
        self.process = subprocess.Popen(
            self.command or self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # ffmpeg lee `pipe:0`; este hilo es quien le da de comer del motor, sin dejar que el stream
        # se acabe en cada respuesta finita. Uno por generacion.
        if self.pump:
            threading.Thread(target=self._pump, args=(self.process,), daemon=True).start()

    def _command(self):
        """El ffmpeg que lee el motor y lo sirve en segmentos.

        ffmpeg NO habla HTTP: lee `pipe:0`, y el HTTP lo hace `_pump`. El motivo esta medido
        (2026-10-18): el motor sirve el directo en respuestas FINITAS (`/content/<hash>/<rand>`, de
        3 s a 45 s segun lo que tenga por delante) que traen `Content-Length`, y con una respuesta
        completa ffmpeg termina - los `-reconnect*` no la reabren. Cada final reiniciaba el muxer, o
        sea el manifiesto, o sea el reproductor volviendo a llenar el colchon. Con el bombeo, ffmpeg
        ve una sola entrada continua: el motor puede cerrar y reabrir las veces que quiera.
        """
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts",
            "-f", "mpegts", "-i", "pipe:0",
            "-c", "copy",
            "-f", "hls",
            "-hls_time", SEGMENT_TIME,
            "-hls_init_time", INIT_TIME,
            "-hls_list_size", LIST_SIZE,
            "-hls_delete_threshold", DELETE_THRESHOLD,
            "-hls_flags", "delete_segments+independent_segments",
            "-hls_segment_filename", os.path.join(self.directory, "seg%05d.ts"),
            os.path.join(self.directory, "index.m3u8"),
        ]

    def wait_ready(self, timeout=STARTUP, total=STARTUP_TOTAL):
        """Espera al manifiesto hasta el tope (`total`); solo dice que no cuando de verdad no va a haberlo.

        Antes volvia en cuanto el ffmpeg moria y el reproductor recibia un 504 a los 1,5 s; ahora
        espera el tope entero (24 s por defecto) porque el bombeo puede estar peleandose con el motor
        por el stream (otro lector lo tiene, o acaba de soltarlo) y el manifiesto esta a punto de
        aparecer.
        """
        started = time.time()
        deadline = started + total
        while time.time() < deadline:
            if self.ready():
                return True
            if self.closed:
                return False
            time.sleep(0.25)
        return self.ready()

    def _pump(self, process):
        """Lee el motor y se lo escribe a ffmpeg por stdin; cuando una respuesta acaba, pide otra.

        Escalera, medida el 2026-10-18: el motor da el stream con NUESTRO pid y lo mantiene mientras
        se lo lea; si una respuesta acaba, se vuelve a pedir con el mismo pid SIN soltar la sesion
        (soltarla deja el canal bloqueado ~10 min para todos, incluido nosotros mismos). Solo si dos
        intentos seguidos no dan datos se suelta -para liberar el canal- y se vuelve a pedir. Tras
        PUMP_MAX_MISSES intentos se cierra stdin: ffmpeg sale y manda el reintento en el sitio de
        `_watch`.
        """
        misses = 0
        released = False
        try:
            while not self.closed and self.process is process:
                try:
                    if not self.playback_url:
                        self._acquire()
                    got = 0
                    with urllib.request.urlopen(self.playback_url, timeout=PUMP_TIMEOUT) as response:
                        while not self.closed and self.process is process:
                            chunk = response.read(65536)
                            if not chunk:
                                break  # la respuesta se acabo: se pide otra y se sigue
                            got += len(chunk)
                            process.stdin.write(chunk)
                            process.stdin.flush()
                    self.playback_url = None
                    if got:
                        misses = 0
                        released = False
                    else:
                        misses += 1
                except Exception as exc:  # noqa: BLE001 - el bombeo no puede tumbar el servicio
                    misses += 1
                    self.playback_url = None
                    log("[%s] motor: %s (intentos sin datos: %d)"
                        % (self.infohash[:8], exc, misses))
                if self.closed or self.process is not process:
                    break
                if misses == 0:
                    continue  # hubo datos: se vuelve a pedir en el acto
                if misses >= 2 and not released:
                    # dos intentos sin datos: ahora si, soltar (es lo unico que libera el canal)
                    log("[%s] el motor no acepta la sesion: la suelto y vuelvo a pedir"
                        % self.infohash[:8])
                    self.release_engine()
                    released = True
                if misses >= PUMP_MAX_MISSES:
                    log("[%s] el motor no da el canal: cierro la entrada de ffmpeg"
                        % self.infohash[:8])
                    break
                time.sleep(retry_delay(misses))
        except (BrokenPipeError, ValueError):
            pass  # ffmpeg se cerro: el hilo termina y _watch se ocupa
        finally:
            try:
                process.stdin.close()
            except Exception:  # noqa: BLE001
                pass

    def _acquire(self):
        """Pide sesion al motor con NUESTRO pid y se queda su URL de reproduccion y la de pararla."""
        url = "%s/ace/getstream?infohash=%s&format=json&pid=%s" % (ENGINE, self.infohash, self.pid)
        with urllib.request.urlopen(url, timeout=PUMP_TIMEOUT) as answer:
            data = json.load(answer)
        if data.get("error"):
            raise RuntimeError("motor: %s" % data["error"])
        response = data.get("response") or {}
        self.playback_url = _rebase(response.get("playback_url", ""))
        self.command_url = _rebase(response.get("command_url", ""))
        self.stat_url = _rebase(response.get("stat_url", ""))
        self.session_id = response.get("playback_session_id")
        if not self.playback_url:
            raise RuntimeError("motor: getstream sin playback_url")

    def _watch(self):
        """El log de ffmpeg al log del servicio y, cuando muere, otro intento en la misma sesion.

        Antes se tiraba la sesion aqui mismo - se borraba el directorio y la entrada de `_sessions` -
        y la peticion siguiente abria otra, reiniciando el manifiesto debajo de quien estuviera
        viendo. Ahora la sesion sobrevive al ffmpeg: se reintenta en el sitio mientras alguien pida
        algo, y es el reaper el que la cierra cuando nadie pide.
        """
        while True:
            process = self.process
            try:
                for line in process.stdout:
                    text = line.decode("utf-8", "replace").strip()
                    if text:
                        log("[%s] ffmpeg: %s" % (self.infohash[:8], text[:200]))
            except Exception:  # noqa: BLE001 - el hilo no debe tumbar el servicio
                pass
            # El stdout puede cerrarse con el proceso todavia vivo (medido: "termino con codigo
            # None"): entonces no hay nada que reintentar, solo esperar a que termine de verdad.
            while not self.closed and process.poll() is None:
                time.sleep(1)
            log("[%s] ffmpeg terminó con código %s (intento %d)"
                % (self.infohash[:8], process.poll(), self.attempt))
            if self.closed:
                break
            # Con manifiesto, esta generacion sirvio: el reintento es el corto. Sin manifiesto, el
            # motor no la acepto: se cuenta y se espera mas.
            if self.ready():
                self.failures = 0
            else:
                self.failures += 1
            # NO se suelta aqui: la sesion (con su pid y su colchon) se reusa en el reintento. Se
            # suelta al cerrar la sesion, que es cuando el canal tiene que quedar libre para otro.
            delay = retry_delay(self.failures)
            log("[%s] el motor se fue: rearranco en %.0f s (fallos seguidos: %d)"
                % (self.infohash[:8], delay, self.failures))
            # Espera interrumpible: si el reaper cierra la sesion mientras se espera, no se queda
            # dormida hasta el final del backoff.
            deadline = time.time() + delay
            while not self.closed and time.time() < deadline:
                time.sleep(min(0.25, deadline - time.time()))
            if self.closed:
                break
            self.start()
        with _lock:
            # El directorio se borra solo si nadie lo ocupa. Al cerrar la vieja, otra sesion del
            # mismo canal pudo crear la suya en el mismo sitio: borrarlo entonces le quitaba el
            # manifiesto y los segmentos debajo, con la tele mirando. Y si no hay entrada, nadie lo
            # ocupa: se borra, que es lo que deja libre el tmpfs.
            owner = _sessions.get(self.infohash)
            if owner is None or owner is self:
                _sessions.pop(self.infohash, None)
                shutil.rmtree(self.directory, ignore_errors=True)

    def release_engine(self):
        """Suelta la sesion del motor de este canal. Nunca lanza: no poder soltarla no es un fallo.

        Sin esto el motor guarda la sesion ~10 min despues de que el consumidor desaparezca y volver
        a ese canal contesta 500 (medido), que es lo que dejaba la tele en «cargando contenido» al
        cambiar de canal y volver.

        Se usa el `command_url` que el motor dio para NUESTRO `pid`. Si no lo dio, no se suelta nada:
        el token por defecto (`/ace/cmd/<hash>/f528764d…`) es la sesion sin `pid`, compartida por
        todos los clientes de la casa, y mandarle `stop` pararia el stream de otro lector.
        """
        if not self.command_url:
            log("[%s] el motor no dio command_url: no suelto nada" % self.infohash[:8])
            return
        url = self.command_url + "?method=stop"
        try:
            with urllib.request.urlopen(url, timeout=STOP_TIMEOUT) as answer:
                log("[%s] sesion del motor soltada: %s"
                    % (self.infohash[:8], answer.read(80).decode("utf-8", "replace").strip()))
        except Exception as exc:  # noqa: BLE001 - es limpieza, no puede tumbar la sesion
            log("[%s] no se pudo soltar la sesion del motor: %s" % (self.infohash[:8], exc))

    def stop(self):
        """Cierra la sesion: el reaper decide, y el hilo que vigila deja de reintentar."""
        self.closed = True
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.release_engine()


def _dir_bytes(directory):
    """Bytes de segmentos que ocupa una sesion en el tmpfs (0 si el directorio ya no esta)."""
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    total = 0
    for name in names:
        if name.endswith(".ts"):
            try:
                total += os.path.getsize(os.path.join(directory, name))
            except OSError:
                pass
    return total


def _biggest(sessions):
    """La sesion que mas ocupa, o None: la que se sacrifica cuando el tmpfs se esta llenando."""
    return max(sessions, key=lambda s: _dir_bytes(s.directory), default=None)


def session_for(infohash):
    """La sesión del canal, arrancándola si hace falta (una sola vez por canal).

    Cerrar una sesión son dos llamadas de red con tope (matar el ffmpeg, soltar la sesión del motor),
    así que se hacen SIEMPRE fuera de `_lock`: con el lock tomado, cerrar una sesión paraba a todos
    los handlers, la tele incluida, hasta diez segundos. Y no se abre otra del mismo canal hasta que
    la que se está cerrando ha terminado, porque las dos escribirían en el mismo directorio.
    """
    while True:
        with _lock:
            current = _sessions.get(infohash)
            if current is not None and not current.closed:
                # Puede existir con el ffmpeg muerto y estar reintentando: eso es justo lo que se
                # quiere, y devolverla evita el reinicio del manifiesto que dejaba colgada a la tele.
                current.touch()
                return current
            busy = current is not None  # cerrandose en otro hilo
            victim = None
            if not busy and len(_sessions) >= MAX_SESSIONS:
                live = [s for s in _sessions.values() if not s.closed]
                victim = min(live, key=lambda s: s.last_seen) if live else None
                busy = victim is None  # el tope esta lleno de sesiones que se estan cerrando
        if busy:
            time.sleep(0.1)  # se espera aqui, no con el lock tomado
            continue
        if victim is not None:
            log("tope de sesiones: cierro %s para abrir %s" % (victim.infohash[:8], infohash[:8]))
            victim.stop()
            with _lock:
                if _sessions.get(victim.infohash) is victim:
                    _sessions.pop(victim.infohash, None)
            continue
        with _lock:
            if len(_sessions) >= MAX_SESSIONS:
                continue  # otro hilo se quedo el hueco mientras se cerraba la victima
            log("abriendo sesión para %s" % infohash)
            current = Session(infohash)  # dentro del lock: crea el directorio antes de publicarse
            _sessions[infohash] = current
            return current


def summarize():
    """Una linea cada 30 s por sesion: la pelicula de lo que hace el reproductor, sin inundar."""
    while True:
        time.sleep(30)
        with _lock:
            for session in list(_sessions.values()):
                log(
                    "resumen %s: %d manifiestos, %d segmentos, %d fallos, %d sustituidos, secuencia hasta %s, "
                    "cliente %s, %.0f s sin peticiones"
                    % (session.infohash[:8], session.manifests, session.segments, session.failures,
                       session.substituted, session.last_sequence, session.client or "?",
                       time.time() - session.last_seen)
                )


def reaper():
    """Cierra las sesiones que nadie está viendo y, si el tmpfs se llena, la que más ocupa.

    Un tmpfs lleno no rompe una sesión: rompe todas a la vez, porque ffmpeg deja de poder escribir
    segmentos en cualquiera de ellas. Por eso aquí se mira el espacio libre además del silencio, y
    se sacrifica la sesión más cara en vez de dejar que se caigan todas.
    """
    while True:
        time.sleep(5)
        now = time.time()
        with _lock:
            idle = [s for s in _sessions.values() if now - s.last_seen > IDLE]
        for session in idle:
            log("cerrando %s: %d s sin peticiones" % (session.infohash[:8], now - session.last_seen))
            session.stop()  # fuera del lock: mata el ffmpeg y suelta la sesion del motor
            with _lock:
                if _sessions.get(session.infohash) is session:
                    _sessions.pop(session.infohash, None)
        try:
            free = shutil.disk_usage(ROOT).free
        except OSError:
            free = None
        if free is not None and free < MIN_FREE_MB * 2 ** 20:
            with _lock:
                victim = _biggest([s for s in _sessions.values() if not s.closed])
            if victim is not None:
                log("quedan %d MB en %s: cierro %s, que ocupa %d MB"
                    % (free // 2 ** 20, ROOT, victim.infohash[:8],
                       _dir_bytes(victim.directory) // 2 ** 20))
                victim.stop()
                with _lock:
                    if _sessions.get(victim.infohash) is victim:
                        _sessions.pop(victim.infohash, None)


class Handler(BaseHTTPRequestHandler):
    server_version = "acehls"

    def log_message(self, fmt, *args):  # el log de peticiones, en una línea y sin el ruido de sempre
        pass

    def _send(self, status, body, content_type, cache="no-cache", session=None, what=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)
        if session is not None and status >= 400:
            # `failures` NO se toca: es la espera del rearranque (generaciones seguidas sin
            # manifiesto). Una tormenta de 504 de la tele no debe alargar el reintento del motor.
            log("fallo %s: %s -> HTTP %s (%s)" % (session.infohash[:8], what or "?", status,
                                                  self.client_address[0]))

    def do_GET(self):  # noqa: N802 - lo pide BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]
        if path in ("/healthz", "/"):
            with _lock:
                data = {
                    "sessions": [
                        {"infohash": s.infohash, "ready": s.ready(),
                         "segundos_sin_peticiones": round(time.time() - s.last_seen, 1)}
                        for s in _sessions.values()
                    ],
                    "max_sessions": MAX_SESSIONS,
                    "engine": ENGINE,
                }
            return self._send(200, json.dumps(data, indent=2).encode(), "application/json")

        if path.startswith("/diag/") and len(path) > 6:
            infohash = path[6:].strip("/").lower()
            with _lock:
                session = _sessions.get(infohash)
            if session is None:
                return self._send(404, b"no hay sesion\n", "text/plain")
            stats = {}
            if session.stat_url:
                try:
                    with urllib.request.urlopen(session.stat_url, timeout=5) as answer:
                        stats = json.load(answer).get("response") or {}
                except Exception as exc:  # noqa: BLE001 - el diagnostico no puede fallar
                    stats = {"error": str(exc)[:80]}
            data = {
                "infohash": infohash, "attempt": session.attempt, "failures": session.failures,
                "substituted": session.substituted, "manifests": session.manifests,
                "segments": session.segments, "client": session.client,
                "session_id": session.session_id, "engine": stats,
                "segments_on_disk": len([n for n in os.listdir(session.directory)
                                         if n.endswith(".ts")]) if os.path.isdir(session.directory) else 0,
            }
            return self._send(200, json.dumps(data, indent=2).encode(), "application/json")

        match = INFO.match(path)
        if match is None:
            return self._send(404, b"usa /<infohash>/index.m3u8\n", "text/plain")

        infohash = match.group("infohash").lower()
        alias = ALIASES.get(infohash)
        if alias:
            infohash = alias.lower()
        name = match.group("name")
        session = session_for(infohash)
        session.client = self.client_address[0]

        if name.endswith(".m3u8") and not session.ready():
            started = time.time()
            if not session.wait_ready():
                log("[%s] sin manifiesto tras %.1f s y %d intento(s)"
                    % (infohash[:8], time.time() - started, session.attempt))
                return self._send(504, b"el canal no arranco: sin datos del motor\n", "text/plain",
                                  session=session, what=name)
        if name.endswith(".m3u8"):
            session.touch()
            session.manifests += 1

        target = os.path.join(session.directory, os.path.basename(name))
        if not os.path.exists(target):
            if name.endswith(".m3u8"):
                return self._send(404, b"manifiesto no disponible\n", "text/plain",
                                  session=session, what=name)
            # Un segmento que ya no esta (el reproductor se quedo atras, o el manifiesto se reinicio)
            # NO aborta la reproduccion: se sirve el mas nuevo de la ventana, asi el reproductor
            # salta al directo en vez de morir con «failed to download segment». Medido el
            # 2026-10-18: la tele pedia seg00130/131/132 ya borrados y el reproductor cortaba.
            newest = _newest_segment(session.directory)
            if newest is None:
                return self._send(404, b"segmento no disponible\n", "text/plain",
                                  session=session, what=name)
            session.substituted += 1
            log("[%s] %s ya no estaba: sirvo %s" % (infohash[:8], name, os.path.basename(newest)))
            target = newest
        with open(target, "rb") as handle:
            body = handle.read()
        if name.endswith(".m3u8"):
            texto = body.decode("utf-8", "replace")
            texto = _delayed_manifest(texto, DELAY_SEGMENTS)
            body = _served_manifest(texto, SERVED_SEGMENTS).encode()
        if name.endswith(".m3u8"):
            import re as _re  # local: solo aquí se necesita
            found = _re.search(rb"MEDIA-SEQUENCE:(\d+)", body)
            if found:
                session.last_sequence = int(found.group(1))
        else:
            session.segments += 1
        content_type = ("application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t")
        cache = "no-cache" if name.endswith(".m3u8") else "max-age=60"
        return self._send(200, body, content_type, cache)


def self_test():
    """Las comprobaciones de las decisiones de arriba, sin motor y sin red.

    El motor de verdad no se puede provocar a voluntad, así que el ffmpeg se sustituye por un
    comando de mentira: uno que vive, uno que muere al instante y uno que falla una vez y luego
    escribe el manifiesto, que es el caso que se midió en producción.
    """
    class FakeSession:
        """Sesion sin ffmpeg ni motor, para el tope de sesiones de `session_for`."""

        def __init__(self, infohash, *, command=None, pump=None):
            self.infohash = infohash
            self.closed = False
            self.last_seen = time.time()
            self.stopped = 0

        def touch(self):
            self.last_seen = time.time()

        def stop(self):
            self.stopped += 1
            self.closed = True

    global ROOT, RETRY, ENGINE, MAX_SESSIONS
    checks = 0

    def check(name, condition, detail=""):
        nonlocal checks
        if not condition:
            raise AssertionError("%s%s" % (name, ": " + detail if detail else ""))
        checks += 1
        print("ok %d: %s" % (checks, name))

    with tempfile.TemporaryDirectory(prefix="acehls-selftest-") as root:
        ROOT = root
        RETRY = 0.3  # el self-test no espera 2 s por reintento
        # El motor de verdad no se toca: ENGINE apunta a un motor de mentira que anota lo que le
        # piden, y asi se comprueba el `?method=stop` con el que `release_engine` libera el canal.
        seen = []

        class StubEngine(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - lo pide BaseHTTPRequestHandler
                seen.append(self.path)
                port = self.server.server_address[1]
                if "format=json" in self.path:
                    body = json.dumps({"response": {
                        "playback_url": "http://127.0.0.1:%d/play" % port,
                        "command_url": "http://127.0.0.1:%d/ace/cmd/%s/TOKENSESION"
                                       % (port, self.server.infohash),
                        "playback_session_id": "sesion-fija",
                    }, "error": None}).encode()
                elif self.path.startswith("/play"):
                    body = b"x" * 35  # una respuesta finita del directo
                else:
                    body = b'{"response": "ok", "error": null}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass

        engine = ThreadingHTTPServer(("127.0.0.1", 0), StubEngine)
        engine.infohash = "9" * 40
        threading.Thread(target=engine.serve_forever, daemon=True).start()
        ENGINE = "http://127.0.0.1:%d" % engine.server_address[1]

        # 1. El comando de ffmpeg: lee `pipe:0` (el HTTP del motor lo hace el bombeo) y NO habla
        #    HTTP, asi que no lleva ni URL ni -reconnect: una respuesta finita del motor ya no le
        #    puede terminar la entrada.
        session = Session("a" * 40, command=["/bin/sh", "-c", "sleep 60"])
        command = session._command()
        check("-i presente", "-i" in command)
        check("lee pipe:0", command[command.index("-i") + 1] == "pipe:0")
        check("mpegts explicito", command[command.index("-f") + 1] == "mpegts")
        check("ffmpeg no habla HTTP", not any(part.startswith("-reconnect") for part in command)
              and not any("ace/getstream" in part for part in command))
        check("ventana de %s segmentos" % LIST_SIZE,
              command[command.index("-hls_list_size") + 1] == LIST_SIZE)
        check("se quedan %s segmentos de mas antes de borrar" % DELETE_THRESHOLD,
              command[command.index("-hls_delete_threshold") + 1] == DELETE_THRESHOLD)
        session.stop()

        # 1b. La espera: corta cuando la generacion sirvio, doble en cada fallo seguido, con tope.
        check("retry corto sin fallos", retry_delay(0) == RETRY)
        check("retry se dobla con fallos",
              [retry_delay(n) for n in (1, 2, 3)] == [RETRY * 2, RETRY * 4, RETRY * 8])
        check("retry con tope", retry_delay(40) == MAX_RETRY)

        # 1c. El retardo del manifiesto: se recortan los ultimos segmentos, la secuencia no se toca.
        manifiesto = "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n" + "".join(
            "#EXTINF:2.0,\nseg%05d.ts\n" % n for n in (7, 8, 9, 10, 11, 12))
        recortado = _delayed_manifest(manifiesto, 2)
        check("el retardo quita los ultimos segmentos",
              "seg00010.ts" in recortado and "seg00011.ts" not in recortado
              and "seg00012.ts" not in recortado)
        check("el retardo no toca la secuencia",
              "#EXT-X-MEDIA-SEQUENCE:7" in recortado)
        check("sin retardo el manifiesto va entero", _delayed_manifest(manifiesto, 0) == manifiesto)
        check("con pocos segmentos no se recorta", _delayed_manifest(manifiesto, 99) == manifiesto)
        ventana = _served_manifest(manifiesto, 2)
        check("la ventana servida se queda con el final",
              "seg00011.ts" in ventana and "seg00012.ts" in ventana and "seg00007.ts" not in ventana)
        check("la ventana servida reescribe la secuencia",
              "#EXT-X-MEDIA-SEQUENCE:11" in ventana, ventana.splitlines()[:5])

        # 2. INFO: solo <40 hex>/<fichero>.
        check("manifiesto reconocido", INFO.match("/%s/index.m3u8" % ("b" * 40)) is not None)
        check("segmento reconocido", INFO.match("/%s/seg00001.ts" % ("b" * 40)) is not None)
        check("basura rechazada", INFO.match("/../../etc/passwd") is None)

        # 3. Un ffmpeg que escribe el manifiesto acaba listo.
        directory = os.path.join(ROOT, "c" * 40)
        writes = ["/bin/sh", "-c",
                  "printf '#EXTM3U\\n' > %s/index.m3u8; sleep 60" % directory]
        session = Session("c" * 40, command=writes)
        check("wait_ready espera al manifiesto", session.wait_ready(timeout=3) is True)
        session.stop()

        # 4. Un ffmpeg que muere al instante se reintenta EN EL SITIO, y la sesión no se tira.
        #    El registro en `_sessions` es lo que hace `session_for`; aquí se pone a mano porque el
        #    comando de ffmpeg está sustituido y no hay motor al que pedir una sesión.
        directory = os.path.join(ROOT, "d" * 40)
        session = Session("d" * 40, command=["/bin/sh", "-c", "exit 1"])
        _sessions["d" * 40] = session
        time.sleep(RETRY * 3 + 1)
        check("la sesión sobrevive a la muerte de ffmpeg", _sessions.get("d" * 40) is session)
        check("el directorio sobrevive", os.path.isdir(directory))
        check("ffmpeg se reintenta en el sitio", session.attempt >= 2,
              "intentos=%d" % session.attempt)
        check("los fallos seguidos se cuentan", session.failures >= 1,
              "fallos=%d" % session.failures)
        check("con fallos la espera crece", retry_delay(session.failures) > RETRY)
        attempts = session.attempt
        session.stop()
        time.sleep(RETRY * 4 + 0.5)
        check("cerrada deja de reintentar", session.attempt == attempts)
        check("cerrada sale del registro", _sessions.get("d" * 40) is None)
        check("cerrada limpia el directorio", not os.path.exists(directory))
        check("cerrada responde que no (el 504)", session.wait_ready(timeout=1) is False)

        # 5. Si el motor acepta al segundo intento, la espera termina bien: el reintento no se
        #    sustituye por un 504, que era el fallo medido en producción.
        directory = os.path.join(ROOT, "e" * 40)
        tries = directory + ".tries"
        flaky = ["/bin/sh", "-c",
                 "echo x >> %s; if [ $(wc -l < %s) -gt 1 ]; then printf '#EXTM3U\\n' > "
                 "%s/index.m3u8; sleep 60; else exit 1; fi" % (tries, tries, directory)]
        session = Session("e" * 40, command=flaky)
        check("la espera sobrevive a un intento fallido",
              session.wait_ready(timeout=5, total=6) is True)
        session.stop()

        # 6. El bombeo: encadena las respuestas finitas del motor en una sola entrada. El motor de
        #    mentira devuelve un cuerpo corto y cierra; el comando de mentira cuenta los bytes que le
        #    llegan por stdin, que solo se escriben al cerrar (read() espera EOF).
        count_file = os.path.join(ROOT, "pump-bytes.txt")
        # El comando de mentira lee 64 bytes (dos respuestas del motor de mentira, de 35) y los deja
        # en un fichero: eso prueba que el bombeo encadena respuestas y que lo leido llega al proceso.
        child = [sys.executable, "-c",
                 "import sys;d=sys.stdin.buffer.read(64);open(%r,'wb').write(d)" % count_file]
        before = len(seen)
        session = Session("9" * 40, command=child, pump=True)
        for _ in range(40):
            if os.path.exists(count_file):
                break
            time.sleep(0.1)
        session.stop()
        time.sleep(0.5)
        check("el bombeo reabre el motor al acabar cada respuesta", len(seen) - before >= 2,
              "peticiones=%d" % (len(seen) - before))
        check("los trozos llegan al proceso",
              os.path.exists(count_file) and os.path.getsize(count_file) == 64,
              "bytes=%d" % (os.path.getsize(count_file) if os.path.exists(count_file) else -1))
        check("el getstream lleva nuestro pid (sesion estable)",
              any(("/ace/getstream?infohash=%s&format=json&pid=" % ("9" * 40)) in p for p in seen))
        check("el bombeo lee la URL de reproduccion que da el motor",
              any(p.startswith("/play") for p in seen))
        check("al soltar se usa la URL de la propia sesion",
              any(p == "/ace/cmd/%s/TOKENSESION?method=stop" % ("9" * 40) for p in seen))

        # 7. Una sesion SIN `command_url` no suelta nada en el motor: el token por defecto es la
        #    sesion sin `pid`, compartida por todos los clientes de la casa, y soltarla pararia el
        #    stream de otro lector. La sesion "d" no tiene bombeo, asi que no tiene `command_url`.
        stops = [path for path in seen if "method=stop" in path]
        check("sin command_url no se suelta la sesion del motor",
              not any(("d" * 40) in path for path in stops), str(stops[:3]))

        # 8. Cerrar una sesion NO borra el directorio de otra del mismo canal: entre el pop del
        #    registro y el borrado hay una ventana en la que la peticion siguiente abre la nueva, y
        #    el borrado le quitaba el manifiesto y los segmentos debajo (la tele se quedaba sin
        #    canal). Se hace con el lock tomado para que el hilo de la vieja no se cuele en medio.
        hash8 = "f" * 40
        marca = os.path.join(ROOT, hash8, "seg00099.ts")
        with _lock:
            vieja = Session(hash8, command=["/bin/sh", "-c", "true"])
            vieja.closed = True  # como la deja el reaper antes de que su hilo termine
            _sessions[hash8] = vieja
            nueva = Session(hash8, command=["/bin/sh", "-c", "sleep 60"])
            _sessions[hash8] = nueva
            os.makedirs(os.path.join(ROOT, hash8), exist_ok=True)
            with open(marca, "wb"):
                pass
        time.sleep(1.0)  # deja que el hilo de la vieja llegue a su final
        check("la sesion vieja no borra el directorio de la nueva", os.path.exists(marca))
        check("la sesion vieja no saca a la nueva del registro", _sessions.get(hash8) is nueva)
        _sessions.pop(hash8, None)
        nueva.stop()

        # 9. Aviso de tmpfs: la victima es la sesion que mas ocupa, y sin sesiones no hay victima.
        grande = os.path.join(ROOT, "grande")
        pequena = os.path.join(ROOT, "pequena")
        os.makedirs(grande)
        os.makedirs(pequena)
        for n in range(3):
            with open(os.path.join(grande, "seg%05d.ts" % n), "wb") as handle:
                handle.write(b"x" * 1000)
        with open(os.path.join(pequena, "seg00000.ts"), "wb") as handle:
            handle.write(b"x" * 10)
        falsas = [type("S", (), {"infohash": "1" * 40, "directory": pequena})(),
                  type("S", (), {"infohash": "2" * 40, "directory": grande})()]
        check("la victima del tmpfs es la sesion que mas ocupa", _biggest(falsas).directory == grande)
        check("sin sesiones no hay victima", _biggest([]) is None)
        check("solo cuentan los segmentos y un directorio ausente son 0 bytes",
              _dir_bytes(grande) == 3000
              and _dir_bytes(os.path.join(ROOT, "no-existe")) == 0)

        # 10. El tope de sesiones: se cierra la que lleva mas tiempo sin peticiones y la nueva entra
        #     en su hueco, con el cierre fuera del lock (un stop con el lock tomado paraba a todos
        #     los handlers). Se sustituye `Session` por una falsa para no arrancar ffmpeg.
        real_session = Session
        try:
            globals()["Session"] = FakeSession
            with _lock:
                _sessions.clear()
            viejas = [FakeSession("a" * 40), FakeSession("b" * 40)]
            viejas[0].last_seen -= 100  # la que mas tiempo lleva sin peticiones
            with _lock:
                for fake in viejas:
                    _sessions[fake.infohash] = fake
            before = MAX_SESSIONS
            MAX_SESSIONS = 2
            entrada = session_for("c" * 40)
            MAX_SESSIONS = before
        finally:
            globals()["Session"] = real_session
            with _lock:
                _sessions.clear()
        check("el tope de sesiones cierra la mas vieja",
              viejas[0].stopped == 1 and viejas[0].infohash not in _sessions)
        check("el tope de sesiones deja entrar a la nueva", entrada.infohash == "c" * 40)
        engine.shutdown()

    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="El puente HLS de AceList (ver el docstring).")
    parser.add_argument("--self-test", action="store_true",
                        help="comprueba las decisiones sin motor ni red, y sale")
    args = parser.parse_args(argv)
    if args.self_test:
        print("%d comprobaciones" % self_test())
        return 0
    os.makedirs(ROOT, exist_ok=True)
    # Al arrancar no hay ninguna sesión: lo que haya en ROOT es de un proceso muerto y solo ocupa
    # tmpfs (un reinicio del puente dejaba los segmentos del anterior ahí, y con dos reinicios el
    # tmpfs se llenaba sin que nadie estuviera viendo nada).
    for name in os.listdir(ROOT):
        shutil.rmtree(os.path.join(ROOT, name), ignore_errors=True)
    threading.Thread(target=reaper, daemon=True).start()
    threading.Thread(target=summarize, daemon=True).start()
    log("escuchando en :%d | motor %s | raíz %s | hasta %d sesiones" % (PORT, ENGINE, ROOT, MAX_SESSIONS))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
