"""
ProtoView definitions for RecordEntry.

``test.Record`` has a polymorphic ``oneof source { BranchA; BranchB; }``
field.  Each view below represents a different "lens" on the same table:

AllRecordsView
    Shows every row.  Uses a OneofColumn so the active branch is visible
    and sortable.  COALESCE is applied in the view function to produce a
    unified ``combined_label`` column that works because inactive branches
    now return NULL instead of the proto3 default.

BranchAView / BranchBView
    Fixed filter scopes the queryset to one branch.  Dynamic filters let
    the user search within that branch.  Serialising the view config
    (``view.serialize_config()``) produces a JSON dict that can be stored
    per-user and restored with ``BranchAView.from_config(config)``.

Generated column + index (shown here as reference SQL; see models.py):

    ALTER TABLE people_recordentry
      ADD COLUMN branch_a_label TEXT GENERATED ALWAYS AS (
        protobuf_extract(proto_data, X'<descriptor_hex>',
                         'test.Record', 'branch_a.label')
      ) VIRTUAL;

    CREATE INDEX people_recordentry_branch_a_label
        ON people_recordentry(branch_a_label);

In Django model code this is:

    from django_sqlite_protobuf.expressions import make_protobuf_generated_field

    class RecordEntry(models.Model):
        proto_data = models.BinaryField()
        branch_a_label = make_protobuf_generated_field(
            "proto_data", DESCRIPTOR, "test.Record", "branch_a.label",
            output_field=models.TextField(), db_persist=True,
        )
        class Meta:
            indexes = [models.Index(fields=["branch_a_label"],
                                    name="recordentry_branch_a_label_idx")]
"""

from django.db.models import IntegerField

from django_sqlite_protobuf.descriptors import DescriptorRef
from django_sqlite_protobuf.proto_view import (
    DynamicFilter,
    FieldFilter,
    ModelDynamicFilter,
    OneofColumn,
    OneofFilter,
    ProtoColumn,
    ProtoView,
)

DESCRIPTOR = DescriptorRef("test")
MESSAGE = "test.Record"


class AllRecordsView(ProtoView):
    """All rows; active branch visible via OneofColumn for sorting."""

    descriptor = DESCRIPTOR
    message_type = MESSAGE
    blob_field = "proto_data"

    columns = [
        OneofColumn("source_type", "source", verbose_name="Type",
                    badges={"branch_a": "bg-primary", "branch_b": "bg-success"}),
        ProtoColumn("branch_a_label", "branch_a.label", verbose_name="A: Label"),
        ProtoColumn(
            "branch_a_value", "branch_a.value",
            output_field=IntegerField(), verbose_name="A: Value",
        ),
        ProtoColumn("branch_b_label", "branch_b.label", verbose_name="B: Label"),
        ProtoColumn("branch_b_category", "branch_b.category", verbose_name="B: Category"),
    ]

    # No fixed filters — every row is included.
    # combined_label (COALESCE of the two branch labels) is added by the view
    # function after apply(), to keep the ProtoView definition self-contained.
    # source_type is already annotated by OneofColumn above, so
    # ModelDynamicFilter filters on the annotation directly without
    # needing a separate protobuf_extract call.
    dynamic_filters = [
        ModelDynamicFilter(
            "type", "source_type",
            choices=[("branch_a", "Branch A"), ("branch_b", "Branch B")],
            multiple=True,
            label="Branch type",
        ),
    ]


class BranchAView(ProtoView):
    """Only Branch A rows (``oneof source = branch_a``)."""

    descriptor = DESCRIPTOR
    message_type = MESSAGE
    blob_field = "proto_data"

    fixed_filters = [OneofFilter("source", "branch_a")]

    columns = [
        ProtoColumn("label", "branch_a.label", verbose_name="Label"),
        ProtoColumn(
            "value", "branch_a.value",
            output_field=IntegerField(), verbose_name="Value",
        ),
    ]

    dynamic_filters = [
        DynamicFilter("search", "branch_a.label", lookup="icontains", label="Search label"),
        DynamicFilter(
            "min_value", "branch_a.value",
            output_field=IntegerField(), lookup="gte", label="Min value",
        ),
    ]


class BranchBView(ProtoView):
    """Only Branch B rows (``oneof source = branch_b``)."""

    descriptor = DESCRIPTOR
    message_type = MESSAGE
    blob_field = "proto_data"

    fixed_filters = [OneofFilter("source", "branch_b")]

    columns = [
        ProtoColumn("label", "branch_b.label", verbose_name="Label"),
        ProtoColumn("category", "branch_b.category", verbose_name="Category"),
    ]

    dynamic_filters = [
        DynamicFilter("search", "branch_b.label", lookup="icontains", label="Search label"),
        DynamicFilter("category", "branch_b.category", lookup="exact", label="Category"),
    ]


VIEWS: dict[str, type[ProtoView]] = {
    "all": AllRecordsView,
    "branch_a": BranchAView,
    "branch_b": BranchBView,
}

VIEW_LABELS = {
    "all": "All Records",
    "branch_a": "Branch A only",
    "branch_b": "Branch B only",
}
