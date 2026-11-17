"""Binary and JSONL codec round-trip tests."""

from __future__ import annotations

import pytest

from raftwire.codec import (
    MAGIC_ENTRY,
    decode_any,
    decode_conf_state,
    decode_entries_stream,
    decode_entry,
    decode_hard_state,
    decode_snapshot,
    decode_soft_state,
    dumps_jsonl,
    encode_conf_state,
    encode_entries,
    encode_entry,
    encode_hard_state,
    encode_record,
    encode_snapshot,
    encode_soft_state,
    entry_from_json,
    entry_to_json,
    loads_jsonl,
    peek_magic,
    record_digest,
    record_from_json,
    record_to_json,
)
from raftwire.crc import crc32c
from raftwire.errors import ChecksumError, CodecError, MagicError, TruncatedError, VersionError
from raftwire.types import (
    ConfState,
    Entry,
    EntryType,
    HardState,
    Snapshot,
    SnapshotMetadata,
    SoftState,
)


@pytest.mark.parametrize(
    "entry",
    [
        Entry(term=0, index=0, data=b""),
        Entry(term=1, index=1, data=b"hi"),
        Entry(term=2, index=99, data=bytes(range(255)), context=b"ctx", entry_type=EntryType.CONF_CHANGE),
        Entry(term=2**40, index=2**40, data=b"x" * 1000),
    ],
)
def test_entry_binary_roundtrip(entry: Entry):
    blob = encode_entry(entry)
    assert decode_entry(blob) == entry
    assert peek_magic(blob) == MAGIC_ENTRY


def test_hard_soft_conf_snapshot_roundtrip(hard_state, soft_state, conf_state, snapshot):
    assert decode_hard_state(encode_hard_state(hard_state)) == hard_state
    assert decode_soft_state(encode_soft_state(soft_state)) == soft_state
    assert decode_conf_state(encode_conf_state(conf_state)) == conf_state
    assert decode_snapshot(encode_snapshot(snapshot)) == snapshot


def test_decode_any_dispatch(sample_entry, hard_state):
    assert decode_any(encode_entry(sample_entry)) == sample_entry
    assert decode_any(encode_hard_state(hard_state)) == hard_state


def test_entry_crc_corruption(sample_entry):
    blob = bytearray(encode_entry(sample_entry))
    blob[10] ^= 0x55
    with pytest.raises(ChecksumError):
        decode_entry(bytes(blob))


def test_truncated_entry():
    with pytest.raises((TruncatedError, ChecksumError)):
        decode_entry(b"\x46\x52")


def test_bad_magic():
    blob = bytearray(encode_entry(Entry(term=1, index=1, data=b"a")))
    blob[0] = 0x00
    blob[1] = 0x00
    # Recalc would be needed for CRC — just check magic path without CRC
    with pytest.raises(MagicError):
        decode_entry(bytes(blob)[:-4] , with_crc=False)  # type: ignore[call-arg]


def test_entries_stream(sample_entries):
    blob = encode_entries(sample_entries)
    assert decode_entries_stream(blob) == sample_entries


def test_jsonl_roundtrip(sample_entries, hard_state, snapshot):
    records = [hard_state, snapshot, *sample_entries[:3]]
    text = dumps_jsonl(records)
    back = loads_jsonl(text)
    assert len(back) == len(records)
    assert back[0] == hard_state
    assert back[1] == snapshot
    assert back[2] == sample_entries[0]


def test_jsonl_skips_comments_and_blank():
    text = "# comment\n\n" + dumps_jsonl([HardState(term=1)])
    assert loads_jsonl(text) == [HardState(term=1)]


def test_jsonl_bad_line():
    with pytest.raises(CodecError):
        loads_jsonl("{not json")


def test_entry_json_helpers(sample_entry):
    d = entry_to_json(sample_entry)
    assert d["_type"] == "entry"
    assert entry_from_json(d) == sample_entry
    assert record_from_json(record_to_json(sample_entry)) == sample_entry


def test_encode_record_and_digest(sample_entry, hard_state):
    assert encode_record(sample_entry) == encode_entry(sample_entry)
    assert record_digest(sample_entry) == crc32c(encode_entry(sample_entry, with_crc=False))
    with pytest.raises(CodecError):
        encode_record(object())


def test_version_error_on_hard_state():
    body = bytearray(encode_hard_state(HardState(term=1), with_crc=False))
    body[2] = 99  # version byte
    with pytest.raises(VersionError):
        decode_hard_state(bytes(body), with_crc=False)
