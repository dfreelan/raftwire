"""Deterministic log replay engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, Sequence

from raftwire.codec import encode_entry, record_digest
from raftwire.crc import checksum_trail, crc32c, pack_crc32c
from raftwire.errors import ReplayError
from raftwire.logstore import LogStore
from raftwire.types import Entry, HardState, Snapshot


class StateMachine(Protocol):
    """Minimal protocol for a replaying state machine."""

    def apply(self, entry: Entry) -> Any:
        """Apply a single committed entry; return value is optional."""
        ...


class SnapshotAware(Protocol):
    """Optional protocol for state machines that can load snapshots."""

    def load_snapshot(self, snapshot: Snapshot) -> None:
        ...

    def take_snapshot(self) -> bytes:
        ...


ApplyFn = Callable[[Entry], Any]


@dataclass
class ReplayResult:
    """Outcome of a replay run."""

    applied: int
    last_applied: int
    checksum: int
    results: list[Any] = field(default_factory=list)


@dataclass
class Replayer:
    """Applies entries in order to a state machine.

    Parameters
    ----------
    state_machine:
        Object with an ``apply(entry)`` method, or any callable.
    last_applied:
        Index of the last applied entry (0 = nothing applied yet).
    collect_results:
        If True, stash each ``apply`` return value in :class:`ReplayResult`.
    """

    state_machine: Any
    last_applied: int = 0
    collect_results: bool = False
    _trail: list[bytes] = field(default_factory=list, repr=False)
    _checksum: int = 0

    def __post_init__(self) -> None:
        if self.last_applied < 0:
            raise ValueError("last_applied must be non-negative")

    @property
    def checksum(self) -> int:
        return self._checksum

    def _call_apply(self, entry: Entry) -> Any:
        sm = self.state_machine
        if callable(sm) and not hasattr(sm, "apply"):
            return sm(entry)
        return sm.apply(entry)

    def apply_one(self, entry: Entry) -> Any:
        """Apply a single entry; must be exactly ``last_applied + 1``."""
        expected = self.last_applied + 1
        if entry.index != expected:
            raise ReplayError(
                f"expected index {expected}, got {entry.index}",
                index=entry.index,
            )
        try:
            result = self._call_apply(entry)
        except ReplayError:
            raise
        except Exception as exc:
            raise ReplayError(
                f"state machine apply failed: {exc}",
                index=entry.index,
            ) from exc
        self.last_applied = entry.index
        blob = encode_entry(entry, with_crc=False)
        self._trail.append(blob)
        self._checksum = checksum_trail(self._trail)
        return result

    def replay(self, entries: Sequence[Entry] | Iterable[Entry]) -> ReplayResult:
        """Apply a sequence of entries in order."""
        results: list[Any] = []
        applied = 0
        for entry in entries:
            if entry.index <= self.last_applied:
                # Already applied — skip (idempotent catch-up).
                continue
            if entry.index > self.last_applied + 1:
                raise ReplayError(
                    f"gap: last_applied={self.last_applied}, next={entry.index}",
                    index=entry.index,
                )
            result = self.apply_one(entry)
            applied += 1
            if self.collect_results:
                results.append(result)
        return ReplayResult(
            applied=applied,
            last_applied=self.last_applied,
            checksum=self._checksum,
            results=results,
        )

    def replay_store(
        self,
        store: LogStore,
        *,
        up_to: int | None = None,
    ) -> ReplayResult:
        """Replay committed entries from *store*.

        Uses ``hard_state.commit`` as the upper bound unless *up_to* is given.
        Installs the store snapshot first if the state machine supports it and
        ``last_applied < snapshot.index``.
        """
        snap = store.get_snapshot()
        if (
            not snap.is_empty()
            and self.last_applied < snap.metadata.index
            and hasattr(self.state_machine, "load_snapshot")
        ):
            self.state_machine.load_snapshot(snap)
            self.last_applied = snap.metadata.index
            # Reset trail after snapshot — checksum covers post-snapshot only.
            self._trail.clear()
            self._checksum = 0

        commit = store.get_hard_state().commit if up_to is None else up_to
        commit = min(commit, store.last_index()) if store.last_index() else commit
        if commit <= self.last_applied:
            return ReplayResult(
                applied=0,
                last_applied=self.last_applied,
                checksum=self._checksum,
            )

        lo = self.last_applied + 1
        hi = commit + 1
        # Clamp to available entries.
        fi = store.first_index()
        if lo < fi:
            raise ReplayError(
                f"cannot replay from {lo}: first available is {fi} "
                "(install snapshot first)",
                index=lo,
            )
        entries = store.entries(lo, hi)
        return self.replay(entries)

    def verify_checksum(self, expected: int) -> None:
        if self._checksum != expected:
            raise ReplayError(
                f"checksum mismatch: expected 0x{expected:08X}, "
                f"got 0x{self._checksum:08X}"
            )

    def reset(self, *, last_applied: int = 0) -> None:
        self.last_applied = last_applied
        self._trail.clear()
        self._checksum = 0


def apply_entries(
    state_machine: Any,
    entries: Sequence[Entry],
    *,
    last_applied: int = 0,
) -> ReplayResult:
    """Convenience: create a :class:`Replayer` and replay *entries*."""
    r = Replayer(state_machine, last_applied=last_applied, collect_results=True)
    return r.replay(entries)


def compute_entries_checksum(entries: Sequence[Entry]) -> int:
    """Checksum trail over the CRC-less encodings of *entries*."""
    blobs = [encode_entry(e, with_crc=False) for e in entries]
    return checksum_trail(blobs)


@dataclass
class CountingMachine:
    """Example state machine: counts applied entries and sums integer data."""

    count: int = 0
    total: int = 0
    last_index: int = 0
    log: list[int] = field(default_factory=list)

    def apply(self, entry: Entry) -> int:
        self.count += 1
        self.last_index = entry.index
        try:
            n = int(entry.data)
        except (ValueError, TypeError):
            n = len(entry.data)
        self.total += n
        self.log.append(entry.index)
        return n

    def load_snapshot(self, snapshot: Snapshot) -> None:
        # Snapshot data format: ``count:total`` as ASCII, optional.
        self.last_index = snapshot.metadata.index
        if not snapshot.data:
            return
        try:
            text = snapshot.data.decode("utf-8")
            c, t = text.split(":", 1)
            self.count = int(c)
            self.total = int(t)
        except (ValueError, UnicodeDecodeError):
            self.count = 0
            self.total = len(snapshot.data)

    def take_snapshot(self) -> bytes:
        return f"{self.count}:{self.total}".encode("utf-8")





def fingerprint_store(store: LogStore) -> int:
    """Stable fingerprint of snapshot metadata + all entries + hard state."""
    parts: list[bytes] = []
    hs = store.get_hard_state()
    parts.append(
        f"hs:{hs.term}:{hs.vote}:{hs.commit}".encode()
    )
    snap = store.get_snapshot()
    parts.append(
        f"snap:{snap.metadata.index}:{snap.metadata.term}:{len(snap.data)}".encode()
    )
    for e in store.all_entries():
        parts.append(pack_crc32c(record_digest(e)))
    acc = 0
    for p in parts:
        acc = crc32c(pack_crc32c(acc) + p)
    return acc


__all__ = [
    "StateMachine",
    "SnapshotAware",
    "ReplayResult",
    "Replayer",
    "apply_entries",
    "compute_entries_checksum",
    "CountingMachine",
    "fingerprint_store",
]
