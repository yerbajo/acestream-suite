# acestream-suite — the guide (EPG)

The guide is a feature of the **acestream-suite service** (`http://192.168.1.245:8083`), not a second
deployment: several XMLTV sources, a channel↔guide mapping, a stored programme schedule, a now/next
cell and a grid in the app's own UI, and an XMLTV document plus an M3U that carries `tvg-id`/`url-tvg`
so Dispatcharr and Jellyfin can show what is on.

> **The product is `acestream-suite`**, and the guide is one of its features. The application's source
> is part of this repository (`acestream-suite/`), so no release is downloaded and no patch files are
> applied; the list names, the `ACESTREAM_SUITE_*` variables and the
> `/usr/local/bin/acestream-suite-*` binaries carry that name, and the names of the product before the
> rename no longer exist. The AceStream engine keeps its own name.

| | |
|---|---|
| URL | `http://192.168.1.245:8083` (LAN only, no authentication) |
| Host | `dev.albertucho.home` — 192.168.1.245, RHEL 8.7 |
| Container | `acestream-suite`, compose project `acestream-suite`, image `acestream-suite:1.3.0` |
| Built from | this repository: `acestream-suite/Dockerfile` copies `app/`, `migrations/`, `pyproject.toml` and `alembic.ini` from the checkout, installs that tree and adds `ffmpeg` |
| Engine | the AceStream engine of **192.168.1.28**, inside its WireGuard tunnel |
| Plan and evidence | `odd/tasks/acestream-suite-rename.md`; the earlier plans under `odd/tasks/` keep the names they were created with |
| Files on the host | `/opt/acestream-suite/` (compose, Dockerfile, entrypoint, the HLS bridge, the app source tree, `data/`) |

## History: the guide was proved on a duplicate, then merged

The owner asked on 2026-10-13 for the EPG work to happen on a **copy** (a second deployment of the
product under the name it had then, on port 8087) precisely so the service the house watches was not
the test bed: the guide needs new tables, a refresh, its own screens and an XMLTV output, and
iterating on that inside the deployment that was already serving the TV would have risked its
catalogue, its screenshots and its check queue. The duplicate was seeded from the live service
(`GET /export`), the guide was built and
proved on it during 2026-10-13/14, and once the owner accepted the UI the work was merged into
`acestream-suite` and the duplicate retired: container down, its network removed, its image removed,
its directory on the host deleted and port 8087 free. The seed script went with it — this
service already had the catalogue.

## How it is built

One image, one Dockerfile, one source tree:

- `acestream-suite/Dockerfile` copies `pyproject.toml`, `alembic.ini`, `app/` and `migrations/` from
  the checkout, installs that tree editable and copies the two binary assets into `app/web/static/`.
  There is no release tarball and no patch file, so a change to the app is a change in this repository.
- **No entrypoint of its own.** The image's `entrypoint.sh` migrates the database through the app's
  own `app.launcher.upgrade_database()` and then serves it with uvicorn; the guide's tables arrive as
  Alembic migrations, which is exactly what that entrypoint runs.
- Everything else the image carries: ffmpeg, `ACESTREAM_SUITE_DATA_DIR=/data`, the `/data` volume and
  `EXPOSE 8000`.

## What runs where

| | |
|---|---|
| Container / project | `acestream-suite`, compose project `acestream-suite` — one service now, not two |
| Port | **8083** on the host, 8000 inside |
| State | **`/opt/acestream-suite/data`** on .245 (`acestream-suite.db` + `screenshots/`) |
| Engine | **`http://192.168.1.28:8081`**, the tunneled engine — for playback links, and **not** as a work source |
| Check queue | one, and it is this service's: 581 channels and **1187 checks** at the moment measured |

**The migration ran on the live database and left it intact.** `alembic 0005`; the tables
`epg_channel`, `programme` and `epg_alias` and the column `channel.guide_channel_id` exist; the
catalogue that was there before the change is still there afterwards — **581 channels, 1187 checks,
587 screenshots**, unchanged before and after, with `pragma integrity_check` answering `ok`.

