"""Deciding whether a stream is alive, by asking the engine about it for a while.

The engine never answers "is this channel alive": it reports what it is doing right now, so
aliveness is a judgement over several polls. This module makes that judgement in one place
and says nothing about storing it — the check history does that.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.acestream.client import EngineClient, StreamSession, StreamStats
from app.acestream.content_id import parse_content_id
from app.acestream.errors import ContentNotFoundError, EngineError, EngineUnavailableError
from app.db.models import CheckStatus


@dataclass(frozen=True)
class VerificationSettings:
    """How patient a check is, and how much of a swarm it asks for."""

    min_peers: int = 1
    timeout: float = 20.0
    poll_interval: float = 2.0

    def __post_init__(self) -> None:
        if self.min_peers < 0:
            raise ValueError("min_peers must not be negative")
        if min(self.timeout, self.poll_interval) <= 0:
            raise ValueError("timeout and poll_interval must be positive")


@dataclass(frozen=True)
class VerificationResult:
    """What one check concluded; the fields are what the history stores."""

    status: CheckStatus
    # The best peer count seen, not the last one: a swarm comes and goes during a check.
    peers: int | None = None
    speed_down: int | None = None
    infohash: str | None = None
    error_message: str | None = None


def probe(
    client: EngineClient,
    session: StreamSession,
    settings: VerificationSettings | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> VerificationResult:
    """Polls a session the caller started and owns, and never stops it.

    Only the engine's own downloading state counts: a stream in `prebuf`, or one with
    peers but no bytes, has not delivered anything yet. The `sleep` and `clock` arguments
    are what let a test spend the whole timeout without waiting for it.
    """
    settings = settings or VerificationSettings()
    deadline = clock() + settings.timeout
    peers: int | None = None
    while True:
        stats = client.stats(session)
        if stats.peers is not None:
            peers = stats.peers if peers is None else max(peers, stats.peers)
        if _delivers(stats, settings.min_peers):
            return _result(CheckStatus.ALIVE, peers, stats, session)
        left = deadline - clock()
        if left <= 0:
            return _result(CheckStatus.NO_PEERS, peers, stats, session)
        sleep(min(settings.poll_interval, left))


def _delivers(stats: StreamStats, min_peers: int) -> bool:
    """Whether this poll is proof that the stream is coming down.

    All three conditions are needed: the engine must have reached the downloading state,
    must have received bytes, and must be backed by the peers the caller asked for.
    """
    return stats.status == "dl" and (stats.downloaded or 0) > 0 and (stats.peers or 0) >= min_peers


def _result(
    status: CheckStatus, peers: int | None, stats: StreamStats, session: StreamSession
) -> VerificationResult:
    """The verdict, with the measurements of the poll that produced it."""
    return VerificationResult(status, peers, stats.speed_down, session.infohash)


def verify(
    client: EngineClient,
    content_id: str,
    settings: VerificationSettings | None = None,
    *,
    on_alive: Callable[[StreamSession], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> VerificationResult:
    """Starts a session, probes it and stops it again, whatever happens.

    An unusable Content ID is refused before the engine is asked. A stream the engine does
    not know is `NOT_FOUND`; a failure while polling is `ERROR` with the engine's own
    message, because that is what the user has to be told. An engine that cannot be
    reached is raised instead: it says nothing about the channel, so storing it as the
    channel's outcome would be a lie that survives the restart of the engine.

    `on_alive` runs while the session is still playing — the only moment a frame can be
    captured from it. What it raises is not swallowed: the caller's failure is the report.
    """
    settings = settings or VerificationSettings()
    content_id = parse_content_id(content_id)
    try:
        with client.stream(content_id) as session:
            result = probe(client, session, settings, sleep=sleep, clock=clock)
            if on_alive is not None and result.status is CheckStatus.ALIVE:
                on_alive(session)
            return result
    except ContentNotFoundError:
        return VerificationResult(CheckStatus.NOT_FOUND)
    except EngineUnavailableError:
        raise
    except EngineError as exc:
        return VerificationResult(CheckStatus.ERROR, error_message=str(exc))


async def verify_async(
    client: EngineClient,
    content_id: str,
    settings: VerificationSettings | None = None,
    **kwargs,
) -> VerificationResult:
    """`verify` for a request handler: the polling waits in a thread, not in the event loop."""
    return await asyncio.to_thread(verify, client, content_id, settings, **kwargs)
