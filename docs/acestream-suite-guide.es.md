# acestream-suite — la guía (EPG)

La guía es una función del **servicio acestream-suite** (`http://192.168.1.245:8083`), no un segundo
despliegue: varias fuentes XMLTV, una correspondencia entre canal y canal de guía, una parrilla de
programación guardada, una celda de ahora/después y una parrilla en la propia interfaz de la
aplicación, más un documento XMLTV y una lista M3U con `tvg-id`/`url-tvg` para que Dispatcharr y
Jellyfin muestren qué hay en cada momento.

> **El producto se llamaba AceList**: la guía vive ahora en `acestream-suite`, y el renombrado está
> registrado en `odd/tasks/acestream-suite-rename.md`. Rige la compatibilidad de una versión — la vieja
> `/varios/acelist-epg.m3u` se mantiene como copia de `acestream-suite-guide.m3u`, las variables
> `ACELIST_*` se siguen leyendo cuando falta la nueva, y `/usr/local/bin/acelist-epg-playlist` es un
> envoltorio del binario nuevo. El motor AceStream conserva su nombre.

| | |
|---|---|
| URL | `http://192.168.1.245:8083` (solo LAN, sin autenticación) |
| Host | `dev.albertucho.home` — 192.168.1.245, RHEL 8.7 |
| Contenedor | `acestream-suite`, proyecto de compose `acestream-suite`, imagen `acestream-suite:1.3.0` |
| Construida desde | el tarball fijado del proyecto, etiqueta `v1.3.0`, más **dos** parches locales: `acestream-suite/Dockerfile` aplica `acestream-suite-lan.patch` y después `acestream-suite-guide.patch` |
| Motor | el motor AceStream de **192.168.1.28**, dentro de su túnel WireGuard |
| Plan y evidencias | `odd/tasks/acelist-epg.md` |
| Ficheros en el host | `/opt/acestream-suite/` (compose, Dockerfile, entrypoint, los dos parches, `data/`) |

## Historia: la guía se probó en un duplicado y después se fusionó

El dueño pidió el 2026-10-13 que el trabajo de la EPG se hiciera sobre una **copia** (entonces
llamada `AceListEPG`, en el puerto 8087) precisamente para que el servicio que la casa mira no fuera
el banco de pruebas: la guía necesita tablas nuevas, un refresco, pantallas propias y una salida
XMLTV, y desarrollar eso dentro del despliegue que ya atendía a la tele habría puesto en riesgo su
catálogo, sus capturas y su cola de comprobaciones. El duplicado se sembró desde el servicio vivo
(`GET /export`), la guía se construyó y se probó en él durante el 2026-10-13/14, y cuando el dueño
aceptó la interfaz el trabajo se fusionó en `acestream-suite` y el duplicado se retiró: contenedor
parado, su red eliminada, su imagen eliminada, su directorio (`/opt/acelist-epg`) borrado y el
puerto 8087 libre. El script de sembrado se fue con él: este servicio ya tenía el catálogo.

## Cómo se construye

Una imagen, un Dockerfile y un único pin del proyecto:

- `acestream-suite/Dockerfile` descarga el tarball de la etiqueta fijada, lo verifica por sha256 y
  aplica **dos** parches en orden: `acestream-suite-lan.patch` (alcance en la LAN y reproducción) y
  `acestream-suite-guide.patch` (la guía). Los dos se leen contra el mismo
  `ACESTREAM_SUITE_VERSION`/`ACESTREAM_SUITE_SHA256`, así que no hay un segundo `FROM` que pueda
  desviarse: subir de versión obliga a releer **los dos** parches contra el código nuevo.
- **Sin entrypoint propio.** El `entrypoint.sh` de la imagen migra la base de datos con el
  `app.launcher.upgrade_database()` de la propia aplicación y luego sirve con uvicorn; las tablas de
  la guía llegan como migraciones de Alembic, que es justo lo que ese entrypoint ejecuta.
