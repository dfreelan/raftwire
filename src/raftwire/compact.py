"""Log truncation and snapshot compaction helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from raftwire.errors import CompactError, SnapshotError
from raftwire.logstore import LogStore, MemoryLogStore
from raftwire.types import (
    ConfState,
    Entry,
    HardState,
    Snapshot,
    SnapshotMetadata,
)


@dataclass(frozen=True, slots=True)
class CompactStats:
    """Result of a compaction operation."""

    discarded: int
    first_index_before: int
    first_index_after: int
    snapshot_index: int
    snapshot_term: int
    bytes_before: int
    bytes_after: int


@dataclass(frozen=True, slots=True)
class TruncateStats:
    """Result of a suffix truncation."""

    removed: int
    truncate_index: int
    last_index_after: int


@dataclass
class CompactionPolicy:
    """When to trigger snapshot compaction.

    Attributes
    ----------
    max_entries:
        Compact if live entry count exceeds this (``None`` = ignore).
    max_bytes:
        Compact if approximate entry bytes exceed this.
    retain_entries:
        Keep at least this many of the newest entries after compaction
        when using size-based policies (snapshot still covers the rest).
    """

    max_entries: int | None = 10_000
    max_bytes: int | None = 64 * 1024 * 1024
    retain_entries: int = 0

    def should_compact(self, store: LogStore) -> bool:
        n = len(store)
        if self.max_entries is not None and n > self.max_entries:
            return True
        if self.max_bytes is not None:
            size = sum(e.size() for e in store.all_entries())
            if size > self.max_bytes:
                return True
        return False


def truncate_suffix(store: LogStore, index: int) -> TruncateStats:
    """Remove all entries with index ``>= index``.

    Raises
    ------
    CompactError
        If *index* is below the first available index (would wipe protected
        prefix without a snapshot install).
    """
    before = len(store)
    store.truncate_suffix(index)
    after = len(store)
    return TruncateStats(
        removed=before - after,
        truncate_index=index,
        last_index_after=store.last_index(),
    )


def truncate_conflicts(
    store: LogStore,
    prev_index: int,
    prev_term: int,
    new_entries: Sequence[Entry],
) -> TruncateStats:
    """Raft AppendEntries conflict resolution.

    Given the leader's ``prev_log_index`` / ``prev_log_term`` and the new
    entries to append, truncate any conflicting suffix and return stats.
    Does **not** append *new_entries* — caller does that.
    """
    li = store.last_index()
    if prev_index > li:
        raise CompactError(
            f"prev_index {prev_index} past last_index {li}"
        )
    if prev_index > 0:
        try:
            t = store.term(prev_index)
        except Exception as exc:
            raise CompactError(f"cannot read term at {prev_index}: {exc}") from exc
        if t != prev_term:
            raise CompactError(
                f"term mismatch at {prev_index}: local={t} remote={prev_term}"
            )

    # Find first conflict among new_entries vs existing log.
    truncate_at: int | None = None
    for ent in new_entries:
        if ent.index > store.last_index():
            break
        if ent.index < store.first_index():
            continue
        local = store.entry(ent.index)
        if local.term != ent.term:
            truncate_at = ent.index
            break

    if truncate_at is None:
        return TruncateStats(
            removed=0,
            truncate_index=prev_index + 1,
            last_index_after=store.last_index(),
        )
    return truncate_suffix(store, truncate_at)


def install_snapshot(store: LogStore, snap: Snapshot) -> CompactStats:
    """Install *snap* and discard covered log prefix.

    Raises
    ------
    SnapshotError
        If the snapshot index regresses relative to the current snapshot.
    """
    current = store.get_snapshot()
    if (
        not current.is_empty()
        and snap.metadata.index < current.metadata.index
    ):
        raise SnapshotError(
            f"snapshot index regression: "
            f"{snap.metadata.index} < {current.metadata.index}"
        )

    fi_before = store.first_index()
    bytes_before = sum(e.size() for e in store.all_entries())
    n_before = len(store)

    store.set_snapshot(snap)

    # Advance commit if needed.
    hs = store.get_hard_state()
    if hs.commit < snap.metadata.index:
        store.set_hard_state(hs.with_commit(snap.metadata.index))

    fi_after = store.first_index()
    bytes_after = sum(e.size() for e in store.all_entries())
    n_after = len(store)

    return CompactStats(
        discarded=max(0, n_before - n_after),
        first_index_before=fi_before,
        first_index_after=fi_after,
        snapshot_index=snap.metadata.index,
        snapshot_term=snap.metadata.term,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
    )


def compact_to(
    store: LogStore,
    index: int,
    *,
    term: int | None = None,
    data: bytes = b"",
    conf_state: ConfState | None = None,
) -> CompactStats:
    """Create and install a snapshot covering entries through *index*.

    If *term* is omitted, the term of the entry at *index* is used (or the
    existing snapshot term if *index* equals the current snapshot index).
    """
    if index < 0:
        raise CompactError("compact index must be non-negative")

    snap_existing = store.get_snapshot()
    if index == 0:
        snap = Snapshot(
            data=data,
            metadata=SnapshotMetadata(
                conf_state=conf_state or ConfState(),
                index=0,
                term=0,
            ),
        )
        return install_snapshot(store, snap)

    if term is None:
        if not snap_existing.is_empty() and index == snap_existing.metadata.index:
            term = snap_existing.metadata.term
        elif index < store.first_index():
            raise CompactError(
                f"index {index} already compacted "
                f"(first_index={store.first_index()})"
            )
        elif index > store.last_index():
            raise CompactError(
                f"index {index} past last_index {store.last_index()}"
            )
        else:
            term = store.term(index)

    assert term is not None
    cs = conf_state
    if cs is None:
        cs = (
            snap_existing.metadata.conf_state
            if not snap_existing.is_empty()
            else ConfState()
        )

    snap = Snapshot(
        data=data,
        metadata=SnapshotMetadata(conf_state=cs, index=index, term=term),
    )
    return install_snapshot(store, snap)


def maybe_compact(
    store: LogStore,
    policy: CompactionPolicy,
    *,
    snapshot_data_fn: Callable[[LogStore], bytes] | None = None,
    conf_state: ConfState | None = None,
) -> CompactStats | None:
    """Compact according to *policy* if thresholds are exceeded.

    Snapshots through ``last_index - retain_entries``. Returns ``None`` if
    no compaction was needed.
    """
    if not policy.should_compact(store):
        return None
    li = store.last_index()
    target = li - policy.retain_entries
    fi = store.first_index()
    if target < fi:
        return None
    data = b""
    if snapshot_data_fn is not None:
        data = snapshot_data_fn(store)
    return compact_to(store, target, data=data, conf_state=conf_state)


def discard_until(store: LogStore, index: int) -> CompactStats:
    """Alias for :func:`compact_to` with empty snapshot data.

    Kept for CLI / API clarity: "discard log prefix until index".
    """
    return compact_to(store, index, data=b"")


def estimate_storage(store: LogStore) -> dict[str, int]:
    """Return a rough storage breakdown for *store*."""
    ents = store.all_entries()
    snap = store.get_snapshot()
    return {
        "entry_count": len(ents),
        "entry_bytes": sum(e.size() for e in ents),
        "snapshot_bytes": snap.size(),
        "first_index": store.first_index(),
        "last_index": store.last_index(),
        "commit": store.get_hard_state().commit,
    }


__all__ = [
    "CompactStats",
    "TruncateStats",
    "CompactionPolicy",
    "truncate_suffix",
    "truncate_conflicts",
    "install_snapshot",
    "compact_to",
    "maybe_compact",
    "discard_until",
    "estimate_storage",
]
