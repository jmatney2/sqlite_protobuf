"""
Helpers for reducing boilerplate when annotating querysets and building
django-tables2 Table classes from protobuf-backed models.

The core idea is to define your protobuf fields *once* as a list of
``ProtoField`` objects, then derive both the ORM annotations and the
table column definitions from that single definition.

Example
-------
::

    # myapp/proto_schema.py
    from django.db.models import IntegerField, TextField
    from django_sqlite_protobuf.utils import ProtoField

    DESCRIPTOR = Path("proto/my_schema.pb")
    MESSAGE    = "mypackage.Person"

    FIELDS = [
        ProtoField("name",  "name"),
        ProtoField("age",   "age",          IntegerField()),
        ProtoField("email", "contact.email"),
        ProtoField("city",  "address.city", verbose_name="City"),
    ]

    # myapp/views.py
    from django_sqlite_protobuf.utils import annotate_proto, proto_table_class
    from .models import PersonRecord
    from .proto_schema import DESCRIPTOR, FIELDS, MESSAGE

    def index(request):
        qs = annotate_proto(PersonRecord.objects.all(), DESCRIPTOR, MESSAGE, FIELDS)
        PersonTable = proto_table_class(FIELDS)
        table = PersonTable(qs)
        ...

    # Or with a custom base table that adds extra non-proto columns:
    import django_tables2 as tables

    class PersonTable(proto_table_class(FIELDS, model=PersonRecord)):
        actions = tables.Column(empty_values=(), orderable=False)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import models

from .expressions import ProtobufExtract

if TYPE_CHECKING:
    from django.db.models import QuerySet


@dataclass
class ProtoField:
    """
    Describes a single field extracted from a protobuf message.

    Parameters
    ----------
    name:
        Python attribute name used on the annotated queryset row and as the
        column accessor in the generated Table class.
    path:
        Dot-notation field path passed to ``protobuf_extract``, e.g.
        ``"address.city"`` or ``"tags[0]"``.
    output_field:
        Django field instance that describes the Python type of the extracted
        value.  Defaults to ``TextField()``.
    verbose_name:
        Human-readable label used as the table column header.  Defaults to
        ``name`` with underscores replaced by spaces, title-cased.
    """

    name: str
    path: str
    output_field: models.Field = field(default_factory=models.TextField)
    verbose_name: str = ""

    def __post_init__(self) -> None:
        if not self.verbose_name:
            self.verbose_name = self.name.replace("_", " ").title()


def annotate_proto(
    queryset: QuerySet,
    descriptor: str | Path | bytes,
    message_type: str,
    proto_fields: list[ProtoField],
    data_field: str = "proto_data",
) -> QuerySet:
    """
    Annotate *queryset* with one ``ProtobufExtract`` expression per field.

    Parameters
    ----------
    queryset:
        The base queryset to annotate (e.g. ``MyModel.objects.all()``).
    descriptor:
        Path to (or raw bytes of) the compiled ``.pb`` FileDescriptorSet.
    message_type:
        Fully-qualified protobuf message name, e.g. ``"mypackage.Person"``.
    proto_fields:
        List of :class:`ProtoField` objects defining which fields to extract.
    data_field:
        Name of the model field that stores the binary protobuf blob.
        Defaults to ``"proto_data"``.
    """
    annotations = {
        f.name: ProtobufExtract(
            data_field, descriptor, message_type, f.path,
            output_field=f.output_field,
        )
        for f in proto_fields
    }
    return queryset.annotate(**annotations)


def proto_table_class(
    proto_fields: list[ProtoField],
    model: type | None = None,
    base: type | None = None,
    **meta_attrs,
) -> type:
    """
    Dynamically build a ``django-tables2`` ``Table`` class from a list of
    :class:`ProtoField` definitions.

    Requires ``django-tables2`` to be installed (``pip install django-tables2``).

    Parameters
    ----------
    proto_fields:
        Field definitions — the same list passed to :func:`annotate_proto`.
    model:
        Optional Django model class added to the ``Meta`` inner class.
    base:
        Base ``Table`` class to inherit from.  Defaults to ``tables.Table``.
    **meta_attrs:
        Additional attributes merged into the ``Meta`` inner class
        (e.g. ``attrs={"class": "table"}``, ``template_name=...``).

    Returns
    -------
    type
        A new ``Table`` subclass whose columns mirror *proto_fields*.  You can
        subclass the returned class to add extra columns or override behaviour::

            class MyTable(proto_table_class(FIELDS, model=MyModel)):
                actions = tables.TemplateColumn(...)
    """
    try:
        import django_tables2 as tables
    except ImportError as exc:
        raise ImportError(
            "django-tables2 is required. Install with: pip install django-tables2"
        ) from exc

    base_class = base or tables.Table

    columns = {
        f.name: tables.Column(verbose_name=f.verbose_name)
        for f in proto_fields
    }

    meta_dict = {"fields": [f.name for f in proto_fields], **meta_attrs}
    if model is not None:
        meta_dict["model"] = model

    columns["Meta"] = type("Meta", (), meta_dict)
    return type("ProtoTable", (base_class,), columns)