## Runbook — deploying a change to the guide

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
cd /opt/acestream-suite && docker compose build acestream-suite && docker compose up -d acestream-suite
```

`up -d acestream-suite` deliberately names one service: the same compose file also holds the
**`acestream-suite-hls`** HLS bridge, and `docker compose up -d` with no arguments would recreate it
too. The `chown` of the data directory only matters on a first deployment: Docker creates a missing
bind source as a root-owned empty directory and the container runs as `1000:1000` (the host's
`ingenieria` account), so anything else is unwritable. The entrypoint applies the migrations on the
way up.

Nothing else has to run: this service already had its catalogue, and the guide's tables come with the
migrations. After a rebuild the guide is refreshed by hand (below).

## The guide: two sources, a 72 h window, and a refresh nobody schedules

Both sources are configured in `acestream-suite/docker-compose.yml` as a JSON list
(`ACESTREAM_SUITE_EPG_SOURCES`), because the catalogue is 19 categories of mixed origin and no
single provider covers it:

| Source | Size (uncompressed) | Channels | Notes |
|---|---|---|---|
| `https://www.tdtchannels.com/epg/TV.xml.gz` | 5.0 MB | 184 | Spanish national and Movistar+ line-up; **no DAZN** (0 matches) |
| `https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz` | 24.2 MB | 373 | the only one of the two that carries DAZN 1/2/3/4 and DAZN F1 |

Sizes and channel counts are the 2026-10-13 research measurements recorded in the plan. A source is a
cache, never a dependency: a dead or moved URL logs and degrades, it must not fail a request.
`iptv-org` grabbers, `epg.pw` and the UK/IT/DE per-country files were evaluated and left out.

**Window.** `ACESTREAM_SUITE_EPG_WINDOW_HOURS=72` — what a TV grid shows. Only programmes inside the
window are stored, and each refresh prunes the store as listings fall out of it, which is what stops
a 24 MB feed from accumulating on a host with limited free space.

**Refresh.** `python3 -m app.cli epg-refresh` inside the container. Measured on this service:

```bash
ssh root@192.168.1.245 'docker exec acestream-suite python3 -m app.cli epg-refresh'
```

The first run after the merge stored **28,788 programmes** across **557 guide channels** and took
**4.9 s**. The parse of a real 24 MB feed alone is ~1.5 s and ~101 MiB RSS (a 5 MB feed: ~0.4 s,
~60 MiB) — which is why the parse is streaming (`iterparse`).

**Both jobs have been scheduled since 2026-10-15**, through the house installer:
`scripts/crons-install.sh` now carries `acestream-suite-guide-playlist` (`7,22,37,52 * * * *`) and
`acestream-suite-guide-refresh` (`7 4,16 * * *`, `flock`, log to
`/var/log/acestream-suite-guide-refresh.log`) in its `JOBS` array, and both point at the
**`acestream-suite`** container. Their absence had exactly one cause: the array. The two `.cron`
files existed and had run on this host while the guide lived in the retired duplicate, but they were
never added to `JOBS`, so the generated crontab was two jobs short of the live one and the installer
was left alone for that reason. With them in it, `--install` on 2026-10-15 wrote **21 job lines**,
fixed the duplicated `acestream-suite-prune` entry the panel had left in the crontab, and dropped
Cronmaster's line wrappers (the panel re-wraps a job the next time one is edited from its UI). Both
commands were also run by hand that evening: a full refresh (2 sources, 7550 + 678 programmes
stored, 0 channels retired) and a playlist with **582 rows, 103 with a real `tvg-id`** — the count
of that evening; the same file serves **540 rows and 83** today (see "The outputs"). To pull either
forward by hand:

```bash
ssh root@192.168.1.245 'docker exec acestream-suite python3 -m app.cli epg-refresh'
ssh root@192.168.1.245 '/usr/local/bin/acestream-suite-guide-playlist'
```

**The failure mode to watch: without a refresh the guide empties itself after 72 h.** Nobody refreshes
and the guide goes quiet: every stored programme has ended, so the UI has no now/next and `/epg.xml`
publishes nothing ahead. A container that is `Up (healthy)` says nothing about the guide's freshness —
the window is what tells you.

## The mapping: 103 of 581 channels, and an alias table the matcher never overrules

The matcher maps a catalogue title to a guide channel name **only when the difference between the two
names is noise**: country markers (`[PL]`, `[RU]`), quality tags (`FHD`, `HD`, `4K`), stream brands
(`--> NEW ERA`), and a generic `channel`/`canal`/`tv` that needs a distinctive token on the other side.
Any content token left over on either side — `tve`, `bbc`, `m`, `laliga`, `hypermotion`, a channel
number — means no mapping. It runs **alias-first**: a human decision stored in `epg_alias` wins, and
the matcher never overrules it.

