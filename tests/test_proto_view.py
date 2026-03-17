"""
Tests for ProtoView: column annotations, fixed/dynamic filters, oneof sorting,
and config serialization/deserialization.

These tests use a real in-memory SQLite database with the extension loaded
(via the ``db`` fixture) and raw SQL helpers to avoid needing a Django ORM
stack.  The ProtoView logic is exercised by inspecting the annotations dict
and filter kwargs it produces, and by running the resulting SQL through
SQLite.
"""

import json

import pytest


# ---------------------------------------------------------------------------
# Helpers: build test messages and a simple in-memory table
# ---------------------------------------------------------------------------


def build_table(db, descriptor_bytes, pb2):
    """Create a 'records' table with four rows mixing BranchA and BranchB."""
    db.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, proto BLOB)")
    rows = [
        pb2.Record(branch_a=pb2.BranchA(label="alpha", value=10)),
        pb2.Record(branch_b=pb2.BranchB(label="beta",  category="news")),
        pb2.Record(branch_a=pb2.BranchA(label="gamma", value=30)),
        pb2.Record(branch_b=pb2.BranchB(label="delta", category="tech")),
    ]
    db.executemany(
        "INSERT INTO records (proto) VALUES (?)",
        [(r.SerializeToString(),) for r in rows],
    )


def annotate_query(db, descriptor_bytes, columns: list[tuple[str, str, str]]) -> list[dict]:
    """
    Run a query that extracts named columns from the records table.

    columns: list of (annotation_name, function, path_or_oneof)
    function: "extract" | "which_oneof"
    """
    hex_desc = descriptor_bytes.hex()
    parts = []
    for name, func, arg in columns:
        if func == "extract":
            parts.append(
                f"protobuf_extract(proto, X'{hex_desc}', 'test.Record', '{arg}') AS {name}"
            )
        elif func == "which_oneof":
            parts.append(
                f"protobuf_which_oneof(proto, X'{hex_desc}', 'test.Record', '{arg}') AS {name}"
            )
    sql = "SELECT id, " + ", ".join(parts) + " FROM records ORDER BY id"
    rows = db.execute(sql).fetchall()
    col_names = ["id"] + [c[0] for c in columns]
    return [dict(zip(col_names, row)) for row in rows]


# ---------------------------------------------------------------------------
# ProtoView unit-level tests (no Django ORM — just inspect the dicts)
# ---------------------------------------------------------------------------


def _make_view(descriptor_bytes):
    """Build a minimal ProtoView instance for unit testing."""
    from django_sqlite_protobuf.proto_view import (
        DynamicFilter, FieldFilter, OneofColumn, OneofFilter, ProtoColumn,
        ProtoView,
    )
    from django.db.models import IntegerField

    class RecordView(ProtoView):
        descriptor = descriptor_bytes
        message_type = "test.Record"
        blob_field = "proto"

        columns = [
            OneofColumn("source_type", "source"),
            ProtoColumn("branch_a_label", "branch_a.label"),
            ProtoColumn("branch_b_label", "branch_b.label"),
            ProtoColumn("branch_a_value", "branch_a.value", output_field=IntegerField()),
        ]
        fixed_filters = [OneofFilter("source", "branch_a")]
        dynamic_filters = [
            DynamicFilter("label", "branch_a.label", lookup="icontains"),
        ]

    return RecordView()


