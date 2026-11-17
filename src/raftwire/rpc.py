"""Mock Raft RPC messages: AppendEntries and RequestVote."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from raftwire.codec import decode_entry, encode_entry
from raftwire.crc import append_crc32c, verify_crc32c
from raftwire.errors import CodecError, MagicError, RpcError, TruncatedError, VersionError
from raftwire.types import Entry

MAGIC_AE, MAGIC_AER, MAGIC_RV, MAGIC_RVR = 0x4145, 0x4152, 0x5256, 0x5252
_RPC_VERSION = 1
_U16, _U8, _U32, _U64 = struct.Struct("<H"), struct.Struct("<B"), struct.Struct("<I"), struct.Struct("<Q")


def _need(buf: memoryview, offset: int, size: int, what: str) -> None:
    if offset + size > len(buf):
        raise TruncatedError(offset + size, len(buf), what)


@dataclass(frozen=True, slots=True)
class AppendEntries:
    term: int
    leader_id: int
    prev_log_index: int
    prev_log_term: int
    entries: tuple[Entry, ...] = ()
    leader_commit: int = 0

    def __post_init__(self) -> None:
        for name in ("term", "leader_id", "prev_log_index", "prev_log_term", "leader_commit"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        object.__setattr__(self, "entries", tuple(self.entries))

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term, "leader_id": self.leader_id,
            "prev_log_index": self.prev_log_index, "prev_log_term": self.prev_log_term,
            "entries": [e.to_dict() for e in self.entries], "leader_commit": self.leader_commit,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> AppendEntries:
        return cls(
            term=int(d["term"]), leader_id=int(d["leader_id"]),
            prev_log_index=int(d["prev_log_index"]), prev_log_term=int(d["prev_log_term"]),
            entries=tuple(Entry.from_dict(e) for e in d.get("entries", [])),
            leader_commit=int(d.get("leader_commit", 0)),
        )


@dataclass(frozen=True, slots=True)
class AppendEntriesReply:
    term: int
    success: bool
    conflict_index: int = 0
    conflict_term: int = 0
    match_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term, "success": self.success,
            "conflict_index": self.conflict_index, "conflict_term": self.conflict_term,
            "match_index": self.match_index,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> AppendEntriesReply:
        return cls(
            term=int(d["term"]), success=bool(d["success"]),
            conflict_index=int(d.get("conflict_index", 0)),
            conflict_term=int(d.get("conflict_term", 0)),
            match_index=int(d.get("match_index", 0)),
        )


@dataclass(frozen=True, slots=True)
class RequestVote:
    term: int
    candidate_id: int
    last_log_index: int
    last_log_term: int

    def __post_init__(self) -> None:
        for name in ("term", "candidate_id", "last_log_index", "last_log_term"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term, "candidate_id": self.candidate_id,
            "last_log_index": self.last_log_index, "last_log_term": self.last_log_term,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> RequestVote:
        return cls(
            term=int(d["term"]), candidate_id=int(d["candidate_id"]),
            last_log_index=int(d["last_log_index"]), last_log_term=int(d["last_log_term"]),
        )


@dataclass(frozen=True, slots=True)
class RequestVoteReply:
    term: int
    vote_granted: bool

    def to_dict(self) -> dict[str, Any]:
        return {"term": self.term, "vote_granted": self.vote_granted}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> RequestVoteReply:
        return cls(term=int(d["term"]), vote_granted=bool(d["vote_granted"]))


def encode_append_entries(msg: AppendEntries, *, with_crc: bool = True) -> bytes:
    body = bytearray(
        _U16.pack(MAGIC_AE) + _U8.pack(_RPC_VERSION) + _U8.pack(0)
        + _U64.pack(msg.term) + _U64.pack(msg.leader_id)
        + _U64.pack(msg.prev_log_index) + _U64.pack(msg.prev_log_term)
        + _U64.pack(msg.leader_commit) + _U32.pack(len(msg.entries))
    )
    for ent in msg.entries:
        blob = encode_entry(ent, with_crc=False)
        body += _U32.pack(len(blob)) + blob
    raw = bytes(body)
    return append_crc32c(raw) if with_crc else raw


def decode_append_entries(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> AppendEntries:
    data = verify_crc32c(buf, context="append_entries") if with_crc else bytes(buf)
    view = memoryview(data)
    _need(view, 0, 4 + 40 + 4, "append_entries header")
    (magic,) = _U16.unpack_from(view, 0)
    if magic != MAGIC_AE:
        raise MagicError(MAGIC_AE, magic)
    (ver,) = _U8.unpack_from(view, 2)
    if ver != _RPC_VERSION:
        raise VersionError(ver)
    term, leader_id, prev_idx, prev_term, leader_commit = struct.unpack_from("<QQQQQ", view, 4)
    offset = 44
    (n,) = _U32.unpack_from(view, offset); offset += 4
    entries: list[Entry] = []
    for _ in range(n):
        _need(view, offset, 4, "entry length")
        (elen,) = _U32.unpack_from(view, offset); offset += 4
        _need(view, offset, elen, "entry body")
        entries.append(decode_entry(view[offset:offset + elen], with_crc=False))
        offset += elen
    if offset != len(view):
        raise CodecError(f"trailing bytes in append_entries: {len(view) - offset}")
    return AppendEntries(
        term=term, leader_id=leader_id, prev_log_index=prev_idx,
        prev_log_term=prev_term, entries=tuple(entries), leader_commit=leader_commit,
    )


def encode_append_entries_reply(msg: AppendEntriesReply, *, with_crc: bool = True) -> bytes:
    body = (
        _U16.pack(MAGIC_AER) + _U8.pack(_RPC_VERSION) + _U8.pack(1 if msg.success else 0)
        + _U64.pack(msg.term) + _U64.pack(msg.conflict_index)
        + _U64.pack(msg.conflict_term) + _U64.pack(msg.match_index)
    )
    return append_crc32c(body) if with_crc else body


def decode_append_entries_reply(
    buf: bytes | bytearray | memoryview, *, with_crc: bool = True
) -> AppendEntriesReply:
    data = verify_crc32c(buf, context="append_entries_reply") if with_crc else bytes(buf)
    view = memoryview(data)
    _need(view, 0, 36, "append_entries_reply")
    (magic,) = _U16.unpack_from(view, 0)
    if magic != MAGIC_AER:
        raise MagicError(MAGIC_AER, magic)
    (ver,) = _U8.unpack_from(view, 2)
    if ver != _RPC_VERSION:
        raise VersionError(ver)
    (success_b,) = _U8.unpack_from(view, 3)
    term, cidx, cterm, midx = struct.unpack_from("<QQQQ", view, 4)
    return AppendEntriesReply(
        term=term, success=bool(success_b), conflict_index=cidx,
        conflict_term=cterm, match_index=midx,
    )


def encode_request_vote(msg: RequestVote, *, with_crc: bool = True) -> bytes:
    body = (
        _U16.pack(MAGIC_RV) + _U8.pack(_RPC_VERSION) + _U8.pack(0)
        + _U64.pack(msg.term) + _U64.pack(msg.candidate_id)
        + _U64.pack(msg.last_log_index) + _U64.pack(msg.last_log_term)
    )
    return append_crc32c(body) if with_crc else body


def decode_request_vote(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> RequestVote:
    data = verify_crc32c(buf, context="request_vote") if with_crc else bytes(buf)
    view = memoryview(data)
    _need(view, 0, 36, "request_vote")
    (magic,) = _U16.unpack_from(view, 0)
    if magic != MAGIC_RV:
        raise MagicError(MAGIC_RV, magic)
    (ver,) = _U8.unpack_from(view, 2)
    if ver != _RPC_VERSION:
        raise VersionError(ver)
    term, cid, lli, llt = struct.unpack_from("<QQQQ", view, 4)
    return RequestVote(term=term, candidate_id=cid, last_log_index=lli, last_log_term=llt)


def encode_request_vote_reply(msg: RequestVoteReply, *, with_crc: bool = True) -> bytes:
    body = (
        _U16.pack(MAGIC_RVR) + _U8.pack(_RPC_VERSION) + _U8.pack(1 if msg.vote_granted else 0)
        + _U64.pack(msg.term)
    )
    return append_crc32c(body) if with_crc else body


def decode_request_vote_reply(
    buf: bytes | bytearray | memoryview, *, with_crc: bool = True
) -> RequestVoteReply:
    data = verify_crc32c(buf, context="request_vote_reply") if with_crc else bytes(buf)
    view = memoryview(data)
    _need(view, 0, 12, "request_vote_reply")
    (magic,) = _U16.unpack_from(view, 0)
    if magic != MAGIC_RVR:
        raise MagicError(MAGIC_RVR, magic)
    (ver,) = _U8.unpack_from(view, 2)
    if ver != _RPC_VERSION:
        raise VersionError(ver)
    (granted,) = _U8.unpack_from(view, 3)
    (term,) = _U64.unpack_from(view, 4)
    return RequestVoteReply(term=term, vote_granted=bool(granted))


_RPC_DECODERS = {
    MAGIC_AE: decode_append_entries,
    MAGIC_AER: decode_append_entries_reply,
    MAGIC_RV: decode_request_vote,
    MAGIC_RVR: decode_request_vote_reply,
}


def encode_rpc(msg: Any, *, with_crc: bool = True) -> bytes:
    if isinstance(msg, AppendEntries):
        return encode_append_entries(msg, with_crc=with_crc)
    if isinstance(msg, AppendEntriesReply):
        return encode_append_entries_reply(msg, with_crc=with_crc)
    if isinstance(msg, RequestVote):
        return encode_request_vote(msg, with_crc=with_crc)
    if isinstance(msg, RequestVoteReply):
        return encode_request_vote_reply(msg, with_crc=with_crc)
    raise RpcError(f"unsupported RPC type: {type(msg)!r}")


def decode_rpc(buf: bytes | bytearray | memoryview, *, with_crc: bool = True) -> Any:
    if len(buf) < 2:
        raise TruncatedError(2, len(buf), "rpc magic")
    magic = _U16.unpack_from(buf, 0)[0]
    decoder = _RPC_DECODERS.get(magic)
    if decoder is None:
        raise RpcError(f"unknown RPC magic 0x{magic:04X}")
    return decoder(buf, with_crc=with_crc)


def rpc_to_json(msg: Any) -> dict[str, Any]:
    mapping = {
        "AppendEntries": "append_entries", "AppendEntriesReply": "append_entries_reply",
        "RequestVote": "request_vote", "RequestVoteReply": "request_vote_reply",
    }
    key = mapping.get(type(msg).__name__)
    if key is None or not hasattr(msg, "to_dict"):
        raise RpcError(f"cannot JSON-encode {type(msg).__name__}")
    return {"_type": key, **msg.to_dict()}


def rpc_from_json(d: Mapping[str, Any]) -> Any:
    t = d.get("_type", "")
    if t in ("append_entries", "AppendEntries"):
        return AppendEntries.from_dict(d)
    if t in ("append_entries_reply", "AppendEntriesReply"):
        return AppendEntriesReply.from_dict(d)
    if t in ("request_vote", "RequestVote"):
        return RequestVote.from_dict(d)
    if t in ("request_vote_reply", "RequestVoteReply"):
        return RequestVoteReply.from_dict(d)
    raise RpcError(f"unknown RPC _type: {t!r}")


def dumps_rpc_jsonl(messages: Sequence[Any]) -> str:
    lines = [json.dumps(rpc_to_json(m), sort_keys=True, separators=(",", ":")) for m in messages]
    return "\n".join(lines) + ("\n" if lines else "")


def loads_rpc_jsonl(text: str) -> list[Any]:
    out: list[Any] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RpcError(f"JSONL line {lineno}: {exc}") from exc
        out.append(rpc_from_json(obj))
    return out


def is_heartbeat(msg: AppendEntries) -> bool:
    return len(msg.entries) == 0


def vote_is_up_to_date(
    candidate_last_term: int, candidate_last_index: int,
    voter_last_term: int, voter_last_index: int,
) -> bool:
    if candidate_last_term != voter_last_term:
        return candidate_last_term > voter_last_term
    return candidate_last_index >= voter_last_index


__all__ = [
    "AppendEntries", "AppendEntriesReply", "RequestVote", "RequestVoteReply",
    "encode_append_entries", "decode_append_entries",
    "encode_append_entries_reply", "decode_append_entries_reply",
    "encode_request_vote", "decode_request_vote",
    "encode_request_vote_reply", "decode_request_vote_reply",
    "encode_rpc", "decode_rpc", "rpc_to_json", "rpc_from_json",
    "dumps_rpc_jsonl", "loads_rpc_jsonl", "is_heartbeat", "vote_is_up_to_date",
    "MAGIC_AE", "MAGIC_AER", "MAGIC_RV", "MAGIC_RVR",
]
