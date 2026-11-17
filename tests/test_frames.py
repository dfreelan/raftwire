"""Frame encoder/decoder tests."""

from __future__ import annotations

import pytest

from raftwire.errors import ChecksumError, FrameTooLargeError, IncompleteFrameError
from raftwire.frames import (
    FrameDecoder,
    FrameEncoder,
    decode_frame,
    decode_frames,
    encode_frame,
    frame_overhead,
    framed_size,
)


def test_frame_roundtrip_simple():
    payload = b"hello frames"
    frame = encode_frame(payload)
    assert decode_frame(frame) == payload
    assert len(frame) == framed_size(len(payload))
    assert frame_overhead() == 9


def test_frame_encoder_decoder_stats():
    enc = FrameEncoder()
    wire = enc.encode_many([b"a", b"bb", b"ccc"])
    assert enc.frames_encoded == 3
    dec = FrameDecoder()
    out = dec.feed_all(wire)
    assert out == [b"a", b"bb", b"ccc"]
    assert dec.frames_decoded == 3
    assert dec.buffered == 0


def test_incremental_feed():
    enc = FrameEncoder()
    frame = enc.encode(b"streamed-payload")
    dec = FrameDecoder()
    # Feed one byte at a time
    got: list[bytes] = []
    for i in range(len(frame)):
        got.extend(dec.feed(frame[i : i + 1]))
    assert got == [b"streamed-payload"]


def test_compressed_frame():
    payload = b"REPEAT" * 500
    enc = FrameEncoder(compress=True, compress_threshold=32)
    frame = enc.encode(payload)
    assert len(frame) < framed_size(len(payload))  # should compress
    assert FrameDecoder().feed_all(frame) == [payload]


def test_frame_crc_corruption():
    frame = bytearray(encode_frame(b"secure"))
    frame[-2] ^= 0xFF
    with pytest.raises(ChecksumError):
        decode_frame(bytes(frame))


def test_frame_too_large():
    enc = FrameEncoder(max_frame=16)
    with pytest.raises(FrameTooLargeError):
        enc.encode(b"x" * 64)


def test_incomplete_flush():
    dec = FrameDecoder()
    list(dec.feed(encode_frame(b"ok")[:4]))
    with pytest.raises(IncompleteFrameError):
        dec.flush()


def test_decode_frames_multi():
    wire = FrameEncoder().encode_many([b"1", b"2", b"3"])
    assert decode_frames(wire) == [b"1", b"2", b"3"]


