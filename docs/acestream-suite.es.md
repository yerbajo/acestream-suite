# acestream-suite — un catálogo de AceStream en la red de casa

acestream-suite es un catálogo de enlaces AceStream: canales con su Content ID, una comprobación de
disponibilidad contra el motor, una captura de imagen de cada canal, categorías, búsqueda y un botón
de reproducción. Es el frontal que el servicio de búsqueda de AceStream nunca tuvo.

> **El producto se llamaba AceList**: ahora es `acestream-suite`, y el renombrado está registrado en
> `odd/tasks/acestream-suite-rename.md`. Rige la compatibilidad de una versión — las viejas
> `/varios/acelist.m3u` y `/varios/acelist-epg.m3u` se mantienen como copia de las listas nuevas, las
> variables `ACELIST_*` se siguen leyendo cuando falta la nueva, y `/usr/local/bin/acelist-*` son
> envoltorios de los binarios nuevos. El motor AceStream conserva su nombre y los datos no se han movido.

| | |
|---|---|
| URL | `http://192.168.1.245:8083` (solo LAN, sin autenticación) |
| Host | `dev.albertucho.home` — 192.168.1.245, RHEL 8.7 |
| Contenedor | `acestream-suite`, a partir de la imagen construida aquí, `acestream-suite:1.3.0` |
| Origen | `https://github.com/fabiomb/AceList` etiqueta `v1.3.0`, más `acestream-suite/acestream-suite-lan.patch` y `acestream-suite/acestream-suite-guide.patch` |
| Motor | el motor AceStream de **192.168.1.28**, dentro de su túnel WireGuard |
| Guía (EPG) | este servicio la lleva — `docs/acestream-suite-guide.md` |
| Plan y evidencias | `odd/tasks/acelist.md` |
| Ficheros en el host | `/opt/acestream-suite/` (compose, Dockerfile, entrypoint, los dos parches, `data/`) |

## Por qué el motor está en otro host

El motor con el que habla este despliegue **no** es el de esta máquina. En 192.168.1.245 ya corre
`ghcr.io/martinbjeldbak/acestream-http-proxy` (motor 3.2.11) en 6878/8621, y el P2P de ese motor sale
con la IP de casa, sin túnel. La casa decidió el 2026-10-06 que el P2P de AceStream va por el túnel
WireGuard, y por eso el servicio de búsqueda vive en la otra VM (ver `odd/tasks/acestream-search.md`
en el repositorio suite-aar).

Así que acestream-suite apunta a `http://192.168.1.28:8081`, el nginx del contenedor `acestream` de ese
stack. Ese nginx sirve toda la API HTTP del motor en la LAN y reescribe las URLs
`http://localhost:6878/...` que el motor se atribuye a sí mismo hacia la dirección por la que se le
preguntó. Por eso basta una sola URL base para la API y para el enlace de reproducción, y por eso no
hubo que cambiar nada en .28 para este despliegue.

## Cómo se construye

El proyecto no publica ninguna imagen, así que la imagen se construye aquí, a partir del tarball de
la release fijado:

- Tarball de la etiqueta `v1.3.0`, verificado contra
  `487bee2f792d2bd30029a32bbd33b9f023cfc0d033c05fdb74bb2e169c72d879`.
- Encima se aplican tres parches locales, en este orden: `acestream-suite-lan.patch` (tres ideas:
  lista de hosts permitidos para la LAN, un modo de reproducción sin reproductor local, y el
  dialecto `infohash` del motor), `acestream-suite-guide.patch` (la guía, fusionada desde el
  despliegue duplicado el 2026-10-14) y `acestream-suite-search.patch` (la página de búsqueda
  contra `acesearch`, y su ajuste, 2026-11-05). El parche de LAN existe porque el repositorio no
  tiene LICENSE — ejecutarlo sí, redistribuir un fork o una imagen no — y porque el propio
  `docs/engine-api.md` del autor dice que la aplicación debe quedarse en loopback: un pull request
  para lo contrario sería discutir su propio diseño.
- La imagen instala `ffmpeg`: las capturas y la verificación de disponibilidad leen el directo a
  través de él.
- `python:3.12-slim`, que es una de las dos versiones que prueba el CI del autor.
  del autor.

Los ficheros del despliegue (`acestream-suite/Dockerfile`, `docker-compose.yml`, `entrypoint.sh`,
`hls-bridge.py` y los tres parches) están versionados en este repositorio; entraron por primera vez en
el commit `0ebf092`, que registró el estado que el host ya estaba ejecutando, de modo que la fusión de
la guía que vino después tuviera ficheros que cambiar y revertir.

## La guía (EPG)

Este servicio lleva ahora también la guía: fuentes XMLTV, una correspondencia entre canal y canal de
guía, una ventana de programas de 72 h, una celda de ahora/después y una parrilla en la interfaz,
más `/epg.xml` y una lista que lleva la guía. Se construyó y se probó en un duplicado llamado
`AceListEPG` para que este servicio no fuera el banco de pruebas, y se fusionó aquí el 2026-10-14.
El procedimiento del operador — fuentes, refresco, correspondencia, salidas y diagnóstico — es
**`docs/acestream-suite-guide.md`** (+ `.es.md`); el plan y sus evidencias están en
`odd/tasks/acelist-epg.md`.

## Buscar en el índice (acesearch)

El motor de .28 lo sirve el contenedor `acestream`, que además publica un índice de los canales que
conoce: `GET /search.m3u?query=…&json=1` contesta nombre, infohash, bitrate, disponibilidad y
etiquetas de cada uno. *Buscar*, en la barra superior, pregunta a ese índice y muestra los canales que
conoce y el catálogo no.

