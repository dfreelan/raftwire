"""Length-prefixed, CRC-protected wire frames."""

from __future__ import annotations

import struct
import zlib
from enum import IntFlag
from typing import Iterator, Sequence

from raftwire.crc import crc32c, pack_crc32c, unpack_crc32c
from raftwire.errors import (
    ChecksumError,
    FrameError,
    FrameTooLargeError,
    IncompleteFrameError,
)

_HEADER = struct.Struct("<IB")  # length u32, flags u8
_HEADER_SIZE = _HEADER.size  # 5
_TRAILER_SIZE = 4
_OVERHEAD = _HEADER_SIZE + _TRAILER_SIZE  # 9

DEFAULT_MAX_FRAME = 64 * 1024 * 1024  # 64 MiB
DEFAULT_COMPRESS_THRESHOLD = 1024


class FrameFlags(IntFlag):
    """Bit flags in the frame header."""

    NONE = 0
    COMPRESSED = 1 << 0


class FrameEncoder:
    """Encode payloads into CRC-protected frames."""

    def __init__(
        self,
        *,
        compress: bool = False,
        compress_threshold: int = DEFAULT_COMPRESS_THRESHOLD,
        max_frame: int = DEFAULT_MAX_FRAME,
        compress_level: int = 6,
    ) -> None:
        if max_frame < 1:
            raise ValueError("max_frame must be >= 1")
        self.compress = compress
        self.compress_threshold = compress_threshold
        self.max_frame = max_frame
        self.compress_level = compress_level
        self._frames_encoded = 0
        self._bytes_out = 0

    @property
    def frames_encoded(self) -> int:
        return self._frames_encoded

    @property
    def bytes_out(self) -> int:
        return self._bytes_out

    def encode(self, payload: bytes | bytearray | memoryview) -> bytes:
        """Encode a single payload into a frame."""
        data = bytes(payload)
        flags = FrameFlags.NONE
        if self.compress and len(data) >= self.compress_threshold:
            compressed = zlib.compress(data, self.compress_level)
            if len(compressed) < len(data):
                data = compressed
                flags = FrameFlags.COMPRESSED
        if len(data) > self.max_frame:
            raise FrameTooLargeError(len(data), self.max_frame)
        header = _HEADER.pack(len(data), int(flags))
        body = header + data
        digest = crc32c(body)
        frame = body + pack_crc32c(digest)
        self._frames_encoded += 1
        self._bytes_out += len(frame)
        return frame

    def encode_many(self, payloads: Sequence[bytes | bytearray]) -> bytes:
        """Encode multiple payloads and concatenate the frames."""
        return b"".join(self.encode(p) for p in payloads)

    def reset_stats(self) -> None:
        self._frames_encoded = 0
        self._bytes_out = 0


class FrameDecoder:
    """Incremental decoder for CRC-protected frames."""

    def __init__(self, *, max_frame: int = DEFAULT_MAX_FRAME) -> None:
        if max_frame < 1:
            raise ValueError("max_frame must be >= 1")
        self.max_frame = max_frame
        self._buf = bytearray()
        self._frames_decoded = 0
        self._bytes_in = 0

    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded

    @property
    def bytes_in(self) -> int:
        return self._bytes_in

    @property
    def buffered(self) -> int:
        return len(self._buf)

    def feed(self, data: bytes | bytearray | memoryview) -> Iterator[bytes]:
        """Append *data* and yield every complete payload decoded so far."""
        if data:
            self._buf.extend(data)
            self._bytes_in += len(data)
        while True:
            payload = self._try_one()
            if payload is None:
                break
            yield payload

    def feed_all(self, data: bytes | bytearray | memoryview) -> list[bytes]:
        """Convenience: feed and collect all complete payloads."""
        return list(self.feed(data))

    def flush(self) -> list[bytes]:
        """Return any remaining complete frames; error if trailing partial."""
        out = list(self.feed(b""))
        if self._buf:
            raise IncompleteFrameError(need=_OVERHEAD, have=len(self._buf))
        return out

    def reset(self) -> None:
        self._buf.clear()
        self._frames_decoded = 0
        self._bytes_in = 0

    def _try_one(self) -> bytes | None:
        if len(self._buf) < _HEADER_SIZE:
            return None
        length, flags = _HEADER.unpack_from(self._buf, 0)
        if length > self.max_frame:
            raise FrameTooLargeError(length, self.max_frame)
        total = _HEADER_SIZE + length + _TRAILER_SIZE
        if len(self._buf) < total:
            return None
        body = bytes(self._buf[: _HEADER_SIZE + length])
        expected = unpack_crc32c(self._buf, _HEADER_SIZE + length)
        actual = crc32c(body)
        if actual != expected:
            raise ChecksumError(expected, actual, context="frame")
        payload = body[_HEADER_SIZE:]
        if flags & FrameFlags.COMPRESSED:
            try:
                payload = zlib.decompress(payload)
            except zlib.error as exc:
                raise FrameError(f"zlib decompress failed: {exc}") from exc
        del self._buf[:total]
        self._frames_decoded += 1
        return payload


def frame_overhead() -> int:
    """Return the fixed per-frame overhead in bytes (header + CRC)."""
    return _OVERHEAD


def framed_size(payload_len: int, *, compressed_len: int | None = None) -> int:
    """Compute on-wire size for a payload of the given length."""
    body = compressed_len if compressed_len is not None else payload_len
    return _OVERHEAD + body


def encode_frame(
    payload: bytes | bytearray | memoryview,
    *,
    compress: bool = False,
    max_frame: int = DEFAULT_MAX_FRAME,
) -> bytes:
    """One-shot frame encode."""
    return FrameEncoder(compress=compress, max_frame=max_frame).encode(payload)


def decode_frame(
    buf: bytes | bytearray | memoryview,
    *,
    max_frame: int = DEFAULT_MAX_FRAME,
) -> bytes:
    """One-shot decode of a single frame; trailing bytes are an error."""
    dec = FrameDecoder(max_frame=max_frame)
    payloads = dec.feed_all(buf)
    if dec.buffered:
        raise FrameError(f"{dec.buffered} trailing bytes after frame")
    if len(payloads) != 1:
        raise FrameError(f"expected exactly one frame, got {len(payloads)}")
    return payloads[0]


def decode_frames(
    buf: bytes | bytearray | memoryview,
    *,
    max_frame: int = DEFAULT_MAX_FRAME,
) -> list[bytes]:
    """Decode a concatenation of frames; require a clean end-of-buffer."""
    dec = FrameDecoder(max_frame=max_frame)
    payloads = dec.feed_all(buf)
    if dec.buffered:
        raise IncompleteFrameError(need=_OVERHEAD, have=dec.buffered)
    return payloads


__all__ = [
    "FrameFlags",
    "FrameEncoder",
    "FrameDecoder",
    "DEFAULT_MAX_FRAME",
    "DEFAULT_COMPRESS_THRESHOLD",
    "frame_overhead",
    "framed_size",
    "encode_frame",
    "decode_frame",
    "decode_frames",
]
