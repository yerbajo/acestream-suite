# acestream-suite

**English** · [Español](README.es.md)

<p align="center"><img src="assets/logo-3d.png" alt="acestream-suite" width="560"></p>

A self-hosted web front end for an **AceStream** engine. It keeps a catalogue of `acestream://`
links — channels with their Content ID, an availability check against the engine, a captured frame
of each one, categories, languages and countries — and adds four things around that catalogue:

- **a guide (EPG) layer**: XMLTV sources, a channel↔guide mapping, a stored programme window, an
  XMLTV document at `/epg.xml` and an M3U playlist that carries the guide;
- **a search page** over the engine's own index (`acesearch`), so the channels the index knows and
  the catalogue does not can be found and imported;
- **an HLS bridge** that turns one engine stream into a manifest several players in the house can
  watch at the same time;
- **scripts** that publish the catalogue as playlists for the players that want a URL instead of a
  web page (VLC, Kodi, Dispatcharr/Jellyfin Live TV, a smart TV).

`acestream-suite` is this product. **AceStream** is the engine it plays through: the engine is
third-party software and keeps its own name.

> **No authentication.** The app is meant for a private network. Anyone who can reach the port can
> browse the catalogue and start streams. Do not publish it to the internet.

## Quickstart (Docker)

Requirements: Docker with Compose v2, and an AceStream engine reachable over HTTP from the
container (this suite does not start one).

```bash
git clone https://github.com/yerbajo/acestream-suite.git
cd acestream-suite
cp .env.example .env
$EDITOR .env                      # ACESTREAM_SUITE_ENGINE_URL, ACESTREAM_SUITE_ALLOWED_HOSTS
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
curl -fsS http://localhost:8080/health
```

Then open **`http://<host>:8080`** — `ACESTREAM_SUITE_PORT` in `.env` is the host port (`8080` unless
you change it; the app always listens on 8000 inside the container). The HLS bridge publishes **8084**.

Two details that will bite if skipped:

- **`data/` must exist and be writable by uid 1000.** Docker creates a missing bind source as a
  root-owned empty directory and the app runs as `"1000:1000"`, so it could not write its database.
- **The deployment values come from `.env`, not from the compose file.** `docker-compose.yml`
  interpolates every value a deployment differs on (`${ACESTREAM_SUITE_ENGINE_URL:-…}` and friends),
  so `cp .env.example .env` plus an edit is enough and the compose file itself never has to be
  touched. Anything you leave out of `.env` falls back to the safe default written in the compose.
  The engine URL, its timeouts, the search URL and the check concurrency are also *saved settings*
  in the app's **Ajustes** page, and a saved value wins over the environment — so a wrong `.env` is
  fixable from the UI without a rebuild.

## The image is built locally, on purpose

`docker compose up -d --build` **builds** the image; nothing is pulled. The upstream base — the
**AceList** project — publishes no image at all (its only supported way to run is the Windows
executable), so there is nothing to pull.

The build takes the upstream release **tarball**, not a git clone and not a moving tag: the
`Dockerfile` pins the release by version **and** by sha256 (`ACELIST_VERSION=1.3.0`,
`ACELIST_SHA256=487bee2f…72d879`), verifies the download against it and only then applies this
project's three patches. `ffmpeg` is installed in the image because the screenshots and the
availability check read the live stream through it.

Those two `ACELIST_*` build arguments are the *upstream base's* name, not this product's, and they
stay in step with upstream's release tags on purpose: a version bump means bumping them and
re-reading all three patches against the new source.

Updating:

```bash
git pull
docker compose build acestream-suite && docker compose up -d acestream-suite
```

Naming the service matters: the same compose file also holds the `acestream-suite-hls` bridge, and a
bare `docker compose up -d` would recreate it too.

## Configuration

Every variable is read by the app from its environment. The primary names are `ACESTREAM_SUITE_*`;
the old `ACELIST_*` names are still read as a fallback for one version, so a deployment that has not
been renamed yet keeps working (the new name wins when both are set).

