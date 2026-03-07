"""Tests for protobuf_to_json() and protobuf_valid()."""

import json

import pytest


# ---------------------------------------------------------------------------
# protobuf_to_json
# ---------------------------------------------------------------------------


def to_json(db, descriptor_bytes, msg_bytes, message_type="test.Person"):
    row = db.execute(
        "SELECT protobuf_to_json(?, ?, ?)",
        (msg_bytes, descriptor_bytes, message_type),
    ).fetchone()
    return json.loads(row[0]) if row[0] is not None else None


def test_json_scalar_fields(db, descriptor_bytes, pb2):
    msg = pb2.Person(name="Alice", age=30, score=9.5, active=True)
    result = to_json(db, descriptor_bytes, msg.SerializeToString())
    assert result["name"] == "Alice"
    assert result["age"] == 30
    assert abs(result["score"] - 9.5) < 1e-9
    assert result["active"] is True


def test_json_nested_message(db, descriptor_bytes, pb2):
    addr = pb2.Address(street="1 Main St", city="Springfield")
    msg = pb2.Person(name="Alice", address=addr)
    result = to_json(db, descriptor_bytes, msg.SerializeToString())
    assert result["address"]["street"] == "1 Main St"
    assert result["address"]["city"] == "Springfield"


def test_json_repeated_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(tags=["x", "y", "z"])
    result = to_json(db, descriptor_bytes, msg.SerializeToString())
    assert result["tags"] == ["x", "y", "z"]


def test_json_map_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(metadata={"hits": 100, "misses": 5})
    result = to_json(db, descriptor_bytes, msg.SerializeToString())
    assert result["metadata"]["hits"] == 100
    assert result["metadata"]["misses"] == 5


def test_json_bytes_field_is_base64(db, descriptor_bytes, pb2):
    import base64

    raw = b"\xde\xad\xbe\xef"
    msg = pb2.Person(avatar=raw)
    result = to_json(db, descriptor_bytes, msg.SerializeToString())
    # Proto3 JSON encodes bytes as base64
    decoded = base64.b64decode(result["avatar"])
    assert decoded == raw


def test_json_null_data(db, descriptor_bytes):
    row = db.execute(
        "SELECT protobuf_to_json(NULL, ?, 'test.Person')",
        (descriptor_bytes,),
    ).fetchone()
    assert row[0] is None


def test_json_is_sqlite_json(db, descriptor_bytes, pb2):
    """The JSON result should be queryable with SQLite's json_extract()."""
    msg = pb2.Person(name="Alice", age=42)
    blob = msg.SerializeToString()
    row = db.execute(
        "SELECT json_extract(protobuf_to_json(?, ?, 'test.Person'), '$.name')",
        (blob, descriptor_bytes),
    ).fetchone()
    assert row[0] == "Alice"


def test_json_address_message(db, descriptor_bytes, pb2):
    addr = pb2.Address(street="456 Oak Ave", city="Shelbyville", zip_code=67890, country="US")
    result = to_json(db, descriptor_bytes, addr.SerializeToString(), "test.Address")
    assert result["street"] == "456 Oak Ave"
    assert result["zipCode"] == 67890  # proto3 JSON uses camelCase


# ---------------------------------------------------------------------------
# protobuf_valid
# ---------------------------------------------------------------------------


def valid(db, descriptor_bytes, data, message_type="test.Person"):
    row = db.execute(
        "SELECT protobuf_valid(?, ?, ?)",
        (data, descriptor_bytes, message_type),
    ).fetchone()
    return row[0]


def test_valid_message(db, descriptor_bytes, pb2):
    msg = pb2.Person(name="Alice", age=30)
    assert valid(db, descriptor_bytes, msg.SerializeToString()) == 1


def test_valid_empty_message(db, descriptor_bytes, pb2):
    # An empty bytes string is a valid proto3 message (all fields take defaults).
    assert valid(db, descriptor_bytes, b"") == 1


def test_invalid_message(db, descriptor_bytes):
    assert valid(db, descriptor_bytes, b"\xff\xff\xff") == 0


def test_valid_null_returns_null(db, descriptor_bytes):
    row = db.execute(
        "SELECT protobuf_valid(NULL, ?, 'test.Person')",
        (descriptor_bytes,),
    ).fetchone()
    assert row[0] is None


def test_valid_unknown_type(db, descriptor_bytes, pb2):
    msg = pb2.Person(name="Alice")
    with pytest.raises(Exception):
        valid(db, descriptor_bytes, msg.SerializeToString(), "test.DoesNotExist")
