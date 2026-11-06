# acestream-suite

[English](README.md) · **Español**

<p align="center"><img src="assets/logo-3d.png" alt="acestream-suite" width="560"></p>

Un front end web autoalojado para un motor **AceStream**. Mantiene un catálogo de enlaces
`acestream://` — canales con su Content ID, una comprobación de disponibilidad contra el motor, una
captura de cada uno, categorías, idiomas y países — y añade cuatro cosas alrededor de ese catálogo:

- **una capa de guía (EPG)**: fuentes XMLTV, un mapeo canal↔guía, una ventana de programación
  almacenada, un documento XMLTV en `/epg.xml` y una lista M3U que lleva la guía;
- **una página de búsqueda** sobre el índice del propio motor (`acesearch`), para encontrar e
  importar los canales que el índice conoce y el catálogo no;
- **un puente HLS** que convierte un stream del motor en un manifiesto que varios reproductores de la
  casa pueden ver a la vez;
- **scripts** que publican el catálogo como listas para los reproductores que quieren una URL en vez
  de una página web (VLC, Kodi, Dispatcharr/Jellyfin Live TV, una tele).

`acestream-suite` es este producto. **AceStream** es el motor con el que reproduce: el motor es
software de terceros y conserva su propio nombre.

> **Sin autenticación.** La app está pensada para una red privada. Cualquiera que llegue al puerto
> puede navegar por el catálogo y arrancar streams. No la publiques a internet.

## Inicio rápido (Docker)

Requisitos: Docker con Compose v2, y un motor AceStream alcanzable por HTTP desde el contenedor
(esta suite no arranca ninguno).

```bash
git clone https://github.com/yerbajo/acestream-suite.git
cd acestream-suite
cp .env.example .env
$EDITOR .env                      # ACESTREAM_SUITE_ENGINE_URL, ACESTREAM_SUITE_ALLOWED_HOSTS
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
curl -fsS http://localhost:8080/health
```

Después abre **`http://<host>:8080`** — `ACESTREAM_SUITE_PORT` en `.env` es el puerto del host (`8080`
si no lo cambias; la app siempre escucha en 8000 dentro del contenedor). El puente HLS publica **8084**.

Dos detalles que se echan de menos si se saltan:

- **`data/` tiene que existir y ser escribible por el uid 1000.** Docker crea un origen de bind que
  falta como un directorio vacío propiedad de root, y la app corre como `"1000:1000"`, así que no
  podría escribir su base de datos.
- **Los valores del despliegue salen de `.env`, no del compose.** `docker-compose.yml` interpola
  todo lo que cambia de un despliegue a otro (`${ACESTREAM_SUITE_ENGINE_URL:-…}` y compañía), así que
  basta `cp .env.example .env` y editarlo: el compose no hay que tocarlo nunca. Lo que dejes fuera de
  `.env` cae al valor seguro que trae escrito el propio compose. La URL del motor, sus timeouts, la
  URL de búsqueda y la concurrencia de comprobaciones son además *ajustes guardados* en la página
  **Ajustes** de la app, y un valor guardado manda sobre el entorno — así que un `.env` equivocado se
  corrige desde la interfaz sin reconstruir nada.

## La imagen se construye en local, desde este repositorio

`docker compose up -d --build` **construye** la imagen; no se descarga nada salvo la base
`python:3.12-slim` y los paquetes de la distribución. El código de la aplicación está aquí: `app/`
(la aplicación FastAPI), `migrations/` (Alembic) y `pyproject.toml`, que se copian a la imagen y se
instalan en modo editable, porque la app lee sus plantillas, sus ficheros estáticos y sus migraciones
relativas al árbol de código.

`ffmpeg` se instala en la imagen porque las capturas y la comprobación de disponibilidad leen el
stream en directo a través de él.

Actualizar:

```bash
git pull
docker compose build acestream-suite && docker compose up -d acestream-suite
```

Nombrar el servicio importa: el mismo compose también tiene el puente `acestream-suite-hls`, y un
`docker compose up -d` a secas lo recrearía también.

## Configuración