- **Busca por canal, no por evento.** Medido el 2026-11-04: el índice no sabe de partidos ni de
  horarios; casa el nombre del canal, así que encontrar quién emite un evento es buscar el nombre del
  canal. (`acestream-suite-partido` lo hace solo; ver *Los canales del partido, en directo*.)
- **No se añade nada hasta marcarlo.** Cada fila lleva el hash, el bitrate y la disponibilidad que
  publica el índice — esa disponibilidad es una foto histórica, no una comprobación en vivo.
  *Reproducir* abre una sesión real contra el motor, que es lo único que dice si el enlace vive.
- **Añadir los marcados** pasa por el mismo importador que *Importar*: categoría o una nueva, idioma,
  país, y la misma cola de verificación en segundo plano que cualquier alta.
- **Los pares tienen dos sabores.** La columna *Pares* es lo que guardó *Comprobar* la última vez para
  un hash que ya está en el catálogo, con su fecha (`hace 23 d` cuando nadie comprueba desde hace días);
  *Medir* abre una sesión real en el motor para esa fila (2-3 s, una sesión) y contesta los de ahora. El
  índice no publica pares en absoluto, así que ninguna de las dos cifras es una promesa: una es una
  foto, la otra cuesta una sesión.
- **Dónde busca** está en *Ajustes* → *Búsqueda (acesearch)*: `search_url`, vacío por defecto, que
  significa la URL del motor, porque ahí es donde se sirve el buscador. *Probar búsqueda* le pregunta
  antes de guardar nada, con una consulta real (`dazn`) y cuántos resultados contestó. Es un botón
  aparte del *Probar conexión* del motor a propósito: la búsqueda es otro servicio, y un motor que
  contesta `get_version` no dice nada de si el índice está arriba.
- **Un resultado cuyo nombre empieza por `#`** (`#VAMOS FHD --> NEW ERA`) sí llega al catálogo: el
  formulario manda el enlace delante, porque el importador lee como comentario M3U cualquier línea que
  empiece por `#`. Medido el 2026-11-05: con el título delante, marcar ese canal no añadía nada y la
  página contestaba `0 canal(es) añadidos, 0 duplicados y 0 inválidos`.

## Procedimiento

### Primer despliegue (ya hecho el 2026-10-09, se conserva para reproducirlo)

Acción, desde el portátil:

```bash
cd ~/webserver/acestream-suite
scp Dockerfile entrypoint.sh docker-compose.yml \
    acestream-suite-lan.patch acestream-suite-guide.patch root@192.168.1.245:/opt/acestream-suite/
```

Acción, en el host:

```bash
mkdir -p /opt/acelist/data && chown 1000:1000 /opt/acelist/data
cd /opt/acestream-suite && docker compose build && docker compose up -d
```

Registro:

```
#13 writing image sha256:2c93dd862e9d5afd2853a983042133696dd36c518990401ce8844f94b2f36052 done
#13 naming to docker.io/library/acelist:1.3.0 done
Container acelist  Started
acelist	Up 10 seconds (health: starting)
{"status":"ok","app":"AceList","version":"1.3.0"}
```

Ese registro es del 2026-10-09, anterior al renombrado, así que la imagen, el contenedor y la respuesta
de salud llevan los nombres de ese día; un despliegue de hoy nombra la imagen y el contenedor
`acestream-suite`.

El `chown` importa: Docker crea un origen de montaje que no existe como directorio vacío propiedad de
root, y el contenedor corre como `1000:1000` (la cuenta `ingenieria` del host), así que con otro
propietario no puede escribir.

### Comprobaciones que merecen la pena

Acción:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: 192.168.1.245:8083" http://127.0.0.1:8083/
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: evil.example.com" http://127.0.0.1:8083/
curl -s -o /dev/null -w "%{http_code}\n" -H "Origin: http://evil.example.com" \
  --data-urlencode "links=acestream://6d325cda6ed591647ef5e91edd043147bce8be30" \
  http://127.0.0.1:8083/import
```

Registro:

```
200
400
403
```

El primero es la aplicación atendiendo a la dirección de la LAN; el segundo y el tercero demuestran
que la comprobación de Host y la de CSRF siguen haciendo su trabajo, que es exactamente por lo que el
parche añade direcciones reales a su lista de permitidos en vez de desactivarlas.

### Uso diario

- **Añadir canales**: *Importar* acepta un enlace `acestream://<infohash>` por línea, con categoría,
  idioma y país opcionales. Los nombres se pueden editar después.
- **Comprobar disponibilidad**: *Comprobar* abre una sesión real contra el motor y anota pares y
  estado. Es una foto, no una promesa: un canal puede estar `alive` y no entregar nada.
- **Captura**: toma un fotograma del directo con ffmpeg (unos 30 s, y necesita que el canal emita).
- **Reproducir**: el botón abre una sesión y devuelve el enlace del motor. Se pega en VLC o mpv
  (`Ctrl+N` en VLC). Es un directo MPEG-TS, así que un navegador solo ofrecerá descargarlo.
- **Ajustes del motor**: la URL del motor, los tiempos de espera, la concurrencia, las rutas de
  ffmpeg y VLC y la URL de la búsqueda están en *Ajustes*; un valor guardado gana al entorno, así que
  la página de ajustes puede sobrescribir el compose.

## Límites y peajes conocidos

- **Sin autenticación.** Cualquiera en la LAN puede navegar el catálogo y abrir sesiones. Los directos
  solo salen de casa por el túnel de .28, pero consumen ancho de banda P2P. El 8083 no se reenvía
  nunca en el router.
- **Sin reproducción en el navegador.** Se intentó con `acexy` y se retiró el mismo día: el punto de
  entrada HLS del motor (`/ace/manifest.m3u8`) redirige a
  `/ace/m/<infohash>/<token>.m3u8`, y esa URL no llega a enviar ni una cabecera — presupuestos de
  30 s y 45 s, tres canales distintos, y también probado desde dentro del propio espacio de red del
  motor. El único modo del `acexy` que un navegador puede reproducir es justo esa llamada. Meter
  imagen en el navegador exige otra compilación del motor o un puente que reempaquete el MPEG-TS: es
  una tarea aparte.