Measured with `python3 -m app.cli epg-map --auto` over the 581-channel catalogue: **103 matched, 478
left without a guide** (18 %), and **0 manual aliases** so far. The report prints the reason for every
rejection, e.g. `[390] Star TV HD — descartado: 'star tv' ⊄ 'tve star tv': sobra 'tve'`, and the
mapping screen prints the same subtraction (`'red' ⊄ 'vivid red': sobra 'vivid'`).

That number is low on purpose, and it was paid for. The first matcher scored containment symmetrically
and unconditionally, so a guide name that was merely a fragment of a catalogue title mapped at 0.85:
114 channels, and **four of the ten pairs it printed for review were wrong** — `Star TV HD`→`TVE_STAR.TV`,
`TV4 HD [PL]`→`M+ LALIGA TV 4`, `Laliga Hypermotion 1 720 --> NEW ERA`→`LaLiga+`, `HISTORY [PL]`→
`BBC History`. No threshold fixes that; the rule was wrong. Tightening it cost 12 pairs (114 → 103) and
bought precision: an unmapped channel is a blank cell, a wrong mapping is a lie about what is on. The
32 % in the plan's research section was measured with loose containment and is **not** a target; the
five-source comparison with the final matcher gives 28 %, and the two sources actually stored give 18 %.

`epg_alias` holds the human decisions — `epg-map --set` writes one, `--clear` reverts it, and the row
records who made it. The assign/undo pair was measured in the UI: assign → `303` and mapped 103→104,
undo → `303` and mapped back to 103.

## The screens

| Screen | URL | What it shows |
|---|---|---|
| Catalogue with what is on now | `http://192.168.1.245:8083/` | the list, with the programme on now per mapped channel; what follows is one click away in the grid |
| Guide | `http://192.168.1.245:8083/channels/{id}/guide` | a 24 h grid in Madrid time, with the running programme marked |
| Mapping | `http://192.168.1.245:8083/epg/mapping` | the candidates, the rejection reason, search, assign and undo |

All three answered **200** at the moment measured.

**Two states that are not the same thing.** *No mapping* means the channel has no guide channel;
*nothing in this slot* means it has one and the window holds no programme for it right then. They must
not render the same message. Measured on this service: of the **557 stored guide channels, 510 carry
programmes** in the window — the rest were declared by a source with no listings, so a mapping onto one
of those shows an empty grid with a real guide channel behind it.

Also normal, not a conflict: several catalogue channels share one guide channel — up to **5 AceStream
sources for the same `DAZN 2`** — so the same now/next on several rows is correct.

**The table is now 11 columns, not 13.** The screenshot folded into `Título` (rendered at 64×36) and
`Idioma` + `País` merged into one `Idioma/País` column. Estimated width **1092 px** against the
**1230 px** available at 1366–1440 px viewports — an estimate from the longest content per column, **not
a browser measurement**.