Todas las variables las lee la app de su entorno. Los nombres primarios son `ACESTREAM_SUITE_*`; los
nombres viejos `ACELIST_*` se siguen leyendo como respaldo durante una versión, así que un despliegue
que aún no se ha renombrado sigue funcionando (si están las dos, gana la nueva).

| Variable | Por defecto | Qué hace |
|---|---|---|
| `ACESTREAM_SUITE_ENGINE_URL` | `http://127.0.0.1:6878` | URL base de la API HTTP del motor. También es un ajuste guardado. |
| `ACESTREAM_SUITE_ENGINE_TIMEOUT` | `15` | Segundos de espera al motor antes de dar una llamada por fallida. |
| `ACESTREAM_SUITE_SEARCH_URL` | vacío = la URL del motor | URL base de `acesearch`, el índice que lee la página de búsqueda (`/search.m3u`). También es un ajuste guardado. |
| `ACESTREAM_SUITE_ALLOWED_HOSTS` | vacío | Valores `host` / `host:port` separados por comas, aceptados en la cabecera `Host` y en el `Origin` de una escritura. Sin la dirección desde la que navegas, toda petición es un `400` y toda escritura un `403`. |
| `ACESTREAM_SUITE_PLAYBACK_MODE` | `vlc` | `vlc` arranca el stream y abre un reproductor local (el comportamiento de escritorio). `url` arranca el stream y le pasa el enlace a la página — el modo que necesita un contenedor, que no tiene reproductor. |
| `ACESTREAM_SUITE_STREAM_PARAM` | `id` | Parámetro de consulta que arranca un stream en el motor: `id` para la build de Windows 3.1.x, `infohash` para la de Linux 3.2.x (que responde `failed to load content` a `id`). |
| `ACESTREAM_SUITE_CHECK_CONCURRENCY` | `2` | Comprobaciones de disponibilidad a la vez. Cada una es una sesión real en el motor, así que esto es también la carga sobre el motor. |
| `ACESTREAM_SUITE_SCREENSHOT_TIMEOUT` | `30` | Segundos permitidos para la captura con ffmpeg. |
| `ACESTREAM_SUITE_FFMPEG_PATH` | vacío = `ffmpeg` en el `PATH` | El binario que se usa para las capturas. |
| `ACESTREAM_SUITE_VLC_PATH` | vacío = carpetas habituales, luego `PATH` | Solo se lee en el modo de reproducción `vlc`. |
| `ACESTREAM_SUITE_DATA_DIR` | `/data` en la imagen, el directorio de datos del sistema en otro sitio | Dónde viven `acestream-suite.db` y `screenshots/`. |
| `ACESTREAM_SUITE_PORT` | `8000` | Puerto que la app ocupa **dentro** del contenedor. El puerto publicado lo decide `ports:` en el fichero compose. |
| `ACESTREAM_SUITE_EPG_SOURCES` | `[]` | Fuentes XMLTV, como lista JSON de URLs. Vacío es un estado soportado: el catálogo funciona sin guía. |
| `ACESTREAM_SUITE_EPG_WINDOW_HOURS` | `72` | Cuánto por delante de *ahora* se guarda la guía; los programas más viejos se podan en cada refresco. |
| `ACESTREAM_SUITE_EPG_TIMEOUT_SECONDS` | `120` | Timeout por fuente del refresco. Una fuente que nunca responde cuesta una fuente, no la pasada. |
| `ACESTREAM_SUITE_EPG_MAX_MB` | `64` | Una fuente mayor que esto se rechaza en vez de llenar la memoria del contenedor. |
| `ACESTREAM_SUITE_EPG_GRACE_SECONDS` | `300` | Segundos extra que se guarda un programa que acaba de terminar, para reproductores con el reloj atrasado. |
| `ACESTREAM_SUITE_EPG_ACTOR` | vacío = el usuario del sistema | Nombre que queda registrado como autor de una decisión manual canal↔guía. |