- **Un reproductor por sesión.** El motor responde `500` al segundo consumidor del mismo token de
  reproducción. Volver a pulsar *Reproducir* emite una sesión nueva, y esa es la forma de reabrir el
  canal.
- **Sin copia de seguridad.** `/opt/acelist/data` guarda la única copia del catálogo (`acelist.db`) y
  de las capturas. Ningún cron lo toca todavía.
- **El motor no se comporta como el del autor.** acestream-suite está hecho para el motor 3.1.74 de
  Windows; aquí corre la compilación Linux 3.2.11, que solo emite con `infohash=` y contesta `failed
  to load content` a `id=`. Esa diferencia es la razón de que exista
  `ACESTREAM_SUITE_STREAM_PARAM=infohash`.
- **Un canal atascado parece sano en el catálogo.** Con pares pero sin datos, el motor se queda en
  `prebuf` y el enlace de reproducción no entrega nada. El canal CNN Turk que se usó en las pruebas
  era justo eso.
- **Actualizar es manual y a propósito**: subir `ACESTREAM_SUITE_VERSION` y
  `ACESTREAM_SUITE_SHA256`, releer **los dos** parches contra el tarball nuevo, volver a pasar las
  pruebas y reconstruir (`docker compose build acestream-suite && docker compose up -d
  acestream-suite`, que deja el puente `acestream-suite-hls` en paz a propósito).

## Diagnóstico

| Síntoma | Causa | Qué hacer |
|---|---|---|
| La página responde `400 Invalid host header` | La dirección que has escrito no está en `ACESTREAM_SUITE_ALLOWED_HOSTS` | Añadirla en `docker-compose.yml` y recrear el contenedor |
| Guardar un formulario responde `403 cross-site request rejected` | Es la misma lista, usada por la comprobación de origen (CSRF) | Lo mismo que arriba; no desactivar la comprobación |
| `El engine de Ace Stream no responde` | .28 caído, Gluetun parado, o `ACESTREAM_SUITE_ENGINE_URL` mal | `curl http://192.168.1.28:8081/webui/api/service?method=get_version` desde .245; debe contestar `3.2.11` |
| `El engine no pudo cargar este Content ID` | El motor rechazó la petición de sesión — normalmente el nombre del parámetro o un enlace muerto | Revisar `ACESTREAM_SUITE_STREAM_PARAM` (aquí debe ser `infohash`) y buscar ese infohash en `/search` |
| El enlace de reproducción nunca reproduce | El canal tiene pares pero no datos (`prebuf`) | Probar otro canal; la disponibilidad del catálogo es una foto |
| El enlace reproduce una vez y luego falla | El motor permite un solo reproductor por sesión | Pulsar *Reproducir* otra vez para una sesión nueva |
| La captura agota el tiempo | ffmpeg espera a un directo que no entrega | Igual que arriba; la captura sigue el mismo directo |

## La lista de reproducción permanente, y Dispatcharr

    http://192.168.1.245/varios/acestream-suite.m3u      # el enlace, no cambia nunca
    http://192.168.1.245/varios/acelist.m3u              # el mismo contenido, como copia
    /usr/local/bin/acestream-suite-playlist              # el script que lo mantiene al día
    /etc/cron.d/acestream-suite-playlist                 # cada 15 minutos, a las :05 :20 :35 :50
    /var/log/acestream-suite-playlist.log                # qué hizo cada ejecución

`scripts/acestream-suite-playlist.py` pide a acestream-suite su propio export (`GET
/export?status=alive`), construye **cinco** listas y las escribe en `/srv/www/varios/`, que el Caddy
de este host ya sirve por **HTTP plano**: por eso el enlace no necesita ningún cambio en Caddy ni
certificado, y por eso lo puede cargar un móvil, un TV box o un contenedor. Un sexto fichero,
`acestream-suite-guide.m3u`, lo escribe `scripts/acestream-suite-guide-playlist.py`; es el mismo
catálogo más la guía. Los dos scripts solo escriben cuando el contenido cambia, así que una lista
igual conserva su mtime; los dos toman su propio `flock`; y los dos traen `--self-test` (ejecutado
en el despliegue), `--dry-run` y `--output`. Mientras dure esta versión se escriben también los dos
nombres viejos — `/srv/www/varios/acelist.m3u` y `acelist-epg.m3u` son copia de
`acestream-suite.m3u` y `acestream-suite-guide.m3u` — así que nada que lea las rutas viejas se rompe.

Decisiones que van dentro del fichero, y todas importan:

- **Las URLs de reproducción pasan por el puente HLS, no por el motor**:
  `http://192.168.1.245:8084/<hash>/index.m3u8`, el mismo servicio `acestream-suite-hls` que usa la
  tele. El motor sirve **un solo lector por contenido**, así que una lista que llame a
  `http://192.168.1.28:8081/ace/getstream?infohash=<hash>` directamente compite con el puente por el
  mismo canal y los dos se quedan sin él (medido el 2026-10-18: 0 manifiestos y 23 fallos en un
  minuto). `direct.m3u` es la única excepción: es el MPEG-TS del propio motor, para Dispatcharr (que
  no proxya HLS) y para diagnosticar.
- **`group-title` lleva la categoría de acestream-suite** (`Movistar`, `Deportes ES`, `Deportes
  int.`), que es lo que se convierte en grupo de canales en Dispatcharr.
- **Los atributos se sanean, el nombre visible no**: una coma dentro de `tvg-name` parecería el
  separador entre atributos y nombre para cualquier analizador que corte en la primera coma, así que
  ahí se quita, mientras que el nombre que muestra el reproductor la conserva.
