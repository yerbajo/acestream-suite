# acestream-suite — an AceStream catalogue on the house LAN

acestream-suite is a catalogue of AceStream links: channels with a Content ID, an availability check
against the engine, a captured frame of each channel, categories, search and a play button. It is
the front end the AceStream search service never had.

> **The product is `acestream-suite`.** The application's source is part of this repository
> (`acestream-suite/`), so no release is downloaded and no patch files are applied; the list names, the
> `ACESTREAM_SUITE_*` variables and the `/usr/local/bin/acestream-suite-*` binaries carry that name, and
> the names of the product before the rename no longer exist. The AceStream engine keeps its own name.
> The rename is recorded in `odd/tasks/acestream-suite-rename.md`.

| | |
|---|---|
| URL | `http://192.168.1.245:8083` (LAN only, no authentication) |
| Host | `dev.albertucho.home` — 192.168.1.245, RHEL 8.7 |
| Container | `acestream-suite`, from the locally built image `acestream-suite:1.3.0` |
| Source | this repository: `acestream-suite/` holds the application (`app/`, `migrations/`, `pyproject.toml`, `alembic.ini`) and the `Dockerfile` that builds it — no downloaded release, no patch files |
| Engine | the AceStream engine of **192.168.1.28**, inside its WireGuard tunnel |
| Guide (EPG) | this service carries it — `docs/acestream-suite-guide.md` |
| Plan and evidence | `odd/tasks/acestream-suite-rename.md`; the earlier plans under `odd/tasks/` keep the names they were created with |
| Files on the host | `/opt/acestream-suite/` (compose, Dockerfile, entrypoint, the HLS bridge, the app source tree, `data/`) |

## Why the engine is on another host

The engine this deployment talks to is **not** the one on this machine. 192.168.1.245 already runs
`ghcr.io/martinbjeldbak/acestream-http-proxy` (engine 3.2.11) on 6878/8621, and that engine's P2P
leaves with the house IP, untunneled. The house decided on 2026-10-06 that AceStream P2P goes through
the WireGuard tunnel, which is why the search service lives on the other VM at all (see
`odd/tasks/acestream-search.md` in the suite-aar repository).

So acestream-suite is pointed at `http://192.168.1.28:8081`, the nginx of that stack's `acestream`
container. That nginx proxies the engine's whole HTTP API over the LAN and rewrites the engine's
`http://localhost:6878/...` URLs to the address you asked on — which is why one base URL is enough
for both the API and the play link. Nothing on .28 had to change for this deployment.

## How it is built

The image is built here, from this repository's checkout. There is no release tarball and no patch
file: the application the host runs is the source in `acestream-suite/`.

- `acestream-suite/Dockerfile` takes `pyproject.toml`, `alembic.ini`, `app/` and `migrations/` from the
  checkout with `COPY`, installs that tree editable, and copies the two binary assets — the favicon and
  the wordmark — into `app/web/static/`.
- `ffmpeg` is installed in the image: the screenshots and the availability verification read the
  live stream through it.
- `python:3.12-slim` as the base, the application state in `/data` (`ACESTREAM_SUITE_DATA_DIR`) and
  port 8000 inside the container.

The deployment files (`acestream-suite/Dockerfile`, `docker-compose.yml`, `entrypoint.sh`,
`hls-bridge.py` and the app source tree) are versioned in this repository; they first landed in commit
`0ebf092`, which recorded the state the host was already running, so the guide merge that followed
had files to change and revert.

## The guide (EPG)

This service now carries the guide too: XMLTV sources, a channel↔guide mapping, a 72 h programme
window, a now/next cell and a grid in the UI, and `/epg.xml` plus a guide-carrying playlist. It was
built and proved in a duplicate — a second deployment of the product under the name it had then — so
this service was not the test bed, and merged here on 2026-10-14. The operator runbook — sources,
refresh, mapping, outputs and troubleshooting — is **`docs/acestream-suite-guide.md`** (+ `.es.md`);
the plan and its evidence are the files under `odd/tasks/`, which keep the names they were created
with.

## Searching the index (acesearch)

The engine on .28 is served by the `acestream` container, which also publishes an index of the
channels it knows: `GET /search.m3u?query=…&json=1` answers name, infohash, bitrate, availability and
tags for each one. *Buscar* in the top bar asks that index and shows the channels it knows and the
catalogue does not.

- **It searches by channel, not by event.** Measured 2026-11-04: the index knows nothing about
  matches or schedules; it matches the channel name, so finding who airs an event means searching
  the channel's name. (`acestream-suite-partido` does that on its own; see *The match channels, on
  air* below.)
- **Nothing is added until you mark it.** Each row carries the hash, the bitrate and the availability
  the index publishes — that availability is a historical stamp, not a live check. *Reproducir* opens
  a real engine session, which is the only thing that says the link lives.
