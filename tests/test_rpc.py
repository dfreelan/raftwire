"""RPC message codec and helper tests."""

from __future__ import annotations

import pytest

from raftwire.errors import ChecksumError, RpcError
from raftwire.rpc import (
    AppendEntries,
    AppendEntriesReply,
    RequestVote,
    RequestVoteReply,
    decode_append_entries,
    decode_rpc,
    dumps_rpc_jsonl,
    encode_append_entries,
    encode_append_entries_reply,
    encode_request_vote,
    encode_request_vote_reply,
    encode_rpc,
    is_heartbeat,
    loads_rpc_jsonl,
    rpc_from_json,
    rpc_to_json,
    vote_is_up_to_date,
)
from raftwire.types import Entry


def test_append_entries_roundtrip():
    msg = AppendEntries(
        term=4,
        leader_id=1,
        prev_log_index=10,
        prev_log_term=3,
        entries=(
            Entry(term=4, index=11, data=b"a"),
            Entry(term=4, index=12, data=b"b"),
        ),
        leader_commit=9,
    )
    blob = encode_append_entries(msg)
    assert decode_append_entries(blob) == msg
    assert decode_rpc(blob) == msg
    assert not is_heartbeat(msg)


def test_append_entries_heartbeat():
    msg = AppendEntries(term=1, leader_id=1, prev_log_index=0, prev_log_term=0)
    assert is_heartbeat(msg)
    assert decode_rpc(encode_rpc(msg)) == msg


def test_append_entries_reply_roundtrip():
    msg = AppendEntriesReply(
        term=2, success=True, conflict_index=0, conflict_term=0, match_index=15
    )
    assert decode_rpc(encode_append_entries_reply(msg)) == msg
    fail = AppendEntriesReply(term=2, success=False, conflict_index=8, conflict_term=1)
    assert decode_rpc(encode_rpc(fail)) == fail


def test_request_vote_roundtrip():
    msg = RequestVote(term=5, candidate_id=3, last_log_index=20, last_log_term=4)
    assert decode_rpc(encode_request_vote(msg)) == msg
    reply = RequestVoteReply(term=5, vote_granted=True)
    assert decode_rpc(encode_request_vote_reply(reply)) == reply


def test_rpc_crc_corruption():
    msg = RequestVote(term=1, candidate_id=1, last_log_index=0, last_log_term=0)
    blob = bytearray(encode_request_vote(msg))
    blob[6] ^= 0x01
    with pytest.raises(ChecksumError):
        decode_rpc(bytes(blob))


def test_rpc_jsonl():
    messages = [
        AppendEntries(term=1, leader_id=1, prev_log_index=0, prev_log_term=0, entries=()),
        RequestVote(term=2, candidate_id=2, last_log_index=1, last_log_term=1),
        RequestVoteReply(term=2, vote_granted=False),
    ]
    text = dumps_rpc_jsonl(messages)
    back = loads_rpc_jsonl(text)
    assert back == messages
    assert rpc_from_json(rpc_to_json(messages[1])) == messages[1]


def test_vote_up_to_date():
    assert vote_is_up_to_date(3, 10, 2, 100)
    assert not vote_is_up_to_date(2, 100, 3, 1)
    assert vote_is_up_to_date(3, 10, 3, 10)
    assert vote_is_up_to_date(3, 11, 3, 10)
    assert not vote_is_up_to_date(3, 9, 3, 10)


def test_reject_negative():
    with pytest.raises(ValueError):
        AppendEntries(term=-1, leader_id=1, prev_log_index=0, prev_log_term=0)


def test_unknown_rpc_type():
    with pytest.raises(RpcError):
        encode_rpc(object())
    with pytest.raises(RpcError):
        decode_rpc(b"\x00\x00xxxx")
