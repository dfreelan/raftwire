"""Shared fixtures for raftwire tests."""

from __future__ import annotations

import pytest

from raftwire.logstore import MemoryLogStore, make_entries
from raftwire.types import Entry, HardState, SoftState, ConfState, Snapshot, SnapshotMetadata


@pytest.fixture
def sample_entry() -> Entry:
    return Entry(term=3, index=7, data=b'{"op":"set","k":"x"}')


@pytest.fixture
def sample_entries() -> list[Entry]:
    return make_entries(1, 20, term=2, prefix=b"payload-")


@pytest.fixture
def hard_state() -> HardState:
    return HardState(term=5, vote=2, commit=10)


@pytest.fixture
def soft_state() -> SoftState:
    return SoftState(lead=2, raft_state=2)


@pytest.fixture
def conf_state() -> ConfState:
    return ConfState(voters=(1, 2, 3), learners=(4,), auto_leave=False)


@pytest.fixture
def snapshot(conf_state: ConfState) -> Snapshot:
    return Snapshot(
        data=b"STATE_BLOB",
        metadata=SnapshotMetadata(conf_state=conf_state, index=10, term=5),
    )


@pytest.fixture
def populated_store(sample_entries: list[Entry], hard_state: HardState) -> MemoryLogStore:
    store = MemoryLogStore()
    store.append(*sample_entries)
    store.set_hard_state(hard_state.with_commit(min(hard_state.commit, store.last_index())))
    return store