| Variable | Default | What it does |
|---|---|---|
| `ACESTREAM_SUITE_ENGINE_URL` | `http://127.0.0.1:6878` | Base URL of the engine's HTTP API. Also a saved setting. |
| `ACESTREAM_SUITE_ENGINE_TIMEOUT` | `15` | Seconds to wait for the engine before a call is failed. |
| `ACESTREAM_SUITE_SEARCH_URL` | empty = the engine URL | Base URL of `acesearch`, the index the search page reads (`/search.m3u`). Also a saved setting. |
| `ACESTREAM_SUITE_ALLOWED_HOSTS` | empty | Comma-separated `host` / `host:port` values accepted in the `Host` header and in the `Origin` of a write. Without the address you browse by, every request is a `400` and every write a `403`. |
| `ACESTREAM_SUITE_PLAYBACK_MODE` | `vlc` | `vlc` starts the stream and opens a local player (the desktop behaviour). `url` starts the stream and hands the link to the page — the mode a container needs, since it has no player. |
| `ACESTREAM_SUITE_STREAM_PARAM` | `id` | Query parameter that starts a stream on the engine: `id` for the Windows 3.1.x build, `infohash` for the Linux 3.2.x build (which answers `failed to load content` to `id`). |
| `ACESTREAM_SUITE_CHECK_CONCURRENCY` | `2` | Availability checks at once. Each one is a live engine session, so this is also the load on the engine. |
| `ACESTREAM_SUITE_SCREENSHOT_TIMEOUT` | `30` | Seconds allowed for the ffmpeg frame capture. |
| `ACESTREAM_SUITE_FFMPEG_PATH` | empty = `ffmpeg` on `PATH` | The binary used for screenshots. |
| `ACESTREAM_SUITE_VLC_PATH` | empty = usual folders, then `PATH` | Only read in `vlc` playback mode. |
| `ACESTREAM_SUITE_DATA_DIR` | `/data` in the image, the OS app-data directory elsewhere | Where `acelist.db` and `screenshots/` live. |
| `ACESTREAM_SUITE_PORT` | `8080` | Port published on the host. The app always listens on `8000` inside the container. |
| `ACESTREAM_SUITE_EPG_SOURCES` | `[]` | XMLTV sources, as a JSON list of URLs. Empty is a supported state: the catalogue works without a guide. |
| `ACESTREAM_SUITE_EPG_WINDOW_HOURS` | `72` | How far ahead of *now* the guide is kept; older programmes are pruned as each refresh runs. |
| `ACESTREAM_SUITE_EPG_TIMEOUT_SECONDS` | `120` | Per-source timeout of the refresh. A source that never answers costs one source, not the run. |
| `ACESTREAM_SUITE_EPG_MAX_MB` | `64` | A source larger than this is refused instead of filling the container's memory. |
| `ACESTREAM_SUITE_EPG_GRACE_SECONDS` | `300` | Extra seconds a programme that just ended is kept, for players whose clock lags. |
| `ACESTREAM_SUITE_EPG_ACTOR` | empty = the OS user | Name recorded as the author of a manual channel↔guide decision. |