- **Añadir los marcados** goes through the same importer as *Importar*: category or a new one,
  language, country, and the same background verification queue as any other insertion.
- **Peers have two flavours.** The *Pares* column is what *Comprobar* stored the last time for a hash
  already in the catalogue, with its date (`hace 23 d` when nobody has checked lately); *Medir* opens a
  real engine session for that row (2-3 s, one session) and answers today's count. The index publishes
  no peers at all, so neither number is a promise: one is a photograph, the other costs a session.
- **Where it searches** lives in *Ajustes* → *Búsqueda (acesearch)*: `search_url`, empty by default,
  which means the engine's own URL, because that is where the searcher is served. *Probar búsqueda*
  asks it before anything is saved, with one real query (`dazn`) and how many results it answered. It
  is a separate button from the engine's *Probar conexión* on purpose: the search is a different
  service, and an engine that answers `get_version` says nothing about whether the index is up.
- **A result whose name starts with `#`** (`#VAMOS FHD --> NEW ERA`) does reach the catalogue: the
  form sends the link first, because the importer reads any line starting with `#` as an M3U comment.
  Measured 2026-11-05: with the title first the pick was dropped in silence and the page answered
  `0 canal(es) añadidos, 0 duplicados y 0 inválidos`.

## Runbook

### First deployment (already done on 2026-10-09, kept for reproducibility)

Action, on the workstation:

```bash
cd ~/webserver
scp -r acestream-suite/Dockerfile acestream-suite/docker-compose.yml acestream-suite/entrypoint.sh \
    acestream-suite/hls-bridge.py acestream-suite/pyproject.toml acestream-suite/alembic.ini \
    acestream-suite/app acestream-suite/migrations acestream-suite/assets \
    root@192.168.1.245:/opt/acestream-suite/
```

Action, on the host:

```bash
mkdir -p /opt/acestream-suite/data && chown 1000:1000 /opt/acestream-suite/data
cd /opt/acestream-suite && docker compose build && docker compose up -d
```

Record — literal output of that date (2026-10-09), with the names of that day, before the rename:

```
#13 writing image sha256:2c93dd862e9d5afd2853a983042133696dd36c518990401ce8844f94b2f36052 done
#13 naming to docker.io/library/acelist:1.3.0 done
Container acelist  Started
acelist	Up 10 seconds (health: starting)
{"status":"ok","app":"AceList","version":"1.3.0"}
```

A deployment today names the image and the container `acestream-suite`.

