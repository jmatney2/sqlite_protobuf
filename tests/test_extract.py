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
    # proto3 optional field not set → NULL (not the default empty string)
    result = extract(db, descriptor_bytes, msg.SerializeToString(), "nickname")
    assert result is None


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


# ---------------------------------------------------------------------------
# oneof field presence
# ---------------------------------------------------------------------------


def make_record_a(pb2, label, value):
    return pb2.Record(branch_a=pb2.BranchA(label=label, value=value))


def make_record_b(pb2, label, category):
    return pb2.Record(branch_b=pb2.BranchB(label=label, category=category))


def test_oneof_active_branch_returns_value(db, descriptor_bytes, pb2):
    rec = make_record_a(pb2, "hello", 42)
    result = extract(db, descriptor_bytes, rec.SerializeToString(), "branch_a.label", "test.Record")
    assert result == "hello"


def test_oneof_inactive_branch_returns_null(db, descriptor_bytes, pb2):
    # branch_b is set — branch_a fields must be NULL, not the proto default ""
    rec = make_record_b(pb2, "world", "tech")
    result = extract(db, descriptor_bytes, rec.SerializeToString(), "branch_a.label", "test.Record")
    assert result is None


def test_oneof_coalesce_picks_active_label(db, descriptor_bytes, pb2):
    # With branch_b set, COALESCE should skip NULL branch_a and return branch_b label.
    rec = make_record_b(pb2, "from_b", "news")
    row = db.execute(
        """
        SELECT COALESCE(
            protobuf_extract(?, ?, 'test.Record', 'branch_a.label'),
            protobuf_extract(?, ?, 'test.Record', 'branch_b.label')
        )
        """,
        (rec.SerializeToString(), descriptor_bytes,
         rec.SerializeToString(), descriptor_bytes),
    ).fetchone()
    assert row[0] == "from_b"


def test_oneof_coalesce_with_table(db, descriptor_bytes, pb2):
    db.execute("CREATE TABLE records (proto BLOB)")
    rows = [
        make_record_a(pb2, "alpha", 1),
        make_record_b(pb2, "beta", "x"),
        make_record_a(pb2, "gamma", 3),
    ]
    db.executemany("INSERT INTO records VALUES (?)", [(r.SerializeToString(),) for r in rows])
    results = db.execute(
        """
        SELECT COALESCE(
            protobuf_extract(proto, ?, 'test.Record', 'branch_a.label'),
            protobuf_extract(proto, ?, 'test.Record', 'branch_b.label')
        ) FROM records
        """,
        (descriptor_bytes, descriptor_bytes),
    ).fetchall()
    assert [r[0] for r in results] == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# protobuf_which_oneof
# ---------------------------------------------------------------------------


def which_oneof(db, descriptor_bytes, msg_bytes, oneof_name, message_type="test.Record"):
    row = db.execute(
        "SELECT protobuf_which_oneof(?, ?, ?, ?)",
        (msg_bytes, descriptor_bytes, message_type, oneof_name),
    ).fetchone()
    return row[0]


def test_which_oneof_branch_a(db, descriptor_bytes, pb2):
    rec = make_record_a(pb2, "x", 0)
    assert which_oneof(db, descriptor_bytes, rec.SerializeToString(), "source") == "branch_a"


def test_which_oneof_branch_b(db, descriptor_bytes, pb2):
    rec = make_record_b(pb2, "y", "cat")
    assert which_oneof(db, descriptor_bytes, rec.SerializeToString(), "source") == "branch_b"


def test_which_oneof_empty_returns_null(db, descriptor_bytes, pb2):
    rec = pb2.Record()
    assert which_oneof(db, descriptor_bytes, rec.SerializeToString(), "source") is None


def test_which_oneof_null_data_returns_null(db, descriptor_bytes):
    row = db.execute(
        "SELECT protobuf_which_oneof(NULL, ?, 'test.Record', 'source')",
        (descriptor_bytes,),
    ).fetchone()
    assert row[0] is None


def test_which_oneof_unknown_name_raises(db, descriptor_bytes, pb2):
    import pytest

    rec = make_record_a(pb2, "x", 0)
    with pytest.raises(Exception, match="no_such_oneof"):
        db.execute(
            "SELECT protobuf_which_oneof(?, ?, 'test.Record', 'no_such_oneof')",
            (rec.SerializeToString(), descriptor_bytes),
        ).fetchone()


# ---------------------------------------------------------------------------
# Generated columns + expression indexes (SQLite DDL)
# ---------------------------------------------------------------------------


def test_generated_virtual_column(db, descriptor_bytes, pb2):
    """A VIRTUAL generated column extracts a field without storing extra data."""
    hex_desc = descriptor_bytes.hex()
    db.execute(
        f"""
        CREATE TABLE people (
            proto BLOB,
            name TEXT GENERATED ALWAYS AS (
                protobuf_extract(proto, X'{hex_desc}', 'test.Person', 'name')
            ) VIRTUAL
        )
        """
    )
    people = [pb2.Person(name="Alice", age=30), pb2.Person(name="Bob", age=25)]
    db.executemany("INSERT INTO people (proto) VALUES (?)", [(p.SerializeToString(),) for p in people])
    rows = db.execute("SELECT name FROM people ORDER BY name").fetchall()
    assert [r[0] for r in rows] == ["Alice", "Bob"]


def test_generated_stored_column_with_index(db, descriptor_bytes, pb2):
    """A STORED generated column can be indexed and queried efficiently."""
    hex_desc = descriptor_bytes.hex()
    db.execute(
        f"""
        CREATE TABLE people (
            proto BLOB,
            age INTEGER GENERATED ALWAYS AS (
                protobuf_extract(proto, X'{hex_desc}', 'test.Person', 'age')
            ) STORED
        )
        """
    )
    db.execute("CREATE INDEX people_age_idx ON people(age)")
    people = [
        pb2.Person(name="Alice", age=30),
        pb2.Person(name="Bob", age=25),
        pb2.Person(name="Carol", age=35),
    ]
    db.executemany("INSERT INTO people (proto) VALUES (?)", [(p.SerializeToString(),) for p in people])
    rows = db.execute("SELECT age FROM people WHERE age > 28 ORDER BY age").fetchall()
    assert [r[0] for r in rows] == [30, 35]


def test_expression_index(db, descriptor_bytes, pb2):
    """An expression index on protobuf_extract can be created."""
    hex_desc = descriptor_bytes.hex()
    db.execute("CREATE TABLE people (proto BLOB)")
    db.execute(
        f"""
        CREATE INDEX people_name_idx ON people(
            protobuf_extract(proto, X'{hex_desc}', 'test.Person', 'name')
        )
        """
    )
    people = [pb2.Person(name="Alice"), pb2.Person(name="Bob")]
    db.executemany("INSERT INTO people VALUES (?)", [(p.SerializeToString(),) for p in people])
    # Query using the same literal expression so SQLite can use the index.
    rows = db.execute(
        f"SELECT protobuf_extract(proto, X'{hex_desc}', 'test.Person', 'name') "
        "FROM people ORDER BY 1",
    ).fetchall()
    assert [r[0] for r in rows] == ["Alice", "Bob"]
