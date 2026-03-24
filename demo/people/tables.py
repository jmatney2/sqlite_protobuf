import django_tables2 as tables
from django.db.models import DateTimeField

from django_sqlite_protobuf.descriptors import DescriptorRef
from django_sqlite_protobuf.tables import ProtobufTable
from django_sqlite_protobuf.utils import ProtoField

DESCRIPTOR = DescriptorRef("test")
MESSAGE = "test.Person"


class PersonTable(ProtobufTable):
    # --- columns that need custom rendering ----------------------------------

    # Custom template to render tags as Bootstrap badge pills.
    tags = tables.TemplateColumn(
        template_code=(
            "{% for tag in value %}"
            '<span class="badge bg-light text-dark border" style="font-size:.7rem">{{ tag }}</span> '
            "{% endfor %}"
        ),
        verbose_name="Tags",
        orderable=False,
    )

    # Override the auto-generated DateTimeColumn to control display format.
    # auto-detection already sets output_field=DateTimeField() via ProtobufMeta;
    # this declaration just controls how the resulting datetime is rendered.
    created_ts = tables.DateTimeColumn(
        format="Y-m-d H:i",  # Django date-format string → "2025-03-07 14:30"
        verbose_name="Created (UTC)",
    )

    # --- model-native column (not in protobuf) --------------------------------

    # `flagged` lives on PersonRecord itself, not in the blob.  No ProtoField
    # entry is needed; django-tables2 reads it directly from the model instance.
    flagged = tables.BooleanColumn(verbose_name="⚑ Flagged")

    class ProtobufMeta:
        descriptor = DESCRIPTOR
        message_type = MESSAGE
        fields = [
            ProtoField("name", "name"),
            ProtoField("nickname", "nickname"),
            ProtoField("age", "age"),
            ProtoField("score", "score"),
            ProtoField("city", "address.city", verbose_name="City"),
            ProtoField("tags", "tags"),
            # bool and enum: output_field and column class are auto-detected
            # from the descriptor — no IntegerField() or BooleanColumn needed.
            ProtoField("active", "active"),
            ProtoField("status", "status"),
            # Timestamp: auto-detected as DateTimeField; created_ts declared
            # above only to customise the display format.
            ProtoField("created_ts", "created_ts", verbose_name="Created (UTC)"),
        ]

    class Meta:
        attrs = {"class": "table table-hover table-sm mb-0"}
        order_by = "-inserted_at"
        sequence = (
            "name",
            "nickname",
            "status",
            "active",
            "age",
            "score",
            "city",
            "tags",
            "created_ts",
            "flagged",
        )
