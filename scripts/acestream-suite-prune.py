#!/usr/bin/env python3
"""Removes the channels acestream-suite verified as offline, once it has finished verifying all of them.

WHY THIS EXISTS
    The catalog grew to the whole engine index (1,651 channels) and every one of them was queued for
    a live availability check. When the queue empties, the catalogue holds what it should: channels
    that answer. What it also holds is the ones the engine answered for — `no_peers`, `not_found`,
    `error` — and those are noise in a catalog whose point is to be watchable.

    This is a ONE-SHOT job, not a service: it waits for the verification to finish, backs everything
    up, deletes the dead channels through acestream-suite's own API (which also removes their check history
    and their screenshots), regenerates the playlists and marks itself done. It runs from cron every
    20 minutes and does nothing at all until the checks are finished.

WHAT IT DELETES, AND WHAT IT REFUSES TO DELETE
    Only channels whose **latest** check is `no_peers`, `not_found` or `error`. A channel with no
    check at all is never touched: "not verified yet" is not "offline", and deleting on partial
    information is how a catalogue loses channels that were merely unlucky in the queue. That is why
    the job waits for `unchecked == 0` — and why `--force` exists: if the batch was lost (a container
    restart drops the queue), `--force` does the deletion on what IS known, still never touching an
    unchecked channel.

BACKUP
    /backup/acelist/<stamp>/ before anything is deleted (esa ruta no cambia en esta version: son los
    respaldos que ya existen y no se mueven):
      acestream-suite.m3u, deportes.m3u, espana.m3u   the playlists as they were
      catalogo.json                                  the full export, re-importable con su import
      acelist.db                                     a consistent copy of the database (S5)
      canales-a-borrar.txt                           what was deleted, with its status and peers

INSTALL (on dev.albertucho.home)
    install -m 0755 scripts/acestream-suite-prune.py /usr/local/bin/acestream-suite-prune
    install -m 0644 scripts/acestream-suite-prune.cron /etc/cron.d/acestream-suite-prune
    /usr/local/bin/acestream-suite-prune --dry-run    # what it would do right now, touching nothing

USAGE
    acestream-suite-prune               # waits, or does the work when the verification has finished
    acestream-suite-prune --dry-run     # report the plan and exit
    acestream-suite-prune --force       # delete what is already known to be dead, without waiting
    acestream-suite-prune --undo-guard  # allow it to run again (the backup is taken again, too)
"""

import argparse
import fcntl
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request


def env(name, legacy, default):
    """S4: la variable nueva y, mientras el host solo exporte la vieja, la vieja.

    Se lee `ACESTREAM_SUITE_*` primero; `ACELIST_*` sigue valiendo en esta version para que un host
    que aun la exporte (el crontab viejo, un docker-compose sin actualizar) no cambie de
    comportamiento el dia del renombrado.
    """
    value = os.environ.get(name)
    if value is None:
        value = os.environ.get(legacy)
    return default if value is None else value


def with_legacy(path, current, legacy):
    """S4: la ruta nueva y, mientras la vieja sea la unica que existe, la vieja.

    Solo se cae a la ruta vieja cuando no se ha pedido otra cosa (`path == current`) y el fichero
    nuevo aun no esta: es la ventana entre este cambio y la primera pasada que escribe el nuevo.
    """
    if path == current and not os.path.exists(path) and legacy and os.path.exists(legacy):
        return legacy
    return path


BASE_URL = env("ACESTREAM_SUITE_URL", "ACELIST_URL", "http://127.0.0.1:8083")
# S5: la base de datos NO se mueve de sitio en esta feature. El nombre de la copia dentro del respaldo
# sale de aqui, asi que tambien se queda como esta.
DB_PATH = env("ACESTREAM_SUITE_DB", "ACELIST_DB", "/opt/acelist/data/acelist.db")
PLAYLIST_DIR = env("ACESTREAM_SUITE_PLAYLIST_DIR", "ACELIST_PLAYLIST_DIR", "/srv/www/varios")
PLAYLIST_FILES = ("acestream-suite.m3u", "deportes.m3u", "espana.m3u")
# S4: mientras la lista nueva no exista, se respalda la vieja, y el binario viejo sigue existiendo
# como envoltorio del nuevo hasta que el crontab se regenere.
PLAYLIST_FILE_LEGACY = "acelist.m3u"
PLAYLIST_SCRIPT = env("ACESTREAM_SUITE_PLAYLIST_SCRIPT", "ACELIST_PLAYLIST_SCRIPT",
                      "/usr/local/bin/acestream-suite-playlist")