**Which of these `.env` can set is decided by the compose file.** It interpolates `ENGINE_URL`,
`ALLOWED_HOSTS`, `PLAYBACK_MODE`, `STREAM_PARAM`, `CHECK_CONCURRENCY`, `PORT`, `EPG_SOURCES`,
`EPG_WINDOW_HOURS`, `EPG_REFRESH_HOURS` and the bridge's `HLS_ALIASES` — exactly the names
`.env.example` ships. The rest are read by the app but not passed by the compose: they are either
saved settings in **Ajustes** (engine timeout, search URL, check timeout, minimum peers, screenshot
timeout, ffmpeg path, VLC path) or values you add to `environment:` in the compose yourself (data
directory, the guide's timeouts and size cap, the actor name).

Two notes:

- One thing the compose file declares and **no code reads** is `ACESTREAM_SUITE_EPG_REFRESH_HOURS`.
  It is there so the refresh interval is decided in the deployment; the schedule itself lives in
  `scripts/acestream-suite-guide-refresh.cron`, which has to be kept in step with it by hand.
- The database file keeps the name `acelist.db`. It is inside the data directory the app is pointed
  at; the name is not configuration and does not follow the product rename.

## Data and backups

The whole application state is one directory, mounted at `/data` and bound to `./data` in the
compose file:

```
data/
  acelist.db      SQLite: catalogue, check history, guide tables, settings
  screenshots/    one captured frame per channel
```

To back it up, copy the directory while the app is not writing to it:

```bash
docker compose stop acestream-suite
tar czf "acestream-suite-data-$(date +%F).tar.gz" data
docker compose start acestream-suite
```

A copy taken from a running container can catch the SQLite file mid-write. The
`acestream-suite-prune` script (below) takes exactly this kind of copy before it deletes anything,
together with a re-importable export of the whole catalogue.

To restore: stop the app, replace `data/`, start it. The entrypoint applies the database migrations
on the way up, so a backup from an older version is upgraded rather than rejected.

## The three patches

The app is not forked: upstream stays upstream. Everything this project changes is a diff applied at
build time, in this order, each read against the same pinned tarball.

| Patch | What it adds |
|---|---|
| `acestream-suite-lan.patch` | LAN reachability and playback: the host allow-list used by both the `Host` and the CSRF-origin check, the `url` playback mode that works on a host with no player, and the engine's `infohash` dialect. |
| `acestream-suite-guide.patch` | The guide: XMLTV tables and migrations, the refresh command, `/epg.xml` and `/epg.json`, the now/next cell and the grid, and the channel↔guide mapping screens. |
| `acestream-suite-search.patch` | The search page over `acesearch` and its setting — plus the `ACESTREAM_SUITE_*` names with the `ACELIST_*` fallback. |

`build-patch.sh` regenerates them. It builds the trees the diffs are taken between — `$WORK/base`
(the pinned tarball plus the LAN patch), `$WORK/epg` (you edit here), `$WORK/search` — reads the
version and the sha256 from the `Dockerfile`, so the pin lives in one place, and refuses to overwrite
a tree you are in the middle of editing:

```bash
./build-patch.sh --help                    # the options, and the reasoning, in full
./build-patch.sh --prepare                 # build/refresh the trees (default)
./build-patch.sh --generate                # rewrite the guide patch from base vs epg
./build-patch.sh --generate-search         # rewrite the search patch from epg vs search
```

It needs `patch`, `curl` and `sha256sum`, and `--tarball FILE` reuses a tarball you already have
(the checksum is still verified).

## The HLS bridge

Engines in the 3.2.x line do not serve an HLS manifest, and several clients (smart TVs, Dispatcharr,
Jellyfin Live TV) want one. `acestream-suite-hls` — the second service in the same compose file, from
the same image — remuxes the engine's MPEG-TS, without re-encoding, into 2-second segments and
serves:

```
http://<host>:8084/<infohash>/index.m3u8
```

One `ffmpeg` per channel, at most four sessions, a session closed 45 seconds after the last request,
the segment window in a 2 GB tmpfs, and the engine session released when the bridge lets a channel
go. When free space in the tmpfs drops below `HLS_MIN_FREE_MB` it closes the session holding the most
bytes: a full tmpfs breaks every session at once, not just one. `python3 hls-bridge.py --self-test`
checks those decisions offline, with a fake engine and a fake ffmpeg.

The bridge is a workaround for a measured engine limitation, not a preference: the engine serves one
reader per content, so pointing several players straight at it makes them compete for the same
channel.

## The house scripts (optional)

Nothing in `scripts/` is needed to run the app. They are the publishing layer the author uses, they
expect the paths at the top of each file, and they default to that deployment's own hosts
(`http://127.0.0.1:8083` for the app, `/srv/www/varios` for the playlists). Every path and URL is a
flag and, for the ones a host moves, an environment variable — read the header of the script you are
about to install.

| Script | What it writes |
|---|---|
| `acestream-suite-playlist.py` | Six M3U files in the web directory: `acestream-suite.m3u` (everything), `deportes.m3u`, `espana.m3u`, `hls.m3u` (with a `Campeones` folder), `direct.m3u` (the engine's own MPEG-TS) and `madrid.m3u` (the Real Madrid match on air, empty otherwise). Only channels the app has verified as alive, one entry per display name, every URL through the HLS bridge except `direct.m3u`. Writes only when the content changed. |
| `acestream-suite-guide-playlist.py` | `acestream-suite-guide.m3u`: the same catalogue with `url-tvg` on the header and the guide channel id as `tvg-id`, for the channels that have one. |
| `acestream-suite-partido.py` | Imports the channels airing a Real Madrid match, live, into the catalogue (category `Real Madrid`); writes the live set to `/var/lib/acestream-suite-partido/live.json` and its own state to `state.json`, read by the playlist script. |
| `acestream-suite-prune.py` | A one-shot cleanup: waits until every channel has been checked, backs up to `/backup/…/<stamp>/` (playlists, a re-importable catalogue export, a copy of the database, the deletion list), then deletes the channels whose last check was not alive, through the app's own API. Marks itself done; `--undo-guard` lets it run again. |
| `crons-install.sh` | Root's crontab on the target host, generated from `scripts/*.cron`. `--dry-run` prints, `--diff` compares against the live crontab, `--install` backs up and writes. Its `JOBS` array also carries jobs from other stacks — read it before installing. |

All of them take `--dry-run`; the two playlist scripts also take `--self-test`, which runs their
checks with no network and no files. The playlist jobs need a web server serving that directory;
`acestream-suite-guide-refresh.cron` is the guide refresh, and it runs it inside the container
(`docker exec acestream-suite python3 -m app.cli epg-refresh`).

## Documentation

- **`docs/acestream-suite.md`** — deployment and operation: how the image is built, the runbook, the
  playlists and who reads them, the bridge, troubleshooting, known limits.
- **`docs/acestream-suite-guide.md`** — the guide/EPG layer: sources, the refresh, the mapping, the
  outputs and what breaks them.
- Each has a complete Spanish mirror next to it: `docs/acestream-suite.es.md` and
  `docs/acestream-suite-guide.es.md`.

## License and third-party notices

This repository's own work — the deployment files, the three patches, the scripts, the documentation
and the logo — is MIT-licensed; see `LICENSE`. The upstream base the image is built from is **not**
redistributed here: the `Dockerfile` downloads it at build time, and it carries no license of its
own. Third-party notices, including the origin of the AceStream mark inside the logo and the
licenses of the software the image installs, are in `NOTICE.md`.

## How this is deployed here

The runbooks in `docs/` describe the author's own home deployment: one LAN host, a container next to
a WireGuard-tunnelled engine on another machine, a reverse proxy serving a directory of playlists,
and cron jobs on the host. Read them as a worked example with the measurements that back it, not as
the only way to run this — the compose file is the interface, and the environment variables above are
the knobs.
