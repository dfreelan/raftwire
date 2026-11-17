"""CLI smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raftwire.cli import main
from raftwire.codec import encode_entry
from raftwire.frames import encode_frame
from raftwire.types import Entry


def test_cli_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_cli_encode_decode_entry(tmp_path: Path, capsys):
    out = tmp_path / "e.bin"
    assert main([
        "encode-entry", "--term", "2", "--index", "5", "--data", "hello", "-o", str(out), "-q"
    ]) == 0
    assert out.exists() and out.stat().st_size > 0
    assert main(["decode-entry", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["term"] == 2
    assert payload["index"] == 5


def test_cli_frame_pack_unpack(tmp_path: Path):
    raw = tmp_path / "raw.bin"
    framed = tmp_path / "framed.bin"
    out = tmp_path / "out.bin"
    raw.write_bytes(b"payload-data")
    assert main(["frame", "pack", str(raw), "-o", str(framed)]) == 0
    assert main(["frame", "unpack", str(framed), "-o", str(out)]) == 0
    assert out.read_bytes() == b"payload-data"


def test_cli_crc(tmp_path: Path, capsys):
    f = tmp_path / "x.bin"
    f.write_bytes(b"123456789")
    assert main(["crc", str(f)]) == 0
    assert capsys.readouterr().out.strip() == "e3069283"


def test_cli_crc_verify(tmp_path: Path, capsys):
    from raftwire.crc import append_crc32c

    f = tmp_path / "c.bin"
    f.write_bytes(append_crc32c(b"ok"))
    assert main(["crc", str(f), "--verify"]) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_verify_frames(tmp_path: Path, capsys):
    f = tmp_path / "fr.bin"
    f.write_bytes(encode_frame(b"abc") + encode_frame(b"def"))
    assert main(["verify", str(f)]) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_inspect_jsonl(tmp_path: Path, capsys):
    from raftwire.codec import dumps_jsonl
    from raftwire.types import HardState

    f = tmp_path / "w.jsonl"
    f.write_text(dumps_jsonl([HardState(term=1), Entry(term=1, index=1, data=b"a")]))
    assert main(["inspect", str(f), "--jsonl"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["count"] == 2


def test_cli_inspect_store_dir(tmp_path: Path, capsys):
    from raftwire.logstore import FileLogStore, make_entries
    from raftwire.types import HardState

    d = tmp_path / "store"
    store = FileLogStore(d)
    store.set_hard_state(HardState(term=1, vote=1, commit=2))
    store.append(*make_entries(1, 5))
    assert main(["inspect", str(d)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["entry_count"] == 5


def test_cli_compact(tmp_path: Path, capsys):
    from raftwire.codec import encode_entries
    from raftwire.logstore import make_entries

    src = tmp_path / "entries.bin"
    src.write_bytes(encode_entries(make_entries(1, 20)))
    out = tmp_path / "out.jsonl"
    assert main(["compact", str(src), "--until", "10", "-o", str(out), "--jsonl"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["snapshot_index"] == 10
    assert out.exists()


def test_cli_help_exit():
    assert main([]) == 2


def test_cli_encode_from_data_file(tmp_path: Path):
    data = tmp_path / "d.bin"
    data.write_bytes(b"\x00\x01\x02")
    out = tmp_path / "e.bin"
    assert main([
        "encode-entry", "--term", "1", "--index", "1",
        "--data-file", str(data), "-o", str(out), "-q",
    ]) == 0
    from raftwire.codec import decode_entry

    assert decode_entry(out.read_bytes()).data == b"\x00\x01\x02"
