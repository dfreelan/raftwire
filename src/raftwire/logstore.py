"""Raft log store abstractions (memory + file-backed)."""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

from raftwire.codec import (
    decode_entries_stream,
    decode_hard_state,
    decode_snapshot,
    encode_entry,
    encode_hard_state,
    encode_snapshot,
    dumps_jsonl,
    loads_jsonl,
)
from raftwire.errors import IndexError_, InvariantError, LogStoreError
from raftwire.types import Entry, HardState, Snapshot, empty_hard_state, empty_snapshot


class LogStore(ABC):
    """Abstract Raft log storage."""

    @abstractmethod
    def first_index(self) -> int: ...

    @abstractmethod
    def last_index(self) -> int: ...

    @abstractmethod
    def term(self, index: int) -> int: ...

    @abstractmethod
    def entry(self, index: int) -> Entry: ...

    @abstractmethod
    def entries(self, lo: int, hi: int) -> list[Entry]: ...

    @abstractmethod
    def append(self, *entries: Entry) -> None: ...

    @abstractmethod
    def truncate_suffix(self, index: int) -> None: ...

    @abstractmethod
    def get_hard_state(self) -> HardState: ...

    @abstractmethod
    def set_hard_state(self, hs: HardState) -> None: ...

    @abstractmethod
    def get_snapshot(self) -> Snapshot: ...

    @abstractmethod
    def set_snapshot(self, snap: Snapshot) -> None: ...

    def last_term(self) -> int:
        li = self.last_index()
        return self.term(li) if li > 0 else 0

    def is_empty(self) -> bool:
        return self.last_index() == 0 and self.get_snapshot().is_empty()

    def all_entries(self) -> list[Entry]:
        fi, li = self.first_index(), self.last_index()
        if li == 0 and self.get_snapshot().is_empty():
            return []
        if li < fi:
            return []
        return self.entries(fi, li + 1)

    def append_batch(self, entries: Sequence[Entry]) -> None:
        if entries:
            self.append(*entries)

    def validate(self) -> None:
        """Check index / term / commit invariants."""
        fi, li = self.first_index(), self.last_index()
        snap, hs = self.get_snapshot(), self.get_hard_state()
        if not snap.is_empty() and li >= fi and fi != snap.metadata.index + 1:
            raise InvariantError(
                "first_index_after_snapshot",
                f"first={fi} snap.index={snap.metadata.index}",
            )
        if li > 0 and fi > li:
            raise InvariantError("first_gt_last", f"first={fi} last={li}")
        prev_term = -1
        if li > 0:
            for idx in range(fi, li + 1):
                ent = self.entry(idx)
                if ent.index != idx:
                    raise InvariantError("index_mismatch", f"entry.index={ent.index} expected={idx}")
                if prev_term >= 0 and ent.term < prev_term:
                    raise InvariantError(
                        "term_regression", f"index={idx} term={ent.term} prev_term={prev_term}"
                    )
                prev_term = ent.term
        if hs.commit > max(li, snap.metadata.index):
            raise InvariantError(
                "commit_past_last",
                f"commit={hs.commit} last={li} snap={snap.metadata.index}",
            )

    def __len__(self) -> int:
        li, fi = self.last_index(), self.first_index()
        if li == 0 and self.get_snapshot().is_empty():
            return 0
        if li < fi:
            return 0
        return li - fi + 1


class MemoryLogStore(LogStore):
    """In-memory log store suitable for tests and single-process use."""

    def __init__(self) -> None:
        self._entries: list[Entry] = []
        self._offset: int = 1
        self._hard: HardState = empty_hard_state()
        self._snap: Snapshot = empty_snapshot()
        self._lock = threading.RLock()

    def first_index(self) -> int:
        with self._lock:
            if not self._entries:
                return self._snap.metadata.index + 1 if not self._snap.is_empty() else 1
            return self._offset

    def last_index(self) -> int:
        with self._lock:
            if not self._entries:
                return self._snap.metadata.index if not self._snap.is_empty() else 0
            return self._offset + len(self._entries) - 1

    def term(self, index: int) -> int:
        with self._lock:
            if index == 0:
                return 0
            if not self._snap.is_empty() and index == self._snap.metadata.index:
                return self._snap.metadata.term
            if not self._entries:
                raise IndexError_(index, self.first_index(), self.last_index())
            lo, hi = self._offset, self._offset + len(self._entries) - 1
            if index < lo or index > hi:
                raise IndexError_(index, lo, hi)
            return self._entries[index - lo].term

    def entry(self, index: int) -> Entry:
        with self._lock:
            if not self._entries:
                raise IndexError_(index, self.first_index(), self.last_index())
            lo, hi = self._offset, self._offset + len(self._entries) - 1
            if index < lo or index > hi:
                raise IndexError_(index, lo, hi)
            return self._entries[index - lo]

    def entries(self, lo: int, hi: int) -> list[Entry]:
        with self._lock:
            if lo > hi:
                raise LogStoreError(f"invalid range [{lo}, {hi})")
            if not self._entries or lo >= hi:
                return []
            start, end = self._offset, self._offset + len(self._entries)
            a, b = max(lo, start), min(hi, end)
            return [] if a >= b else list(self._entries[a - start:b - start])

    def append(self, *entries: Entry) -> None:
        with self._lock:
            if not entries:
                return
            expected = self.last_index() + 1
            if not self._entries and self._snap.is_empty():
                expected = entries[0].index
                if expected < 1:
                    raise LogStoreError(f"first entry index must be >= 1, got {expected}")
                self._offset = expected
            for ent in entries:
                if ent.index != expected:
                    raise LogStoreError(
                        f"non-contiguous append: expected index {expected}, got {ent.index}"
                    )
                self._entries.append(ent)
                expected += 1

    def truncate_suffix(self, index: int) -> None:
        with self._lock:
            if not self._entries:
                return
            if index <= self._offset:
                self._entries.clear()
                return
            keep = index - self._offset
            if keep < len(self._entries):
                del self._entries[keep:]

    def get_hard_state(self) -> HardState:
        with self._lock:
            return self._hard

    def set_hard_state(self, hs: HardState) -> None:
        with self._lock:
            self._hard = hs

    def get_snapshot(self) -> Snapshot:
        with self._lock:
            return self._snap

    def set_snapshot(self, snap: Snapshot) -> None:
        with self._lock:
            self._snap = snap
            cut = snap.metadata.index
            if not self._entries:
                self._offset = cut + 1
                return
            i = 0
            while i < len(self._entries) and self._entries[i].index <= cut:
                i += 1
            if i:
                del self._entries[:i]
            self._offset = cut + 1 if not self._entries else self._entries[0].index

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._offset = 1
            self._hard = empty_hard_state()
            self._snap = empty_snapshot()

    def clone(self) -> MemoryLogStore:
        with self._lock:
            other = MemoryLogStore()
            other._entries = list(self._entries)
            other._offset = self._offset
            other._hard = self._hard
            other._snap = self._snap
            return other


