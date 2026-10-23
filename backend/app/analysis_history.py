"""Bound terminal analysis records by completion age and aggregate count."""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import math
from time import perf_counter

from .schemas import AnalysisState


_TERMINAL = {"completed", "failed"}


@dataclass
class _TerminalRecord:
    state: AnalysisState
    store: dict[str, AnalysisState]
    finished_at: float


class AnalysisHistory:
    """Use on the event-loop thread, after the scheduler finishes job cleanup.

    Entries hold state/store references, never the runnable or its input closure.
    Removing an entry drops container references without mutating a result that
    an in-flight reader may still hold.
    """

    def __init__(self, ttl: float = 600.0, capacity: int = 100,
                 *, clock: Callable[[], float] = perf_counter,
                 on_remove: Callable[[str], None] | None = None):
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be finite and positive")
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.ttl = ttl
        self.capacity = capacity
        self._clock = clock
        self._on_remove = on_remove
        self._records: OrderedDict[str, _TerminalRecord] = OrderedDict()

    def __len__(self) -> int:
        return len(self._records)

    def remember(self, state: AnalysisState, store: dict[str, AnalysisState]) -> None:
        identity = state.analysisId
        if state.status not in _TERMINAL or store.get(identity) is not state:
            return
        previous = self._records.get(identity)
        if previous is not None and previous.state is state:
            # Repeated notification must not extend the original retention time.
            self.prune()
            return
        if previous is not None:
            self._discard(identity)
        self._records[identity] = _TerminalRecord(state, store, self._clock())
        self.prune()

    def _discard(self, identity: str) -> None:
        record = self._records.pop(identity)
        # A replacement or reactivated object does not belong to this entry.
        if (record.store.get(identity) is record.state
                and record.state.status in _TERMINAL):
            del record.store[identity]
            if self._on_remove is not None:
                self._on_remove(identity)

    def prune(self) -> int:
        now = self._clock()
        removed = 0
        while self._records:
            identity, oldest = next(iter(self._records.items()))
            if now - oldest.finished_at < self.ttl and len(self) <= self.capacity:
                break
            self._discard(identity)
            removed += 1
        return removed

    def clear(self) -> None:
        """Discard managed terminal records at the end of the application lifetime."""
        while self._records:
            self._discard(next(iter(self._records)))