- Todo lo demás es la imagen de acestream-suite ya desplegada: ffmpeg, la instalación editable del
  árbol parcheado, `ACESTREAM_SUITE_DATA_DIR=/data`, el volumen `/data` y `EXPOSE 8000`.

## Qué corre y dónde

| | |
|---|---|
| Contenedor / proyecto | `acestream-suite`, proyecto de compose `acestream-suite` — un solo servicio, no dos |
| Puerto | **8083** en el host, 8000 dentro |
| Estado | **`/opt/acelist/data`** en .245 (`acelist.db` + `screenshots/`) |
| Motor | **`http://192.168.1.28:8081`**, el motor tunelado — para los enlaces de reproducción, y **no** como fuente de trabajo |
| Cola de comprobaciones | una, y es la de este servicio: 581 canales y **1187 comprobaciones** en el momento de medir |

**La migración se aplicó sobre la base de datos viva y la dejó intacta.** `alembic 0005`; existen las
tablas `epg_channel`, `programme` y `epg_alias` y la columna `channel.guide_channel_id`; el catálogo
que había antes del cambio sigue ahí después — **581 canales, 1187 comprobaciones, 587 capturas**, sin
cambios antes y después, con `pragma integrity_check` respondiendo `ok`.

## Procedimiento — desplegar un cambio en la guía

Acción, desde el portátil:

```bash
cd ~/webserver
scp acestream-suite/Dockerfile acestream-suite/docker-compose.yml acestream-suite/entrypoint.sh \
    acestream-suite/acestream-suite-lan.patch acestream-suite/acestream-suite-guide.patch root@192.168.1.245:/opt/acestream-suite/
```

Acción, en el host:

```bash
cd /opt/acestream-suite && docker compose build acestream-suite && docker compose up -d acestream-suite
```

`up -d acestream-suite` nombra un servicio a propósito: el mismo fichero de compose también contiene
el puente HLS **`acestream-suite-hls`**, y un `docker compose up -d` sin argumentos lo recrearía
también. El `chown` del directorio de datos solo importa en un primer despliegue: Docker crea un
origen de montaje que no existe como directorio vacío propiedad de root, y el contenedor corre como
`1000:1000` (la cuenta `ingenieria` del host), así que con otro propietario no puede escribir. El
entrypoint aplica las migraciones al levantar.

No hace falta nada más: este servicio ya tenía su catálogo, y las tablas de la guía llegan con las
migraciones. Tras reconstruir, la guía se refresca a mano (abajo).

## La guía: dos fuentes, ventana de 72 h y un refresco que nadie programa

Las dos fuentes están en `acestream-suite/docker-compose.yml` como lista JSON
(`ACESTREAM_SUITE_EPG_SOURCES`), porque el catálogo son 19 categorías de origen mezclado y ningún
proveedor por sí solo lo cubre:

| Fuente | Tamaño (sin comprimir) | Canales | Notas |
|---|---|---|---|
| `https://www.tdtchannels.com/epg/TV.xml.gz` | 5,0 MB | 184 | parrilla nacional española y Movistar+; **sin DAZN** (0 coincidencias) |
| `https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz` | 24,2 MB | 373 | la única de las dos que lleva DAZN 1/2/3/4 y DAZN F1 |

Los tamaños y el número de canales son las mediciones de investigación del 2026-10-13 anotadas en el
plan. Una fuente es una caché, nunca una dependencia: una URL muerta o movida deja una línea en el
registro y degrada, no puede hacer fallar una petición. Los *grabbers* de `iptv-org`, `epg.pw` y los
ficheros por país de UK/IT/DE se evaluaron y quedaron fuera.

**Ventana.** `ACESTREAM_SUITE_EPG_WINDOW_HOURS=72`, lo que muestra una parrilla de tele. Solo se
guardan los programas dentro de la ventana, y cada refresco poda el almacén a medida que los
listados salen de ella, que es lo que evita que un fichero de 24 MB se acumule en un host con poco
espacio libre.

