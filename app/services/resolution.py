"""Reading a JPEG's frame size and turning it into one of the app's resolution classes."""

import struct
from pathlib import Path

from app.db.models import Resolution
from app.services.errors import ValidationError

# Frame size lives in a Start Of Frame segment. These are the marker bytes that open one;
# C4, C8 and CC fall in the same range but hold something else entirely.
_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_FRAME_SIZE_ENDS = 9  # marker (2) + segment length (2) + precision (1) + height and width (4)
_JPEG_START = b"\xff\xd8"

# Height decides the class; the width is only there to catch a wide, short 4K frame.
_CLASSES_BY_HEIGHT = (
    (1000, 1200, Resolution.FULL_HD),
    (680, 800, Resolution.HD),
    (440, 500, Resolution.SD),
)
_UHD_HEIGHT = 2000
_UHD_WIDTH = 3800


def frame_size(path: Path) -> tuple[int, int] | None:
    """(width, height) read from a JPEG's header, or None when the file is not one."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data.startswith(_JPEG_START):
        return None
    index = len(_JPEG_START)
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:
            # Padding a marker with extra 0xFF bytes is legal; the marker itself is the last one.
            index += 1
            continue
        if marker in _SOF_MARKERS:
            return _size_in_segment(data, index)
        (length,) = struct.unpack(">H", data[index + 2 : index + 4])
        index += 2 + length
    return None


def _size_in_segment(data: bytes, offset: int) -> tuple[int, int] | None:
    """The frame size an SOF segment declares, when the segment is really all there."""
    if offset + _FRAME_SIZE_ENDS > len(data):
        return None
    height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
    return (width, height) if width and height else None


def classify(width: int, height: int) -> Resolution:
    """The class a frame size belongs to, or `OTHER` when it fits none of the four."""
    if height >= _UHD_HEIGHT or width >= _UHD_WIDTH:
        return Resolution.UHD
    for lowest, below, kind in _CLASSES_BY_HEIGHT:
        if lowest <= height < below:
            return kind
    return Resolution.OTHER


def normalize_resolution(value: str | None) -> Resolution | None:
    """The resolution a user asked for; blank means "not known yet"."""
    text = (value or "").strip().lower()
    if not text:
        return None
    try:
        return Resolution(text)
    except ValueError:
        allowed = ", ".join(kind.value for kind in Resolution)
        raise ValidationError(f"unknown resolution '{value}' (use one of: {allowed})") from None
