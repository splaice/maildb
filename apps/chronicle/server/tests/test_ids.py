# tests/test_ids.py
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from chronicle_server.ids import decode_source_id, encode_source_id, msg_key_to_uuid


def test_msg_roundtrip_uuid() -> None:
    u = uuid4()
    sid = encode_source_id("msg", u)
    assert sid.startswith("msg_")
    kind, key = decode_source_id(sid)
    assert kind == "msg"
    assert isinstance(key, int)
    assert msg_key_to_uuid(key) == u


def test_msg_roundtrip_int() -> None:
    u = UUID("12345678-1234-5678-1234-567812345678")
    sid = encode_source_id("msg", u.int)
    kind, key = decode_source_id(sid)
    assert kind == "msg"
    assert key == u.int
    assert msg_key_to_uuid(key) == u  # type: ignore[arg-type]


def test_att_roundtrip() -> None:
    sid = encode_source_id("att", 42)
    assert sid == "att_42"
    kind, key = decode_source_id(sid)
    assert kind == "att"
    assert key == 42


def test_thr_roundtrip_plain() -> None:
    tid = "thread-abc-001"
    sid = encode_source_id("thr", tid)
    assert sid.startswith("thr_")
    kind, key = decode_source_id(sid)
    assert kind == "thr"
    assert key == tid


def test_thr_roundtrip_unicode() -> None:
    tid = "线程/話題-üñîçødé"
    sid = encode_source_id("thr", tid)
    kind, key = decode_source_id(sid)
    assert kind == "thr"
    assert key == tid


def test_thr_roundtrip_slash_plus() -> None:
    tid = "refs/+/foo+bar/baz=="
    sid = encode_source_id("thr", tid)
    kind, key = decode_source_id(sid)
    assert kind == "thr"
    assert key == tid


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "msg",
        "msg_",
        "msg_abc",
        "msg_-1",
        "att_",
        "att_x",
        "att_-3",
        "thr",
        "thr_",
        "thr_!!!",
        "foo_1",
        "MSG_1",
        "nope",
    ],
)
def test_malformed_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        decode_source_id(bad)


def test_unknown_kind_encode_raises() -> None:
    with pytest.raises(ValueError):
        encode_source_id("nope", 1)
