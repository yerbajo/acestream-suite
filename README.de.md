# acestream-suite

🇬🇧 [English](README.md) · 🇪🇸 [Español](README.es.md) · 🇫🇷 [Français](README.fr.md) · 🇮🇹 [Italiano](README.it.md) · 🇩🇪 **Deutsch** · 🇷🇺 [Русский](README.ru.md) · 🇨🇳 [中文](README.zh.md) · 🇯🇵 [日本語](README.ja.md)

<p align="center"><img src="assets/logo-3d.png" alt="acestream-suite" width="560"></p>

**Herkunft.** Die Idee und die Grundlage dieser Anwendung stammen aus dem Projekt **AceList**
(<https://github.com/fabiomb/AceList>): ein lokaler Katalog von AceStream-Links. Diese Suite ist
diese Idee, erweitert um eigene Komponenten, und `LICENSE` hält fest, was das bedeutet, wenn sie
weiterverbreitet wird.

Eine selbst gehostete Weboberfläche für eine **AceStream**-Engine. Sie führt einen Katalog von
`acestream://`-Links — Sender mit ihrer Content ID, eine Verfügbarkeitsprüfung gegen die Engine, ein
aufgenommenes Einzelbild je Sender, Kategorien, Sprachen und Länder — und ergänzt vier Dinge um
diesen Katalog:

- **eine Programmführer-Schicht (EPG)**: XMLTV-Quellen, eine Zuordnung Sender↔Programmführer, ein
  gespeichertes Programmfenster, ein XMLTV-Dokument unter `/epg.xml` und eine M3U-Playlist, die den
  Programmführer mitführt;
- **eine Suchseite** über den Index der Engine selbst (`acesearch`), sodass Sender, die der Index
  kennt und der Katalog nicht, gefunden und importiert werden können;
- **eine HLS-Brücke**, die einen Stream der Engine in ein Manifest umsetzt, das mehrere Player im
  Haus gleichzeitig ansehen können;
- **Skripte**, die den Katalog als Playlists für die Player veröffentlichen, die eine URL statt
  einer Webseite wollen (VLC, Kodi, Dispatcharr/Jellyfin Live TV, ein Smart-TV).

`acestream-suite` ist dieses Produkt. **AceStream** ist die Engine, über die es abspielt: die Engine
ist Software eines Dritten und behält ihren eigenen Namen.

> **Keine Authentifizierung.** Die App ist für ein privates Netz gedacht. Wer den Port erreichen
> kann, kann den Katalog durchsuchen und Streams starten. Nicht ins Internet veröffentlichen.

## Schnellstart (Docker)

Voraussetzungen: Docker mit Compose v2 und eine AceStream-Engine, die aus dem Container über HTTP
erreichbar ist (diese Suite startet keine).

```bash
git clone https://github.com/yerbajo/acestream-suite.git
cd acestream-suite
cp .env.example .env
$EDITOR .env                      # ACESTREAM_SUITE_ENGINE_URL, ACESTREAM_SUITE_ALLOWED_HOSTS
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
curl -fsS http://localhost:8080/health
```

Dann **`http://<host>:8080`** öffnen — `ACESTREAM_SUITE_PORT` in `.env` ist der Host-Port (`8080`,
sofern nicht geändert; die App hört im Container immer auf 8000). Die HLS-Brücke veröffentlicht
**8084**.

Zwei Details, die beißen, wenn sie übersprungen werden:

- **`data/` muss existieren und für uid 1000 beschreibbar sein.** Docker legt eine fehlende
  Bind-Quelle als root-eigenes leeres Verzeichnis an, und die App läuft als `"1000:1000"` — sie
  könnte ihre Datenbank also nicht schreiben.
- **Die Deployment-Werte kommen aus `.env`, nicht aus der Compose-Datei.** `docker-compose.yml`
  interpoliert jeden Wert, in dem sich eine Installation unterscheidet (`${ACESTREAM_SUITE_ENGINE_URL:-…}`
  und Verwandte), also genügt `cp .env.example .env` plus eine Anpassung, und die Compose-Datei selbst
  muss nie angefasst werden. Was in `.env` fehlt, fällt auf den sicheren Standardwert zurück, der in
  der Compose-Datei steht. Engine-URL, deren Timeouts, die Such-URL und die Parallelität der Prüfungen
  sind außerdem *gespeicherte Einstellungen* auf der Seite **Ajustes** der App, und ein gespeicherter
  Wert gewinnt gegen die Umgebung — eine falsche `.env` ist also ohne Rebuild über die Oberfläche
  korrigierbar.

## Das Image wird lokal aus diesem Repository gebaut

`docker compose up -d --build` **baut** das Image; gezogen wird nichts außer dem Basisimage
`python:3.12-slim` und den Distributionspaketen. Der Quellcode der Anwendung liegt hier: `app/`
(die FastAPI-App), `migrations/` (Alembic) und `pyproject.toml`, in das Image kopiert und im
editierbaren Modus installiert, weil die App ihre Templates, ihre statischen Dateien und ihre
Migrationen relativ zum Quellbaum liest.

`ffmpeg` ist im Image installiert, weil die Screenshots und die Verfügbarkeitsprüfung den
Live-Stream dadurch lesen.

Aktualisieren:

```bash
git pull
docker compose build acestream-suite && docker compose up -d acestream-suite
```

Den Dienst zu benennen ist wichtig: dieselbe Compose-Datei enthält auch die Brücke
`acestream-suite-hls`, und ein blankes `docker compose up -d` würde sie ebenfalls neu erzeugen.

## Konfiguration

Jede Variable liest die App aus ihrer Umgebung. Die primären Namen sind `ACESTREAM_SUITE_*`; die
alten Namen `ACELIST_*` werden für eine Version noch als Rückfall gelesen, damit eine Installation,
die noch nicht umbenannt wurde, weiterläuft (sind beide gesetzt, gewinnt der neue Name).

| Variable | Standard | Wirkung |
|---|---|---|
| `ACESTREAM_SUITE_ENGINE_URL` | `http://127.0.0.1:6878` | Basis-URL der HTTP-API der Engine. Ebenfalls eine gespeicherte Einstellung. |
| `ACESTREAM_SUITE_ENGINE_TIMEOUT` | `15` | Sekunden, die auf die Engine gewartet wird, bevor ein Aufruf als fehlgeschlagen gilt. |
| `ACESTREAM_SUITE_SEARCH_URL` | leer = die Engine-URL | Basis-URL von `acesearch`, des Index, den die Suchseite liest (`/search.m3u`). Ebenfalls eine gespeicherte Einstellung. |
| `ACESTREAM_SUITE_ALLOWED_HOSTS` | leer | Kommaseparierte `host`- / `host:port`-Werte, die im `Host`-Header und im `Origin` eines Schreibzugriffs akzeptiert werden. Ohne die Adresse, über die zugegriffen wird, ist jede Anfrage ein `400` und jeder Schreibzugriff ein `403`. |
| `ACESTREAM_SUITE_PLAYBACK_MODE` | `vlc` | `vlc` startet den Stream und öffnet einen lokalen Player (das Desktop-Verhalten). `url` startet den Stream und übergibt den Link an die Seite — der Modus, den ein Container braucht, da er keinen Player hat. |
| `ACESTREAM_SUITE_STREAM_PARAM` | `id` | Query-Parameter, der einen Stream auf der Engine startet: `id` für den Windows-Build 3.1.x, `infohash` für den Linux-Build 3.2.x (der auf `id` mit `failed to load content` antwortet). |
| `ACESTREAM_SUITE_CHECK_CONCURRENCY` | `2` | Verfügbarkeitsprüfungen gleichzeitig. Jede ist eine Live-Sitzung auf der Engine, das ist also zugleich die Last auf der Engine. |
| `ACESTREAM_SUITE_SCREENSHOT_TIMEOUT` | `30` | Sekunden, die für die Einzelbildaufnahme mit ffmpeg erlaubt sind. |
| `ACESTREAM_SUITE_FFMPEG_PATH` | leer = `ffmpeg` aus dem `PATH` | Das Binary, das für Screenshots verwendet wird. |
| `ACESTREAM_SUITE_VLC_PATH` | leer = übliche Ordner, dann `PATH` | Wird nur im Wiedergabemodus `vlc` gelesen. |
| `ACESTREAM_SUITE_DATA_DIR` | im Image `/data`, sonst das App-Data-Verzeichnis des Betriebssystems | Wo `acestream-suite.db` und `screenshots/` liegen. |
| `ACESTREAM_SUITE_PORT` | `8080` | Auf dem Host veröffentlichter Port. Die App hört im Container immer auf `8000`. |
| `ACESTREAM_SUITE_EPG_SOURCES` | `[]` | XMLTV-Quellen als JSON-Liste von URLs. Leer ist ein unterstützter Zustand: der Katalog funktioniert ohne Programmführer. |
| `ACESTREAM_SUITE_EPG_WINDOW_HOURS` | `72` | Wie weit vor *jetzt* der Programmführer gehalten wird; ältere Sendungen werden bei jeder Aktualisierung entfernt. |
| `ACESTREAM_SUITE_EPG_TIMEOUT_SECONDS` | `120` | Timeout je Quelle bei der Aktualisierung. Eine Quelle, die nie antwortet, kostet eine Quelle, nicht den Lauf. |
| `ACESTREAM_SUITE_EPG_MAX_MB` | `64` | Eine Quelle, die größer ist, wird abgelehnt, statt den Speicher des Containers zu füllen. |
| `ACESTREAM_SUITE_EPG_GRACE_SECONDS` | `300` | Zusätzliche Sekunden, die eine gerade beendete Sendung bleibt, für Player, deren Uhr nachgeht. |
| `ACESTREAM_SUITE_EPG_ACTOR` | leer = der Betriebssystem-Benutzer | Name, der als Autor einer manuellen Entscheidung Sender↔Programmführer festgehalten wird. |

**Welche davon `.env` setzen kann, entscheidet die Compose-Datei.** Sie interpoliert `ENGINE_URL`,
`ALLOWED_HOSTS`, `PLAYBACK_MODE`, `STREAM_PARAM`, `CHECK_CONCURRENCY`, `PORT`, `EPG_SOURCES`,
`EPG_WINDOW_HOURS` und die `HLS_ALIASES` der Brücke — genau die Namen, die `.env.example` mitliefert.
Den Rest liest die App, die Compose-Datei reicht ihn aber nicht durch: es sind entweder gespeicherte
Einstellungen in **Ajustes** (Engine-Timeout, Such-URL, Prüf-Timeout, minimale Peers,
Screenshot-Timeout, ffmpeg-Pfad, VLC-Pfad) oder Werte, die selbst unter `environment:` in der
Compose-Datei ergänzt werden (Datenverzeichnis, die Timeouts und die Größenobergrenze des
Programmführers, der Autorname).

Zwei Anmerkungen:

- Wie oft der Programmführer aktualisiert wird, ist keine Konfiguration: es ist der Zeitplan in
  `scripts/acestream-suite-guide-refresh.cron` (04:07 und 16:07), und nichts im Container liest ein
  Intervall. Diese Zeile ändern, um die Frequenz zu ändern.
- Die Datenbankdatei ist `acestream-suite.db`, im Datenverzeichnis, auf das die App zeigt.

## Daten und Backups

Der ganze Zustand der Anwendung ist ein Verzeichnis, eingehängt unter `/data` und in der
Compose-Datei an `./data` gebunden:

```
data/
  acestream-suite.db   SQLite: catalogue, check history, guide tables, settings
  screenshots/    one captured frame per channel
```

Für ein Backup das Verzeichnis kopieren, während die App nicht schreibt:

```bash
docker compose stop acestream-suite
tar czf "acestream-suite-data-$(date +%F).tar.gz" data
docker compose start acestream-suite
```

Eine Kopie aus einem laufenden Container kann die SQLite-Datei mitten im Schreiben erwischen. Das
Skript `acestream-suite-prune` (unten) legt genau so eine Kopie an, bevor es etwas löscht, zusammen
mit einem re-importierbaren Export des ganzen Katalogs.

Zum Wiederherstellen: die App stoppen, `data/` ersetzen, starten. Das Entrypoint wendet beim
Hochfahren die Datenbankmigrationen an, ein Backup aus einer älteren Version wird also aktualisiert
statt zurückgewiesen.

## Wie der Quellcode aufgebaut ist

| Pfad | Was es ist |
|---|---|
| `app/` | Die Webanwendung: FastAPI-Routen, Templates, statische Dateien, der Engine-Client, der Programmführer und die Suche. |
| `migrations/` + `alembic.ini` | Das Datenbankschema, beim Containerstart durch `entrypoint.sh` angewendet. |
| `tests/` | Die Testsuite der Anwendung (pytest, ~640 Tests, kein Netz): `pytest -q` aus dem Wurzelverzeichnis des Repositorys. |
| `scripts/` | Die optionalen Host-Jobs: die Playlist-Bauer, der Import der Spieltage, das Prune, ihre `.cron`-Dateien und `crons-install.sh`. |
| `assets/` | Das Logo und das Favicon, die das Image in das statische Verzeichnis der App kopiert. |

Die Herkunft, und was sie für den Leser bedeutet, steht in `LICENSE`.

## Die HLS-Brücke

Engines der 3.2.x-Reihe liefern kein HLS-Manifest, und mehrere Clients (Smart-TVs, Dispatcharr,
Jellyfin Live TV) wollen eines. `acestream-suite-hls` — der zweite Dienst in derselben Compose-Datei,
aus demselben Image — packt das MPEG-TS der Engine ohne Neukodierung in 2-Sekunden-Segmente um und
liefert:

```
http://<host>:8084/<infohash>/index.m3u8
```

Ein `ffmpeg` pro Sender, höchstens vier Sitzungen, eine Sitzung 45 Sekunden nach der letzten Anfrage
geschlossen, das Segmentfenster in einem 2-GB-tmpfs, und die Engine-Sitzung wird freigegeben, wenn
die Brücke einen Sender loslässt. Fällt der freie Platz im tmpfs unter `HLS_MIN_FREE_MB`, wird die
Sitzung mit den meisten Bytes geschlossen: ein volles tmpfs bricht alle Sitzungen auf einmal, nicht
nur eine. `python3 hls-bridge.py --self-test` prüft diese Entscheidungen offline, mit einer
Fake-Engine und einem Fake-ffmpeg.

Die Brücke ist ein Workaround für eine gemessene Einschränkung der Engine, keine Vorliebe: die
Engine bedient einen Leser pro Inhalt, mehrere Player direkt darauf zu richten lässt sie also um
denselben Sender konkurrieren.

## Die Skripte dieses Setups (optional)

Nichts in `scripts/` wird gebraucht, um die App zu betreiben. Sie sind die Veröffentlichungsschicht,
die der Autor verwendet, sie erwarten die Pfade oben in jeder Datei, und sie fallen standardmäßig
auf die Hosts dieser Installation zurück (`http://127.0.0.1:8083` für die App, `/srv/www/varios` für
die Playlists). Jeder Pfad und jede URL ist ein Flag und, für die ein Host verschiebt, eine
Umgebungsvariable — den Kopf des Skripts lesen, das installiert werden soll.

| Skript | Was es schreibt |
|---|---|
| `acestream-suite-playlist.py` | Fünf M3U-Dateien im Webverzeichnis: `acestream-suite.m3u` (alles), `deportes.m3u`, `espana.m3u`, `hls.m3u` (mit einem Ordner `Campeones`) und `direct.m3u` (das MPEG-TS der Engine selbst). Nur Sender, die die App als lebend bestätigt hat, ein Eintrag pro Anzeigename, jede URL über die HLS-Brücke außer `direct.m3u`. Schreibt nur, wenn sich der Inhalt geändert hat. |
| `acestream-suite-guide-playlist.py` | `acestream-suite-guide.m3u`: derselbe Katalog mit `url-tvg` im Kopf und der Kanal-ID des Programmführers als `tvg-id`, für die Sender, die eine haben. |
| `acestream-suite-prune.py` | Ein einmaliges Aufräumen: wartet, bis jeder Sender geprüft ist, sichert nach `/backup/…/<stamp>/` (Playlists, ein re-importierbarer Katalogexport, eine Kopie der Datenbank, die Löschliste) und löscht dann die Sender, deren letzte Prüfung nicht lebend war, über die API der App selbst. Markiert sich als erledigt; `--undo-guard` lässt es erneut laufen. |
| `crons-install.sh` | Die crontab von root auf dem Zielhost, erzeugt aus `scripts/*.cron`. `--dry-run` gibt aus, `--diff` vergleicht mit der laufenden crontab, `--install` sichert und schreibt. Sein `JOBS`-Array enthält auch Jobs aus anderen Stacks — vor dem Installieren lesen. |

Alle nehmen `--dry-run`; die beiden Playlist-Skripte nehmen außerdem `--self-test`, das ihre
Prüfungen ohne Netz und ohne Dateien ausführt. Die Playlist-Jobs brauchen einen Webserver, der
dieses Verzeichnis ausliefert; `acestream-suite-guide-refresh.cron` ist die Aktualisierung des
Programmführers, und sie führt sie im Container aus
(`docker exec acestream-suite python3 -m app.cli epg-refresh`).

## Dokumentation

- Acht Sprachen: der Umschalter oben verlinkt dieses Dokument auf Englisch, Spanisch, Französisch, Italienisch, Russisch, Chinesisch und Japanisch.
- `LICENSE` — MIT für die hier verfasste Arbeit, und die Herkunft des Anwendungscodes.
- Inline: jedes Modul trägt einen Docstring, der sagt, was es entscheidet und warum;
  `python -m app.cli --help` und das `--help` jedes Skripts dokumentieren ihre eigenen Optionen.

## Lizenz und Hinweise auf Drittanbieter

Die hier verfasste Arbeit — die Deployment-Dateien, die Skripte, die Dokumentation, das Logo und der
Anwendungscode — steht unter MIT; siehe `LICENSE`. Dieselbe Datei hält fest, woher die Idee und die
Grundlage kommen: **AceList** (<https://github.com/fabiomb/AceList>), das keine eigene Lizenz
veröffentlicht, und was das für die Weiterverbreitung bedeutet. Sie sagt auch, woher die
AceStream-Marke im Logo kommt. Vor dem Weiterverbreiten eines hier gebauten Images lesen. Das Image
installiert außerdem Software Dritter: `python:3.12-slim`, die Debian-Pakete (`ffmpeg`, `curl`) und
die Python-Abhängigkeiten in `pyproject.toml`, jede unter ihrer eigenen Lizenz; `htmx`
(BSD-2-Clause) und die Lucide-Icon-Striche (ISC) liegen vendored in `app/web/`.

## Wie das hier ausgerollt wird

**Dieses Repository ist die Suite, nicht die Engine.** Eine AceStream-Engine ist eine Abhängigkeit,
auf die gezeigt wird: jede Engine, deren HTTP-API der Container erreicht, genügt, und wo die Engine
des Autors selbst läuft — eine andere Maschine, in einem VPN, hinter einem Proxy — liegt außerhalb
dieses Repositorys und ist dafür unerheblich. Die Suite setzt nur voraus, dass die URL in
`ACESTREAM_SUITE_ENGINE_URL` antwortet.

Was dieses Repository beschreibt, ist ein kleines, gewöhnliches Deployment: ein Host, der
`docker compose up -d` ausführt, seine Werte in `.env`, ein Webserver oder Reverse Proxy, der das
Verzeichnis veröffentlicht, in das das Playlist-Skript schreibt, und — wenn Listen und
Programmführer nach Zeitplan aktualisiert werden sollen — die obigen Cron-Jobs. Die Compose-Datei ist
die Schnittstelle, die obigen Umgebungsvariablen sind die Stellschrauben, und die Skripte unter
`scripts/` sind optionale Helfer, die gelesen, von Hand ausgeführt oder eingeplant werden können.
