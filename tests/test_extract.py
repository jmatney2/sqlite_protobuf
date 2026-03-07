"""Tests for protobuf_extract()."""

import pytest


def extract(db, descriptor_bytes, msg_bytes, field_path, message_type="test.Person"):
    row = db.execute(
        "SELECT protobuf_extract(?, ?, ?, ?)",
        (msg_bytes, descriptor_bytes, message_type, field_path),
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Scalar fields
# ---------------------------------------------------------------------------


def test_string_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(name="Alice")
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "name") == "Alice"


def test_int32_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(age=30)
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "age") == 30


def test_double_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(score=9.5)
    result = extract(db, descriptor_bytes, msg.SerializeToString(), "score")
    assert abs(result - 9.5) < 1e-9


def test_float_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(temperature=36.6)
    result = extract(db, descriptor_bytes, msg.SerializeToString(), "temperature")
    assert abs(result - 36.6) < 1e-4


def test_bool_field_true(db, descriptor_bytes, pb2):
    msg = pb2.Person(active=True)
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "active") == 1


def test_bool_field_false(db, descriptor_bytes, pb2):
    msg = pb2.Person(active=False)
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "active") == 0


def test_bytes_field(db, descriptor_bytes, pb2):
    avatar_data = b"\x01\x02\x03\xff"
    msg = pb2.Person(avatar=avatar_data)
    result = extract(db, descriptor_bytes, msg.SerializeToString(), "avatar")
    assert bytes(result) == avatar_data


def test_int64_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(created_at=1_700_000_000)
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "created_at") == 1_700_000_000


def test_uint64_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(large_id=2**62)
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "large_id") == 2**62


# ---------------------------------------------------------------------------
# Enum field
# ---------------------------------------------------------------------------


def test_enum_field(db, descriptor_bytes, pb2):
    msg = pb2.Person(status=pb2.STATUS_ACTIVE)
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "status") == 1


def test_enum_default(db, descriptor_bytes, pb2):
    msg = pb2.Person()
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "status") == 0


# ---------------------------------------------------------------------------
# Optional field
# ---------------------------------------------------------------------------


def test_optional_present(db, descriptor_bytes, pb2):
    msg = pb2.Person(nickname="Bob")
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "nickname") == "Bob"


def test_optional_absent(db, descriptor_bytes, pb2):
    msg = pb2.Person()
    # Field is absent; proto3 optional returns default value (empty string)
    result = extract(db, descriptor_bytes, msg.SerializeToString(), "nickname")
    assert result == "" or result is None


# ---------------------------------------------------------------------------
# Nested message
# ---------------------------------------------------------------------------


def test_nested_message_as_json(db, descriptor_bytes, pb2):
    import json

    addr = pb2.Address(street="123 Main St", city="Springfield", zip_code=12345)
    msg = pb2.Person(address=addr)
    result = extract(db, descriptor_bytes, msg.SerializeToString(), "address")
    parsed = json.loads(result)
    assert parsed["street"] == "123 Main St"
    assert parsed["city"] == "Springfield"


def test_nested_field_extraction(db, descriptor_bytes, pb2):
    addr = pb2.Address(city="Shelbyville", zip_code=99999)
    msg = pb2.Person(address=addr)
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "address.city") == "Shelbyville"
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "address.zip_code") == 99999


# ---------------------------------------------------------------------------
# Repeated field
# ---------------------------------------------------------------------------


def test_repeated_as_json(db, descriptor_bytes, pb2):
    import json

    msg = pb2.Person(tags=["alpha", "beta", "gamma"])
    result = extract(db, descriptor_bytes, msg.SerializeToString(), "tags")
    assert json.loads(result) == ["alpha", "beta", "gamma"]


def test_repeated_index(db, descriptor_bytes, pb2):
    msg = pb2.Person(tags=["first", "second", "third"])
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "tags[0]") == "first"
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "tags[2]") == "third"


def test_repeated_index_out_of_bounds(db, descriptor_bytes, pb2):
    msg = pb2.Person(tags=["only"])
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "tags[5]") is None


def test_repeated_nested_message_index(db, descriptor_bytes, pb2):
    addr0 = pb2.Address(city="First City")
    addr1 = pb2.Address(city="Second City")
    msg = pb2.Person(previous_addresses=[addr0, addr1])
    assert (
        extract(db, descriptor_bytes, msg.SerializeToString(), "previous_addresses[1].city")
        == "Second City"
    )


# ---------------------------------------------------------------------------
# Map field
# ---------------------------------------------------------------------------


def test_map_field_as_json(db, descriptor_bytes, pb2):
    import json

    msg = pb2.Person(metadata={"views": 42, "likes": 7})
    result = extract(db, descriptor_bytes, msg.SerializeToString(), "metadata")
    parsed = json.loads(result)
    assert parsed["views"] == 42
    assert parsed["likes"] == 7


# ---------------------------------------------------------------------------
# NULL handling
# ---------------------------------------------------------------------------


def test_null_data_returns_null(db, descriptor_bytes):
    row = db.execute(
        "SELECT protobuf_extract(NULL, ?, ?, ?)",
        (descriptor_bytes, "test.Person", "name"),
    ).fetchone()
    assert row[0] is None


def test_missing_field_returns_null(db, descriptor_bytes, pb2):
    msg = pb2.Person(name="Alice")
    assert extract(db, descriptor_bytes, msg.SerializeToString(), "nonexistent_field") is None


# ---------------------------------------------------------------------------
# Multiple rows in a table
# ---------------------------------------------------------------------------


def test_table_query(db, descriptor_bytes, pb2):
    db.execute("CREATE TABLE people (proto BLOB)")
    people = [
        pb2.Person(name="Alice", age=30, score=9.0),
        pb2.Person(name="Bob", age=25, score=7.5),
        pb2.Person(name="Carol", age=35, score=8.0),
    ]
    db.executemany(
        "INSERT INTO people VALUES (?)",
        [(p.SerializeToString(),) for p in people],
    )
    rows = db.execute(
        """
        SELECT
            protobuf_extract(proto, ?, 'test.Person', 'name'),
            protobuf_extract(proto, ?, 'test.Person', 'age')
        FROM people
        ORDER BY protobuf_extract(proto, ?, 'test.Person', 'age')
        """,
        (descriptor_bytes, descriptor_bytes, descriptor_bytes),
    ).fetchall()
    assert [r[0] for r in rows] == ["Bob", "Alice", "Carol"]
    assert [r[1] for r in rows] == [25, 30, 35]


def test_filter_by_field(db, descriptor_bytes, pb2):
    db.execute("CREATE TABLE people (proto BLOB)")
    people = [
        pb2.Person(name="Alice", active=True),
        pb2.Person(name="Bob", active=False),
        pb2.Person(name="Carol", active=True),
    ]
    db.executemany(
        "INSERT INTO people VALUES (?)",
        [(p.SerializeToString(),) for p in people],
    )
    rows = db.execute(
        "SELECT protobuf_extract(proto, ?, 'test.Person', 'name') FROM people "
        "WHERE protobuf_extract(proto, ?, 'test.Person', 'active') = 1",
        (descriptor_bytes, descriptor_bytes),
    ).fetchall()
    names = [r[0] for r in rows]
    assert set(names) == {"Alice", "Carol"}