**A `Guía` cell holds the event and its hours, and nothing else.** The programme on now is a link into
that channel's grid (`/channels/{id}/guide`), and a channel with no guide gets one line — `Sin guía ·
Asignar` — pointing at the mapping screen instead of a dead cell. `Ahora` and `Después` are one
column, because they are the same information. Two things were tried in this cell and removed on the
owner's word: the **next programme**, which is one click away in the grid and was making the widest
column of the table out of a fact the guide page answers, and the **guide channel's own name**, which
repeated the row — the row *is* that channel's catalogue entry. What survives as a property of the
list is **`Añadido`**, the date the channel entered the catalogue. Measured on the served page: 103
links into the grids, 479 to the mapping screen, 478 `Sin guía · Asignar`, 581 dates and **0** guide
names.

**The list shows only what is on now.** The next programme is not repeated there: it is read in the
grid the cell links to, which is what the click is for. Repeating it made the widest column of the
table out of a fact the guide page already answers — and the grid, unlike the list, marks the running
programme and shows the rest of the day.

**The grid's description wraps.** It is prose, and the table's `white-space: nowrap` — right for a
title, a hash or a status — is wrong for a synopsis: on one line it is either clipped by the table
wrap or drags the whole table wider. The cell wraps at 46ch, honours the source's own paragraph
breaks (`pre-line`, because a feed that ships two paragraphs means it), and is the only cell in that
table allowed to grow tall. Measured on the deployed page: 17 description cells, the longest 294
characters reaching the page whole.

**A container detail the workstation could not see.** `actor_name()` called `getpass.getuser()`, and
this container's uid 1000 has no `/etc/passwd` entry (`KeyError: 'getpwuid(): uid not found: 1000'`),
so the assign button answered `500` while `clear` answered `303` and the write path looked half
healthy. The guard now returns `unknown (no OS user in this deployment)`, which is what
`epg_alias.created_by` records here. Verified in the container: `getent passwd 1000` finds nothing and
`HOME` is `/`. No new code may resolve the operator's user or home directory without the same guard.

## The outputs

| Output | URL | Measured |
|---|---|---|
| XMLTV | `http://192.168.1.245:8083/epg.xml` | `200 application/xml`, **57 channels, 3,114 programmes** at the moment measured |
| Mapping as JSON | `http://192.168.1.245:8083/epg.json` | `200 application/json`, **103 mapping entries** under `mapping` |
| Playlist with the guide | `http://192.168.1.245/varios/acestream-suite-guide.m3u` | **540 rows, 83 with a real `tvg-id`** (the guide channel id) and the rest without one; `#EXTM3U url-tvg="http://192.168.1.245:8083/epg.xml"` on the first line; every URL is the HLS bridge, `http://192.168.1.245:8084/<hash>/index.m3u8`; every display name ends with the channel's quality (`Canal 1080p`), as in the other lists |

Only the mapped channels appear in the XMLTV document, and an unmapped channel has no guide to
publish: that is visible rather than invented. The programme and channel counts are a snapshot of the
72 h window, so they move.

**Two older measurements of the same file, kept as what they were on their date:** **582 rows, 103 with
a real `tvg-id`** on 2026-10-14/15 (the whole 582-channel catalogue, one row per publisher), and **551
rows, 89 with `tvg-id`** just before the change below. The gap to the table's numbers is the prune (the
catalogue went 582 → 551) plus the 2026-10-25 change, which brought the guide list in line with the
other four lists and their shared rules:

- **`?status=alive`**, the same freshness filter as `acestream-suite.m3u`, `deportes.m3u`,
  `espana.m3u` and `hls.m3u`. The old reason for publishing everything — the retired duplicate
  queued no live checks, so `check_result` was empty — died with the duplicate: the guide lives
  inside acestream-suite now (`epg_channel` and `programme` in the same SQLite) and every channel
  has a `check_result` row, so the filter no longer empties the list. It removed the **4** channels
  with no peers (the basketball imports of 2026-10-25).
- **One entry per visible name**: best declared quality, then a sports category, then the lowest
  content id, so two publishers of one channel are a single row. That removed **7** duplicate names,
  and `tvg-id` fell with it: `epg.json` still reports **103** mappings, but a row carries a `tvg-id`
  only when the publisher that survived the dedupe is the mapped one, so the playlist's count
  (103 on 2026-10-14, 89 before this change, 83 in the table) is not the mapping's count.
- **Every URL now goes through the HLS bridge** (`http://192.168.1.245:8084/<hash>/index.m3u8`), like the
  other four lists, instead of the engine's `http://192.168.1.28:8081/ace/getstream?infohash=<hash>`:
  the engine serves one reader per content, and a list that calls it directly competes with the bridge
  for the same channel (measured 2026-10-18: 0 manifests and 23 failures in a minute). `direct.m3u`
  remains the engine's own list, for Dispatcharr and for diagnosis.

**The list Dispatcharr reads is the pre-rename copy of `acestream-suite.m3u`, and it was deliberately
not touched.** That copy is what account 4 consumes today, with `tvg-id` = the 40-hex AceStream
content id of every stream; rewriting it in place would re-map the live line-up (the account has
~1,072 streams marked stale and the owner deferred that decision). The new list is a **second**
artefact, written by
`/usr/local/bin/acestream-suite-guide-playlist` (from `scripts/acestream-suite-guide-playlist.py`),
and it writes only when its content changes. The owner decides whether account 4 ever moves to it;
what stands is the decision not to add the guide to that list in place. The md5
`b0a727950fd28077f6ac691d215a3fec` recorded on 2026-10-14 proved the merge left that file alone — it
is a measurement of that day, not a standing invariant: the file has changed since for its own
reasons (the HLS bridge URLs on 2026-10-18 and the one-entry-per-name rule on 2026-10-25, see
`docs/acestream-suite.md`), and it still carries no `url-tvg` and no guide `tvg-id`.