**Refresco.** `python3 -m app.cli epg-refresh` dentro del contenedor. Medido en este servicio:

```bash
ssh root@192.168.1.245 'docker exec acestream-suite python3 -m app.cli epg-refresh'
```

La primera pasada tras la fusión guardó **28.788 programas** en **557 canales de guía** y tardó
**4,9 s**. El análisis de una fuente real de 24 MB, solo, son ~1,5 s y ~101 MiB de RSS (una de 5 MB:
~0,4 s, ~60 MiB) — por eso el análisis es en flujo (`iterparse`).

**Los dos trabajos están programados desde el 2026-10-15**, a través del instalador de la casa:
`scripts/crons-install.sh` lleva ya `acestream-suite-guide-playlist` (`7,22,37,52 * * * *`) y
`acestream-suite-guide-refresh` (`7 4,16 * * *`, `flock`, registro en
`/var/log/acestream-suite-guide-refresh.log`) en su array `JOBS`, y los dos apuntan al contenedor
**`acestream-suite`**. Su ausencia tenía una sola causa: el array. Los dos `.cron` existían y habían
corrido en este host mientras la guía vivía en el duplicado ya retirado, pero nunca se añadieron a
`JOBS`, así que la crontab generada tenía dos trabajos menos que la viva y por eso el instalador se
dejaba en paz. Con ellos dentro, `--install` del 2026-10-15 escribió **21 líneas de trabajo**,
arregló el `acestream-suite-prune` duplicado que el panel había dejado en la crontab y quitó los
envoltorios de Cronmaster (el panel vuelve a envolver un trabajo la próxima vez que se edite desde
su interfaz). Los dos comandos también se ejecutaron a mano esa tarde: un refresco completo (2
fuentes, 7550 + 678 programas guardados, 0 canales retirados) y una lista con **582 filas, 103 con
`tvg-id` real** — el recuento de esa tarde; hoy el mismo fichero sirve **540 filas y 83** (ver «Las
salidas»). Para adelantar cualquiera de los dos a mano:

```bash
ssh root@192.168.1.245 'docker exec acestream-suite python3 -m app.cli epg-refresh'
ssh root@192.168.1.245 '/usr/local/bin/acestream-suite-guide-playlist'
```

**El modo de fallo que hay que vigilar: sin refresco la guía se vacía a las 72 h.** Nadie refresca y
la guía se calla: todos los programas guardados han terminado, así que la interfaz no tiene
ahora/después y `/epg.xml` no publica nada por delante. Un contenedor `Up (healthy)` no dice nada sobre
la frescura de la guía: lo que lo dice es la ventana.

## La correspondencia: 103 de 581 canales y una tabla de alias que el emparejador nunca corrige

El emparejador asigna un título del catálogo a un nombre de canal de guía **solo cuando la diferencia
entre los dos nombres es ruido**: marcadores de país (`[PL]`, `[RU]`), etiquetas de calidad (`FHD`,
`HD`, `4K`), marcas de reemisión (`--> NEW ERA`) y un `channel`/`canal`/`tv` genérico que necesita un
testigo distintivo al otro lado. Cualquier testigo de contenido que sobre en cualquiera de los dos
lados — `tve`, `bbc`, `m`, `laliga`, `hypermotion`, un número de canal — significa que no hay
correspondencia. Va **primero al alias**: una decisión humana guardada en `epg_alias` manda, y el
emparejador no la corrige nunca.

Medido con `python3 -m app.cli epg-map --auto` sobre el catálogo de 581 canales: **103 emparejados, 478
sin guía** (18 %), y **0 alias manuales** por ahora. El informe imprime el motivo de cada rechazo, por
ejemplo `[390] Star TV HD — descartado: 'star tv' ⊄ 'tve star tv': sobra 'tve'`, y la pantalla de
correspondencia imprime la misma resta (`'red' ⊄ 'vivid red': sobra 'vivid'`).