**Cuáles de estas puede poner `.env` lo decide el compose.** Interpola `ENGINE_URL`, `ALLOWED_HOSTS`,
`PLAYBACK_MODE`, `STREAM_PARAM`, `CHECK_CONCURRENCY`, `PORT`, `EPG_SOURCES`, `EPG_WINDOW_HOURS`,
`EPG_REFRESH_HOURS` y el `HLS_ALIASES` del puente — exactamente los nombres que trae `.env.example`.
El resto los lee la app pero el compose no los pasa: o son ajustes guardados en **Ajustes** (timeout
del motor, URL de búsqueda, timeout de comprobación, peers mínimos, timeout de captura, ruta de
ffmpeg y de VLC), o valores que añades tú a `environment:` en el compose (directorio de datos, los
timeouts y el tope de tamaño de la guía, el nombre del actor).

Dos notas:

- Una cosa que el fichero compose declara y **ningún código lee** es
  `ACESTREAM_SUITE_EPG_REFRESH_HOURS`. Está ahí para que el intervalo del refresco se decida en el
  despliegue; el horario en sí vive en `scripts/acestream-suite-guide-refresh.cron`, que hay que
  mantener en paso con ella a mano.
- El fichero de base de datos es `acestream-suite.db`, dentro del directorio de datos al que se apunta
  la app.

## Datos y copias de seguridad

Todo el estado de la aplicación es un directorio, montado en `/data` y atado a `./data` en el
fichero compose:

```
data/
  acestream-suite.db   SQLite: catálogo, historial de comprobaciones, tablas de la guía, ajustes
  screenshots/    una captura por canal
```

Para respaldarlo, copia el directorio mientras la app no está escribiendo en él:

```bash
docker compose stop acestream-suite
tar czf "acestream-suite-data-$(date +%F).tar.gz" data
docker compose start acestream-suite
```

Una copia tomada de un contenedor en marcha puede pillar el fichero SQLite a medio escribir. El script
`acestream-suite-prune` (más abajo) toma exactamente este tipo de copia antes de borrar nada, junto
con una exportación del catálogo completo que se puede reimportar.

Para restaurar: para la app, sustituye `data/`, arráncala. El entrypoint aplica las migraciones de la
base de datos al arrancar, así que una copia de una versión anterior se actualiza en vez de
rechazarse.

## Cómo se organiza el código

| Ruta | Qué es |
|---|---|
| `app/` | La aplicación web: rutas FastAPI, plantillas, ficheros estáticos, el cliente del motor, la guía y la búsqueda. |
| `migrations/` + `alembic.ini` | El esquema de la base de datos, que `entrypoint.sh` aplica al arrancar el contenedor. |
| `tests/` | La suite de pruebas de la aplicación (pytest, ~640 pruebas, sin red): `pytest -q` desde la raíz del repositorio. |
| `scripts/` | Los trabajos opcionales del host: las listas, el importador del partido, la poda, sus `.cron` y `crons-install.sh`. |
| `assets/` | El logotipo y el favicon que la imagen copia al directorio estático de la app. |

La procedencia, y lo que significa para ti, está en `LICENSE`.

## El puente HLS

Los motores de la línea 3.2.x no sirven un manifiesto HLS, y varios clientes (teles, Dispatcharr,
Jellyfin Live TV) quieren uno. `acestream-suite-hls` — el segundo servicio del mismo fichero compose,
desde la misma imagen — remuxea el MPEG-TS del motor, sin recodificar, en segmentos de 2 segundos y
sirve:

```
http://<host>:8084/<infohash>/index.m3u8
```

Un `ffmpeg` por canal, como mucho cuatro sesiones, una sesión se cierra 45 segundos después de la
última petición, la ventana de segmentos en un tmpfs de 2 GB, y la sesión del motor se libera cuando
el puente suelta un canal. Cuando el espacio libre del tmpfs baja de `HLS_MIN_FREE_MB` cierra la
sesión que más bytes ocupa: un tmpfs lleno rompe todas las sesiones a la vez, no solo una.
`python3 hls-bridge.py --self-test` comprueba esas decisiones sin red, con un motor y un ffmpeg
falsos.

El puente es una solución a una limitación medida del motor, no una preferencia: el motor sirve un
solo lector por contenido, así que apuntar varios reproductores directamente a él hace que compitan
por el mismo canal.

## Los scripts de la casa (opcionales)