PLAYLIST_SCRIPT_LEGACY = "/usr/local/bin/acelist-playlist"
BACKUP_DIR = env("ACESTREAM_SUITE_BACKUP_DIR", "ACELIST_BACKUP_DIR", "/backup/acelist")
GUARD_PATH = env("ACESTREAM_SUITE_PRUNE_GUARD", "ACELIST_PRUNE_GUARD",
                 "/var/lib/acestream-suite-prune/done")
# S4: la guarda vieja se sigue leyendo; si existe, el trabajo ya se hizo y no se vuelve a borrar.
GUARD_PATH_LEGACY = "/var/lib/acelist-prune/done"
LOG_PATH = env("ACESTREAM_SUITE_PRUNE_LOG", "ACELIST_PRUNE_LOG",
               "/var/log/acestream-suite-prune.log")
LOCK_PATH = env("ACESTREAM_SUITE_PRUNE_LOCK", "ACELIST_PRUNE_LOCK",
                "/var/lock/acestream-suite-prune.lock")
DEAD = ("no_peers", "not_found", "error")
TIMEOUT = float(env("ACESTREAM_SUITE_PRUNE_TIMEOUT", "ACELIST_PRUNE_TIMEOUT", "30"))


def log(message, path=LOG_PATH):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line)
    if path:
        try:
            with open(path, "a") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            print("acestream-suite-prune: cannot write %s: %s" % (path, exc), file=sys.stderr)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """The app answers a delete with a redirect; following it would fetch a page for nothing."""

    def redirect_request(self, *args, **kwargs):
        return None


def request(url, *, data=None, headers=None, timeout=TIMEOUT, follow=False):
    opener = urllib.request.build_opener(*([] if follow else [NoRedirect]))
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:  # 303 and friends are answers, not failures
        return exc.code, exc.read()


def state(db=DB_PATH):
    """`(unchecked, dead)`: what has never been checked, and what is checked and not alive."""
    connection = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        unchecked = connection.execute(
            "select count(*) from channel ch"
            " where not exists (select 1 from check_result r where r.channel_id = ch.id)"
        ).fetchone()[0]
        dead = connection.execute(
            "select ch.id, ch.title, ch.content_id, r.status, r.peers"
            " from channel ch join check_result r"
            "   on r.id = (select max(id) from check_result where channel_id = ch.id)"
            " where r.status in (%s) order by ch.title" % ",".join("?" * len(DEAD)),
            DEAD,
        ).fetchall()
    finally:
        connection.close()
    return unchecked, dead