Ese número es bajo a propósito, y se pagó. El primer emparejador puntuaba la contención de forma
simétrica e incondicional, así que un nombre de guía que era solo un fragmento de un título del
catálogo se asignaba a 0,85: 114 canales, y **cuatro de los diez pares que imprimió para revisión eran
incorrectos** — `Star TV HD`→`TVE_STAR.TV`, `TV4 HD [PL]`→`M+ LALIGA TV 4`,
`Laliga Hypermotion 1 720 --> NEW ERA`→`LaLiga+`, `HISTORY [PL]`→`BBC History`. Ningún umbral arregla
eso; la regla era incorrecta. Apretarla costó 12 pares (114 → 103) y compró precisión: un canal sin
emparejar es una celda en blanco, una correspondencia equivocada es una mentira sobre lo que se emite.
El 32 % de la sección de investigación del plan se midió con contención laxa y **no es un objetivo**;
la comparación de cinco fuentes con el emparejador final da 28 %, y las dos fuentes realmente guardadas
dan 18 %.

`epg_alias` guarda las decisiones humanas — `epg-map --set` escribe una, `--clear` la revierte, y la
fila anota quién la tomó. El par asignar/deshacer se midió en la interfaz: asignar → `303` y 103→104
emparejados, deshacer → `303` y vuelta a 103.

## Las tres pantallas

| Pantalla | URL | Qué muestra |
|---|---|---|
| Catálogo con lo que emiten ahora | `http://192.168.1.245:8083/` | la lista, con el programa en curso de cada canal emparejado; lo siguiente está a un clic en la parrilla |
| Guía | `http://192.168.1.245:8083/channels/{id}/guide` | una parrilla de 24 h en hora de Madrid, con el programa en curso marcado |
| Correspondencia | `http://192.168.1.245:8083/epg/mapping` | los candidatos, el motivo del rechazo, búsqueda, asignar y deshacer |

Las tres contestaron **200** en el momento de medir.

**Dos estados que no son lo mismo.** *Sin correspondencia* significa que el canal no tiene canal de
guía; *nada en esta franja* significa que lo tiene y la ventana no guarda ningún programa suyo en ese
momento. No deben mostrar el mismo mensaje. Medido en este servicio: de los **557 canales de guía
guardados, 510 llevan programas** en la ventana — el resto venían declarados por una fuente y sin
listados, así que una correspondencia hacia uno de ellos muestra una parrilla vacía con un canal de
guía real detrás.

También es normal, y no un conflicto: varios canales del catálogo comparten un mismo canal de guía —
hasta **5 fuentes AceStream para el mismo `DAZN 2`** — así que el mismo ahora/después en varias filas es
correcto.

**La tabla tiene ahora 11 columnas, no 13.** La captura se plegó dentro de `Título` (representada a
64×36) y `Idioma` + `País` se unieron en una sola columna `Idioma/País`. Ancho estimado **1092 px**
frente a los **1230 px** disponibles en ventanas de 1366–1440 px — una estimación a partir del
contenido más largo por columna, **no una medición de navegador**.

**La celda `Guía` lleva el evento y sus horas, y nada más.** El programa en curso es un enlace a la
parrilla de ese canal (`/channels/{id}/guide`), y un canal sin guía recibe una línea — `Sin guía ·
Asignar` — que lleva a la pantalla de mapeo en vez de una celda muerta. `Ahora` y `Después` son una
sola columna, porque son la misma información. Dos cosas se probaron en esta celda y se retiraron a
petición del dueño: el **programa siguiente**, que está a un clic en la parrilla y convertía la
columna más ancha de la tabla en un dato que la página de guía ya responde, y el **nombre del canal de
guía**, que repetía la fila — la fila *es* la entrada de ese canal en el catálogo. Lo que queda como
propiedad de la lista es **`Añadido`**, la fecha en que el canal entró en el catálogo. Medido en la
página servida: 103 enlaces a las parrillas, 479 a la pantalla de mapeo, 478 `Sin guía · Asignar`, 581
fechas y **0** nombres de canal de guía.

