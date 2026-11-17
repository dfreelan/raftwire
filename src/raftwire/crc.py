"""CRC32C (Castagnoli) checksum helpers."""

from __future__ import annotations

import struct
from typing import Final

from raftwire.errors import ChecksumError

# Castagnoli polynomial (reflected form used by the table-driven algorithm).
_POLY: Final[int] = 0x82F63B78

# Precompute the 256-entry reflected CRC32C table once at import time.
def _build_table() -> tuple[int, ...]:
    table: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ _POLY
            else:
                crc >>= 1
        table.append(crc & 0xFFFFFFFF)
    return tuple(table)


_TABLE: Final[tuple[int, ...]] = _build_table()

# Struct helpers for packing/unpacking the 4-byte trailer.
_U32_LE: Final[struct.Struct] = struct.Struct("<I")


def crc32c(data: bytes | bytearray | memoryview, init: int = 0xFFFFFFFF) -> int:
    """Compute the CRC32C checksum of *data*.

    Parameters
    ----------
    data:
        Bytes to checksum.
    init:
        Initial CRC register value. Default ``0xFFFFFFFF`` is the standard
        Castagnoli init. Pass a previous result (before final XOR) only when
        intentionally chaining; for normal use leave the default.

    Returns
    -------
    int
        32-bit checksum in ``[0, 2**32)``.
    """
    crc = init & 0xFFFFFFFF
    for byte in data:
        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def crc32c_update(crc: int, data: bytes | bytearray | memoryview) -> int:
    """Continue a CRC32C computation.

    *crc* must be the **raw** register value (after init XOR, before final
    XOR). Prefer :func:`crc32c` for one-shot hashing. This helper exists for
    streaming frame builders that accumulate payload in chunks.
    """
    # Undo the final XOR that crc32c applies, continue, then re-apply.
    # Actually for streaming we keep the inverted form internally.
    # Accept a finalized crc and convert back to register form.
    reg = (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF
    for byte in data:
        reg = _TABLE[(reg ^ byte) & 0xFF] ^ (reg >> 8)
    return (reg ^ 0xFFFFFFFF) & 0xFFFFFFFF


def crc32c_hex(data: bytes | bytearray | memoryview) -> str:
    """Return the CRC32C of *data* as an 8-character lowercase hex string."""
    return f"{crc32c(data):08x}"


def pack_crc32c(value: int) -> bytes:
    """Pack a CRC32C value as 4 little-endian bytes."""
    return _U32_LE.pack(value & 0xFFFFFFFF)


def unpack_crc32c(buf: bytes | bytearray | memoryview, offset: int = 0) -> int:
    """Unpack a little-endian CRC32C value from *buf* at *offset*."""
    if offset < 0 or offset + 4 > len(buf):
        raise ChecksumError(0, 0, context="truncated CRC trailer")
    return _U32_LE.unpack_from(buf, offset)[0]


def append_crc32c(payload: bytes | bytearray) -> bytes:
    """Return ``payload || crc32c(payload)`` as a new ``bytes`` object."""
    digest = crc32c(payload)
    return bytes(payload) + pack_crc32c(digest)


def split_crc32c(buf: bytes | bytearray | memoryview) -> tuple[bytes, int]:
    """Split ``payload || crc`` into ``(payload, crc_value)``.

    Raises
    ------
    ChecksumError
        If *buf* is shorter than 4 bytes.
    """
    if len(buf) < 4:
        raise ChecksumError(0, 0, context="buffer shorter than CRC trailer")
    payload = bytes(buf[:-4])
    digest = unpack_crc32c(buf, len(buf) - 4)
    return payload, digest


def verify_crc32c(
    buf: bytes | bytearray | memoryview,
    *,
    context: str = "",
) -> bytes:
    """Verify a ``payload || crc`` buffer and return the payload.

    Raises
    ------
    ChecksumError
        If the trailer does not match ``crc32c(payload)``.
    """
    payload, expected = split_crc32c(buf)
    actual = crc32c(payload)
    if actual != expected:
        raise ChecksumError(expected, actual, context=context or "verify_crc32c")
    return payload


def verify_crc32c_value(
    data: bytes | bytearray | memoryview,
    expected: int,
    *,
    context: str = "",
) -> None:
    """Verify that ``crc32c(data) == expected``.

    Raises
    ------
    ChecksumError
        On mismatch.
    """
    actual = crc32c(data)
    if actual != expected:
        raise ChecksumError(expected, actual, context=context or "verify_crc32c_value")


def checksum_trail(items: list[bytes]) -> int:
    """Compute a running CRC32C over a sequence of byte blobs.

    Useful for replay checksum trails: feed entries in order and obtain a
    single digest that fingerprints the whole sequence.
    """
    # Chain by hashing previous digest bytes + next item.
    acc = 0
    for item in items:
        chunk = pack_crc32c(acc) + item
        acc = crc32c(chunk)
    return acc


__all__ = [
    "crc32c",
    "crc32c_update",
    "crc32c_hex",
    "pack_crc32c",
    "unpack_crc32c",
    "append_crc32c",
    "split_crc32c",
    "verify_crc32c",
    "verify_crc32c_value",
    "checksum_trail",
]
