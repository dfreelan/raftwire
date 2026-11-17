"""Tests for core record types."""

from __future__ import annotations

import pytest

from raftwire.types import (
    ConfState,
    Entry,
    EntryType,
    HardState,
    Snapshot,
    SnapshotMetadata,
    SoftState,
    empty_hard_state,
    empty_snapshot,
    empty_soft_state,
)


def test_hard_state_roundtrip_dict():
    hs = HardState(term=9, vote=3, commit=4)
    assert HardState.from_dict(hs.to_dict()) == hs
    assert not hs.is_empty()
    assert empty_hard_state().is_empty()


def test_hard_state_withers():
    hs = HardState(term=1, vote=0, commit=0)
    assert hs.with_term(2).term == 2
    assert hs.with_vote(7).vote == 7
    assert hs.with_commit(5).commit == 5


def test_hard_state_rejects_negative():
    with pytest.raises(ValueError):
        HardState(term=-1)


def test_soft_state():
    ss = SoftState(lead=1, raft_state=2)
    assert ss.is_leader()
    assert not ss.is_follower()
    assert SoftState.from_dict(ss.to_dict()) == ss
    with pytest.raises(ValueError):
        SoftState(raft_state=9)
    assert empty_soft_state().is_follower()


def test_entry_basics(sample_entry: Entry):
    assert sample_entry.size() == len(sample_entry.data)
    assert not sample_entry.is_conf_change()
    assert not sample_entry.is_empty()
    e2 = sample_entry.with_data(b"new")
    assert e2.data == b"new" and e2.index == sample_entry.index
    assert Entry.from_dict(sample_entry.to_dict()) == sample_entry


def test_entry_conf_change():
    e = Entry(term=1, index=1, data=b"x", entry_type=EntryType.CONF_CHANGE)
    assert e.is_conf_change()



def test_conf_state():
    cs = ConfState(voters=(1, 2), learners=(3,), voters_outgoing=(4,), auto_leave=True)
    assert cs.is_joint()
    assert cs.all_ids() == frozenset({1, 2, 3, 4})
    assert ConfState.from_dict(cs.to_dict()) == cs
    with pytest.raises(ValueError):
        ConfState(voters=(-1,))


def test_snapshot(snapshot: Snapshot):
    assert snapshot.size() == len(snapshot.data)
    assert not snapshot.is_empty()
    assert Snapshot.from_dict(snapshot.to_dict()) == snapshot
    assert empty_snapshot().is_empty()