**La lista muestra solo lo que emiten ahora.** El programa siguiente no se repite ahí: se lee en la
parrilla a la que enlaza la celda, que es para lo que sirve el clic. Repetirlo convertía la columna más
ancha de la tabla en un dato que la página de guía ya responde — y la parrilla, a diferencia de la
lista, marca el programa en curso y enseña el resto del día.

**La descripción de la parrilla se parte en varias líneas.** Es prosa, y el `white-space: nowrap` de la
tabla — correcto para un título, un hash o un estado — está mal para una sinopsis: en una sola línea o
la recorta el contenedor de la tabla o arrastra toda la tabla a lo ancho. La celda se ajusta a 46ch,
respeta los saltos de párrafo de la fuente (`pre-line`, porque quien manda dos párrafos los quiere), y
es la única de esa tabla que puede crecer en alto. Medido en la página desplegada: 17 celdas de
descripción, la más larga de 294 caracteres, completa.

**Un detalle del contenedor que el portátil no podía ver.** `actor_name()` llamaba a
`getpass.getuser()`, y el uid 1000 de este contenedor no tiene entrada en `/etc/passwd`
(`KeyError: 'getpwuid(): uid not found: 1000'`), así que el botón de asignar contestaba `500` mientras
que `clear` contestaba `303` y la ruta de escritura parecía medio sana. La guarda devuelve ahora
`unknown (no OS user in this deployment)`, que es lo que anota aquí `epg_alias.created_by`. Verificado
en el contenedor: `getent passwd 1000` no encuentra nada y `HOME` es `/`. Ningún código nuevo puede
resolver el usuario o el directorio personal del operador sin la misma guarda.

## Las salidas

| Salida | URL | Medido |
|---|---|---|
| XMLTV | `http://192.168.1.245:8083/epg.xml` | `200 application/xml`, **57 canales, 3.114 programas** en el momento de medir |
| Correspondencia en JSON | `http://192.168.1.245:8083/epg.json` | `200 application/json`, **103 entradas** bajo `mapping` |
| Lista con la guía | `http://192.168.1.245/varios/acestream-suite-guide.m3u` | **540 filas, 83 con `tvg-id` real** (el id del canal de guía) y el resto sin él; `#EXTM3U url-tvg="http://192.168.1.245:8083/epg.xml"` en la primera línea; todas las URLs son el puente HLS, `http://192.168.1.245:8084/<hash>/index.m3u8`; cada nombre visible acaba con la calidad del canal (`Canal 1080p`), igual que en las demás listas |

En el documento XMLTV solo aparecen los canales emparejados, y un canal sin emparejar no tiene guía que
publicar: eso se ve, en lugar de inventarse. Los números de canales y programas son una foto de la
ventana de 72 h, así que se mueven.

**Dos medidas anteriores del mismo fichero, conservadas como lo que fueron en su fecha:** **582 filas,
103 con `tvg-id` real** el 2026-10-14/15 (el catálogo entero de 582 canales, una fila por publicador), y
**551 filas, 89 con `tvg-id`** justo antes del cambio de abajo. La distancia hasta los números de la tabla
es la poda (el catálogo pasó de 582 a 551) más el cambio del 2026-10-25, que puso la lista de la guía
en línea con las otras cuatro y con sus reglas compartidas:

- **`?status=alive`**, el mismo filtro de frescura que `acestream-suite.m3u`, `deportes.m3u`,
  `espana.m3u` y `hls.m3u`. La razón vieja para publicarlo todo —el duplicado retirado no encolaba
  comprobaciones en vivo, así que `check_result` estaba vacío— murió con el duplicado: la guía vive
  ahora dentro de acestream-suite (`epg_channel` y `programme` en el mismo SQLite) y todos los
  canales tienen su fila en `check_result`, así que el filtro ya no deja la lista vacía. Quitó los
  **4** canales sin pares (las importaciones de baloncesto del 2026-10-25).