The `chown` matters: Docker creates a missing bind source as a root-owned empty directory, and the
container runs as `1000:1000` (the host's `ingenieria` account), so anything else is unwritable.

### Checks that are worth running

Action:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: 192.168.1.245:8083" http://127.0.0.1:8083/
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: evil.example.com" http://127.0.0.1:8083/
curl -s -o /dev/null -w "%{http_code}\n" -H "Origin: http://evil.example.com" \
  --data-urlencode "links=acestream://6d325cda6ed591647ef5e91edd043147bce8be30" \
  http://127.0.0.1:8083/import
```

Record:

```
200
400
403
```

The first is the app answering the LAN address; the second and third prove the Host check and the CSRF
check are still doing their job, which is why the app reads the real addresses from
`ACESTREAM_SUITE_ALLOWED_HOSTS` instead of switching them off.

### Operating it

- **Add channels**: *Importar* accepts one `acestream://<infohash>` link per line, with an optional
  category, language and country. Names can be edited afterwards.
- **Check availability**: *Comprobar* starts a live engine session and records peers and status. It is
  a snapshot, not a promise: a channel can be `alive` and still deliver nothing.
- **Screenshot**: captures a frame from the live stream with ffmpeg (about 30 s, and it needs the
  channel to actually emit).
- **Play**: the button starts a session and returns the engine link. Paste it into VLC or mpv
  (`Ctrl+N` in VLC). It is a live MPEG-TS stream, so a browser will only offer to download it.
- **Engine settings**: engine URL, timeouts, concurrency, ffmpeg and VLC paths — and the search URL —
  live in *Ajustes*; a saved value beats the environment, so the settings page can override the
  compose file.

## Limits and known tolls

- **No authentication.** Anyone on the LAN can browse the catalogue and start streams. Streams leave
  the house only through the .28 tunnel, but they do consume P2P bandwidth. Never port-forward 8083.
- **No browser playback.** It was attempted with `acexy` and removed the same day: the engine's HLS
  entry point (`/ace/manifest.m3u8`) redirects to `/ace/m/<infohash>/<token>.m3u8`, and that URL never
  sends a single header — 30 s and 45 s budgets, three channels, and also probed from inside the
  engine's own network namespace. `acexy`'s only browser-playable mode is exactly that call. Putting a
  picture in the browser needs another engine build or a bridge that re-packages the MPEG-TS: a
  separate task.
- **One player per session.** The engine answers a second consumer of the same playback token with
  `500`. Pressing *Play* again issues a new session, which is the way to reopen a stream.
- **No backup.** `/opt/acestream-suite/data` holds the only copy of the catalogue
  (`acestream-suite.db`) and the screenshots. No cron touches it yet.
- **The engine's behaviour is not upstream's.** acestream-suite targets the Windows 3.1.74 engine;
  this deployment runs the Linux 3.2.11 build, which only streams with `infohash=` and answers
  `failed to load content` to `id=`. That difference is why `ACESTREAM_SUITE_STREAM_PARAM=infohash`
  exists.
- **A stalled channel looks healthy in the catalogue.** With peers but no data the engine sits in
  `prebuf` and the play link yields nothing. The CNN Turk entry used during testing was exactly that.
- **Updating is manual and deliberate**: change the source under `acestream-suite/`, re-run the tests
  (`cd acestream-suite && python -m pytest`), rebuild (`docker compose build acestream-suite &&
  docker compose up -d acestream-suite`, which deliberately leaves the `acestream-suite-hls` bridge
  alone).

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| The page answers `400 Invalid host header` | The address you typed is not in `ACESTREAM_SUITE_ALLOWED_HOSTS` | Add it in `docker-compose.yml` and recreate the container |
| Saving a form answers `403 cross-site request rejected` | Same list, used by the CSRF origin check | Same as above; do not disable the check |
| `El engine de Ace Stream no responde` | .28 is down, or Gluetun is not up, or `ACESTREAM_SUITE_ENGINE_URL` is wrong | `curl http://192.168.1.28:8081/webui/api/service?method=get_version` from .245; it must answer `3.2.11` |
| `El engine no pudo cargar este Content ID` | The engine rejected the stream request — usually a wrong parameter name or a dead link | Check `ACESTREAM_SUITE_STREAM_PARAM` (must be `infohash` for this engine) and try the infohash in `/search` |
| The play link never plays | The channel has peers but no data (`prebuf`) | Try another channel; the catalogue's availability is a snapshot |
| The play link plays once and then fails | The engine allows one player per session | Press *Play* again for a fresh session |
| The screenshot times out | ffmpeg waits for a stream that never delivers | Same as above; the screenshot follows the same stream |

## The permanent playlist, and Dispatcharr

    http://192.168.1.245/varios/acestream-suite.m3u      # the link, never changes
    /usr/local/bin/acestream-suite-playlist              # the script that keeps it current
    /etc/cron.d/acestream-suite-playlist                 # every 15 minutes, at :05 :20 :35 :50
    /var/log/acestream-suite-playlist.log                # what each run did

`scripts/acestream-suite-playlist.py` asks acestream-suite for its own export (`GET
/export?status=alive`), builds **five** playlists and writes them into `/srv/www/varios/`, which the
Caddy on this host already serves over **plain HTTP** — that is why the link needs no Caddy change
and no certificate, and why a phone, a TV box or a container can load it. A sixth file,
`acestream-suite-guide.m3u`, is written by `scripts/acestream-suite-guide-playlist.py`; it is the
same catalogue plus the guide. Both scripts write only when the content differs, so an unchanged
playlist keeps its mtime; both take their own `flock`; and both carry a `--self-test` (run in the
deployment) plus `--dry-run` and `--output`. Both scripts also write an exact copy of their list
under the name it had before the rename, so nothing that reads the old paths breaks.

Decisions inside the file, all of them load-bearing:

- **The stream URLs go through the HLS bridge, not to the engine**:
  `http://192.168.1.245:8084/<hash>/index.m3u8`, the same `acestream-suite-hls` service the TV uses.
  The engine serves **one reader per content**, so a list that calls
  `http://192.168.1.28:8081/ace/getstream?infohash=<hash>` directly competes with the bridge for the
  same channel and both lose it (measured 2026-10-18: 0 manifests and 23 failures in a minute).
  `direct.m3u` is the one exception: it is the engine's own MPEG-TS, kept for Dispatcharr (which
  does not proxy HLS) and for diagnosis.
- **`group-title` carries acestream-suite's category** (`Movistar`, `Deportes ES`, `Deportes int.`),
  which is what turns into a channel group in Dispatcharr.
- **Attributes are sanitised, the display name is not**: a comma inside `tvg-name` would look like the
  separator between attributes and name to any parser that splits on the first comma, so it is
  removed there — while the name the player shows keeps it.
- **The display name ends with the channel's quality** (`Canal 1080p`), because that is what a player's
  search sees; `tvg-name` keeps the bare name, since that is what guide matching reads and a
  re-detected screenshot must not re-map the guide. `other` means "not classified", not a quality, so
  it is not appended.
- **An empty catalog still produces a valid playlist**, not an empty file.

