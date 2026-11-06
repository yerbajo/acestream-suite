"""Locating the engine executable that Ace Stream Media installs, and launching it."""

import os
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

# Where the installer puts it, relative to whichever root this Windows account uses.
_ENGINE = Path("ACEStream", "engine", "ace_engine.exe")
# Per-user first: an install that needed no administrator comes before the machine-wide one.
_USER_ROOTS = ("APPDATA", "LOCALAPPDATA")
_MACHINE_ROOTS = ("ProgramFiles", "ProgramFiles(x86)")


def _engine_under(root: str) -> Path:
    return Path(root) / _ENGINE


def engine_candidates() -> list[Path]:
    """Every path the installer may have used, in the order worth trying."""
    found = [_engine_under(os.environ[name]) for name in _USER_ROOTS if os.environ.get(name)]
    # The two machine-wide variables can name the same folder, or none at all.
    shared = sorted(root for root in {os.environ.get(name) for name in _MACHINE_ROOTS} if root)
    return found + [_engine_under(root) for root in shared]


def find_engine_executable(candidates: Iterable[Path] | None = None) -> str | None:
    """The first candidate that is actually a file, or None when the engine is not installed."""
    for candidate in engine_candidates() if candidates is None else candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class EngineStartError(Exception):
    """Asking the engine executable to start did not work."""


def start_engine(
    path: str, *, launcher: Callable[..., subprocess.Popen] = subprocess.Popen
) -> None:
    """Launches the engine and returns immediately: it needs a few seconds before it answers.

    The process is detached from ours, the way a player is, so closing acestream-suite
    does not take the engine down with it.
    """
    executable = Path(path)
    if not executable.is_file():
        raise EngineStartError(f"engine executable not found: {path}")
    try:
        # An argument list and no shell, so a path with spaces or quotes is never parsed
        # by a command interpreter.
        launcher(
            [str(executable)],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as exc:
        raise EngineStartError(f"could not start the engine: {exc}") from exc