- **El nombre visible acaba con la calidad del canal** (`Canal 1080p`), porque es lo que ve la búsqueda
  del reproductor; `tvg-name` se queda con el nombre a secas, ya que es lo que lee el emparejamiento
  con la guía y una captura re-detectada no debe re-mapear la guía. `other` significa "sin
  clasificar", no una calidad, así que no se pega.
- **Un catálogo vacío sigue produciendo una lista válida**, no un fichero vacío.

**Dispatcharr** (`http://192.168.1.28:9191`) ya la tiene como cuenta M3U:

    cuenta 4  AceList (192.168.1.245)  http://192.168.1.245/varios/acelist.m3u
              refresco cada 6 h · activa · perfiles: 1 (por defecto)
              registro del refresco: 152 streams creados, 0 actualizados, 0 obsoletos, 0 borrados en 0,3 s
              grupos nuevos: Movistar, Deportes ES, Deportes int. (los tres habilitados)

La etiqueta y la URL de la cuenta son las que tenía antes del renombrado: sigue apuntando a
`acelist.m3u`, que ahora lleva el mismo contenido que `acestream-suite.m3u`.

Lo que **todavía no** hace: crear *canales* de Dispatcharr a partir de esos 152 streams. Los streams
son el origen; los canales son la parrilla que exponen el reproductor de Dispatcharr y el endpoint
HDHomeRun para el Live TV de Jellyfin. Es una llamada en bloque
(`/api/channels/channels/from-stream/bulk/`) y una decisión sobre una parrilla compartida, así que
queda para el dueño. Ver `odd/tasks/acelist.md`.

## Actualización — el catálogo crece a todo el motor (2026-10-09)

acestream-suite ya no tiene solo la selección de deportes y Movistar. Se recorrió el índice completo del
buscador del motor (`/search?page=N&page_size=200`, 8 páginas) y los 1.499 canales que no estaban ya
en el catálogo ni llevan la etiqueta `erotic_18_plus` se importaron como documento de export de
acestream-suite, así que cada uno conservó su categoría, idioma y país. El catálogo tiene ahora **1.651
canales en 19 categorías** (`TV` 600, `Otros` 251, `Películas` 169, `Regionales` 152, `Amateur` 77,
`Deportes ES` 22, …, más `Movistar` 13 y `Deportes int.` 117 de la primera importación).

Dos consecuencias que conviene conocer:

- La importación **encola una comprobación real por canal**, captura incluida: medido a ~2,3
  canales/minuto, la cola pendiente son unas **11 horas** con la concurrencia por defecto (2), es
  decir dos sesiones del motor a la vez. La interfaz no se resiente. Reiniciar el contenedor
  pierde la parte de la cola que aún no ha corrido; el botón *Comprobar* de la lista la vuelve a
  lanzar para lo que muestre el filtro. `check_concurrency`, `check_timeout` y
  `screenshot_timeout` son ajustes guardados, así que se puede hacer que termine antes sin tocar
  la imagen ni el compose.
- La lista de reproducción (`http://192.168.1.245/varios/acestream-suite.m3u`) y Dispatcharr
  siguieron solos: 1.651 canales ahora, y la cuenta 4 informó `1.499 streams creados, 0
  actualizados, 0 obsoletos, 0 borrados en 3,2 segundos`, en los mismos 19 grupos.

Los canales para adultos que el propio motor etiqueta (`erotic_18_plus`, 63) se dejaron fuera a
propósito en un catálogo doméstico; añadirlos es una línea del script de recolección.

### Las seis listas

| Enlace | Canales | Qué lleva |
|---|---|---|
| `http://192.168.1.245/varios/acestream-suite.m3u` | 540 | todo; es la que consume Dispatcharr, a través de la copia `acelist.m3u` a la que sigue apuntando |
| `http://192.168.1.245/varios/deportes.m3u` | 165 | las categorías `Deportes*`, más cualquier nombre deportivo esté donde esté |
| `http://192.168.1.245/varios/espana.m3u` | 71 | canales de España: `ES` como país, las categorías españolas o un nombre español |
| `http://192.168.1.245/varios/hls.m3u` | 196 | deportes o España, en HLS por el puente |
| `http://192.168.1.245/varios/direct.m3u` | 540 | todo, en el MPEG-TS del propio motor — la única lista que no pasa por el puente |
| `http://192.168.1.245/varios/acestream-suite-guide.m3u` | 540 | todo, y la única que lleva la guía: `url-tvg` en la cabecera y el id de canal de guía como `tvg-id` en los 83 canales que la tienen (`scripts/acestream-suite-guide-playlist.py`, `docs/acestream-suite-guide.md`) |
| `http://192.168.1.245/varios/madrid.m3u` | 0-8 | solo los canales que están emitiendo el partido del Real Madrid en directo, y nada más; vacía (solo cabecera) cuando no hay partido (`scripts/acestream-suite-playlist.py`, sección siguiente) |

Dos reglas valen en todos estos ficheros salvo `madrid.m3u` (que está vacía casi todo el día), y los
dos scripts las implementan: **una entrada por nombre
visible** — gana la mejor calidad declarada, a igual calidad una categoría de deportes, y a igual todo
el content id menor, de modo que dos publicadores del mismo canal son una sola fila y el orden de
entrada no decide nada — y **todas las URLs por el puente**, salvo `direct.m3u`, que es la lista del
motor a propósito.