**Dispatcharr** (`http://192.168.1.28:9191`) has it as an M3U account. Record — literal output of
that date (2026-11-05), with the names of that day:

```
account 4  AceList (192.168.1.245)  http://192.168.1.245/varios/acelist.m3u
           refresh every 6 h · active · profiles: 1 (default)
           refresh log: 152 streams created, 0 updated, 0 stale, 0 removed in 0.3 s
           new channel groups: Movistar, Deportes ES, Deportes int. (all enabled)
```

The account's own label and URL are the ones it had before the rename: it is still pointed at the
pre-rename copy, which holds the same content as `acestream-suite.m3u`.

What this does **not** do yet: create Dispatcharr *channels* from those 152 streams. Streams are the
source; channels are the lineup that Dispatcharr's own player and the HDHomeRun endpoint expose to
Jellyfin's Live TV. That is one bulk call (`/api/channels/channels/from-stream/bulk/`) and a decision
about the shared lineup, so it was left for the owner. See `odd/tasks/`.

## Update — the catalogue grows to the whole engine (2026-10-09)

acestream-suite no longer holds only the sports and Movistar selection. The engine's own search was
paged through its whole index (`/search?page=N&page_size=200`, 8 pages) and the 1,499 channels that
were neither already in the catalogue nor tagged `erotic_18_plus` were imported as an
acestream-suite export document, so each one kept its own category, language and country. The
catalogue is now **1,651 channels in 19 categories** (`TV` 600, `Otros` 251, `Películas` 169,
`Regionales` 152, `Amateur` 77, `Deportes ES` 22, …, plus `Movistar` 13 and `Deportes int.` 117 from
the first import).

Two consequences worth knowing:

- The import queues a **live check per channel**, screenshots included: measured at ~2.3
  channels/minute, the backlog is about **11 hours** at the default concurrency of 2, two engine
  sessions at a time. The UI is unaffected. A container restart drops the part of the queue that
  has not run yet — the *Comprobar* button on the list starts it again for whatever the filter
  shows. `check_concurrency`, `check_timeout` and `screenshot_timeout` are saved settings, so it
  can be made to finish sooner without touching the image or the compose file.
- The playlist (`http://192.168.1.245/varios/acestream-suite.m3u`) and Dispatcharr followed by
  themselves: 1,651 channels now, and account 4 reported `1,499 streams created, 0 updated, 0
  marked stale, 0 removed in 3.2 seconds`, in the same 19 groups.

The adult channels the engine itself tags (`erotic_18_plus`, 63 of them) were deliberately left out
of a household catalogue; adding them is one line in the harvest script.

### The six lists

| Link | Channels | What it holds |
|---|---|---|
| `http://192.168.1.245/varios/acestream-suite.m3u` | 540 | everything; this is the one Dispatcharr consumes, through the pre-rename copy it is still pointed at |
| `http://192.168.1.245/varios/deportes.m3u` | 165 | the `Deportes*` categories, plus a sports name anywhere else |
| `http://192.168.1.245/varios/espana.m3u` | 71 | channels from Spain: `ES` as its country, the Spanish categories, or a Spanish name |
| `http://192.168.1.245/varios/hls.m3u` | 196 | sports or Spanish, as HLS through the bridge |
| `http://192.168.1.245/varios/direct.m3u` | 540 | everything, as the engine's own MPEG-TS — the only list that does not go through the bridge |
| `http://192.168.1.245/varios/acestream-suite-guide.m3u` | 540 | everything, and the only one that carries the guide: `url-tvg` on the header and the guide channel id as `tvg-id` on the 83 channels that have one (`scripts/acestream-suite-guide-playlist.py`, `docs/acestream-suite-guide.md`) |
| `http://192.168.1.245/varios/madrid.m3u` | 0-8 | only the channels airing the Real Madrid match that is on air, and nothing else; empty (header only) when there is no match (`scripts/acestream-suite-playlist.py`, next section) |

Two rules hold in every one of these files except `madrid.m3u` (which is empty most of the day), and
both scripts implement them: **one entry per visible
name** — the best declared quality wins, on a tie a sports category, on a tie the lowest content id,
so two publishers of the same channel are one row and the input order decides nothing — and **every URL
through the bridge**, except `direct.m3u`, which is the engine's own list by design.

`hls.m3u` — the one the TV reads — carries two folders of its own: **`Campeones`**, with the Movistar
Liga de Campeones channels and nothing else, and **`Real Madrid`**, which only exists while a match is
on air (next section). The engine files the Campeones channels under three different categories
(`Deportes ES`, `Deportes`, and `Amateur` for the two `M+ LIGA DE CAMPEONES … --> NEW ERA` feeds), so
that folder is decided by name (`carpeta_campeones` in the script) and only in this list; the others
keep grouping by category. Nine channels on 2026-11-04: the four `M. Liga de Campeones` feeds, the two
`elcano.top` publishes (`Campeones elcano`, 720p, and `Campeones elcano FHD`) and the three
`M+ LIGA DE CAMPEONES …`.