def test_get_annotations_includes_columns(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    ann = view.get_annotations()
    assert "source_type" in ann
    assert "branch_a_label" in ann
    assert "branch_b_label" in ann
    assert "branch_a_value" in ann


def test_get_annotations_includes_fixed_filter_annotation(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    ann = view.get_annotations()
    # OneofFilter on "source" → _oneof_source annotation
    assert "_oneof_source" in ann


def test_get_annotations_dynamic_filter_only_when_param_present(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    ann_no_param = view.get_annotations({})
    assert "_df_label" not in ann_no_param

    ann_with_param = view.get_annotations({"label": "alpha"})
    assert "_df_label" in ann_with_param


def test_get_filter_kwargs_fixed_filter(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    kwargs = view.get_filter_kwargs()
    assert kwargs.get("_oneof_source") == "branch_a"


def test_get_filter_kwargs_dynamic_filter(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    kwargs = view.get_filter_kwargs({"label": "alpha"})
    assert "_df_label__icontains" in kwargs
    assert kwargs["_df_label__icontains"] == "alpha"


def test_get_filter_kwargs_skips_empty_dynamic(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    kwargs = view.get_filter_kwargs({"label": ""})
    assert "_df_label__icontains" not in kwargs


def test_column_names(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    assert view.column_names() == ["source_type", "branch_a_label", "branch_b_label", "branch_a_value"]


def test_sortable_columns(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    # All columns are sortable by default
    assert set(view.sortable_columns()) == {"source_type", "branch_a_label", "branch_b_label", "branch_a_value"}


def test_filter_form_fields(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    fields = view.filter_form_fields()
    assert len(fields) == 1
    assert fields[0]["name"] == "label"
    assert fields[0]["lookup"] == "icontains"


# ---------------------------------------------------------------------------
# SQL integration: oneof column + sorting
# ---------------------------------------------------------------------------


def test_oneof_column_shows_active_branch(db, descriptor_bytes, pb2):
    build_table(db, descriptor_bytes, pb2)
    rows = annotate_query(db, descriptor_bytes, [
        ("source_type", "which_oneof", "source"),
    ])
    assert [r["source_type"] for r in rows] == [
        "branch_a", "branch_b", "branch_a", "branch_b",
    ]


def test_sort_by_oneof_branch(db, descriptor_bytes, pb2):
    """Ordering by the oneof annotation groups records by their active branch."""
    build_table(db, descriptor_bytes, pb2)
    hex_desc = descriptor_bytes.hex()
    rows = db.execute(
        f"""
        SELECT
            id,
            protobuf_which_oneof(proto, X'{hex_desc}', 'test.Record', 'source') AS source_type
        FROM records
        ORDER BY source_type, id
        """
    ).fetchall()
    source_types = [r[1] for r in rows]
    # All branch_a rows before branch_b (alphabetical), then by id within each group
    assert source_types == ["branch_a", "branch_a", "branch_b", "branch_b"]
    ids_a = [r[0] for r in rows if r[1] == "branch_a"]
    assert ids_a == [1, 3]


def test_inactive_oneof_branch_is_null(db, descriptor_bytes, pb2):
    """branch_b records should yield NULL for branch_a fields (not the default "")."""
    build_table(db, descriptor_bytes, pb2)
    rows = annotate_query(db, descriptor_bytes, [
        ("branch_a_label", "extract", "branch_a.label"),
        ("branch_b_label", "extract", "branch_b.label"),
    ])
    # Row 2 has branch_b set: branch_a fields must be NULL
    assert rows[1]["branch_a_label"] is None
    assert rows[1]["branch_b_label"] == "beta"
    # Row 1 has branch_a set: branch_b fields must be NULL
    assert rows[0]["branch_a_label"] == "alpha"
    assert rows[0]["branch_b_label"] is None


def test_oneof_filter_scopes_results(db, descriptor_bytes, pb2):
    """A OneofFilter should restrict to only the matching branch."""
    build_table(db, descriptor_bytes, pb2)
    hex_desc = descriptor_bytes.hex()
    rows = db.execute(
        f"""
        SELECT id FROM records
        WHERE protobuf_which_oneof(proto, X'{hex_desc}', 'test.Record', 'source') = 'branch_a'
        ORDER BY id
        """
    ).fetchall()
    assert [r[0] for r in rows] == [1, 3]


def test_dynamic_filter_icontains(db, descriptor_bytes, pb2):
    """A DynamicFilter with icontains should filter by substring match."""
    build_table(db, descriptor_bytes, pb2)
    hex_desc = descriptor_bytes.hex()
    rows = db.execute(
        f"""
        SELECT id FROM records
        WHERE LOWER(protobuf_extract(proto, X'{hex_desc}', 'test.Record', 'branch_a.label'))
              LIKE '%amm%'
        """
    ).fetchall()
    assert [r[0] for r in rows] == [3]  # "gamma" contains "amm"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_serialize_config_round_trips(descriptor_bytes):
    view = _make_view(descriptor_bytes)
    config = view.serialize_config()

    assert len(config["columns"]) == 4
    assert len(config["dynamic_filters"]) == 1

    # Verify it's JSON-serializable
    json_str = json.dumps(config)
    config2 = json.loads(json_str)

    from django_sqlite_protobuf.proto_view import ProtoView

    view2 = type(view).from_config(config2)

    assert view2.column_names() == view.column_names()
    assert view2.dynamic_filters[0].name == "label"
    assert view2.dynamic_filters[0].lookup == "icontains"


def test_from_config_preserves_fixed_filters(descriptor_bytes):
    """Fixed filters defined in the subclass are not touched by from_config."""
    view = _make_view(descriptor_bytes)
    config = view.serialize_config()
    config["dynamic_filters"] = []  # user removed all dynamic filters

    view2 = type(view).from_config(config)
    # Fixed filter still present
    kwargs = view2.get_filter_kwargs()
    assert kwargs.get("_oneof_source") == "branch_a"


def test_from_config_with_user_column_subset(descriptor_bytes):
    """A user can reduce the column set via a saved config."""
    view = _make_view(descriptor_bytes)
    config = {
        "columns": [
            {"type": "oneof", "name": "source_type", "oneof_name": "source"},
            {"type": "proto", "name": "branch_a_label", "path": "branch_a.label"},
        ],
        "dynamic_filters": [],
    }
    view2 = type(view).from_config(config)
    assert view2.column_names() == ["source_type", "branch_a_label"]
    # Fixed filter still scopes to branch_a
    assert view2.get_filter_kwargs()["_oneof_source"] == "branch_a"


def test_from_config_with_user_added_dynamic_filter(descriptor_bytes):
    """A user can add a dynamic filter not in the original view definition."""
    view = _make_view(descriptor_bytes)
    config = view.serialize_config()
    config["dynamic_filters"].append(
        {"name": "value", "path": "branch_a.value", "label": "Value", "lookup": "gte"}
    )
    view2 = type(view).from_config(config)
    kwargs = view2.get_filter_kwargs({"value": "20"})
    assert "_df_value__gte" in kwargs
    assert kwargs["_df_value__gte"] == "20"
