"""One JPEG per live channel, so the list can be read at a glance instead of clicked.

ffmpeg is the only thing here that knows how to decode the engine's stream, and it is
deliberately the only external program the app depends on at check time: when it is not
installed the check still happens, it just leaves the channel without a picture.
"""

import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.acestream.content_id import parse_content_id
from app.config import DATA_DIR, FFMPEG_PATH, SCREENSHOT_TIMEOUT

SCREENSHOTS_SUBDIR = "screenshots"  # under the data directory, and part of the stored path
# The engine serves the stream over HTTP. A `file:` or `concat:` source would make ffmpeg
# read whatever it is pointed at, so anything but these two schemes never reaches it.
_SCHEMES = ("http", "https")
# A closed protocol list, for the same reason: a redirect cannot add a protocol to the run.
_PROTOCOLS = "http,https,tcp,tls"
# Only the end of stderr is shown: the reason is there, the banner and the warnings are not.
_DETAIL = 300
_NO_FFMPEG = "ffmpeg not found: install it and add it to PATH, or set ACESTREAM_SUITE_FFMPEG_PATH"


@dataclass(frozen=True)
class CaptureSettings:
    """Which ffmpeg to use and how long it may take."""

    ffmpeg_path: str | None = None  # None: the configured setting first, then PATH
    timeout: float = SCREENSHOT_TIMEOUT


class ScreenshotError(Exception):
    """No picture this time. The channel keeps its check; it only lacks the frame."""


class FfmpegNotFoundError(ScreenshotError):
    """Nothing to take a picture with: the executable is neither configured nor on PATH."""


def find_ffmpeg(configured: str | None = FFMPEG_PATH) -> str | None:
    """The ffmpeg to run, or None when there is none.

    A configured path that does not exist returns None instead of falling back to PATH:
    the user said where it is, and quietly using another copy would hide the typo.
    """
    if configured:
        return configured if Path(configured).is_file() else None
    return shutil.which("ffmpeg")


def capture_screenshot(
    playback_url: str,
    content_id: str,
    *,
    data_dir: Path = DATA_DIR,
    ffmpeg_path: str | None = None,
    timeout: float = SCREENSHOT_TIMEOUT,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    """Saves one frame of an already playing stream and returns its path inside `data_dir`.

    The name comes from the validated Content ID, a timestamp and a random suffix, so it
    can never point outside `data_dir` and two captures in the same second are still two
    files. The frame is written to a `.part.jpg` first and only renamed once it holds
    something: a failed capture leaves nothing behind for a gallery to show as a picture.
    """
    content_id = parse_content_id(content_id)
    if urlsplit(playback_url).scheme not in _SCHEMES:
        raise ScreenshotError("playback URL must be http or https")
    ffmpeg = ffmpeg_path or find_ffmpeg()
    if not ffmpeg:
        raise FfmpegNotFoundError(_NO_FFMPEG)

    relative, final, partial = _targets(content_id, data_dir)
    partial.parent.mkdir(parents=True, exist_ok=True)
    _write_frame(runner, _command(ffmpeg, playback_url, partial), timeout, partial)
    os.replace(partial, final)
    return relative


def _targets(content_id: str, data_dir: Path) -> tuple[str, Path, Path]:
    """The stored path, the file it names, and the `.part.jpg` the frame is written to."""
    # The Windows clock is coarse, so the timestamp can repeat within the same second; the
    # random suffix, not the clock, is what keeps two captures apart.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    relative = f"{SCREENSHOTS_SUBDIR}/{content_id}_{stamp}_{uuid.uuid4().hex[:8]}.jpg"
    final = data_dir / relative
    return relative, final, final.with_name(final.stem + ".part.jpg")


def _command(ffmpeg: str, playback_url: str, partial: Path) -> list[str]:
    """ffmpeg's argument list: one frame, no stdin, and no shell to interpret the URL."""
    return [
        ffmpeg,
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        _PROTOCOLS,
        "-i",
        playback_url,
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(partial),
    ]


def _write_frame(
    runner: Callable[..., subprocess.CompletedProcess],
    command: list[str],
    timeout: float,
    partial: Path,
) -> None:
    """Runs ffmpeg and insists that a non-empty frame came out; failures leave no partial file.

    A successful capture can still print decoder warnings while it waits for a keyframe,
    so the exit code and the size of the file are what decide, never the message.
    """
    try:
        completed = runner(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _discard(partial)
        raise ScreenshotError(f"ffmpeg timed out after {timeout:g} s") from exc
    except OSError as exc:
        _discard(partial)
        raise ScreenshotError(f"could not run ffmpeg: {exc}") from exc
    if completed.returncode != 0 or not _has_frame(partial):
        _discard(partial)
        detail = (completed.stderr or "").strip()[-_DETAIL:]
        raise ScreenshotError(f"ffmpeg produced no frame (exit {completed.returncode}): {detail}")


def _has_frame(path: Path) -> bool:
    """Whether ffmpeg really wrote a picture there, whatever it reported on exit."""
    return path.is_file() and path.stat().st_size > 0


def _discard(path: Path) -> None:
    path.unlink(missing_ok=True)
