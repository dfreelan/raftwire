"""Deterministic replay tests."""

from __future__ import annotations

import pytest

from raftwire.errors import ReplayError
from raftwire.logstore import MemoryLogStore, make_entries
from raftwire.replay import (
    CountingMachine,
    Replayer,
    apply_entries,
    compute_entries_checksum,
    fingerprint_store,
)
from raftwire.types import Entry, HardState, Snapshot, SnapshotMetadata


def test_apply_entries_counting():
    sm = CountingMachine()
    entries = [
        Entry(term=1, index=1, data=b"10"),
        Entry(term=1, index=2, data=b"5"),
        Entry(term=1, index=3, data=b"7"),
    ]
    result = apply_entries(sm, entries)
    assert result.applied == 3
    assert sm.total == 22
    assert sm.count == 3
    assert result.checksum == compute_entries_checksum(entries)


def test_replayer_gap_detection():
    r = Replayer(CountingMachine())
    r.apply_one(Entry(term=1, index=1, data=b"1"))
    with pytest.raises(ReplayError):
        r.apply_one(Entry(term=1, index=3, data=b"3"))


def test_replayer_idempotent_skip():
    sm = CountingMachine()
    r = Replayer(sm)
    ents = [Entry(term=1, index=i, data=str(i).encode()) for i in range(1, 4)]
    r.replay(ents)
    result = r.replay(ents)  # replay again — should skip
    assert result.applied == 0
    assert sm.count == 3


def test_replay_store_with_commit():
    store = MemoryLogStore()
    ents = [Entry(term=1, index=i, data=str(i * 2).encode()) for i in range(1, 11)]
    store.append(*ents)
    store.set_hard_state(HardState(term=1, vote=1, commit=7))
    sm = CountingMachine()
    r = Replayer(sm)
    result = r.replay_store(store)
    assert result.applied == 7
    assert sm.total == sum(i * 2 for i in range(1, 8))


def test_replay_store_with_snapshot():
    store = MemoryLogStore()
    store.append(*[Entry(term=1, index=i, data=b"1") for i in range(1, 21)])
    store.set_hard_state(HardState(term=1, vote=1, commit=20))
    # Compact through 10
    from raftwire.compact import compact_to

    compact_to(store, 10, data=b"10:10")
    sm = CountingMachine()
    r = Replayer(sm)
    result = r.replay_store(store)
    assert r.last_applied == 20
    assert sm.last_index == 20
    # Snapshot loaded count/total from data, then applied 11..20
    assert result.applied == 10


def test_verify_checksum():
    sm = CountingMachine()
    r = Replayer(sm)
    ents = [Entry(term=1, index=1, data=b"3"), Entry(term=1, index=2, data=b"4")]
    result = r.replay(ents)
    r.verify_checksum(result.checksum)
    with pytest.raises(ReplayError):
        r.verify_checksum(0)


def test_fingerprint_stable(populated_store):
    a = fingerprint_store(populated_store)
    b = fingerprint_store(populated_store)
    assert a == b
    populated_store.append(Entry(term=2, index=populated_store.last_index() + 1, data=b"z"))
    assert fingerprint_store(populated_store) != a


def test_callable_state_machine():
    seen: list[int] = []

    def apply_fn(entry: Entry) -> None:
        seen.append(entry.index)

    r = Replayer(apply_fn)
    r.replay([Entry(term=1, index=1, data=b"x"), Entry(term=1, index=2, data=b"y")])
    assert seen == [1, 2]


def test_apply_failure_wrapped():
    class Boom:
        def apply(self, entry: Entry) -> None:
            raise RuntimeError("nope")

    r = Replayer(Boom())
    with pytest.raises(ReplayError):
        r.apply_one(Entry(term=1, index=1, data=b"x"))
