"""Binary and JSONL codecs for Raft / WAL records."""

from __future__ import annotations

import json
import struct
from typing import Any, Callable, Iterable, Mapping, Sequence

from raftwire.crc import append_crc32c, crc32c, verify_crc32c
from raftwire.errors import CodecError, MagicError, TruncatedError, VersionError
from raftwire.types import (
    ConfState,
    Entry,
    EntryType,
    HardState,
    Snapshot,
    SnapshotMetadata,
    SoftState,
)

VERSION = 1
MAGIC_ENTRY = 0x5246
MAGIC_HARD_STATE = 0x4853
MAGIC_SOFT_STATE = 0x5353
MAGIC_SNAPSHOT = 0x534E
MAGIC_CONF_STATE = 0x4353

_U16, _U8, _U32, _U64 = struct.Struct("<H"), struct.Struct("<B"), struct.Struct("<I"), struct.Struct("<Q")


def _need(buf: bytes | memoryview, offset: int, size: int, what: str) -> None:
    if offset + size > len(buf):
        raise TruncatedError(offset + size, len(buf), what)


def _pack_bytes(data: bytes) -> bytes:
    return _U32.pack(len(data)) + data


def _unpack_bytes(buf: bytes | memoryview, offset: int) -> tuple[bytes, int]:
    _need(buf, offset, 4, "bytes length")
    (n,) = _U32.unpack_from(buf, offset)
    offset += 4
    _need(buf, offset, n, "bytes payload")
    return bytes(buf[offset:offset + n]), offset + n


def _pack_u64_list(values: Sequence[int]) -> bytes:
    out = bytearray(_U32.pack(len(values)))
    for v in values:
        out += _U64.pack(int(v))
    return bytes(out)


def _unpack_u64_list(buf: bytes | memoryview, offset: int) -> tuple[tuple[int, ...], int]:
    _need(buf, offset, 4, "u64 list length")
    (n,) = _U32.unpack_from(buf, offset)
    offset += 4
    vals = []
    for _ in range(n):
        _need(buf, offset, 8, "u64 list item")
        (v,) = _U64.unpack_from(buf, offset)
        vals.append(v)
        offset += 8
    return tuple(vals), offset


def _check_magic_ver(view: memoryview, magic: int) -> int:
    _need(view, 0, 3, "header")
    (got,) = _U16.unpack_from(view, 0)
    if got != magic:
        raise MagicError(magic, got)
    (ver,) = _U8.unpack_from(view, 2)
    if ver != VERSION:
        raise VersionError(ver)
    return 3


def encode_entry(entry: Entry, *, with_crc: bool = True) -> bytes:
    body = (
        _U16.pack(MAGIC_ENTRY) + _U8.pack(VERSION) + _U8.pack(int(entry.entry_type))
        + _U64.pack(entry.term) + _U64.pack(entry.index)
        + _pack_bytes(entry.data) + _pack_bytes(entry.context)
    )
    return append_crc32c(body) if with_crc else body


