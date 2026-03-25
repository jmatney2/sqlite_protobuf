"""
ProtoView definition for RecordEntry.

Columns and groups are auto-generated from the protobuf descriptor so that
adding or renaming fields in test.proto (followed by a descriptor migration)
is immediately reflected here without manual updates.

AVAILABLE_COLUMNS  — flat registry (OneofColumn + ProtoColumn + CoalesceColumn).
COLUMN_BY_NAME     — O(1) lookup by annotation name.
COLUMN_GROUPS      — grouped structure used by the picker UI.
DEFAULT_COLUMN_NAMES — columns shown on first visit.
"""

from django_sqlite_protobuf.descriptors import DescriptorRef
from django_sqlite_protobuf.proto_view import (
    ModelDynamicFilter,
    ProtoView,
)
from django_sqlite_protobuf.utils import columns_from_descriptor

DESCRIPTOR = DescriptorRef("test")
MESSAGE = "test.Record"


# ── Auto-generated column registry ───────────────────────────────────────────
#
# columns_from_descriptor walks the descriptor and produces:
#   • OneofColumn  — one per oneof (shows which branch is active)
#   • ProtoColumn  — one per leaf scalar field in each branch (up to max_depth)
#   • CoalesceColumn — one per leaf field name shared across branches
#
# Annotation names are derived from the field path:
#   branch_a.person.name  →  branch_a_person_name
#
# CoalesceColumn names follow the pattern:
#   coalesce_{oneof_name}_{leaf_field_name}
#   e.g. coalesce_source_label  (COALESCE of branch_a.label and branch_b.label)

AVAILABLE_COLUMNS, COLUMN_GROUPS = columns_from_descriptor(
    DESCRIPTOR,
    MESSAGE,
    max_depth=2,
    oneof_badges={"branch_a": "branch-a", "branch_b": "branch-b"},
)

COLUMN_BY_NAME: dict = {col.name: col for col in AVAILABLE_COLUMNS}

# Shown on first visit before the user has made a selection.
DEFAULT_COLUMN_NAMES: list[str] = [
    "source_type",
    "branch_a_label", "branch_a_value",
    "branch_b_label", "branch_b_category",
    "coalesce_source_label",
]


# ── Single runtime view ──────────────────────────────────────────────────────

class RecordView(ProtoView):
    """
    Universal view for test.Record — columns are chosen at runtime by the user.

    The class defines all available dynamic filters; the column list is
    replaced per-request from the user's selection (see views.py).
    """

    descriptor = DESCRIPTOR
    message_type = MESSAGE
    blob_field = "proto_data"

    # Default: all columns.  Overridden per-request in views.py.
    columns = list(AVAILABLE_COLUMNS)

    dynamic_filters = [
        ModelDynamicFilter(
            "type", "source_type",
            choices=[("branch_a", "Branch A"), ("branch_b", "Branch B")],
            multiple=True,
            label="Branch type",
        ),
    ]
