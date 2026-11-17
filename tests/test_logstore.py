"""Log store tests including file persistence."""

from __future__ import annotations

import pytest

from raftwire.errors import IndexError_, InvariantError, LogStoreError
from raftwire.logstore import FileLogStore, MemoryLogStore, make_entries
from raftwire.types import Entry, HardState, Snapshot, SnapshotMetadata, ConfState


def test_memory_append_and_slice(sample_entries):
    store = MemoryLogStore()
    store.append(*sample_entries)
    assert store.first_index() == 1
    assert store.last_index() == 20
    assert len(store) == 20
    assert store.entry(5).index == 5
    assert [e.index for e in store.entries(3, 6)] == [3, 4, 5]
    assert store.term(10) == 2
    assert store.last_term() == 2


def test_truncate_suffix(sample_entries):
    store = MemoryLogStore()
    store.append(*sample_entries)
    store.truncate_suffix(11)
    assert store.last_index() == 10
    assert len(store.all_entries()) == 10


def test_noncontiguous_append_rejected():
    store = MemoryLogStore()
    store.append(Entry(term=1, index=1, data=b"a"))
    with pytest.raises(LogStoreError):
        store.append(Entry(term=1, index=3, data=b"c"))


def test_index_error():
    store = MemoryLogStore()
    store.append(Entry(term=1, index=1, data=b"a"))
    with pytest.raises(IndexError_):
        store.entry(9)


def test_hard_state_and_validate(populated_store):
    populated_store.validate()
    populated_store.set_hard_state(HardState(term=9, vote=1, commit=5))
    assert populated_store.get_hard_state().commit == 5
    populated_store.validate()


def test_snapshot_discards_prefix(sample_entries):
    store = MemoryLogStore()
    store.append(*sample_entries)
    snap = Snapshot(
        data=b"snap",
        metadata=SnapshotMetadata(
            conf_state=ConfState(voters=(1,)),
            index=10,
            term=2,
        ),
    )
    store.set_snapshot(snap)
    assert store.first_index() == 11
    assert store.last_index() == 20
    assert store.term(10) == 2
    store.validate()


def test_clone_independent(sample_entries):
    store = MemoryLogStore()
    store.append(*sample_entries[:5])
    other = store.clone()
    other.append(Entry(term=2, index=6, data=b"x"))
    assert store.last_index() == 5
    assert other.last_index() == 6


def test_file_log_store_persist(tmp_path, sample_entries):
    path = tmp_path / "wal"
    store = FileLogStore(path)
    store.set_hard_state(HardState(term=3, vote=1, commit=0))
    store.append(*sample_entries[:8])
    store.set_hard_state(HardState(term=3, vote=1, commit=5))
    store.sync()

    reloaded = FileLogStore(path)
    assert reloaded.last_index() == 8
    assert reloaded.get_hard_state().commit == 5
    assert reloaded.entry(3) == sample_entries[2]
    reloaded.validate()


def test_file_log_store_snapshot_and_jsonl(tmp_path, sample_entries):
    path = tmp_path / "wal2"
    store = FileLogStore(path)
    store.append(*sample_entries)
    store.set_snapshot(
        Snapshot(
            data=b"S",
            metadata=SnapshotMetadata(index=5, term=2, conf_state=ConfState()),
        )
    )
    text = store.export_jsonl()
    path2 = tmp_path / "wal3"
    other = FileLogStore(path2)
    other.import_jsonl(text)
    assert other.first_index() == 6
    assert other.last_index() == 20


def test_make_entries_helper():
    ents = make_entries(5, 3, term=7, prefix=b"p")
    assert [e.index for e in ents] == [5, 6, 7]
    assert all(e.term == 7 for e in ents)
    assert ents[0].data == b"p5"


def test_empty_store():
    store = MemoryLogStore()
    assert store.is_empty()
    assert store.last_index() == 0
    assert store.all_entries() == []
    store.validate()
