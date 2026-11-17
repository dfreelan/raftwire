"""Compaction and truncation tests."""

from __future__ import annotations

import pytest

from raftwire.compact import (
    CompactionPolicy,
    compact_to,
    discard_until,
    estimate_storage,
    install_snapshot,
    maybe_compact,
    truncate_conflicts,
    truncate_suffix,
)
from raftwire.errors import CompactError, SnapshotError
from raftwire.logstore import MemoryLogStore, make_entries
from raftwire.types import ConfState, Entry, Snapshot, SnapshotMetadata


def test_truncate_suffix_stats():
    store = MemoryLogStore()
    store.append(*make_entries(1, 10))
    stats = truncate_suffix(store, 6)
    assert stats.removed == 5
    assert store.last_index() == 5


def test_truncate_conflicts_no_conflict():
    store = MemoryLogStore()
    store.append(*make_entries(1, 5, term=1))
    new = [Entry(term=1, index=6, data=b"x")]
    stats = truncate_conflicts(store, 5, 1, new)
    assert stats.removed == 0


def test_truncate_conflicts_with_conflict():
    store = MemoryLogStore()
    store.append(*make_entries(1, 5, term=1))
    # Conflict at index 4: local term 1, remote term 2
    new = [
        Entry(term=1, index=3, data=b"a"),
        Entry(term=2, index=4, data=b"b"),
        Entry(term=2, index=5, data=b"c"),
    ]
    stats = truncate_conflicts(store, 2, 1, new)
    assert stats.removed >= 1
    assert store.last_index() == 3


def test_truncate_conflicts_prev_mismatch():
    store = MemoryLogStore()
    store.append(*make_entries(1, 3, term=1))
    with pytest.raises(CompactError):
        truncate_conflicts(store, 2, 99, [])


def test_install_and_compact_to():
    store = MemoryLogStore()
    store.append(*make_entries(1, 30, term=3))
    stats = compact_to(store, 20, data=b"SNAP", conf_state=ConfState(voters=(1, 2)))
    assert stats.discarded == 20
    assert stats.snapshot_index == 20
    assert store.first_index() == 21
    assert store.get_snapshot().data == b"SNAP"
    store.validate()


def test_snapshot_regression_rejected():
    store = MemoryLogStore()
    store.append(*make_entries(1, 10))
    compact_to(store, 8, data=b"a")
    with pytest.raises(SnapshotError):
        install_snapshot(
            store,
            Snapshot(data=b"b", metadata=SnapshotMetadata(index=5, term=1)),
        )


def test_compact_past_last_rejected():
    store = MemoryLogStore()
    store.append(*make_entries(1, 5))
    with pytest.raises(CompactError):
        compact_to(store, 50)


def test_discard_until_alias():
    store = MemoryLogStore()
    store.append(*make_entries(1, 15))
    stats = discard_until(store, 7)
    assert store.first_index() == 8
    assert stats.snapshot_index == 7


def test_maybe_compact_policy():
    store = MemoryLogStore()
    store.append(*make_entries(1, 50))
    policy = CompactionPolicy(max_entries=10, retain_entries=5)
    assert policy.should_compact(store)
    stats = maybe_compact(store, policy, snapshot_data_fn=lambda s: b"auto")
    assert stats is not None
    assert store.last_index() - store.first_index() + 1 <= 10
    # Below threshold → None
    assert maybe_compact(store, CompactionPolicy(max_entries=10_000)) is None


def test_estimate_storage():
    store = MemoryLogStore()
    store.append(*make_entries(1, 5, prefix=b"abcd"))
    info = estimate_storage(store)
    assert info["entry_count"] == 5
    assert info["entry_bytes"] > 0