`deportes.m3u` and `espana.m3u` are filters over the same catalogue, and the **name** rule is what
makes them work: the engine files a re-stream of La 1 under `Amateur`, a DAZN channel under `Otros`,
and leaves the country and the language empty for most Spanish channels. Two false positives are
already paid for
and covered by the script's self-test — `beIN Movies` is not sport and `Premiere` (the Russian movie
channels) is not `Premier Sports`. **VAMOS** (Movistar's sports channel) needed the same treatment: the
engine files it under `Amateur`, with no country and without the "Movistar" prefix, so the catalogue
titles it `#VAMOS FHD --> NEW ERA` and `Vamos`; the name rules now carry it into `deportes`,
`espana` and `hls`.

`espana.m3u` is about **Spain, not about the Spanish language**: the engine tags every Latin
American channel `es` too, and letting that through would add 26 channels from Mexico, Chile or
Colombia to a list whose name says Spain. If you want them, it is one condition in the script.

### The match channels, on air

`madrid.m3u` and the `Real Madrid` folder are the same set: the channels airing a Real Madrid match
**right now**, football or basketball. Two surfaces of one fact — the folder for whoever already has
`hls.m3u` loaded on the TV, the list for whoever wants only the match — so they cannot contradict each
other, and both come from one file written by one job.

**Where the fixtures come from, and why the guide's title is not the signal.** Two keyless sources,
both measured on 2026-11-04: `eventsnext.php?id=133738` (Real Madrid CF) on thesportsdb.com for
football, with `strTimestamp` in UTC and `strLeague`; and the official EuroLeague API,
`api-live.euroleague.net/v2/competitions/E/seasons/E2026/games`, with `utcDate` and `local`/`road`.
Liga Endesa (ACB) has no keyless source that could be verified, so those matches are not added and the
job says so in its log on every run.

The guide cannot decide this, and the measurements say why: it airs
`Euroliga de baloncesto: Real Madrid - Panathinaikos` at 23:30 on `M+ Deportes` while the real tip-off
was 19:45Z — that is the **delayed** airing, and the live one is `Movistar Plus+` at 19:45 titled just
`Euroliga`, which never names the club; and it airs `LALIGA EA SPORTS: R. Racing Club - Real Madrid` at
18:00, a **replay** of the match played on 1 November. A rule that reads titles picks both wrong
programmes; the real kick-off picks both right ones.

**The live rule.** A guide programme is the live airing of a fixture when all three hold:

- its `start` falls between 20 minutes before the kick-off (`--live-tolerance-before`) and 5 minutes
after it (`--live-tolerance-after`). Asymmetric on purpose: an airing may open its pre-match show early
(the 17:38 programme for a 17:45 kick-off) but it never starts a quarter of an hour late — with a
symmetric 20 minutes the 18:00 replay slipped through the time test and only the competition keyword
rejected it;
- its channel is not the club's own (`Real Madrid TV`, `RMTV`);
- its title names the fixture's competition: Champions → `champions|liga de campeones`, LaLiga →
`laliga|la liga`, EuroLeague → `euroliga|baloncesto`, Copa del Rey → `copa`.

A replay starts at another hour and never satisfies the first condition. That table is what keeps a
programme that happens to start at the right minute from being taken for the match.

**What `scripts/acestream-suite-partido.py` does**, from cron
`/usr/local/bin/acestream-suite-partido` every 10 minutes at `:01 :11 :21 :31 :41 :51` (log
`/var/log/acestream-suite-partido.log`, its own flock):

- Reads the fixtures from the two sources on every run — a non-200 from TheSportsDB means the free key
  rotated, which is logged as "unknown" and adds nothing — and reads the guide only for the **channel
  that airs them at that hour**: a programme is the live airing of a fixture when the three conditions
  above hold together.
- **The window** runs from 60 minutes before the real kick-off (`--window-before`) to the `stop` of the
  matched programme. While the match is on air it keeps searching every 10 minutes, because publishers
  appear and others die; a match of three hours ago or a replay tomorrow falls outside it and never
  triggers.
- For each live airing, searches the engine for that channel's name (and for the catalogue title
  `/epg.json` maps to the same guide id), and keeps only the results that **are that channel**, not its
  neighbours: both names are normalised (platform prefix, publisher tail, punctuation and case dropped)
  and the result has to be equal to the target or start with it followed by something that is not a
  digit. That is what keeps `M+ LIGA DE CAMPEONES 2 FHD --> NEW ERA` out when the channel airing the
  match is `M+ Liga de Campeones`: it carries every token of the query and is the wrong channel. At most
  8 streams per term (`--max-per-channel`), none of them already in the catalogue, and they go in with
  `POST /import` as an acestream-suite export document with category `Real Madrid`, `language: es`,
  `country: ES`. The app queues its own availability checks, so a dead stream never reaches a player.