def backup(dead):
    """Everything needed to undo this, written before the first delete."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = os.path.join(BACKUP_DIR, stamp)
    os.makedirs(destination)
    for name in PLAYLIST_FILES:
        source = os.path.join(PLAYLIST_DIR, name)
        # S4: durante la ventana del renombrado la lista nueva puede no existir todavia y la vieja si.
        if not os.path.exists(source) and name == PLAYLIST_FILES[0]:
            source = os.path.join(PLAYLIST_DIR, PLAYLIST_FILE_LEGACY)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(destination, name))
    status, body = request(BASE_URL.rstrip("/") + "/export")  # unfiltered: the whole catalog
    if status != 200:
        raise OSError("cannot export the catalog: HTTP %s" % status)
    with open(os.path.join(destination, "catalogo.json"), "wb") as handle:
        handle.write(body)
    # El nombre de la copia es el de la base de datos, que no se mueve de sitio (S5).
    copy_database(os.path.join(destination, os.path.basename(DB_PATH)))
    with open(os.path.join(destination, "canales-a-borrar.txt"), "w") as handle:
        for _, title, content_id, channel_status, peers in dead:
            handle.write("%s\t%s\t%s\tpeers=%s\n" % (title, content_id, channel_status, peers))
    return destination


def copy_database(destination, db=DB_PATH):
    """A consistent copy, also on the host's Python 3.6 (`Connection.backup` needs 3.7)."""
    if hasattr(sqlite3.Connection, "backup"):
        source = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            copy = sqlite3.connect(destination)
            try:
                source.backup(copy)  # consistent even while the app is writing
            finally:
                copy.close()
        finally:
            source.close()
        return
    # RHEL 8's sqlite3 CLI has `.backup`, which is the same online backup API.
    result = subprocess.run(
        ["sqlite3", db, ".backup '%s'" % destination],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise OSError("sqlite3 .backup failed: %s" % result.stdout.decode("utf-8", "replace"))
    if not os.path.exists(destination):
        raise OSError("sqlite3 .backup produced no file")


def delete(channel_id):
    """Through the app, so its check history and its screenshots go with the channel."""
    status, _ = request(
        "%s/channels/%d/delete" % (BASE_URL.rstrip("/"), channel_id),
        data=b"",
        headers={"Origin": BASE_URL, "HX-Request": "true", "Content-Length": "0"},
    )
    return status in (200, 204, 303)


def refresh_playlists(script=None):
    # S4: el binario nuevo y, mientras no exista, el viejo (que en el host es un envoltorio del nuevo
    # hasta que el crontab se regenere).
    script = script or with_legacy(PLAYLIST_SCRIPT, PLAYLIST_SCRIPT, PLAYLIST_SCRIPT_LEGACY)
    result = subprocess.run([script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        log("  playlist: %s" % line)
    return result.returncode == 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Delete the channels that are not online")
    parser.add_argument("--dry-run", action="store_true", help="report the plan, change nothing")
    parser.add_argument("--force", action="store_true",
                        help="do not wait for the verification to finish")
    parser.add_argument("--undo-guard", action="store_true", help="allow it to run again")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    # S4: la guarda nueva y, mientras la vieja sea la unica que existe, la vieja. Si existe, el
    # trabajo ya se hizo: volver a borrar sin respaldo nuevo no es una opcion.
    guard = with_legacy(GUARD_PATH, GUARD_PATH, GUARD_PATH_LEGACY)
    if args.undo_guard and os.path.exists(guard):
        os.unlink(guard)
        log("guard removed: the job can run again")
    if os.path.exists(guard):
        return 0  # done already: cron keeps calling, this is the cheapest answer

    try:
        unchecked, dead = state()
    except sqlite3.Error as exc:
        log("cannot read the catalog: %s" % exc)
        return 1

    if unchecked and not args.force:
        log("waiting: %d channels still unchecked, %d known dead" % (unchecked, len(dead)))
        return 0
    if unchecked:
        log("--force: %d channels unchecked (left alone), %d known dead" % (unchecked, len(dead)))
    if not dead:
        log("nothing to delete: every checked channel is online")
        if not unchecked:
            _mark_done("no dead channels", guard)
        return 0

    log("deleting %d channels (no_peers, not_found or error)" % len(dead))
    if args.dry_run:
        for _, title, content_id, channel_status, peers in dead[:10]:
            log("  would delete: %s [%s] %s peers=%s" % (title[:40], channel_status, content_id, peers))
        if len(dead) > 10:
            log("  ... and %d more" % (len(dead) - 10))
        return 0

    try:
        destination = backup(dead)
    except (OSError, sqlite3.Error) as exc:
        log("backup failed, nothing deleted: %s" % exc)
        return 1
    log("backup in %s (%d files)" % (destination, len(os.listdir(destination))))

    deleted, failed = 0, []
    for channel_id, title, _, _, _ in dead:
        try:
            if delete(channel_id):
                deleted += 1
            else:
                failed.append(channel_id)
        except (urllib.error.URLError, OSError) as exc:
            failed.append(channel_id)
            log("  could not delete %d (%s): %s" % (channel_id, title[:40], exc))
    log("deleted %d of %d; %d failed" % (deleted, len(dead), len(failed)))

    if not refresh_playlists():
        log("the playlists could not be regenerated; the deletion stands")
    if failed:
        log("there are failures: the next run will try them again")
        return 1
    _mark_done("%d deleted" % deleted, guard)
    return 0


def _mark_done(what, path=None):
    path = path or GUARD_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), what))
    except OSError as exc:
        log("cannot write the guard %s: %s" % (path, exc))
    log("done: %s" % what)


if __name__ == "__main__":
    lock = open(LOCK_PATH, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another run is still going; nothing done")
        sys.exit(0)
    sys.exit(main())
