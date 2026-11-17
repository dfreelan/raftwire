# raftwire

**Raft / write-ahead-log binary and text protocol toolkit.**

`raftwire` is a stdlib-only Python library for encoding, framing, storing, and
replaying Raft-style consensus records. It provides compact binary codecs,
JSONL text interchange, CRC-protected frames, an in-memory / file-backed log
store, truncation and compaction helpers, mock RPC message types, and a
deterministic log replay engine.

Designed for systems engineers building orchestration, agent runtimes, and
distributed control planes that need a clear, testable WAL layer without
pulling in heavy dependencies.

## Features

| Area | What you get |
|------|----------------|
| **Types** | `HardState`, `SoftState`, `Entry`, `Snapshot`, `ConfState` |
| **Codec** | Compact little-endian binary + JSONL text round-trips |
| **Frames** | Length-prefixed, CRC32C-protected wire frames |
| **Log store** | Append, truncate, slice, index invariants, file persistence |
| **Compaction** | Snapshot install, prefix discard, size-based triggers |
| **RPC** | `AppendEntries`, `RequestVote`, and reply types |
| **Replay** | Deterministic, checksummed application of log segments |
| **CLI** | Inspect, encode, decode, compact, and verify WAL files |

Python **3.10+**. Zero runtime dependencies beyond the standard library
(`struct`, `hashlib`, `json`, `dataclasses`, `zlib`, `argparse`, …).

## Install

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

Or with pip:

```bash
pip install -e ".[dev]"
```

## Quick start

```python
from raftwire.types import Entry, HardState, SoftState
from raftwire.codec import encode_entry, decode_entry, encode_hard_state
from raftwire.frames import FrameEncoder, FrameDecoder
from raftwire.logstore import MemoryLogStore
from raftwire.crc import crc32c

# Build a log entry
entry = Entry(term=3, index=42, data=b'{"op":"set","k":"x","v":1}')
blob = encode_entry(entry)
assert decode_entry(blob) == entry

# CRC-framed wire stream
enc = FrameEncoder()
wire = enc.encode(blob)
dec = FrameDecoder()
payloads = list(dec.feed(wire))
assert payloads[0] == blob

# In-memory Raft log
store = MemoryLogStore()
store.append(Entry(term=1, index=1, data=b"a"))
store.append(Entry(term=1, index=2, data=b"b"))
store.set_hard_state(HardState(term=1, vote=7, commit=1))
assert store.last_index() == 2
assert store.entries(1, 3)[0].data == b"a"
```

## Wire format overview

### Binary entry (compact)

```
+--------+--------+----------+----------+------------+
| magic  | ver    | term     | index    | data_len   |
| u16    | u8     | u64      | u64      | u32        |
+--------+--------+----------+----------+------------+
| data bytes ...                                     |
+----------------------------------------------------+
| crc32c over header+data                            |
| u32                                                |
+----------------------------------------------------+
```

Magic is `0x5246` (`RF`). Version is currently `1`. All multi-byte integers
are little-endian. CRC covers everything before the trailer.

### Frame

```
+----------+----------+------------------+----------+
| length   | flags    | payload          | crc32c   |
| u32      | u8       | length bytes     | u32      |
+----------+----------+------------------+----------+
```

Flags bit 0 = compressed (zlib). Frames may be streamed; the decoder buffers
partial input safely.

### JSONL

Each record is one JSON object per line with a `"_type"` discriminator
(`entry`, `hard_state`, `soft_state`, `snapshot`, `append_entries`, …).
Useful for debugging and offline tooling.

## Modules

| Module | Responsibility |
|--------|----------------|
| `raftwire.types` | Dataclass record definitions |
| `raftwire.codec` | Binary + JSONL encode/decode |
| `raftwire.frames` | Length + CRC framing |
| `raftwire.crc` | CRC32C (Castagnoli) helpers |
| `raftwire.logstore` | Log storage abstractions |
| `raftwire.compact` | Truncation and snapshot compaction |
| `raftwire.rpc` | AppendEntries / RequestVote messages |
| `raftwire.replay` | Deterministic log replay |
| `raftwire.cli` | Command-line interface |
| `raftwire.errors` | Typed exception hierarchy |

## CLI

```bash
# Encode a sample entry to binary
raftwire encode-entry --term 1 --index 1 --data hello -o entry.bin

# Decode and print as JSON
raftwire decode-entry entry.bin

# Wrap payloads in CRC frames
raftwire frame pack entry.bin -o framed.bin
raftwire frame unpack framed.bin -o payload.bin

# Inspect a WAL file (JSONL or binary segment)
raftwire inspect wal.jsonl

# Verify CRC integrity
raftwire verify framed.bin

# Compact: discard entries up to a snapshot index
raftwire compact --until 100 wal.bin -o compacted.bin
```

## Log store invariants

The store maintains these invariants (checked by `validate()` and tests):

1. Indices are contiguous from `first_index` through `last_index`.
2. `entry.index` matches the absolute log position.
3. Terms are non-decreasing across consecutive entries.
4. `hard_state.commit` never exceeds `last_index`.
5. After truncation at `i`, no entry with index `>= i` remains.
6. After compaction through snapshot index `s`, `first_index == s + 1`.

## Replay

```python
from raftwire.replay import Replayer, apply_entries
from raftwire.types import Entry

class CounterMachine:
    def __init__(self) -> None:
        self.value = 0

    def apply(self, entry: Entry) -> None:
        self.value += int(entry.data)

sm = CounterMachine()
replayer = Replayer(sm)
replayer.replay([
    Entry(term=1, index=1, data=b"10"),
    Entry(term=1, index=2, data=b"5"),
])
assert sm.value == 15
assert replayer.last_applied == 2
```

Replay is deterministic: same entries, same order, same checksum trail.

## Testing

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/pytest -q
```

The suite covers round-trips, CRC corruption detection, log index invariants,
compaction edge cases, RPC serialization, fuzz-style randomized payloads, and
CLI smoke tests.

## Design notes

- **Stdlib only** — no protobuf, no msgpack, no third-party CRC packages.
- **Explicit endianness** — little-endian everywhere; documented in codecs.
- **Fail loud** — corrupt CRC, truncated frames, and invariant breaks raise
  typed errors from `raftwire.errors`.
- **Composable** — codecs, frames, and stores are independent; mix binary
  storage with JSONL tooling freely.

## License

MIT — see [LICENSE](LICENSE).