- Writes `/var/lib/acestream-suite-partido/live.json` (`--live-file`): the content ids airing the
  match now or in the hour before, each one with the `stop` of its programme — the catalogue
  channels mapped to that guide channel *and* the ones it just imported.
- `/var/lib/acestream-suite-partido/state.json` records every `(kick-off, guide id, term)` it actually
  imported, with the content ids it brought, so the 10-minute cron does not import the same match
  again. It never deletes anything, and a term whose search found nothing is deliberately not
  recorded: the next tick retries, which is the whole point of running inside the window.

**The folder and the list.** `scripts/acestream-suite-playlist.py` reads the same `live.json`
(`--live-file`) and, only while `now < stop`, gives those channels `group-title="Real Madrid"` in
`deportes.m3u` and `hls.m3u`, and carries them — and only them — in `madrid.m3u`. A missing file, an
unreadable one or an expired entry means no folder and no entry, and it is not an error. The `Real
Madrid` category still counts as sport, which is what puts those channels in the two sports lists,
but it does **not** decide the folder: decided by category, the folder would keep the channels of
every past match for ever. In `hls.m3u` `Campeones` wins when a channel is both — that folder is
older and is decided by name, and if the match won it would empty itself during every match.
**Nothing is deleted when the match ends**: the channels stay in the catalogue and in
`acestream-suite.m3u`, and only the folder and the entry in `madrid.m3u` go.

**Measured limitation: it cannot prove the picture.** The fixture sources say when the match is played,
the guide says which channel airs it at that hour, the engine says which streams that channel has, and
the app's check says the stream is alive. None of them proves what is on screen. What can be said is
that the channel airs the match at that hour, that the stream is alive (the check opens a real session
with data and peers) and that it is not the club's own channel; the frame on the TV is the owner's eyes.
The rule is the one in `suite-aar/docs/acestream-manual.md`: acesearch does not search for an event, it
searches for the channel that airs it.

To see what it would do right now, without importing and without writing:
`/usr/local/bin/acestream-suite-partido --dry-run`.

### Who reads which list

There was no way to know which of the six files a player actually asks for, so on 2026-10-25 Caddy
started logging it. `/etc/caddy/Caddyfile`, in two places, each block immediately after its own site
header: `@varios_http` right after `(ip_access) {` (the snippet the `http://192.168.1.245` site
imports), writing `/var/log/caddy/varios.log`; and `@varios_https` right after the
`https://192.168.1.245 {` line, writing `/var/log/caddy/varios-tls.log`:

```caddyfile
@varios_http path /varios/*
log @varios_http {
    output file /var/log/caddy/varios.log {
        roll_size 10MiB
        roll_keep 3
    }
}
```

The HTTPS block is the same with `@varios_https` and `varios-tls.log`. `log` is a **site-scope
directive, not an HTTP handler**: inside `handle_path` Caddy rejects it (`directive 'log' is not an
ordered HTTP handler`), and inside `route` it is rejected too; it needs site scope plus a matcher. Two
matcher names are needed because Caddy refused to reuse one.

**Where the block goes is the whole trick.** A first attempt inserted both blocks by matching anchor
text, hit `(ip_access) {` twice and left both matchers inside the snippet: every HTTP request was then
written to both files and HTTPS requests were not logged at all. It was caught by making one HTTP and
one HTTPS request and counting lines, and fixed by placing each block right after its own site header.
Insertion by anchor text hit the snippet twice; anchor on the site header.

Verified after `systemctl restart caddy` with the live file: one plain-HTTP request appears **once**, in
`varios.log`, under logger `http.log.access.@varios_http`; one HTTPS request appears **once**, in
`varios-tls.log`, under `http.log.access.@varios_https`. No request is logged twice, and every line
carries `request.uri` (the whole `/varios/<file>.m3u`), `request.remote_ip`, `status` and `size` — so
within a day the question "which of the six lists does the house read" is answerable. So far the files
hold the test requests and nothing else.

`caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile` answered *Valid configuration*
and `systemctl reload caddy` took it; `/`, `/varios/`, `:3010/` and `/varios/acestream-suite.m3u`
still answer 200, and the previous file is kept at `/etc/caddy/Caddyfile.bak-20261025-varios-log`.

**Never empty these files with `: > file` or `truncate`.** Caddy keeps the file descriptor open across
`systemctl reload`, so the next line is written at the old offset and the file fills with NUL bytes
before it. To start a clean file, remove it and `systemctl restart caddy` — a reload does not reopen
it. Normal size control is Caddy's own `roll_size 10MiB` / `roll_keep 3`.