class FileLogStore(LogStore):
    """File-backed store: hard_state.bin, snapshot.bin, entries.bin."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._mem = MemoryLogStore()
        self._lock = threading.RLock()
        self._load()

    @property
    def directory(self) -> Path:
        return self._dir

    def _load(self) -> None:
        hp = self._dir / "hard_state.bin"
        if hp.exists() and hp.stat().st_size:
            self._mem.set_hard_state(decode_hard_state(hp.read_bytes()))
        sp = self._dir / "snapshot.bin"
        if sp.exists() and sp.stat().st_size:
            self._mem.set_snapshot(decode_snapshot(sp.read_bytes()))
        ep = self._dir / "entries.bin"
        if ep.exists() and ep.stat().st_size:
            entries = decode_entries_stream(ep.read_bytes())
            if entries:
                self._mem._entries = []
                self._mem._offset = entries[0].index
                self._mem.append(*entries)

    def _persist_hard(self) -> None:
        (self._dir / "hard_state.bin").write_bytes(encode_hard_state(self._mem.get_hard_state()))
        (self._dir / "meta.jsonl").write_text(dumps_jsonl([self._mem.get_hard_state()]), encoding="utf-8")

    def _persist_snap(self) -> None:
        path = self._dir / "snapshot.bin"
        snap = self._mem.get_snapshot()
        if snap.is_empty():
            if path.exists():
                path.unlink()
            return
        path.write_bytes(encode_snapshot(snap))

    def _persist_entries(self) -> None:
        blob = b"".join(encode_entry(e) for e in self._mem.all_entries())
        (self._dir / "entries.bin").write_bytes(blob)

    def _persist_all(self) -> None:
        self._persist_hard()
        self._persist_snap()
        self._persist_entries()

    def first_index(self) -> int:
        with self._lock:
            return self._mem.first_index()

    def last_index(self) -> int:
        with self._lock:
            return self._mem.last_index()

    def term(self, index: int) -> int:
        with self._lock:
            return self._mem.term(index)

    def entry(self, index: int) -> Entry:
        with self._lock:
            return self._mem.entry(index)

    def entries(self, lo: int, hi: int) -> list[Entry]:
        with self._lock:
            return self._mem.entries(lo, hi)

    def append(self, *entries: Entry) -> None:
        with self._lock:
            self._mem.append(*entries)
            self._persist_entries()

    def truncate_suffix(self, index: int) -> None:
        with self._lock:
            self._mem.truncate_suffix(index)
            self._persist_entries()

    def get_hard_state(self) -> HardState:
        with self._lock:
            return self._mem.get_hard_state()

    def set_hard_state(self, hs: HardState) -> None:
        with self._lock:
            self._mem.set_hard_state(hs)
            self._persist_hard()

    def get_snapshot(self) -> Snapshot:
        with self._lock:
            return self._mem.get_snapshot()

    def set_snapshot(self, snap: Snapshot) -> None:
        with self._lock:
            self._mem.set_snapshot(snap)
            self._persist_all()

    def sync(self) -> None:
        with self._lock:
            self._persist_all()

    def export_jsonl(self) -> str:
        with self._lock:
            records: list[object] = [self._mem.get_hard_state()]
            snap = self._mem.get_snapshot()
            if not snap.is_empty():
                records.append(snap)
            records.extend(self._mem.all_entries())
            return dumps_jsonl(records)

    def import_jsonl(self, text: str) -> None:
        records = loads_jsonl(text)
        with self._lock:
            self._mem.clear()
            batch: list[Entry] = []
            for rec in records:
                if isinstance(rec, HardState):
                    self._mem.set_hard_state(rec)
                elif isinstance(rec, Snapshot):
                    self._mem.set_snapshot(rec)
                elif isinstance(rec, Entry):
                    batch.append(rec)
                else:
                    raise LogStoreError(f"unsupported record in JSONL: {type(rec)}")
            if batch:
                self._mem.append(*batch)
            self._persist_all()


def make_entries(
    start_index: int, count: int, *, term: int = 1, prefix: bytes = b"data-"
) -> list[Entry]:
    return [
        Entry(term=term, index=start_index + i, data=prefix + str(start_index + i).encode())
        for i in range(count)
    ]


__all__ = ["LogStore", "MemoryLogStore", "FileLogStore", "make_entries"]