`hls.m3u` — la que lee la tele — lleva dos carpetas propias: **`Campeones`**, con los canales de
Movistar Liga de Campeones y nada más, y **`Real Madrid`**, que solo existe mientras hay un partido en
directo (sección siguiente). El motor reparte los canales de Campeones entre tres categorías
(`Deportes ES`, `Deportes`, y `Amateur` para los dos `M+ LIGA DE CAMPEONES … --> NEW ERA`), así que
esa carpeta se decide por nombre (`carpeta_campeones` en el script) y solo en esta lista; las demás
siguen agrupando por categoría. Nueve canales el 2026-11-04: los cuatro de `M. Liga de Campeones`, los
dos publicados en `elcano.top` (`Campeones elcano`, 720p, y `Campeones elcano FHD`) y los tres
`M+ LIGA DE CAMPEONES …`.

`deportes.m3u` y `espana.m3u` son filtros sobre el mismo catálogo, y la regla del **nombre** es lo que
las hace útiles: el motor archiva un re-stream de La 1 bajo `Amateur`, un canal de DAZN bajo `Otros`,
y deja el país y el idioma vacíos en la mayoría de los canales españoles. Ya hay dos falsos positivos
pagados y cubiertos por el autotest del script: `beIN Movies` no es deporte y `Premiere` (los canales
rusos de cine) no es `Premier Sports`.

**VAMOS** (el canal de deportes de Movistar) necesitaba el mismo trato: el motor lo archiva bajo
`Amateur`, sin país y sin el prefijo «Movistar», así que en el catálogo se llama
`#VAMOS FHD --> NEW ERA` y `Vamos`; las reglas de nombre lo llevan ahora a `deportes`, `espana` y `hls`.

`espana.m3u` es sobre **España, no sobre el idioma español**: el motor etiqueta también como `es`
todos los canales latinoamericanos, y dejar pasar eso metería 26 canales de México, Chile o Colombia
en una lista que dice España. Si los quieres, es una condición del script.

### Los canales del partido, en directo

`madrid.m3u` y la carpeta `Real Madrid` son el mismo conjunto: los canales que están emitiendo un
partido del Real Madrid **ahora mismo**, fútbol o baloncesto. Dos caras del mismo hecho — la carpeta
para quien ya tiene `hls.m3u` cargada en la tele, la lista para quien quiere solo el partido — así que
no pueden contradecirse: las dos salen de un fichero que escribe un solo trabajo.

**De dónde salen los partidos, y por qué el título de la guía no es la señal.** Dos fuentes sin clave,
las dos medidas el 2026-11-04: `eventsnext.php?id=133738` (Real Madrid CF) en thesportsdb.com para el
fútbol, con `strTimestamp` en UTC y `strLeague`; y la API oficial de la Euroliga,
`api-live.euroleague.net/v2/competitions/E/seasons/E2026/games`, con `utcDate` y `local`/`road`. La Liga
Endesa (ACB) no tiene fuente sin clave que se haya podido verificar, así que esos partidos no se añaden
y el trabajo lo dice en su log en cada pasada.

La guía no puede decidirlo, y las medidas dicen por qué: emite
`Euroliga de baloncesto: Real Madrid - Panathinaikos` a las 23:30 en `M+ Deportes` cuando el saque real
fue a las 19:45Z — esa es la emisión **en diferido**, y el directo es `Movistar Plus+` a las 19:45 con el
título `Euroliga` a secas, que no nombra al club; y emite `LALIGA EA SPORTS: R. Racing Club - Real
Madrid` a las 18:00, una **repetición** del partido jugado el 1 de noviembre. Una regla que lee títulos
elige los dos programas equivocados; el saque real elige los dos buenos.

**La regla del directo.** Un programa de la guía es la emisión en directo de un partido cuando se
cumplen las tres cosas:

- su `start` cae entre 20 minutos antes del saque (`--live-tolerance-before`) y 5 minutos después
(`--live-tolerance-after`). Asimétrico a propósito: una emisión puede abrir su previa antes (el
programa de las 17:38 para un saque a las 17:45) pero no empieza un cuarto de hora tarde — con 20
minutos simétricos la repetición de las 18:00 pasaba el filtro de hora y solo la rechazaba la
competición;
- su canal no es del club (`Real Madrid TV`, `RMTV`);
- su título nombra la competición del partido: Champions → `champions|liga de campeones`, LaLiga →
`laliga|la liga`, Euroliga → `euroliga|baloncesto`, Copa del Rey → `copa`.

Una repetición empieza a otra hora y no cumple nunca la primera condición. Esa tabla es lo que evita
que un programa que empieza al minuto correcto se tome por el partido.

**Qué hace `scripts/acestream-suite-partido.py`**, desde el cron
`/usr/local/bin/acestream-suite-partido` cada 10 minutos a las `:01 :11 :21 :31 :41 :51` (log
`/var/log/acestream-suite-partido.log`, flock propio):

- Lee los partidos de las dos fuentes en cada pasada —un no-200 de TheSportsDB significa que la clave
gratuita ha rotado, y se registra como «no lo sé» sin añadir nada— y lee la guía solo para el **canal
que los emite a esa hora**: un programa es la emisión en directo de un partido cuando se cumplen las
tres condiciones de arriba a la vez.
- **La ventana** va de 60 minutos antes del saque real (`--window-before`) al `stop` del programa
  emparejado. Mientras el partido está en directo se vuelve a buscar cada 10 minutos, porque aparecen
  publicadores y otros se caen; un partido de hace tres horas o una repetición de mañana caen fuera y
  no disparan nunca.
- Para cada emisión en directo, busca en el motor el nombre de ese canal (y el título del catálogo que
  `/epg.json` mapea al mismo `guide_id`), y se queda solo con los resultados que **son ese canal**, no
  sus vecinos: los dos nombres se normalizan (fuera el prefijo de plataforma, la cola del publicador,
  la puntuación y las mayúsculas) y el resultado tiene que ser igual al buscado o empezar por él
  seguido de algo que no sea un número. Eso es lo que deja fuera `M+ LIGA DE CAMPEONES 2 FHD --> NEW
  ERA` cuando el canal que emite es `M+ Liga de Campeones`: lleva todos los tokens de la búsqueda y es
  el canal equivocado. Como mucho 8 streams por término (`--max-per-channel`), ninguno ya en el
  catálogo, y entran con `POST /import` como documento de exportación de acestream-suite con categoría
  `Real Madrid`, `language: es` y `country: ES`. La app encola sus propias comprobaciones, así que un
  stream muerto no llega a un reproductor.
