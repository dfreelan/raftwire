"""Randomized / fuzz-ish round-trip tests."""

from __future__ import annotations

import random

import pytest

from raftwire.codec import decode_entry, dumps_jsonl, encode_entry, loads_jsonl
from raftwire.errors import ChecksumError
from raftwire.frames import FrameDecoder, FrameEncoder
from raftwire.logstore import MemoryLogStore
from raftwire.types import Entry, EntryType


def _R(seed: int = 0x52414654) -> random.Random:
    return random.Random(seed)


def _rand_bytes(r: random.Random, n: int) -> bytes:
    return bytes(r.getrandbits(8) for _ in range(n))


@pytest.mark.fuzz
def test_fuzz_entry_roundtrips():
    r = _R(1)
    for i in range(40):
        entry = Entry(
            term=r.randint(0, 10_000),
            index=r.randint(0, 10_000),
            data=_rand_bytes(r, r.randint(0, 512)),
            context=_rand_bytes(r, r.randint(0, 64)),
            entry_type=EntryType(r.choice([0, 1, 2])),
        )
        blob = encode_entry(entry)
        assert decode_entry(blob) == entry
        # JSONL path
        text = dumps_jsonl([entry])
        assert loads_jsonl(text)[0] == entry


@pytest.mark.fuzz
def test_fuzz_frame_stream_reassembly():
    r = _R(2)
    enc = FrameEncoder(compress=True, compress_threshold=64)
    payloads = [_rand_bytes(r, r.randint(0, 2048)) for _ in range(40)]
    wire = enc.encode_many(payloads)
    # Reassemble with random chunk sizes
    dec = FrameDecoder()
    got: list[bytes] = []
    pos = 0
    while pos < len(wire):
        n = r.randint(1, 97)
        got.extend(dec.feed(wire[pos : pos + n]))
        pos += n
    assert got == payloads
    assert dec.buffered == 0


@pytest.mark.fuzz
def test_fuzz_corruption_always_detected():
    r = _R(3)
    detections = 0
    attempts = 0
    for _ in range(40):
        entry = Entry(term=r.randint(1, 50), index=r.randint(1, 50), data=_rand_bytes(r, 128))
        blob = bytearray(encode_entry(entry))
        # Flip a random payload byte (not always CRC — still should fail verify)
        idx = r.randint(0, len(blob) - 1)
        blob[idx] ^= 1 << r.randint(0, 7)
        attempts += 1
        try:
            decode_entry(bytes(blob))
            # Extremely unlikely: flip cancelled out somehow
        except ChecksumError:
            detections += 1
        except Exception:
            detections += 1  # other structural errors also count as detection
    assert detections == attempts


@pytest.mark.fuzz
def test_fuzz_log_append_truncate_cycle():
    r = _R(4)
    store = MemoryLogStore()
    next_index = 1
    term = 1
    for _ in range(15):
        op = r.choice(["append", "append", "truncate", "compact"])
        if op == "append" or store.last_index() == 0:
            n = r.randint(1, 5)
            batch = []
            for i in range(n):
                if r.random() < 0.2:
                    term += 1
                batch.append(
                    Entry(term=term, index=next_index + i, data=_rand_bytes(r, 8))
                )
            store.append(*batch)
            next_index += n
        elif op == "truncate" and store.last_index() > store.first_index():
            cut = r.randint(store.first_index(), store.last_index())
            store.truncate_suffix(cut)
            next_index = store.last_index() + 1
            if store.last_index() >= store.first_index() and store.last_index() > 0:
                term = max(term, store.last_term())
        elif op == "compact" and store.last_index() >= store.first_index() + 2:
            from raftwire.compact import compact_to

            through = r.randint(store.first_index(), store.last_index() - 1)
            compact_to(store, through, data=b"x")
            next_index = store.last_index() + 1
        store.validate()






