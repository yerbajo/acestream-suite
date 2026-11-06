"""Checking many channels at once, a few at a time, without holding the request open.

A batch is shared state: the request that starts it, the page that polls it and the worker
threads that fill it all reach the same object. Every change happens under one lock, and
each change is something a page can show — how far the run got, what each channel ended
as, and whether it was called off.
"""

import logging
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from app.db.base import utcnow
from app.db.models import CheckStatus

log = logging.getLogger(__name__)

FAILED = "failed"  # the check raised, so there is no status to file it under


class AbortBatch(Exception):
    """A check that proves the rest cannot succeed either: the engine itself is gone."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# Verifies one channel and returns the status stored for it. Runs in a worker thread.
CheckWork = Callable[[int], CheckStatus]


@dataclass
class Batch:
    """A run of checks as a page sees it: how far it got and how it ended."""

    id: str
    started_at: datetime
    total: int = 0
    done: int = 0
    counts: Counter = field(default_factory=Counter)  # status value or FAILED -> channels
    finished_at: datetime | None = None
    aborted: str | None = None  # the `AbortBatch` reason, once the run was called off
    pending: set[int] = field(default_factory=set)  # queued, no answer yet
    finished_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def finished(self) -> bool:
        return self.finished_at is not None


class CheckRunner:
    """The app's single run of checks, backed by one thread pool.

    A check asked for while a run is going joins it, so the progress on screen always
    covers everything still pending; a channel already in the queue is not checked twice.
    A check that raises is one channel's problem: it is counted as failed and the run
    continues. A check that raises `AbortBatch` is the opposite — the whole run stops.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._batch: Batch | None = None
        self._pool: ThreadPoolExecutor | None = None

    def submit(
        self, channel_ids: Iterable[int], work: CheckWork, *, max_workers: int
    ) -> Batch | None:
        """Queues the channels and returns the batch they joined, or None if nothing was asked."""
        wanted = list(dict.fromkeys(channel_ids))  # the same channel twice is still one check
        with self._lock:
            if self._batch is None or self._batch.finished:
                if not wanted:
                    return None
                self._open(max_workers)
            batch = self._batch
            fresh = [channel_id for channel_id in wanted if channel_id not in batch.pending]
            batch.total += len(fresh)
            batch.pending.update(fresh)
            for channel_id in fresh:
                self._pool.submit(self._run, batch, channel_id, work)
            return batch

    def current(self) -> Batch | None:
        """The running batch, or the last one until the page dismisses it."""
        return self._batch

    def dismiss(self) -> None:
        """Forgets a finished batch. A running one is not going anywhere."""
        with self._lock:
            if self._batch is not None and self._batch.finished:
                self._batch = None

    def shutdown(self) -> None:
        """Drops the queued checks and lets the running ones finish (app shutdown)."""
        with self._lock:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)

    def _open(self, max_workers: int) -> None:
        """Starts an empty batch with the pool that will fill it."""
        self._batch = Batch(id=uuid.uuid4().hex[:12], started_at=utcnow())
        self._pool = ThreadPoolExecutor(max_workers, thread_name_prefix="check")

    def _run(self, batch: Batch, channel_id: int, work: CheckWork) -> None:
        """One worker's whole job: run the check and file its outcome."""
        try:
            outcome = CheckStatus(work(channel_id)).value
        except AbortBatch as exc:
            self._abort(batch, channel_id, exc.reason)
            return
        except Exception:
            # One broken check must not take the run down with it.
            log.exception("check of channel %s failed", channel_id)
            outcome = FAILED
        with self._lock:
            self._counted(batch, channel_id, outcome)

    def _counted(self, batch: Batch, channel_id: int, outcome: str) -> None:
        """Files one outcome. Called with the lock held."""
        batch.pending.discard(channel_id)
        if batch.aborted is not None:
            # It was already running when the run was called off: its outcome is stale.
            return
        batch.done += 1
        batch.counts[outcome] += 1
        if batch.done == batch.total:
            batch.finished_at = utcnow()
            self._retire(batch)
            batch.finished_event.set()

    def _abort(self, batch: Batch, channel_id: int, reason: str) -> None:
        """Ends the run where it is, keeping the checks that already answered."""
        with self._lock:
            batch.pending.discard(channel_id)
            if batch.finished:
                return
            log.warning("checks stopped after %s of %s: %s", batch.done, batch.total, reason)
            batch.aborted = reason
            batch.finished_at = utcnow()
            batch.pending.clear()
            self._retire(batch, cancel=True)
            batch.finished_event.set()

    def _retire(self, batch: Batch, *, cancel: bool = False) -> None:
        """Lets the pool go once this batch is the one that owns it.

        The ownership check matters: a request can already have replaced the finished batch
        with a new one, and this pool is not that batch's to shut down.
        """
        if self._batch is batch and self._pool is not None:
            # After an abort the queued checks would fail the same way, so they are dropped.
            self._pool.shutdown(wait=False, cancel_futures=cancel)