## Backups taken before the merge

Two directories under the backup root, named on 2026-10-14:

- `/backup/acestream-suite/merge-20261014-083751` — the service as it was: `acestream-suite.db` with
  `integrity_check ok`, 581 channels, 1187 checks, 587 screenshots, and the deployment files.
- `/backup/acestream-suite/epg-duplicado-20261014-085035` — the duplicate's database (12 MB), kept as
  insurance even though the duplicate itself is gone.

## Checks that are worth running

Action, on the host (`ssh root@192.168.1.245`):

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
c = sqlite3.connect("file:%s/acestream-suite.db?mode=ro" % os.environ["ACESTREAM_SUITE_DATA_DIR"], uri=True)
for t in ("channel", "check_result", "epg_channel", "programme", "epg_alias"):
    print(t, c.execute("select count(*) from " + t).fetchone()[0])
print("mapped", c.execute("select count(*) from channel where guide_channel_id is not null").fetchone()[0])'
```

Record — literal output of that date (2026-10-14, the merged deployment on the host), with the names
of that day:

```
{"status":"ok","app":"AceList","version":"1.3.0"}
200 application/xml; charset=utf-8
57
3114
(no output — xmllint says nothing when the document is valid)
#EXTM3U url-tvg="http://192.168.1.245:8083/epg.xml"
b0a727950fd28077f6ac691d215a3fec  /srv/www/varios/acelist.m3u
channel 581
check_result 1187
epg_channel 557
programme 28788
epg_alias 0
mapped 103
```

Two things in that record are the point of the merge: `check_result` is **1187**, not **0** — the
guide now lives beside this service's own live checks instead of in a deployment that queued none; and
the playlist keeping its md5 — it is recorded under the name that file had that day, and so is the
`app` field of the health answer — proves the TV's list was not touched.

## Limits and known tolls

- **No authentication.** Anyone on the LAN can browse the catalogue and read the guide. Never
  port-forward 8083.
- **Third-party community feeds.** tdtchannels and epgshare01 are community aggregators/rippers —
  useful, undocumented and fragile. The same legal note as AceStream applies
  (`docs/acestream-manual.md`, "Aviso legal"): LAN only, no redistribution.
- **`ALL_SOURCES` is out of scope.** The 196 MB gzipped everything-in-one file is too big for a
  periodic refresh; per-country files only.
- **The engine carries one check queue — this service's.** There is no second deployment queueing
  checks any more; the engine and the WireGuard tunnel carry exactly the 1187 above.
- **The container has no `/etc/passwd` entry for its uid and `HOME` is `/`.** Any code that resolves
  the operator's user or home directory at runtime needs the guard described above; the assign button
  was a 500 until it had one.
- **The freshness of the guide depends on the two jobs above.** If the refresh stops running the
  72 h window drains and the guide goes quiet — that is the failure mode to watch.
- **Backups are manual.** The two directories above are the only pre-merge copies; nothing schedules
  the backup root.

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| The grid is empty everywhere | nobody refreshed the guide and the 72 h window drained | run the two hand commands above; the refresh log (`/var/log/acestream-suite-guide-refresh.log`) shows which source answered |
| A channel's guide is blank but it is mapped | "nothing in this slot": its guide channel has no programme in the window right then | check the same guide channel on another channel; a source may simply declare it without listings |
| A channel has no guide and no mapping | the matcher found no noise-only match | map it by hand on `/epg/mapping` and the alias keeps it |
| A refresh leaves the guide unchanged | a source moved, or answered with the same window | the refresh exits non-zero only when **no** source answered; read the per-source line in the log |
| Saving a mapping answers `403 cross-site request rejected` | the address you typed is not in `ACESTREAM_SUITE_ALLOWED_HOSTS` | add it in `docker-compose.yml` and recreate the container; do not disable the check |
| A rebuild recreates `acestream-suite-hls` as well | `docker compose up -d` with no service name | name the service: `docker compose up -d acestream-suite` |
