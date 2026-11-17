"""Command-line interface for raftwire."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from raftwire import __version__
from raftwire.codec import (
    decode_any,
    decode_entry,
    dumps_jsonl,
    encode_entry,
    loads_jsonl,
    record_to_json,
)
from raftwire.compact import discard_until, estimate_storage
from raftwire.crc import crc32c_hex, verify_crc32c
from raftwire.errors import CliError, RaftwireError
from raftwire.frames import FrameDecoder, decode_frames, encode_frame
from raftwire.logstore import FileLogStore, MemoryLogStore
from raftwire.replay import fingerprint_store
from raftwire.types import Entry


def _read_bytes(path: str) -> bytes:
    return sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()


def _write_bytes(path: str, data: bytes) -> None:
    if path == "-":
        sys.stdout.buffer.write(data)
    else:
        Path(path).write_bytes(data)


def _write_text(path: str, text: str) -> None:
    if path == "-":
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")


def cmd_version(_: argparse.Namespace) -> int:
    print(__version__)
    return 0


def cmd_encode_entry(args: argparse.Namespace) -> int:
    data = args.data.encode("utf-8")
    if args.data_file:
        data = _read_bytes(args.data_file)
    blob = encode_entry(Entry(term=args.term, index=args.index, data=data))
    out = args.output or "-"
    _write_bytes(out, blob)
    if out != "-" and not args.quiet:
        print(f"wrote {len(blob)} bytes to {out}", file=sys.stderr)
    return 0


def cmd_decode_entry(args: argparse.Namespace) -> int:
    entry = decode_entry(_read_bytes(args.input))
    _write_text(args.output or "-", json.dumps(record_to_json(entry), indent=2, sort_keys=True) + "\n")
    return 0


def cmd_frame_pack(args: argparse.Namespace) -> int:
    _write_bytes(args.output or "-", encode_frame(_read_bytes(args.input), compress=args.compress))
    return 0


def cmd_frame_unpack(args: argparse.Namespace) -> int:
    payloads = decode_frames(_read_bytes(args.input))
    if len(payloads) != 1 and not args.all:
        raise CliError(f"expected 1 frame, found {len(payloads)} (use --all)")
    out = args.output or "-"
    if args.all and args.concat:
        _write_bytes(out, b"".join(payloads))
    elif args.all:
        for i, p in enumerate(payloads):
            _write_bytes(out if out == "-" else f"{out}.{i}", p)
    else:
        _write_bytes(out, payloads[0])
    return 0


def cmd_crc(args: argparse.Namespace) -> int:
    data = _read_bytes(args.input)
    if args.verify:
        try:
            payload = verify_crc32c(data, context=args.input)
        except RaftwireError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        print(f"OK ({len(payload)} bytes, crc={crc32c_hex(payload)})")
        return 0
    print(crc32c_hex(data))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.input)
    if path.is_dir():
        store = FileLogStore(path)
        info = estimate_storage(store)
        info["hard_state"] = store.get_hard_state().to_dict()
        snap = store.get_snapshot()
        info["snapshot"] = {"index": snap.metadata.index, "term": snap.metadata.term, "bytes": snap.size()}
        info["fingerprint"] = f"0x{fingerprint_store(store):08x}"
        print(json.dumps(info, indent=2, sort_keys=True))
        return 0
    raw = path.read_bytes()
    if args.jsonl or raw[:1] in (b"{", b"#") or (raw and raw[:1].isalpha()):
        records = loads_jsonl(raw.decode("utf-8"))
        summary = {"format": "jsonl", "count": len(records), "types": {}}
        for r in records:
            name = type(r).__name__
            summary["types"][name] = summary["types"].get(name, 0) + 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        if args.verbose:
            for r in records:
                print(json.dumps(record_to_json(r), sort_keys=True))
        return 0
    try:
        payloads = decode_frames(raw)
        print(json.dumps({"format": "frames", "count": len(payloads)}, indent=2))
        if args.verbose:
            for p in payloads:
                try:
                    print(json.dumps(record_to_json(decode_any(p)), sort_keys=True))
                except RaftwireError:
                    print(json.dumps({"raw_len": len(p), "crc": crc32c_hex(p)}))
        return 0
    except RaftwireError:
        pass
    try:
        print(json.dumps(record_to_json(decode_any(raw)), indent=2, sort_keys=True))
        return 0
    except RaftwireError as exc:
        raise CliError(f"unrecognized file format: {exc}") from exc


def cmd_verify(args: argparse.Namespace) -> int:
    raw = _read_bytes(args.input)
    try:
        dec = FrameDecoder()
        payloads = dec.feed_all(raw)
        if dec.buffered:
            print(f"FAIL: {dec.buffered} trailing bytes", file=sys.stderr)
            return 1
        print(f"OK: {len(payloads)} frame(s)")
        return 0
    except RaftwireError as exc:
        try:
            verify_crc32c(raw, context=args.input)
            print("OK: single CRC record")
            return 0
        except RaftwireError as exc2:
            print(f"FAIL: {exc}; also {exc2}", file=sys.stderr)
            return 1


def cmd_compact(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if src.is_dir():
        store = FileLogStore(src)
    else:
        from raftwire.codec import decode_entries_stream
        store = MemoryLogStore()
        ents = decode_entries_stream(src.read_bytes())
        if ents:
            store.append(*ents)
    stats = discard_until(store, args.until)
    if args.output:
        out_path = Path(args.output)
        if out_path.suffix == ".jsonl" or args.jsonl:
            records: list[object] = []
            snap = store.get_snapshot()
            if not snap.is_empty():
                records.append(snap)
            records.extend(store.all_entries())
            _write_text(args.output, dumps_jsonl(records))
        elif out_path.is_dir() or args.as_dir:
            out_path.mkdir(parents=True, exist_ok=True)
            dst = FileLogStore(out_path)
            dst.set_hard_state(store.get_hard_state())
            snap = store.get_snapshot()
            if not snap.is_empty():
                dst.set_snapshot(snap)
            ents = store.all_entries()
            if ents:
                dst.append(*ents)
        else:
            from raftwire.codec import encode_entry, encode_snapshot
            parts = []
            snap = store.get_snapshot()
            if not snap.is_empty():
                parts.append(encode_snapshot(snap))
            parts.extend(encode_entry(e) for e in store.all_entries())
            _write_bytes(args.output, b"".join(parts))
    print(json.dumps({
        "discarded": stats.discarded,
        "snapshot_index": stats.snapshot_index,
        "first_index_after": stats.first_index_after,
        "bytes_after": stats.bytes_after,
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="raftwire", description="Raft / WAL binary and text protocol toolkit")
    p.add_argument("--version", action="store_true", help="print version and exit")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("version", help="print package version")
    sp.set_defaults(func=cmd_version)

    sp = sub.add_parser("encode-entry", help="encode a log entry to binary")
    sp.add_argument("--term", type=int, required=True)
    sp.add_argument("--index", type=int, required=True)
    sp.add_argument("--data", default="")
    sp.add_argument("--data-file")
    sp.add_argument("-o", "--output", default="-")
    sp.add_argument("-q", "--quiet", action="store_true")
    sp.set_defaults(func=cmd_encode_entry)

    sp = sub.add_parser("decode-entry", help="decode a binary log entry to JSON")
    sp.add_argument("input")
    sp.add_argument("-o", "--output", default="-")
    sp.set_defaults(func=cmd_decode_entry)

    fr = sub.add_parser("frame", help="frame pack/unpack")
    fr_sub = fr.add_subparsers(dest="frame_cmd")
    sp = fr_sub.add_parser("pack", help="wrap payload in a CRC frame")
    sp.add_argument("input")
    sp.add_argument("-o", "--output", default="-")
    sp.add_argument("--compress", action="store_true")
    sp.set_defaults(func=cmd_frame_pack)
    sp = fr_sub.add_parser("unpack", help="unwrap CRC frame(s)")
    sp.add_argument("input")
    sp.add_argument("-o", "--output", default="-")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--concat", action="store_true")
    sp.set_defaults(func=cmd_frame_unpack)

    sp = sub.add_parser("crc", help="compute or verify CRC32C")
    sp.add_argument("input")
    sp.add_argument("--verify", action="store_true")
    sp.set_defaults(func=cmd_crc)

    sp = sub.add_parser("inspect", help="summarize a WAL file or store directory")
    sp.add_argument("input")
    sp.add_argument("--jsonl", action="store_true")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("verify", help="verify CRC integrity of frames/records")
    sp.add_argument("input")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("compact", help="discard log prefix through an index")
    sp.add_argument("input")
    sp.add_argument("--until", type=int, required=True)
    sp.add_argument("-o", "--output")
    sp.add_argument("--jsonl", action="store_true")
    sp.add_argument("--as-dir", action="store_true")
    sp.set_defaults(func=cmd_compact)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if getattr(args, "version", False) and not getattr(args, "command", None):
        print(__version__)
        return 0
    func = getattr(args, "func", None)
    if func is None:
        if getattr(args, "command", None) == "frame" and not getattr(args, "frame_cmd", None):
            parser.parse_args(["frame", "--help"])
            return 2
        parser.print_help()
        return 2
    try:
        return int(func(args))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RaftwireError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"io error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
