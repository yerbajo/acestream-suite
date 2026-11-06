"""Handing a playing stream to VLC, or to whoever can play the engine's URL.

The engine can be told to start a stream from wherever this code runs; what it cannot do
is put it on a screen. That is a local player, resolved here once, and the reason this
module exists: it is the only place that knows where VLC lives on the machine.
"""

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlsplit

from app.acestream.client import EngineClient, StreamSession
from app.acestream.content_id import parse_content_id
from app.acestream.errors import EngineError
from app.config import PLAYBACK_MODE, VLC_PATH

# Where a Windows installer puts VLC, relative to the Program Files folder it was given.
_INSTALL_TAIL = ("VideoLAN", "VLC", "vlc.exe")
_PROGRAM_FILES = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
# A stream URL is only ever fetched over HTTP; anything else is not something to open.
_SCHEMES = ("http", "https")
_NO_VLC = "VLC not found: install it or set ACESTREAM_SUITE_VLC_PATH to vlc.exe"


class PlayerError(Exception):
    """The stream started but could not be handed to a player."""


class VlcNotFoundError(PlayerError):
    """There is no VLC to hand it to: not configured, not installed, not on PATH."""


def vlc_candidates() -> list[Path]:
    """The places a Windows VLC installer writes to, for the Program Files folders this
    machine has. Sorted, so the same machine always answers with the same order."""
    roots = {os.environ.get(name) for name in _PROGRAM_FILES}
    return [Path(root).joinpath(*_INSTALL_TAIL) for root in sorted(filter(None, roots))]


def find_vlc(
    configured: str | None = VLC_PATH, *, candidates: Iterable[Path] | None = None
) -> str | None:
    """The VLC to launch: the configured path, a usual install folder, then PATH.

    A configured path that does not exist returns None rather than a discovery found
    elsewhere: the user has already answered the question, and the answer was wrong.
    `candidates` replaces the install folders so a test can name them.
    """
    if configured:
        return configured if Path(configured).is_file() else None
    for candidate in vlc_candidates() if candidates is None else candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("vlc")


def open_in_player(
    client: EngineClient,
    content_id: str,
    *,
    title: str | None = None,
    vlc_path: str | None = None,
    launcher: Callable[..., subprocess.Popen] = subprocess.Popen,
    mode: str | None = None,
) -> StreamSession:
    """Starts the stream on the engine and opens it in VLC.

    The player is resolved before the engine is touched, so a machine without VLC never
    leaves a session running behind it. The session is deliberately kept open: VLC is the
    one reading it now, and only VLC closing it ends the stream.

    In `url` mode nothing is resolved and nothing is launched — the caller gets the
    session, and with it the `playback_url`, to hand to the browser that will play it.
    That is the mode for a host that has no player at all, which is the normal deployment.
    """
    mode = PLAYBACK_MODE if mode is None else mode
    content_id = parse_content_id(content_id)
    vlc = None if mode == "url" else _required_vlc(vlc_path)

    stream = client.start_stream(content_id)
    if urlsplit(stream.playback_url).scheme not in _SCHEMES:
        # Whatever the engine handed out, it is not a link a player should be given.
        _stop_after_failure(client, stream)
        raise PlayerError("playback URL must be http or https")

    if vlc is not None:
        try:
            _launch(launcher, _command(vlc, title, stream.playback_url))
        except OSError as exc:
            _stop_after_failure(client, stream)
            raise PlayerError(f"could not start VLC: {exc}") from exc
    return stream


def _required_vlc(vlc_path: str | None) -> str:
    """The player to launch, or the error a user can act on."""
    vlc = vlc_path or find_vlc()
    if not vlc:
        raise VlcNotFoundError(_NO_VLC)
    return vlc


def _command(vlc: str, title: str | None, playback_url: str) -> list[str]:
    """VLC's arguments. The title rides inside its option, so a name that looks like an
    option ("--extraintf=http") is still a value."""
    command = [vlc]
    if title:
        command.append(f"--meta-title={title}")
    command.append(playback_url)
    return command


def _launch(launcher: Callable[..., subprocess.Popen], command: list[str]) -> None:
    """Runs VLC detached from this process: an argument list, no shell, no streams attached."""
    launcher(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        # Windows: VLC has to outlive this process and its console. Elsewhere the flag is 0.
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )


def _stop_after_failure(client: EngineClient, stream: StreamSession) -> None:
    """Undoes the started session, keeping the original error as the one to report."""
    try:
        client.stop(stream)
    except EngineError:
        pass
