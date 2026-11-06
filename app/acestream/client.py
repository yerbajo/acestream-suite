"""Client for the Acestream engine's HTTP API; its shapes are in docs/engine-api.md."""

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.acestream.content_id import parse_content_id
from app.acestream.errors import (
    ContentNotFoundError,
    EngineError,
    EngineProtocolError,
    EngineTimeoutError,
    EngineUnavailableError,
)
from app.config import ENGINE_TIMEOUT, ENGINE_URL, STREAM_PARAM

log = logging.getLogger(__name__)

# The engine's own wording for "I could not resolve that Content ID into content".
_NO_CONTENT = "failed to load content"
_VERSION_PATH = "webui/api/service"
_MEDIA_FILES_PATH = "server/api"
_STREAM_PATH = "ace/getstream"


@dataclass(frozen=True)
class EngineVersion:
    """The engine describing itself."""

    version: str
    code: int
    platform: str


@dataclass(frozen=True)
class StreamSession:
    """One stream the engine has open, with the URLs the engine itself handed out."""

    content_id: str
    infohash: str
    playback_url: str
    stat_url: str
    command_url: str


@dataclass(frozen=True)
class StreamStats:
    """One reading of an open session.

    The status walks from `idle` through `prebuf` to `dl`, and the counters only exist
    once the engine has transferred something, hence the Nones.
    """

    status: str
    peers: int | None
    speed_down: int | None
    downloaded: int | None


class EngineClient:
    """Calls to the engine, made synchronously.

    Every method blocks on HTTP, so a web request has to run it in a worker thread and
    never on the event loop.
    """

    def __init__(self, base_url: str = ENGINE_URL, *, timeout: float = ENGINE_TIMEOUT):
        self._base_url = base_url.rstrip("/") + "/"
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "EngineClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def version(self) -> EngineVersion:
        reported = self._payload(
            self._json(self._endpoint(_VERSION_PATH), {"method": "get_version"}), "result"
        )
        try:
            return EngineVersion(
                version=str(reported["version"]),
                code=int(reported["code"]),
                platform=str(reported["platform"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EngineProtocolError("unexpected get_version response") from exc

    def is_available(self) -> bool:
        """Whether the engine answers at all; a nonsense answer counts as not answering."""
        try:
            self.version()
        except EngineError:
            return False
        return True

    def media_name(self, content_id: str) -> str | None:
        """The name the content is published under, without opening a session.

        None means the engine could not tell (deciding takes it a few seconds). A broken
        or unreachable engine still raises.
        """
        content_id = parse_content_id(content_id)
        answer = self._json(
            self._endpoint(_MEDIA_FILES_PATH),
            {"api_version": "3", "method": "get_media_files", "content_id": content_id},
        )
        if answer.get("error"):
            return None
        described = answer.get("result")
        if not isinstance(described, dict):
            raise EngineProtocolError("the engine did not describe the content")
        return _published_name(described)

    def start_stream(self, content_id: str) -> StreamSession:
        """Opens a session; a malformed Content ID is refused before the engine is asked."""
        content_id = parse_content_id(content_id)
        answer = self._json(
            self._endpoint(_STREAM_PATH),
            {STREAM_PARAM: content_id, "format": "json", "pid": str(uuid.uuid4())},
        )
        if answer.get("error") == _NO_CONTENT:
            raise ContentNotFoundError(content_id)
        started = self._payload(answer, "response")
        try:
            return StreamSession(
                content_id=content_id,
                infohash=str(started["infohash"]),
                playback_url=self._local(started["playback_url"]),
                stat_url=self._local(started["stat_url"]),
                command_url=self._local(started["command_url"]),
            )
        except (KeyError, TypeError) as exc:
            raise EngineProtocolError("unexpected getstream response") from exc

    def stats(self, session: StreamSession) -> StreamStats:
        reading = self._payload(self._json(session.stat_url), "response")
        if not reading:
            # A session that has just started can be read before it holds any state.
            return StreamStats(status="idle", peers=None, speed_down=None, downloaded=None)
        try:
            return StreamStats(
                status=str(reading["status"]),
                peers=reading.get("peers"),
                speed_down=reading.get("speed_down"),
                downloaded=reading.get("downloaded"),
            )
        except KeyError as exc:
            raise EngineProtocolError("unexpected stat response") from exc

    def stop(self, session: StreamSession) -> None:
        answer = self._json(session.command_url, {"method": "stop"})
        if self._payload(answer, "response", str) != "ok":
            raise EngineProtocolError("engine did not confirm stopping the session")

    @contextmanager
    def stream(self, content_id: str) -> Iterator[StreamSession]:
        """Starts a session and stops it again, whatever the block does in between."""
        session = self.start_stream(content_id)
        try:
            yield session
        finally:
            try:
                self.stop(session)
            except EngineError:
                # The error on its way out of the block matters more than a failed stop,
                # so this one is only logged and never allowed to replace it.
                log.warning("could not stop session for %s", session.content_id, exc_info=True)

    def _endpoint(self, path: str) -> str:
        return urljoin(self._base_url, path)

    def _local(self, url: str) -> str:
        """The engine's own path on our configured host, so a session URL cannot lead elsewhere."""
        parts = urlsplit(url)
        path = parts.path.lstrip("/")
        return urljoin(self._base_url, path + (f"?{parts.query}" if parts.query else ""))

    def _json(self, url: str, params: dict[str, str] | None = None) -> dict:
        """GET an endpoint and read its answer as a JSON object."""
        try:
            response = self._http.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise EngineTimeoutError(f"engine did not answer in time: {url}") from exc
        except httpx.TransportError as exc:
            raise EngineUnavailableError(f"cannot reach the engine at {self._base_url}") from exc
        if response.status_code != 200:
            raise EngineProtocolError(f"unexpected HTTP {response.status_code} from engine")
        try:
            answer = response.json()
        except ValueError as exc:
            raise EngineProtocolError("engine returned invalid JSON") from exc
        if not isinstance(answer, dict):
            raise EngineProtocolError("engine returned an unexpected JSON shape")
        return answer

    @staticmethod
    def _payload(answer: dict, key: str, kind: type = dict):
        """What sits under `key`, once the engine's own error field has been ruled out.

        Every answer arrives as HTTP 200, failures included, so the body is the only
        place that says whether the call worked.
        """
        if answer.get("error"):
            raise EngineProtocolError(f"engine error: {answer['error']}")
        value = answer.get(key)
        if not isinstance(value, kind):
            raise EngineProtocolError(f"engine response has no valid '{key}'")
        return value


def _published_name(described: dict) -> str | None:
    """The content's `name`, or the first file's name when only the file list was filled."""
    name = described.get("name")
    if not name:
        files = described.get("files") or []
        first = files[0] if files else None
        name = first.get("filename") if isinstance(first, dict) else None
    return str(name) if name else None