def decode_entry(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> Entry:
    data = verify_crc32c(buf, context="entry") if with_crc else bytes(buf)
    view = memoryview(data)
    off = _check_magic_ver(view, MAGIC_ENTRY)
    _need(view, off, 1 + 16, "entry fixed")
    (etype,) = _U8.unpack_from(view, off); off += 1
    (term,) = _U64.unpack_from(view, off); off += 8
    (index,) = _U64.unpack_from(view, off); off += 8
    payload, off = _unpack_bytes(view, off)
    context, off = _unpack_bytes(view, off)
    if off != len(view):
        raise CodecError(f"trailing bytes in entry: {len(view) - off}")
    return Entry(term=term, index=index, data=payload, entry_type=EntryType(etype), context=context)


def encode_hard_state(hs: HardState, *, with_crc: bool = True) -> bytes:
    body = _U16.pack(MAGIC_HARD_STATE) + _U8.pack(VERSION) + _U8.pack(0)
    body += _U64.pack(hs.term) + _U64.pack(hs.vote) + _U64.pack(hs.commit)
    return append_crc32c(body) if with_crc else body


def decode_hard_state(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> HardState:
    data = verify_crc32c(buf, context="hard_state") if with_crc else bytes(buf)
    view = memoryview(data)
    off = _check_magic_ver(view, MAGIC_HARD_STATE)
    off += 1  # pad
    _need(view, off, 24, "hard_state")
    term, vote, commit = struct.unpack_from("<QQQ", view, off)
    if off + 24 != len(view):
        raise CodecError(f"trailing bytes in hard_state: {len(view) - (off + 24)}")
    return HardState(term=term, vote=vote, commit=commit)


def encode_soft_state(ss: SoftState, *, with_crc: bool = True) -> bytes:
    body = _U16.pack(MAGIC_SOFT_STATE) + _U8.pack(VERSION) + _U8.pack(ss.raft_state & 0xFF)
    body += _U64.pack(ss.lead)
    return append_crc32c(body) if with_crc else body


def decode_soft_state(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> SoftState:
    data = verify_crc32c(buf, context="soft_state") if with_crc else bytes(buf)
    view = memoryview(data)
    off = _check_magic_ver(view, MAGIC_SOFT_STATE)
    _need(view, off, 1 + 8, "soft_state")
    (raft_state,) = _U8.unpack_from(view, off)
    (lead,) = _U64.unpack_from(view, off + 1)
    if off + 9 != len(view):
        raise CodecError(f"trailing bytes in soft_state: {len(view) - (off + 9)}")
    return SoftState(lead=lead, raft_state=raft_state)


def encode_conf_state(cs: ConfState, *, with_crc: bool = True) -> bytes:
    body = bytearray(_U16.pack(MAGIC_CONF_STATE) + _U8.pack(VERSION) + _U8.pack(1 if cs.auto_leave else 0))
    body += _pack_u64_list(cs.voters)
    body += _pack_u64_list(cs.learners)
    body += _pack_u64_list(cs.voters_outgoing)
    body += _pack_u64_list(cs.learners_next)
    raw = bytes(body)
    return append_crc32c(raw) if with_crc else raw


def decode_conf_state(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> ConfState:
    data = verify_crc32c(buf, context="conf_state") if with_crc else bytes(buf)
    view = memoryview(data)
    off = _check_magic_ver(view, MAGIC_CONF_STATE)
    (auto_leave_b,) = _U8.unpack_from(view, off); off += 1
    voters, off = _unpack_u64_list(view, off)
    learners, off = _unpack_u64_list(view, off)
    voters_outgoing, off = _unpack_u64_list(view, off)
    learners_next, off = _unpack_u64_list(view, off)
    if off != len(view):
        raise CodecError(f"trailing bytes in conf_state: {len(view) - off}")
    return ConfState(
        voters=voters, learners=learners, voters_outgoing=voters_outgoing,
        learners_next=learners_next, auto_leave=bool(auto_leave_b),
    )


def encode_snapshot(snap: Snapshot, *, with_crc: bool = True) -> bytes:
    body = bytearray(_U16.pack(MAGIC_SNAPSHOT) + _U8.pack(VERSION) + _U8.pack(0))
    body += _U64.pack(snap.metadata.index) + _U64.pack(snap.metadata.term)
    body += _pack_bytes(encode_conf_state(snap.metadata.conf_state, with_crc=False))
    body += _pack_bytes(snap.data)
    raw = bytes(body)
    return append_crc32c(raw) if with_crc else raw


def decode_snapshot(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> Snapshot:
    data = verify_crc32c(buf, context="snapshot") if with_crc else bytes(buf)
    view = memoryview(data)
    off = _check_magic_ver(view, MAGIC_SNAPSHOT)
    off += 1  # pad
    _need(view, off, 16, "snapshot meta")
    (index,) = _U64.unpack_from(view, off); off += 8
    (term,) = _U64.unpack_from(view, off); off += 8
    cs_blob, off = _unpack_bytes(view, off)
    payload, off = _unpack_bytes(view, off)
    if off != len(view):
        raise CodecError(f"trailing bytes in snapshot: {len(view) - off}")
    return Snapshot(
        data=payload,
        metadata=SnapshotMetadata(
            conf_state=decode_conf_state(cs_blob, with_crc=False), index=index, term=term
        ),
    )


_MAGIC_DECODERS: dict[int, Callable[..., Any]] = {
    MAGIC_ENTRY: decode_entry,
    MAGIC_HARD_STATE: decode_hard_state,
    MAGIC_SOFT_STATE: decode_soft_state,
    MAGIC_SNAPSHOT: decode_snapshot,
    MAGIC_CONF_STATE: decode_conf_state,
}


def peek_magic(buf: bytes | bytearray | memoryview) -> int:
    if len(buf) < 2:
        raise TruncatedError(2, len(buf), "magic")
    return _U16.unpack_from(buf, 0)[0]


def decode_any(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> Any:
    magic = _U16.unpack_from(buf, 0)[0] if len(buf) >= 2 else peek_magic(buf)
    decoder = _MAGIC_DECODERS.get(magic)
    if decoder is None:
        raise MagicError(MAGIC_ENTRY, magic)
    return decoder(buf, with_crc=with_crc)


def encode_entries(entries: Sequence[Entry], *, with_crc: bool = True) -> bytes:
    return b"".join(encode_entry(e, with_crc=with_crc) for e in entries)


def _decode_entry_at(view: memoryview, offset: int) -> tuple[Entry, int]:
    start = offset
    _need(view, offset, 20, "entry header")
    (magic,) = _U16.unpack_from(view, offset)
    if magic != MAGIC_ENTRY:
        raise MagicError(MAGIC_ENTRY, magic)
    pos = offset + 20
    _need(view, pos, 4, "data_len")
    (data_len,) = _U32.unpack_from(view, pos)
    pos += 4 + data_len
    _need(view, pos, 4, "ctx_len")
    (ctx_len,) = _U32.unpack_from(view, pos)
    pos += 4 + ctx_len + 4
    _need(view, start, pos - start, "entry record")
    return decode_entry(view[start:pos], with_crc=True), pos - start


def decode_entries_stream(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> list[Entry]:
    if not with_crc:
        raise CodecError("decode_entries_stream requires with_crc=True")
    view = memoryview(buf)
    offset, out = 0, []
    while offset < len(view):
        entry, consumed = _decode_entry_at(view, offset)
        out.append(entry)
        offset += consumed
    return out


_TYPE_KEYS = {
    "entry": Entry, "Entry": Entry,
    "hard_state": HardState, "HardState": HardState,
    "soft_state": SoftState, "SoftState": SoftState,
    "snapshot": Snapshot, "Snapshot": Snapshot,
    "conf_state": ConfState, "ConfState": ConfState,
}


def entry_to_json(entry: Entry) -> dict[str, Any]:
    return {"_type": "entry", **entry.to_dict()}


def entry_from_json(d: Mapping[str, Any]) -> Entry:
    return Entry.from_dict(d)


def hard_state_to_json(hs: HardState) -> dict[str, Any]:
    return {"_type": "hard_state", **hs.to_dict()}


def hard_state_from_json(d: Mapping[str, Any]) -> HardState:
    return HardState.from_dict(d)


def record_to_json(obj: Any) -> dict[str, Any]:
    if isinstance(obj, Entry):
        return entry_to_json(obj)
    if isinstance(obj, HardState):
        return hard_state_to_json(obj)
    if isinstance(obj, SoftState):
        return {"_type": "soft_state", **obj.to_dict()}
    if isinstance(obj, Snapshot):
        return {"_type": "snapshot", **obj.to_dict()}
    if isinstance(obj, ConfState):
        return {"_type": "conf_state", **obj.to_dict()}
    raise CodecError(f"unsupported record type: {type(obj)!r}")


def record_from_json(d: Mapping[str, Any]) -> Any:
    cls = _TYPE_KEYS.get(str(d.get("_type", "")))
    if cls is Entry:
        return Entry.from_dict(d)
    if cls is HardState:
        return HardState.from_dict(d)
    if cls is SoftState:
        return SoftState.from_dict(d)
    if cls is Snapshot:
        return Snapshot.from_dict(d)
    if cls is ConfState:
        return ConfState.from_dict(d)
    raise CodecError(f"unknown JSONL _type: {d.get('_type')!r}")


def dumps_jsonl(records: Iterable[Any]) -> str:
    lines = [json.dumps(record_to_json(rec), sort_keys=True, separators=(",", ":")) for rec in records]
    return "\n".join(lines) + ("\n" if lines else "")


def loads_jsonl(text: str) -> list[Any]:
    out: list[Any] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodecError(f"JSONL line {lineno}: {exc}") from exc
        if not isinstance(obj, dict):
            raise CodecError(f"JSONL line {lineno}: expected object")
        out.append(record_from_json(obj))
    return out


def encode_record(obj: Any, *, with_crc: bool = True) -> bytes:
    if isinstance(obj, Entry):
        return encode_entry(obj, with_crc=with_crc)
    if isinstance(obj, HardState):
        return encode_hard_state(obj, with_crc=with_crc)
    if isinstance(obj, SoftState):
        return encode_soft_state(obj, with_crc=with_crc)
    if isinstance(obj, Snapshot):
        return encode_snapshot(obj, with_crc=with_crc)
    if isinstance(obj, ConfState):
        return encode_conf_state(obj, with_crc=with_crc)
    raise CodecError(f"cannot encode type {type(obj)!r}")


def record_digest(obj: Any) -> int:
    return crc32c(encode_record(obj, with_crc=False))


__all__ = [
    "VERSION", "MAGIC_ENTRY", "MAGIC_HARD_STATE", "MAGIC_SOFT_STATE", "MAGIC_SNAPSHOT",
    "MAGIC_CONF_STATE", "encode_entry", "decode_entry", "encode_hard_state", "decode_hard_state",
    "encode_soft_state", "decode_soft_state", "encode_conf_state", "decode_conf_state",
    "encode_snapshot", "decode_snapshot", "peek_magic", "decode_any", "encode_entries",
    "decode_entries_stream", "entry_to_json", "entry_from_json", "hard_state_to_json",
    "hard_state_from_json", "record_to_json", "record_from_json", "dumps_jsonl", "loads_jsonl",
    "encode_record", "record_digest",
]
