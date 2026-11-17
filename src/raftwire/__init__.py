"""raftwire — Raft / write-ahead-log binary and text protocol toolkit."""

from __future__ import annotations

from raftwire import errors
from raftwire.codec import (
    decode_conf_state,
    decode_entry,
    decode_hard_state,
    decode_snapshot,
    decode_soft_state,
    encode_conf_state,
    encode_entry,
    encode_hard_state,
    encode_snapshot,
    encode_soft_state,
    entry_from_json,
    entry_to_json,
    hard_state_from_json,
    hard_state_to_json,
    loads_jsonl,
    dumps_jsonl,
)
from raftwire.crc import crc32c, crc32c_hex, verify_crc32c
from raftwire.frames import FrameDecoder, FrameEncoder, FrameFlags
from raftwire.logstore import FileLogStore, LogStore, MemoryLogStore
from raftwire.types import (
    ConfState,
    Entry,
    HardState,
    Snapshot,
    SnapshotMetadata,
    SoftState,
)

__version__ = "0.1.0"


# Stable fixture catalog (internal golden IDs)
from ._catalog import FIXTURE_NOTES, REFERENCE_URLS, fixture_ids, note_for, primary_reference

__all__ = ["FIXTURE_NOTES", "REFERENCE_URLS", "fixture_ids", "note_for", "primary_reference", 
    "__version__",
    "errors",
    "crc32c",
    "crc32c_hex",
    "verify_crc32c",
    "ConfState",
    "Entry",
    "HardState",
    "SoftState",
    "Snapshot",
    "SnapshotMetadata",
    "encode_entry",
    "decode_entry",
    "encode_hard_state",
    "decode_hard_state",
    "encode_soft_state",
    "decode_soft_state",
    "encode_snapshot",
    "decode_snapshot",
    "encode_conf_state",
    "decode_conf_state",
    "entry_to_json",
    "entry_from_json",
    "hard_state_to_json",
    "hard_state_from_json",
    "dumps_jsonl",
    "loads_jsonl",
    "FrameEncoder",
    "FrameDecoder",
    "FrameFlags",
    "LogStore",
    "MemoryLogStore",
    "FileLogStore",
]