### Only online channels

All the lists except `madrid.m3u` are built from `GET /export?status=alive`, so **a dead link never
reaches a player**; `madrid.m3u` is the live set of the match (previous section) instead. The app's
`alive` is stricter than "has peers": it means the engine reached `dl` with data
downloaded *and* at least one peer, so a channel that only announces itself is not in the list.

Two consequences worth knowing: the lists **grow as the pending checks finish** (about half the
catalogue was checked when this was written), and a channel that later stops delivering **disappears
from the playlists on the next 15-minute run**. Dispatcharr marks those streams stale on its next
refresh — that is what the general playlist being the one it consumes is for. Setting
`ACESTREAM_SUITE_PLAYLIST_STATUS=` to an empty value publishes the whole catalogue in those lists
again, and the guide list takes `--status ""`.


### The dead channels are removed once the verification finishes

A one-shot job (`/usr/local/bin/acestream-suite-prune`, from cron every 20 minutes) waits until
every channel in the catalogue has been checked, then removes the ones whose latest check is not
`alive` — the offline and the peer-less — and runs `acestream-suite-playlist`, which rewrites all
five. It never touches a channel that has not been checked: "not verified yet" is not "offline".

Before deleting anything it writes `/backup/acestream-suite/<stamp>/` with **the three playlists it copies**
(`acestream-suite.m3u`, `deportes.m3u`, `espana.m3u` — `hls.m3u`, `direct.m3u` and
`acestream-suite-guide.m3u` are not in that backup), the full catalogue export (`catalogo.json`,
which acestream-suite's own *Importar* accepts, so the deletion is reversible), a consistent copy of
the database and the list of what it deleted. The channels go through acestream-suite's own delete,
so each one takes its check history and its screenshot.

The job marks itself done in `/var/lib/acestream-suite-prune/done` and its log is
`/var/log/acestream-suite-prune.log`. To let it run again after importing more channels:
`acestream-suite-prune --undo-guard`. To see what it would do without waiting:
`acestream-suite-prune --dry-run`.

**Measured on 2026-10-18: the guard makes the cron a no-op for good, not just after a first pass.**
The marker was written on 2026-10-09; by 18/10 the catalogue had accumulated **35 channels whose last
check was `no_peers`** and none of them would ever be removed. They were deleted with
`acestream-suite-prune --undo-guard --force --dry-run` and then without `--dry-run`: backup in
`/backup/acestream-suite/20261018-221301`, 547 channels left, all `alive`, and the job wrote the guard again
(`35 deleted`). So the cron keeps being a no-op until someone runs `--undo-guard` again: pruning is a
manual decision, not a continuous one.


### Dispatcharr follows the playlist on its own

Dispatcharr reads the pre-rename copy of `acestream-suite.m3u`, so every time that file shrinks
(a channel that stops streaming leaves the playlist on the next run) the next refresh of the account
marks the missing streams **stale** instead of deleting them, and the account's `stale_stream_days`
(7) is what eventually removes them. After the first cleanup the account reported `1072 marked
stale, 0 removed` out of 579 processed, and the rows agree: `is_stale` is `True` for those 1,072 and
`False` for the live ones.

Its refresh interval is 6 hours. To pull it forward by hand:

```bash
TOKEN=$(curl -s -X POST http://192.168.1.28:9191/api/accounts/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"alberto","password":"ingenieria"}' | jq -r .access)
curl -s -X POST http://192.168.1.28:9191/api/m3u/refresh/4/ -H "Authorization: Bearer $TOKEN"
```


### Reproducir en la TV (Tessel, Samsung)

The TV app is **Tessel** (`PatrickSt1991/tessel-tizen-tv`, formerly VLC TV): a Tizen web app on
Samsung's AVPlay that plays HLS/DASH/RTSP and loads M3U lists from a URL. There are two ways to feed
it, and comparing them on the same channel is the test that matters:

| List | What the TV gets |
|---|---|
| `http://192.168.1.245/varios/hls.m3u` | sports and Spanish channels (196) as an **HLS manifest** from the bridge (`:8084`) |
| `http://192.168.1.245/varios/deportes.m3u` (165), `.../espana.m3u` (71), `.../acestream-suite.m3u` (540), `.../acestream-suite-guide.m3u` (540) | the same catalogue, or a filter of it, also as an **HLS manifest** from the bridge; the last of them announces the guide |
| `http://192.168.1.245/varios/direct.m3u` (540) | the engine's **MPEG-TS** directly — the only list that does not go through the bridge |

The HLS bridge (`acestream-suite/hls-bridge.py`, service `acestream-suite-hls`) exists because
neither the engine (`/ace/manifest.m3u8` never answers in 3.2.11) nor Dispatcharr 0.31.0 (its
`hls_proxy` module is in the code but not routed) can serve HLS. It remuxes with `-c copy` — no
re-encode — into 2 s segments (the first one 1 s, so a channel change waits less). The buffer is on
disk and the window is short: 240 segments (8 min) in the tmpfs, of which the player is served the
last 30, ending 20 segments (40 s) behind live, so a dip of the peers is absorbed instead of shown.
One ffmpeg per channel, closes a channel 45 s after the last request and allows 4 at a time; when
the tmpfs drops below 256 MB free the reaper closes the session holding the most bytes, because a
full tmpfs does not break one session, it breaks them all. It logs a per-session summary every 30 s
(manifests, segments, failures, media sequence, client) and every 404/504 as it happens, which is
how you tell a stuck player from a stuck pipeline. Measured: manifest in 2.5 s cold and 0.05 s warm,
21 of 21 segments ready when the player asked for them, 0.2-0.9 % CPU and 51 MB of RAM.

**Since 2026-10-15 the bridge is built around one measured engine fact: it serves one stream per
content.** While it holds that stream, any other reader gets a URL that delivers nothing (the same
ffmpeg that reads fine when the stream is free answers `Invalid data found when processing input`), and
the holder keeps it about ten minutes after its consumer disappears (measured: busy at 20:07, busy at
20:15, free at 20:19). That is what left the TV on "cargando contenido": the bridge's ffmpeg died four
times in ten minutes on 2026-10-15 — every one of them a clean exit, code 0 — the old code threw the
session away and deleted its directory, and every request in the following minutes could only wait for
the engine to let go. Those clean exits were explained the same evening on the `.28` side, and they were
never this service's to fix: the engine ran with its live cache in the wrong unit (`256` bytes, not
256 MB), so it served a short chunk and closed the response; nginx on that side spooled the live stream
to a temp file and had 60 s timeouts as well. See `suite-aar/docs/acestream.md`,
"Reproducción sin cortes". What the bridge does now: the session survives a dead ffmpeg and is retried
in place, in the same directory, as long as anyone asks for the manifest; the pump bounds a motor
that accepts and sends nothing (`urlopen(timeout=60)` per read, and ffmpeg's input is closed after
three attempts without data), and the ffmpeg carries **no** `-reconnect` family — they were
removed on 2026-10-18 after measuring what they do when the engine refuses a stream: they leave
ffmpeg looping inside the same process, so the bridge never notices, never releases and never retries
(**13 027 requests in a day** on one stuck channel). Now ffmpeg exits, the session is retried with a
backoff of 2, 4, 8, 16 and 30 s behind consecutive failures, and a refused channel gets an honest
`504` instead of a frozen playlist; the engine session is released with
`?method=stop` when the bridge closes a session (the reaper after 45 s of silence, or the cap of 4), so
the channel is free for the next player at once; and a 504 waits through those retries up to
`HLS_STARTUP_TOTAL_SECONDS` (24 s) instead of returning 1.5 s after the first failure. Verified on
2026-10-15 on a channel nobody was watching: manifest `200` cold, first segment 2.1 MB and decodable on
its own, manifest `200` in 2 ms right after killing the ffmpeg (the session is not lost), and after
48 s of silence the reaper released the engine session (`ok`) and the next manifest request answered
`200` in 4.2 s. `python3 hls-bridge.py --self-test` checks those decisions offline, with a fake engine
and a fake ffmpeg: 42 assertions.