Nada de `scripts/` hace falta para ejecutar la app. Son la capa de publicación que usa el autor,
esperan las rutas que hay al principio de cada fichero, y por defecto apuntan a los hosts de ese
despliegue (`http://127.0.0.1:8083` para la app, `/srv/www/varios` para las listas). Cada ruta y cada
URL es una opción y, para las que un host mueve, una variable de entorno — lee la cabecera del script
que vayas a instalar.

| Script | Qué escribe |
|---|---|
| `acestream-suite-playlist.py` | Seis ficheros M3U en el directorio web: `acestream-suite.m3u` (todo), `deportes.m3u`, `espana.m3u`, `hls.m3u` (con una carpeta `Campeones`), `direct.m3u` (el MPEG-TS del propio motor) y `madrid.m3u` (el partido del Real Madrid en directo, vacío si no lo hay). Solo canales que la app ha verificado como vivos, una entrada por nombre visible, todas las URLs a través del puente HLS menos `direct.m3u`. Escribe solo cuando el contenido cambia. |
| `acestream-suite-guide-playlist.py` | `acestream-suite-guide.m3u`: el mismo catálogo con `url-tvg` en la cabecera y el id de canal de la guía como `tvg-id`, en los canales que lo tienen. |
| `acestream-suite-partido.py` | Importa al catálogo los canales que emiten un partido del Real Madrid, en directo (categoría `Real Madrid`); escribe el conjunto en directo en `/var/lib/acestream-suite-partido/live.json` y su propio estado en `state.json`, que lee el script de las listas. |
| `acestream-suite-prune.py` | Una limpieza de una sola vez: espera a que todos los canales estén comprobados, respalda en `/backup/…/<stamp>/` (listas, exportación reimportable del catálogo, copia de la base de datos, lista de lo que va a borrar) y después borra los canales cuya última comprobación no fue `alive`, a través de la propia API de la app. Se marca como hecho; `--undo-guard` le deja volver a correr. |
| `crons-install.sh` | El crontab de root en el host de destino, generado a partir de `scripts/*.cron`. `--dry-run` imprime, `--diff` compara con el crontab vivo, `--install` respalda y escribe. Su array `JOBS` lleva también trabajos de otras pilas — léelo antes de instalar. |

Todos aceptan `--dry-run`; los dos scripts de listas aceptan además `--self-test`, que ejecuta sus
comprobaciones sin red y sin ficheros. Los trabajos de listas necesitan un servidor web sirviendo ese
directorio; `acestream-suite-guide-refresh.cron` es el refresco de la guía, y lo ejecuta dentro del
contenedor (`docker exec acestream-suite python3 -m app.cli epg-refresh`).

## Documentación

- `README.es.md` — este documento en español.
- `LICENSE` — MIT para el trabajo propio y la procedencia del código de la aplicación.
- En el propio código: cada módulo lleva su docstring con lo que decide y por qué; `python -m app.cli
  --help` y el `--help` de cada script documentan sus opciones.

## Licencia y avisos de terceros

El trabajo propio de este repositorio — los ficheros de despliegue, los scripts, la documentación, el
logotipo y el código de la aplicación — tiene licencia MIT; ver `LICENSE`. Ese mismo fichero deja
constancia de la procedencia del código (nació del proyecto **AceList**, que no publica licencia) y del
origen de la marca de AceStream dentro del logotipo. Léelo antes de redistribuir una imagen construida
aquí. La imagen instala además software de terceros: `python:3.12-slim`, los paquetes de Debian
(`ffmpeg`, `curl`) y las dependencias de `pyproject.toml`, cada uno con su licencia; `htmx`
(BSD-2-Clause) y los trazos de Lucide (ISC) están vendorizados en `app/web/`.

## Cómo se despliega aquí

Un despliegue doméstico tiene esta forma: un host en la LAN, un contenedor
al lado de un motor tunelizado por WireGuard en otra máquina, un proxy inverso sirviendo un directorio
de listas, y trabajos de cron en el host. Léelos como un ejemplo trabajado con las mediciones que lo
respaldan, no como la única forma de ejecutar esto — el fichero compose es la interfaz, y las
variables de entorno de arriba son las palancas.