- **Una entrada por nombre visible**: la mejor calidad declarada, después una categoría de deportes,
  después el content id menor, de modo que dos publicadores de un canal son una sola fila. Quitó **7**
  nombres duplicados, y `tvg-id` bajó con ellos: `epg.json` sigue informando de **103**
  emparejamientos, pero una fila lleva `tvg-id` solo cuando el publicador que sobrevive a la
  deduplicación es el emparejado, así que el recuento de la lista (103 el 2026-10-14, 89 antes de este
  cambio, 83 en la tabla) no es el del mapeo.
- **Todas las URLs pasan ahora por el puente HLS** (`http://192.168.1.245:8084/<hash>/index.m3u8`), como
  en las otras cuatro listas, en vez de por el motor
  (`http://192.168.1.28:8081/ace/getstream?infohash=<hash>`): el motor sirve un lector por contenido, y
  una lista que lo llame directamente compite con el puente por el mismo canal (medido el 2026-10-18: 0
  manifiestos y 23 fallos en un minuto). `direct.m3u` sigue siendo la lista del propio motor, para
  Dispatcharr y para diagnosticar.

**La lista que lee Dispatcharr es `acelist.m3u` —copia de `acestream-suite.m3u` mientras dure esta
versión— y no se tocó a propósito.** `http://192.168.1.245/varios/acelist.m3u` es la que consume hoy
la cuenta 4, con `tvg-id` = el content id AceStream de 40 hex de cada stream; reescribirla en su
sitio volvería a mapear la parrilla viva (la cuenta tiene ~1.072 streams marcados como obsoletos y
el dueño aplazó esa decisión). La nueva lista es un **segundo** artefacto, escrito por
`/usr/local/bin/acestream-suite-guide-playlist` (a partir de
`scripts/acestream-suite-guide-playlist.py`), y solo escribe cuando su contenido cambia. El dueño
decidirá si la cuenta 4 se pasa alguna vez a ella; lo que se mantiene es la decisión de no meter la
guía en esa lista sobre el fichero vivo. El md5 `b0a727950fd28077f6ac691d215a3fec` medido el
2026-10-14 demostró que la fusión dejó ese fichero en paz: es una medida de ese día, no un
invariante permanente. El fichero ha cambiado después por sus propias razones (las URLs del puente
HLS el 2026-10-18 y la regla de una entrada por nombre el 2026-10-25, ver
`docs/acestream-suite.es.md`), y sigue sin `url-tvg` y sin `tvg-id` de guía.

## Copias de seguridad tomadas antes de la fusión

- `/backup/acelist/merge-20261014-083751` — el servicio tal como estaba: `acelist.db` con
  `integrity_check ok`, 581 canales, 1187 comprobaciones, 587 capturas y los ficheros del despliegue.
- `/backup/acelist/epg-duplicado-20261014-085035` — la base de datos del duplicado (12 MB), conservada
  como seguro aunque el duplicado ya no exista.

## Comprobaciones que merecen la pena

Acción, en el host (`ssh root@192.168.1.245`):

```bash
curl -s http://127.0.0.1:8083/health
curl -s -o /tmp/epg.xml -w "%{http_code} %{content_type}\n" http://127.0.0.1:8083/epg.xml
grep -c "<channel " /tmp/epg.xml
grep -c "<programme " /tmp/epg.xml
xmllint --noout /tmp/epg.xml
head -1 /srv/www/varios/acestream-suite-guide.m3u
md5sum /srv/www/varios/acestream-suite.m3u
docker exec acestream-suite python3 -c '
import sqlite3, os
c = sqlite3.connect("file:%s/acelist.db?mode=ro" % os.environ["ACESTREAM_SUITE_DATA_DIR"], uri=True)
for t in ("channel", "check_result", "epg_channel", "programme", "epg_alias"):
    print(t, c.execute("select count(*) from " + t).fetchone()[0])
print("mapped", c.execute("select count(*) from channel where guide_channel_id is not null").fetchone()[0])'
```

