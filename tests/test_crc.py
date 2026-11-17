"""CRC32C unit tests and corruption detection."""

from __future__ import annotations

import pytest

from raftwire.crc import (
    append_crc32c,
    checksum_trail,
    crc32c,
    crc32c_hex,
    crc32c_update,
    pack_crc32c,
    split_crc32c,
    unpack_crc32c,
    verify_crc32c,
    verify_crc32c_value,
)
from raftwire.errors import ChecksumError


def test_crc32c_empty():
    # Castagnoli CRC of empty message with standard init/xorout is 0.
    assert crc32c(b"") == 0


def test_crc32c_known_vectors():
    # Well-known CRC32C vectors
    assert crc32c(b"123456789") == 0xE3069283
    assert crc32c_hex(b"123456789") == "e3069283"


def test_crc32c_deterministic():
    data = b"raft-wal-" + bytes(range(256))
    assert crc32c(data) == crc32c(data)
    assert crc32c(data) != crc32c(data + b"x")


def test_append_and_verify_roundtrip():
    payload = b"hello-wal"
    framed = append_crc32c(payload)
    assert framed[:-4] == payload
    assert verify_crc32c(framed) == payload


def test_verify_detects_corruption():
    framed = bytearray(append_crc32c(b"important"))
    framed[2] ^= 0xFF
    with pytest.raises(ChecksumError) as ei:
        verify_crc32c(bytes(framed), context="test")
    assert "test" in ei.value.context or "test" in str(ei.value)


def test_verify_detects_trailer_tamper():
    framed = bytearray(append_crc32c(b"data"))
    framed[-1] ^= 0x01
    with pytest.raises(ChecksumError):
        verify_crc32c(bytes(framed))


def test_split_and_pack():
    payload, digest = split_crc32c(append_crc32c(b"abc"))
    assert payload == b"abc"
    assert digest == crc32c(b"abc")
    assert unpack_crc32c(pack_crc32c(digest)) == digest


def test_split_too_short():
    with pytest.raises(ChecksumError):
        split_crc32c(b"ab")


def test_verify_crc32c_value():
    d = b"xyz"
    verify_crc32c_value(d, crc32c(d))
    with pytest.raises(ChecksumError):
        verify_crc32c_value(d, 0xDEADBEEF)



def test_checksum_trail_order_sensitive():
    a, b = b"one", b"two"
    assert checksum_trail([a, b]) != checksum_trail([b, a])
    assert checksum_trail([a, b]) == checksum_trail([a, b])