**Reviewed again on 2026-11-04, with the TV stuck on «cargando contenido» for a reason that was not
the bridge**: `192.168.1.28` was off the network (`arp` says `INCOMPLETE`, nothing answers on 8081),
so every manifest request ended in the honest `504` the bridge serves while it retries. That review
did find four defects of its own, all fixed here: a closing session deleted the directory of a newer
session of the same channel (the player lost the channel mid-stream), `session_for` and the reaper
held the global lock across the two blocking calls of a close (up to 10 s of stall for every request,
the TV's manifest polls included), a 404/504 from the player incremented the backoff counter that
belongs to the engine retries, and `release_engine` fell back to the default pid-less token, which is
the session every client of the house shares — a `stop` there would have killed another reader's
stream, so it now refuses to send it. The tmpfs grew a guard (close the session holding the most
bytes when free space is under `HLS_MIN_FREE_MB`, 256 MB) and a cleanup of whatever a dead process
left behind at startup.

The engine, meanwhile, runs with the buffer flags in `acestream-suite/../acestream/start-engine`
(`--use-internal-buffering 1`, `--live-mem-cache-size 256`, `--max-connections 500`, `--max-peers 50`).
The measurement did not show a difference (the server side was already delivering 45 s with zero
gaps at 1.2x the channel rate, and 5.5-7.2x the channel bitrate from the engine), so those flags are
headroom for peer dips rather than a fix for a measured fault.
