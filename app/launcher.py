"""Starting the app on a desktop: prepare the database, pick a port, serve, open the browser.

Whoever gets here double-clicked something and wants a catalog, so every line on the console is for
them, in Spanish, and a failure ends in a sentence instead of a traceback. Also runs from source
with `python -m app.launcher`.
"""

import argparse
import json
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config

from app import __version__
from app.config import DATA_DIR, DATABASE_URL, FROZEN, RESOURCE_DIR

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PORTS_TO_TRY = 20  # the sweep that starts at DEFAULT_PORT and ends at 8019
READY_TIMEOUT = 60.0
POLL_SECONDS = 0.25  # how often the browser waits on a server that is still coming up
STATE_FILE = DATA_DIR / "server.json"  # where a running instance records its port
TITLE = "acestream-suite"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="acestream-suite", description="Catálogo local de Ace Stream."
    )
    parser.add_argument(
        "--port", type=int, help=f"puerto local (por defecto {DEFAULT_PORT} o el siguiente libre)"
    )
    parser.add_argument("--no-browser", action="store_true", help="no abrir el navegador")
    return parser.parse_args(argv)


def is_acestream_suite(port: int, timeout: float = 2.0) -> bool:
    """Whether this port is answered by an acestream-suite and not by some other program."""
    try:
        response = httpx.get(f"http://{HOST}:{port}/health", timeout=timeout)
        return response.status_code == 200 and response.json().get("app") == "acestream-suite"
    except (httpx.HTTPError, ValueError):
        return False


def running_port(state_file: Path = STATE_FILE) -> int | None:
    """The port of the instance already running over this data folder, if there is one.

    The state file is only a hint: it outlives a crash, so the port it names is believed only when
    a health answer confirms what is listening there.
    """
    try:
        port = int(json.loads(state_file.read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return port if is_acestream_suite(port) else None


def port_is_free(port: int) -> bool:
    """Whether binding the port right now would work. The probe lets go of it again at once."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def _port_sweep() -> range:
    """The ports tried when none was asked for: the default and the ones after it."""
    return range(DEFAULT_PORT, DEFAULT_PORT + PORTS_TO_TRY)


def choose_port(preferred: int | None = None) -> int:
    """The port asked for, or the first free one of the sweep; OSError when there is none.

    An asked-for port that is taken is refused rather than replaced: a bookmark or a script that
    names a port wants that port, not a quiet move to another.
    """
    if preferred is not None:
        if port_is_free(preferred):
            return preferred
        raise OSError(f"el puerto {preferred} está ocupado")
    ports = _port_sweep()
    for port in ports:
        if port_is_free(port):
            return port
    raise OSError(f"no hay puertos libres entre {ports[0]} y {ports[-1]}")


def upgrade_database(database_url: str = DATABASE_URL) -> None:
    """Creates or updates the database with the migrations that ship beside the app.

    Both the script location and the URL are set here, so the upgrade works from any working
    directory and inside the packaged executable, where neither can be guessed.
    """
    config = Config(str(RESOURCE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(RESOURCE_DIR / "migrations"))
    # ConfigParser reads "%" as interpolation, and a path is allowed to contain one.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def open_when_ready(
    url: str,
    port: int,
    *,
    opener: Callable[[str], object] = webbrowser.open,
    timeout: float = READY_TIMEOUT,
) -> bool:
    """Opens the browser once the server answers; False if it never did inside `timeout`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_acestream_suite(port, timeout=1.0):
            opener(url)
            return True
        time.sleep(POLL_SECONDS)
    return False


def set_console_title(title: str) -> None:
    """Names the console window on Windows, where it otherwise shows the path that started it."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except (AttributeError, OSError):
            pass


def serve(port: int) -> None:  # pragma: no cover - runs the real server until Ctrl+C
    import uvicorn

    from app.main import app

    uvicorn.run(app, host=HOST, port=port, log_level="info")


def _utf8_lines() -> None:
    """UTF-8 and one line at a time on both output streams.

    The console already gets both; a pipe or a redirected file would get the ANSI code page, and a
    Spanish accent would then arrive as a byte nobody can read.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", line_buffering=True)


def _open_browser_later(url: str, port: int) -> None:
    """Waits for the server on its own thread: the console must not be held by a slow start."""
    threading.Thread(target=open_when_ready, args=(url, port), daemon=True).start()


def _reuse_running_instance(port: int, *, open_browser: bool) -> int:
    """Sends the person to the instance that is already up, instead of starting a second one."""
    url = f"http://{HOST}:{port}/"
    print(f"acestream-suite ya está abierto en {url}")
    if open_browser:
        webbrowser.open(url)
    return 0


def _serve_new_instance(port: int, *, open_browser: bool, server: Callable[[int], None]) -> int:
    """Records the port for the next start, serves, and clears the record afterwards."""
    url = f"http://{HOST}:{port}/"
    STATE_FILE.write_text(json.dumps({"port": port}), encoding="utf-8")
    print(f"\nacestream-suite funciona en {url}")
    print("Deja esta ventana abierta mientras lo uses; ciérrala o pulsa Ctrl+C para salir.\n")
    if open_browser:
        _open_browser_later(url, port)
    try:
        server(port)
    finally:
        # However the server ended, the next start must not read this record as an instance up.
        STATE_FILE.unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None, *, server: Callable[[int], None] = serve) -> int:
    args = parse_args(argv)
    _utf8_lines()
    set_console_title(TITLE)
    print(f"acestream-suite {__version__}")
    print(f"Datos: {DATA_DIR}")

    already = running_port()
    if already is not None:
        return _reuse_running_instance(already, open_browser=not args.no_browser)

    try:
        port = choose_port(args.port)
        print("Preparando la base de datos…")
        upgrade_database()
    except Exception as exc:  # noqa: BLE001 - whatever went wrong, the person has to read it
        print(f"\nNo se pudo iniciar acestream-suite: {exc}")
        return 1

    return _serve_new_instance(port, open_browser=not args.no_browser, server=server)


def run() -> None:  # pragma: no cover - process entry point
    code = main()
    if code != 0 and FROZEN:
        # A double-clicked console closes the moment it ends: leave the reason on screen.
        input("Pulsa Enter para cerrar…")
    sys.exit(code)


if __name__ == "__main__":  # pragma: no cover
    run()