- Escribe `/var/lib/acestream-suite-partido/live.json` (`--live-file`): los content ids que están
  emitiendo el partido ahora o en la hora previa, cada uno con el `stop` de su programa — los
  canales del catálogo mapeados a ese canal de guía *y* los que acaba de importar.
- `/var/lib/acestream-suite-partido/state.json` guarda cada `(comienzo, guide_id, término)` que de
  verdad importó, con los content ids que trajo, para que el cron cada 10 minutos no repita la
  importación. No borra nada nunca, y un término cuya búsqueda no encontró nada no se anota a
  propósito: la pasada siguiente lo reintenta, que es justo para lo que sirve correr dentro de la
  ventana.

**La carpeta y la lista.** `scripts/acestream-suite-playlist.py` lee el mismo `live.json`
(`--live-file`) y, siempre y cuando `now < stop`, pone `group-title="Real Madrid"` a esos canales en
`deportes.m3u` y `hls.m3u`, y los lleva —solo a ellos— a `madrid.m3u`. Un fichero ausente, uno
ilegible o una entrada caducada son sin carpeta y sin entrada, y no son un error. La categoría `Real
Madrid` sigue contando como deporte, que es lo que mete esos canales en las dos listas de deportes,
pero **no** decide la carpeta: si la decidiera, se quedaría para siempre con los canales de todos
los partidos pasados. En `hls.m3u` gana `Campeones` cuando un canal es de las dos —esa carpeta es
más antigua y va por nombre, y si ganara la del partido se vaciaría durante cada partido—. **Nada se
borra al acabar el partido**: los canales se quedan en el catálogo y en `acestream-suite.m3u`, y
solo se van la carpeta y la entrada de `madrid.m3u`.

**Límite medido: no puede probar la imagen.** Las fuentes de partidos dicen cuándo se juega, la guía
dice qué canal lo emite a esa hora, el motor dice qué streams tiene ese canal, y la comprobación de la
app dice que el stream está vivo. Ninguno de los cuatro prueba qué se ve en pantalla. Lo que se puede
afirmar es que ese canal emite el partido a esa hora, que el stream está vivo (la comprobación abre una
sesión real con datos y pares) y que no es el canal del club; el fotograma en la tele es cosa de los
ojos del dueño. La regla es la de `suite-aar/docs/acestream-manual.md`: acesearch no busca un evento,
busca el canal que lo emite.

Para ver qué haría ahora mismo, sin importar y sin escribir: `/usr/local/bin/acestream-suite-partido
--dry-run`.

### Quién lee cada lista

No había forma de saber cuál de los seis ficheros pide realmente un reproductor, así que el 2026-10-25
Caddy empezó a registrarlo. `/etc/caddy/Caddyfile`, en dos sitios, cada bloque inmediatamente después
de la cabecera de su propio sitio: `@varios_http` justo después de `(ip_access) {` (el snippet que
importa el sitio `http://192.168.1.245`), escribiendo en `/var/log/caddy/varios.log`; y `@varios_https`
justo después de la línea `https://192.168.1.245 {`, escribiendo en
`/var/log/caddy/varios-tls.log`:

```caddyfile
@varios_http path /varios/*
log @varios_http {
    output file /var/log/caddy/varios.log {
        roll_size 10MiB
        roll_keep 3
    }
}
```

El bloque HTTPS es el mismo con `@varios_https` y `varios-tls.log`. `log` es una **directiva de sitio,
no un manejador HTTP**: dentro de `handle_path` Caddy la rechaza (`directive 'log' is not an ordered
HTTP handler`) y dentro de `route` también; necesita el ámbito del sitio y un matcher. Hacen falta dos
nombres de matcher distintos porque Caddy no admitió reutilizar uno.

**Dónde va el bloque es todo el truco.** Un primer intento insertó los dos bloques buscando un texto
ancla, encontró `(ip_access) {` dos veces y dejó los dos matchers dentro del snippet: cada petición HTTP
se escribía entonces en los dos ficheros y las peticiones HTTPS no se registraban en ninguno. Se
detectó haciendo una petición HTTP y una HTTPS y contando líneas, y se corrigió poniendo cada bloque
justo después de la cabecera de su sitio. Insertar por texto ancla encontró el snippet dos veces; el
ancla es la cabecera del sitio.

Verificado después de `systemctl restart caddy` con el fichero vivo: una petición HTTP simple aparece
**una vez**, en `varios.log`, con el logger `http.log.access.@varios_http`; una petición HTTPS aparece
**una vez**, en `varios-tls.log`, con `http.log.access.@varios_https`. Ninguna petición se registra dos
veces, y cada línea lleva `request.uri` (el `/varios/<fichero>.m3u` completo), `request.remote_ip`,
`status` y `size`: en un día se puede responder cuál de las seis listas se lee desde casa. De momento
los ficheros solo tienen las peticiones de prueba.

`caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile` contestó *Valid configuration* y
`systemctl reload caddy` lo tomó; `/`, `/varios/`, `:3010/` y `/varios/acestream-suite.m3u` siguen
contestando 200, y el fichero anterior se guarda en `/etc/caddy/Caddyfile.bak-20261025-varios-log`.

