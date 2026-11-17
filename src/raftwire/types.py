"""Core Raft / WAL record types."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any, Mapping


class EntryType(IntEnum):
    """Discriminates normal application entries from configuration changes."""

    NORMAL = 0
    CONF_CHANGE = 1
    CONF_CHANGE_V2 = 2


class ConfChangeType(IntEnum):
    """Membership change operations."""

    ADD_NODE = 0
    REMOVE_NODE = 1
    UPDATE_NODE = 2
    ADD_LEARNER = 3
    REMOVE_LEARNER = 4


def _require_nonneg(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if not value:
        return b""
    return base64.b64decode(str(value))


@dataclass(frozen=True, slots=True)
class HardState:
    """Persistent Raft state that must survive restarts."""

    term: int = 0
    vote: int = 0
    commit: int = 0

    def __post_init__(self) -> None:
        _require_nonneg(self.term, "term")
        _require_nonneg(self.vote, "vote")
        _require_nonneg(self.commit, "commit")

    def is_empty(self) -> bool:
        return self.term == 0 and self.vote == 0 and self.commit == 0

    def with_term(self, term: int) -> HardState:
        return replace(self, term=term)

    def with_vote(self, vote: int) -> HardState:
        return replace(self, vote=vote)

    def with_commit(self, commit: int) -> HardState:
        return replace(self, commit=commit)

    def to_dict(self) -> dict[str, int]:
        return {"term": self.term, "vote": self.vote, "commit": self.commit}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> HardState:
        return cls(
            term=int(d.get("term", 0)),
            vote=int(d.get("vote", 0)),
            commit=int(d.get("commit", 0)),
        )


@dataclass(frozen=True, slots=True)
class SoftState:
    """Volatile Raft state (leader identity and role)."""

    lead: int = 0
    raft_state: int = 0

    def __post_init__(self) -> None:
        _require_nonneg(self.lead, "lead")
        if self.raft_state < 0 or self.raft_state > 3:
            raise ValueError(f"raft_state out of range: {self.raft_state}")

    def is_leader(self) -> bool:
        return self.raft_state == 2

    def is_follower(self) -> bool:
        return self.raft_state == 0

    def to_dict(self) -> dict[str, int]:
        return {"lead": self.lead, "raft_state": self.raft_state}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SoftState:
        return cls(
            lead=int(d.get("lead", 0)),
            raft_state=int(d.get("raft_state", 0)),
        )


@dataclass(frozen=True, slots=True)
class Entry:
    """A single Raft log entry."""

    term: int
    index: int
    data: bytes = b""
    entry_type: EntryType = EntryType.NORMAL
    context: bytes = b""

    def __post_init__(self) -> None:
        _require_nonneg(self.term, "term")
        _require_nonneg(self.index, "index")
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        if not isinstance(self.context, (bytes, bytearray)):
            raise TypeError("context must be bytes")
        object.__setattr__(self, "data", bytes(self.data))
        object.__setattr__(self, "context", bytes(self.context))
        if not isinstance(self.entry_type, EntryType):
            object.__setattr__(self, "entry_type", EntryType(int(self.entry_type)))

    def is_empty(self) -> bool:
        return (
            self.term == 0
            and self.index == 0
            and not self.data
            and not self.context
            and self.entry_type == EntryType.NORMAL
        )

    def is_conf_change(self) -> bool:
        return self.entry_type in (EntryType.CONF_CHANGE, EntryType.CONF_CHANGE_V2)

    def size(self) -> int:
        return len(self.data) + len(self.context)

    def with_data(self, data: bytes) -> Entry:
        return replace(self, data=data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "index": self.index,
            "data": _b64(self.data),
            "entry_type": int(self.entry_type),
            "context": _b64(self.context),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Entry:
        return cls(
            term=int(d["term"]),
            index=int(d["index"]),
            data=_unb64(d.get("data", "")),
            entry_type=EntryType(int(d.get("entry_type", 0))),
            context=_unb64(d.get("context", "")),
        )


@dataclass(frozen=True, slots=True)
class ConfState:
    """Cluster membership snapshot."""

    voters: tuple[int, ...] = ()
    learners: tuple[int, ...] = ()
    voters_outgoing: tuple[int, ...] = ()
    learners_next: tuple[int, ...] = ()
    auto_leave: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "voters", tuple(int(x) for x in self.voters))
        object.__setattr__(self, "learners", tuple(int(x) for x in self.learners))
        object.__setattr__(
            self, "voters_outgoing", tuple(int(x) for x in self.voters_outgoing)
        )
        object.__setattr__(
            self, "learners_next", tuple(int(x) for x in self.learners_next)
        )
        for name in ("voters", "learners", "voters_outgoing", "learners_next"):
            for v in getattr(self, name):
                if v < 0:
                    raise ValueError(f"negative node id in {name}: {v}")

    def all_ids(self) -> frozenset[int]:
        return frozenset(
            self.voters + self.learners + self.voters_outgoing + self.learners_next
        )

    def is_joint(self) -> bool:
        return bool(self.voters_outgoing)

    def to_dict(self) -> dict[str, Any]:
        return {
            "voters": list(self.voters),
            "learners": list(self.learners),
            "voters_outgoing": list(self.voters_outgoing),
            "learners_next": list(self.learners_next),
            "auto_leave": self.auto_leave,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ConfState:
        return cls(
            voters=tuple(d.get("voters", ())),
            learners=tuple(d.get("learners", ())),
            voters_outgoing=tuple(d.get("voters_outgoing", ())),
            learners_next=tuple(d.get("learners_next", ())),
            auto_leave=bool(d.get("auto_leave", False)),
        )


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    """Metadata accompanying a snapshot."""

    conf_state: ConfState = field(default_factory=ConfState)
    index: int = 0
    term: int = 0

    def __post_init__(self) -> None:
        _require_nonneg(self.index, "index")
        _require_nonneg(self.term, "term")

    def to_dict(self) -> dict[str, Any]:
        return {
            "conf_state": self.conf_state.to_dict(),
            "index": self.index,
            "term": self.term,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SnapshotMetadata:
        cs_raw = d.get("conf_state", {})
        cs = cs_raw if isinstance(cs_raw, ConfState) else ConfState.from_dict(cs_raw)
        return cls(
            conf_state=cs,
            index=int(d.get("index", 0)),
            term=int(d.get("term", 0)),
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Point-in-time state machine snapshot."""

    data: bytes = b""
    metadata: SnapshotMetadata = field(default_factory=SnapshotMetadata)

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        object.__setattr__(self, "data", bytes(self.data))

    def is_empty(self) -> bool:
        return not self.data and self.metadata.index == 0 and self.metadata.term == 0

    def size(self) -> int:
        return len(self.data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": _b64(self.data),
            "metadata": self.metadata.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Snapshot:
        md_raw = d.get("metadata", {})
        md = (
            md_raw
            if isinstance(md_raw, SnapshotMetadata)
            else SnapshotMetadata.from_dict(md_raw)
        )
        return cls(data=_unb64(d.get("data", "")), metadata=md)


def empty_hard_state() -> HardState:
    return HardState()


def empty_soft_state() -> SoftState:
    return SoftState()


def empty_snapshot() -> Snapshot:
    return Snapshot()


__all__ = [
    "EntryType",
    "ConfChangeType",
    "HardState",
    "SoftState",
    "Entry",
    "ConfState",
    "SnapshotMetadata",
    "Snapshot",
    "empty_hard_state",
    "empty_soft_state",
    "empty_snapshot",
]