Registro (el despliegue fusionado, medido en el host el 2026-10-14):

```
{"status":"ok","app":"AceList","version":"1.3.0"}
200 application/xml; charset=utf-8
57
3114
(sin salida — xmllint no dice nada cuando el documento es válido)
#EXTM3U url-tvg="http://192.168.1.245:8083/epg.xml"
b0a727950fd28077f6ac691d215a3fec  /srv/www/varios/acelist.m3u
channel 581
check_result 1187
epg_channel 557
programme 28788
epg_alias 0
mapped 103
```

Dos cosas de ese registro son el sentido de la fusión: `check_result` es **1187**, no **0** — la
guía vive ahora junto a las comprobaciones reales de este servicio, en lugar de en un despliegue que
no encolaba ninguna; y que la lista conserve su md5 —aparece como `acelist.m3u`, el nombre de ese día,
y también el campo `app` de la respuesta de salud— demuestra que la lista de la tele no se tocó.

## Límites y peajes conocidos

- **Sin autenticación.** Cualquiera en la LAN puede navegar el catálogo y leer la guía. El 8083 no se
  reenvía nunca en el router.
- **Fuentes de terceros y comunitarias.** tdtchannels y epgshare01 son agregadores/rippers
  comunitarios: útiles, indocumentados y frágiles. Se aplica la misma nota legal que al propio
  AceStream (`docs/acestream-manual.md`, «Aviso legal»): solo LAN, sin redistribuir.
- **`ALL_SOURCES` queda fuera de alcance.** El fichero de 196 MB comprimido con todo junto es
  demasiado grande para un refresco periódico; solo ficheros por país.
- **El motor lleva una sola cola de comprobaciones — la de este servicio.** Ya no hay un segundo
  despliegue encolando comprobaciones; el motor y el túnel WireGuard llevan exactamente las 1187 de
  arriba.
- **El contenedor no tiene entrada en `/etc/passwd` para su uid y `HOME` es `/`.** Cualquier código que
  resuelva el usuario o el directorio personal del operador en tiempo de ejecución necesita la guarda
  descrita arriba; el botón de asignar daba 500 hasta tenerla.
- **La frescura de la guía depende de los dos trabajos de arriba.** Si el refresco deja de correr, la
  ventana de 72 h se vacía y la guía se calla — ese es el modo de fallo que hay que vigilar.
- **Las copias de seguridad son manuales.** Los dos directorios de arriba son las únicas copias previas
  a la fusión; nada programa `/backup/acelist`.

## Diagnóstico

| Síntoma | Causa | Qué hacer |
|---|---|---|
| La parrilla está vacía en todas partes | nadie refrescó la guía y ya no queda nada dentro de la ventana de 72 h | lanzar los dos comandos manuales de arriba; el registro (`/var/log/acestream-suite-guide-refresh.log`) dice qué fuente contestó |
| La guía de un canal sale en blanco pero está emparejado | «nada en esta franja»: su canal de guía no tiene programa en la ventana en ese momento | mirar el mismo canal de guía en otro canal; puede que la fuente solo lo declare, sin listados |
| Un canal no tiene guía ni correspondencia | el emparejador no encontró ninguna coincidencia de solo-ruido | emparejarlo a mano en `/epg/mapping`; el alias lo conserva |
| Un refresco deja la guía igual | una fuente se movió o contestó con la misma ventana | el refresco sale distinto de cero solo cuando **ninguna** fuente contestó; leer la línea por fuente del registro |
| Guardar una correspondencia responde `403 cross-site request rejected` | la dirección que has escrito no está en `ACESTREAM_SUITE_ALLOWED_HOSTS` | añadirla en `docker-compose.yml` y recrear el contenedor; no desactivar la comprobación |
| Una reconstrucción recrea `acestream-suite-hls` también | `docker compose up -d` sin nombre de servicio | nombrar el servicio: `docker compose up -d acestream-suite` |