**Estos ficheros no se vacían nunca con `: > fichero` ni con `truncate`.** Caddy mantiene el descriptor
abierto a través de `systemctl reload`, así que la línea siguiente se escribe en el offset viejo y el
fichero se llena de bytes NUL por delante. Para empezar un fichero limpio hay que borrarlo y hacer
`systemctl restart caddy`: un reload no lo reabre. El control normal de tamaño es el del propio Caddy,
`roll_size 10MiB` / `roll_keep 3`.

### Solo canales en línea

Las listas se construyen desde `GET /export?status=alive` — todas salvo `madrid.m3u`, que es el
conjunto en directo del partido (sección anterior) —, así que **un enlace muerto no llega a un
reproductor**. El `alive` de la aplicación es más estricto que «tiene pares»: significa que
el motor llegó a `dl` con datos descargados *y* al menos un par, de modo que un canal que solo se
anuncia no entra en la lista.

Dos consecuencias que conviene conocer: las listas **crecen a medida que terminan las comprobaciones
pendientes** (cuando se escribió esto estaba comprobada la mitad del catálogo) y un canal que deje
de emitir **desaparece de las listas en la siguiente pasada de 15 minutos**. Dispatcharr marcará
esos streams como obsoletos en su siguiente refresco: para eso la lista general es la que él
consume. Poner `ACESTREAM_SUITE_PLAYLIST_STATUS=` vacío vuelve a publicar el catálogo entero en esas
listas, y la lista de la guía acepta `--status ""`.


### Los canales caídos se eliminan cuando termina la verificación

Un trabajo de una sola vez (`/usr/local/bin/acestream-suite-prune`, desde cron cada 20 minutos)
espera a que todos los canales del catálogo estén comprobados, elimina los que no están `alive` —
los caídos y los sin pares — y ejecuta `acestream-suite-playlist`, que reescribe las cinco. Nunca
toca un canal sin comprobar: «aún sin verificar» no es «caído».

Antes de borrar nada escribe `/backup/acelist/<fecha>/` con **las tres listas que copia**
(`acestream-suite.m3u`, `deportes.m3u`, `espana.m3u` — `hls.m3u`, `direct.m3u` y
`acestream-suite-guide.m3u` no están en esa copia), el catálogo completo exportado (`catalogo.json`,
que el propio *Importar* de acestream-suite acepta, así que el borrado es reversible), una copia
consistente de la base de datos y la lista de lo que borró. Los canales se borran por la vía propia
de acestream-suite, así que cada uno se lleva su historial de comprobaciones y su captura.

El trabajo se marca como hecho en `/var/lib/acestream-suite-prune/done` y su registro es
`/var/log/acestream-suite-prune.log`. Para dejarlo correr otra vez tras importar más canales:
`acestream-suite-prune --undo-guard`. Para ver qué haría sin esperar: `acestream-suite-prune --dry-run`.

**Medido el 2026-10-18: el guard convierte el cron en un no-op para siempre, no sólo tras la primera
pasada.** El marcador se escribió el 2026-10-09; para el 18/10 el catálogo había acumulado **35
canales cuya última comprobación era `no_peers`** y ninguno se iba a borrar ya. Se eliminaron con
`acestream-suite-prune --undo-guard --force --dry-run` y después sin `--dry-run`: copia en
`/backup/acelist/20261018-221301`, 547 canales, todos `alive`, y el trabajo volvió a escribir el guard
(`35 deleted`). Así que el cron sigue siendo un no-op hasta que alguien ejecute `--undo-guard` otra
vez: podar es una decisión manual, no continua.


### Dispatcharr sigue la lista por su cuenta

Dispatcharr lee `acelist.m3u` (la copia de `acestream-suite.m3u`), así que cada vez que ese fichero
se encoge (un canal que deja de emitir sale de la lista en la siguiente pasada) el siguiente
refresco de la cuenta marca los streams que faltan como **obsoletos** en lugar de borrarlos, y el
`stale_stream_days` de la cuenta (7) es lo que acaba quitándolos. Tras la primera limpieza la cuenta
informó `1072 marked stale, 0 removed` de 579 procesados, y las filas lo confirman: `is_stale` está
en `True` para esos 1.072 y en `False` para los vivos.

Su intervalo de refresco es de 6 horas. Para adelantarlo a mano:

```bash
TOKEN=$(curl -s -X POST http://192.168.1.28:9191/api/accounts/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"alberto","password":"ingenieria"}' | jq -r .access)
curl -s -X POST http://192.168.1.28:9191/api/m3u/refresh/4/ -H "Authorization: Bearer $TOKEN"
```


### Reproducir en la tele (Tessel, Samsung)

La app de la tele es **Tessel** (`PatrickSt1991/tessel-tizen-tv`, antes VLC TV): una web app Tizen
sobre AVPlay de Samsung que reproduce HLS/DASH/RTSP y carga listas M3U desde una URL. Hay dos formas
de darle canales, y compararlas en el mismo canal es la prueba que importa:

| Lista | Qué recibe la tele |
|---|---|
| `http://192.168.1.245/varios/hls.m3u` | deportes y canales españoles (196) como **manifiesto HLS** del puente (`:8084`) |
| `http://192.168.1.245/varios/deportes.m3u` (165), `.../espana.m3u` (71), `.../acestream-suite.m3u` (540), `.../acestream-suite-guide.m3u` (540) | el mismo catálogo, o un filtro suyo, también como **manifiesto HLS** del puente; la última de ellas anuncia la guía |
| `http://192.168.1.245/varios/direct.m3u` (540) | el **MPEG-TS** del motor, directo — la única lista que no pasa por el puente |

