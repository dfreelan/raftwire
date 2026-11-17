"""Log index / term / commit invariant tests."""

from __future__ import annotations

import pytest

from raftwire.compact import compact_to, truncate_suffix
from raftwire.errors import InvariantError, LogStoreError
from raftwire.logstore import MemoryLogStore, make_entries
from raftwire.types import Entry, HardState


def test_contiguous_indices_invariant():
    store = MemoryLogStore()
    store.append(*make_entries(1, 50, term=1))
    store.validate()
    for i in range(store.first_index(), store.last_index() + 1):
        assert store.entry(i).index == i


def test_term_non_decreasing():
    store = MemoryLogStore()
    batch = (
        [Entry(term=1, index=i, data=b"a") for i in range(1, 6)]
        + [Entry(term=2, index=i, data=b"b") for i in range(6, 11)]
        + [Entry(term=2, index=i, data=b"c") for i in range(11, 16)]
    )
    store.append(*batch)
    store.validate()
    prev = 0
    for e in store.all_entries():
        assert e.term >= prev
        prev = e.term


def test_commit_not_past_last():
    store = MemoryLogStore()
    store.append(*make_entries(1, 10))
    store.set_hard_state(HardState(term=1, vote=1, commit=10))
    store.validate()
    store.set_hard_state(HardState(term=1, vote=1, commit=100))
    with pytest.raises(InvariantError):
        store.validate()


def test_truncate_preserves_prefix_invariants():
    store = MemoryLogStore()
    store.append(*make_entries(1, 20, term=3))
    truncate_suffix(store, 12)
    store.set_hard_state(HardState(term=3, vote=1, commit=11))
    store.validate()
    assert store.last_index() == 11


def test_compaction_first_index_invariant():
    store = MemoryLogStore()
    store.append(*make_entries(1, 40, term=2))
    compact_to(store, 25, data=b"x")
    store.set_hard_state(HardState(term=2, vote=1, commit=30))
    store.validate()
    assert store.first_index() == 26
    assert store.get_snapshot().metadata.index == 25


def test_entry_index_mismatch_detected():
    store = MemoryLogStore()
    store.append(*make_entries(1, 5))
    # Corrupt internal list (test-only) to violate invariant
    store._entries[2] = Entry(term=1, index=999, data=b"bad")
    with pytest.raises(InvariantError) as ei:
        store.validate()
    assert ei.value.invariant == "index_mismatch"


def test_term_regression_detected():
    store = MemoryLogStore()
    store.append(
        Entry(term=2, index=1, data=b"a"),
        Entry(term=3, index=2, data=b"b"),
    )
    store._entries[1] = Entry(term=1, index=2, data=b"b")
    with pytest.raises(InvariantError) as ei:
        store.validate()
    assert ei.value.invariant == "term_regression"


def test_append_after_compaction_contiguous():
    store = MemoryLogStore()
    store.append(*make_entries(1, 10, term=1))
    compact_to(store, 10, data=b"full")
    assert store.last_index() == 10
    assert store.first_index() == 11
    store.append(Entry(term=2, index=11, data=b"next"))
    store.validate()
    assert store.last_index() == 11