El puente HLS (`acestream-suite/hls-bridge.py`, servicio `acestream-suite-hls`) existe porque ni el
motor (`/ace/manifest.m3u8` no responde en 3.2.11) ni Dispatcharr 0.31.0 (su módulo `hls_proxy` está
en el código pero no montado en sus URLs) sirven HLS. Reempaqueta con `-c copy` — sin reencodear —
en segmentos de 2 s (el primero de 1 s, para que un cambio de canal espere menos). El colchón está
en disco y la ventana es corta: 240 segmentos (8 min) en el tmpfs, de los que se sirven al
reproductor los últimos 30, que acaban 20 segmentos (40 s) por detrás del directo, así que un bajón
de los pares se absorbe en vez de verse. Un ffmpeg por canal, cierra el canal 45 s después de la
última petición y admite 4 a la vez; cuando al tmpfs le quedan menos de 256 MB libres, el reaper
cierra la sesión que más bytes ocupa, porque un tmpfs lleno no rompe una sesión: las rompe todas.
Registra un resumen por sesión cada 30 s (manifiestos, segmentos, fallos, secuencia, cliente) y cada
404/504 en el momento, que es como se distingue un reproductor atascado de un montaje atascado.
Medido: manifiesto en 2,5 s en frío y 0,05 s en caliente, 21 de 21 segmentos listos cuando el
reproductor los pidió, 0,2-0,9 % de CPU y 51 MB de RAM.

**Desde el 2026-10-15 el puente se apoya en un hecho medido del motor: sirve un solo stream por
contenido.** Mientras lo guarda, cualquier otro lector recibe una URL que no entrega nada (el mismo
ffmpeg que lee bien cuando el stream está libre contesta `Invalid data found when processing input`), y
el dueño de ese stream lo conserva unos diez minutos después de que su consumidor desaparezca (medido:
ocupado a las 20:07, ocupado a las 20:15, libre a las 20:19). Eso es lo que dejó la tele en «cargando
contenido»: el ffmpeg del puente murió cuatro veces en diez minutos el 2026-10-15 —todas con salida
limpia, código 0—, el código viejo tiraba la sesión y borraba su directorio, y cada petición de los
minutos siguientes solo podía esperar a que el motor soltara. Esas salidas limpias se explicaron esa
misma tarde en el lado del `.28`, y nunca fueron cosa de este servicio: el motor corría con la caché
del directo en la unidad equivocada (`256` bytes, no 256 MB), así que servía un trozo corto y cerraba
la respuesta; su nginx, además, embolsaba el directo en un fichero temporal y tenía timeouts de 60 s.
Ver `suite-aar/docs/acestream.md`, «Reproducción sin cortes». Ahora: la sesión sobrevive al ffmpeg
muerto y se reintenta en el sitio, en el mismo directorio, mientras alguien pida el manifiesto;
lo que acota el motor que acepta y no manda nada es el bombeo (cada lectura va con
`urlopen(timeout=60)` y la entrada de ffmpeg se cierra tras tres intentos sin datos), y el ffmpeg **no** lleva la familia
`-reconnect`: se quitó el 2026-10-18 tras medir qué hace cuando el motor rechaza un stream — deja a
ffmpeg pidiendo en bucle dentro del mismo proceso, así que el puente no se entera, no suelta la sesión
y no reintenta (**13 027 peticiones en un día** en un canal atascado). Ahora ffmpeg sale, la sesión se
reintenta con espera de 2, 4, 8, 16 y 30 s tras fallos seguidos, y un canal ocupado recibe un `504`
honesto en vez de una lista helada; al cerrar una sesión el puente suelta la
del motor con `?method=stop` (el reaper a los 45 s de silencio, o el tope de 4), así que el canal queda
libre para el siguiente reproductor al instante; y un 504 espera esos reintentos hasta
`HLS_STARTUP_TOTAL_SECONDS` (24 s) en vez de volver 1,5 s después del primer fallo. Verificado el
2026-10-15 en un canal que nadie veía: manifiesto `200` en frío, primer segmento de 2,1 MB y
decodificable él solo, manifiesto `200` en 2 ms justo después de matar el ffmpeg (la sesión no se
pierde), y tras 48 s de silencio el reaper soltó la sesión del motor (`ok`) y la siguiente petición de
manifiesto contestó `200` en 4,2 s. `python3 hls-bridge.py --self-test` comprueba estas decisiones sin
motor y sin red, con un motor y un ffmpeg de mentira: 42 comprobaciones.

**Revisado otra vez el 2026-11-04, con la tele en «cargando contenido» por un motivo que no era el
puente**: `192.168.1.28` estaba fuera de la red (`arp` dice `INCOMPLETE`, nada contesta en 8081), así
que cada petición de manifiesto acababa en el `504` honesto que el puente da mientras reintenta. La
revisión sí encontró cuatro defectos propios, todos corregidos aquí: al cerrar una sesión se borraba
el directorio de otra más nueva del mismo canal (el reproductor perdía el canal a mitad), `session_for`
y el reaper tenían tomado el lock global durante las dos llamadas bloqueantes de un cierre (hasta 10 s
de espera para cualquier petición, los sondeos de manifiesto de la tele incluidos), un 404/504 del
reproductor incrementaba el contador de espera que pertenece a los reintentos del motor, y
`release_engine` caía al token por defecto sin `pid`, que es la sesión que comparten todos los
clientes de la casa: un `stop` ahí habría matado el stream de otro lector, así que ahora se niega a
mandarlo. El tmpfs ganó un aviso (cerrar la sesión que más bytes ocupa cuando quedan menos de
`HLS_MIN_FREE_MB`, 256 MB) y una limpieza al arrancar de lo que dejó un proceso muerto.

El motor, por su parte, corre con los flags de colchón en `acestream-suite/../acestream/start-engine`
(`--use-internal-buffering 1`, `--live-mem-cache-size 256`, `--max-connections 500`, `--max-peers 50`).
La medición no mostró diferencia (el lado servidor ya entregaba 45 s sin un hueco a 1,2× la velocidad
del canal, y el motor a 5,5-7,2× el bitrate), así que son margen para los huecos entre pares, no el
arreglo de una avería medida.
