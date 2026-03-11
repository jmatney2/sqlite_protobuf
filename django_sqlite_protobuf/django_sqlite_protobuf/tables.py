"""
django-tables2 integration: ProtobufTable base class.

Requires ``django-tables2`` (``pip install django-tables2``).

Define your protobuf schema once in a ``ProtobufMeta`` inner class; the
table automatically annotates any queryset passed to it and generates the
corresponding columns — no separate queryset definition required.

Example
-------
::

    # myapp/tables.py
    from pathlib import Path
    from django.db.models import IntegerField
    from django_sqlite_protobuf.tables import ProtobufTable
    from django_sqlite_protobuf.utils import ProtoField
    from .models import PersonRecord

    class PersonTable(ProtobufTable):
        class ProtobufMeta:
            model       = PersonRecord
            descriptor  = Path("proto/person.pb")
            message_type = "myapp.Person"
            fields = [
                ProtoField("name", "name"),
                ProtoField("age",  "age",          IntegerField()),
                ProtoField("city", "address.city", verbose_name="City"),
            ]

        # Extra non-proto columns are fine — define them normally:
        # actions = tables.TemplateColumn(...)

    # myapp/views.py
    from .models import PersonRecord
    from .tables import PersonTable

    def index(request):
        table = PersonTable(PersonRecord.objects.all())  # auto-annotated
        return render(request, "index.html", {"table": table})

Column ordering
---------------
Columns are emitted in ``ProtobufMeta.fields`` order, followed by any
additional columns defined directly on the class (e.g. action buttons).
Override ``Meta.sequence`` on your subclass for full control.

Subclassing
-----------
You can inherit from a ``ProtobufTable`` subclass and add or override
columns as normal.  The ``ProtobufMeta`` from the nearest parent that
defines one is used for annotation.
"""

from __future__ import annotations

import django_tables2 as tables

from .utils import (
    ProtoField,
    _descriptor_bytes,
    _enum_auto_prefix,
    _enum_choices_cached,
    _inspect_proto_field,
    annotate_proto,
)


class ProtobufEnumColumn(tables.Column):
    """
    Column for proto enum fields, which ``protobuf_extract`` returns as
    integers.

    Pass a ``choices`` dict mapping ``int → str`` to control how each value
    is displayed.  Build it automatically from the compiled descriptor with
    :func:`~django_sqlite_protobuf.utils.enum_choices_from_descriptor` so the
    mapping stays in sync whenever the proto file changes::

        from django_sqlite_protobuf.utils import enum_choices_from_descriptor

        status = ProtobufEnumColumn(
            choices=enum_choices_from_descriptor(
                DESCRIPTOR, "mypackage.Status", strip_prefix="STATUS_"
            ),
            verbose_name="Status",
        )

    Any integer not present in *choices* is rendered as-is.
    """

    def __init__(self, *args: object, choices: dict | None = None, **kwargs: object) -> None:
        self.choices = choices or {}
        super().__init__(*args, **kwargs)

    def render(self, value: object) -> object:
        return self.choices.get(value, value)


def _auto_column(
    f: ProtoField,
    desc_bytes: bytes | None,
    message_type: str,
) -> tables.Column:
    """
    Create the most appropriate ``django-tables2`` column for a
    :class:`~django_sqlite_protobuf.utils.ProtoField`.

    When *desc_bytes* is available the field type is inspected from the proto
    descriptor and the column class is chosen accordingly:

    * bool → ``BooleanColumn``
    * enum → ``ProtobufEnumColumn`` with auto-derived choices
    * ``google.protobuf.Timestamp`` → ``DateTimeColumn``
    * everything else → plain ``Column``

    Falls back to a plain ``Column`` when the descriptor is unavailable or the
    path cannot be resolved.
    """
    if desc_bytes is None:
        return tables.Column(verbose_name=f.verbose_name)

    info = _inspect_proto_field(desc_bytes, message_type, f.path)
    if info is None:
        return tables.Column(verbose_name=f.verbose_name)

    if info.is_bool:
        return tables.BooleanColumn(verbose_name=f.verbose_name)

    if info.is_enum:
        strip_prefix = _enum_auto_prefix(info.enum_full_name)
        choices = _enum_choices_cached(desc_bytes, info.enum_full_name, strip_prefix)
        return ProtobufEnumColumn(choices=choices, verbose_name=f.verbose_name)

    if info.is_timestamp:
        return tables.DateTimeColumn(verbose_name=f.verbose_name)

    return tables.Column(verbose_name=f.verbose_name)


class ProtobufTable(tables.Table):
    """
    A ``django-tables2`` ``Table`` that auto-annotates its queryset.

    See module docstring for full usage examples.
    """

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

        proto_meta = cls.__dict__.get("ProtobufMeta")
        if proto_meta is None:
            return

        # Store on the class so __init__ can find it through normal MRO.
        cls._proto_meta = proto_meta

        # Resolve the descriptor to bytes once at class-definition time so
        # _inspect_proto_field can use its lru_cache effectively.
        descriptor = getattr(proto_meta, "descriptor", None)
        try:
            desc_bytes = _descriptor_bytes(descriptor) if descriptor is not None else None
        except Exception:
            desc_bytes = None

        message_type = getattr(proto_meta, "message_type", "")

        # Auto-generate a column for each ProtoField not already explicitly
        # declared on this class.  The column class is chosen by inspecting
        # the proto descriptor (bool → BooleanColumn, enum → ProtobufEnumColumn,
        # etc.).  Proto fields are prepended so they appear in
        # ProtobufMeta.fields order before any custom columns.
        auto = {
            f.name: _auto_column(f, desc_bytes, message_type)
            for f in getattr(proto_meta, "fields", [])
            if f.name not in cls.base_columns
        }
        if auto:
            merged = {**auto, **cls.base_columns}
            cls.base_columns.clear()
            cls.base_columns.update(merged)

    def __init__(self, data: object, *args: object, **kwargs: object) -> None:
        proto_meta = getattr(self.__class__, "_proto_meta", None)
        if proto_meta is not None and hasattr(data, "annotate"):
            data = annotate_proto(
                data,
                proto_meta.descriptor,
                proto_meta.message_type,
                proto_meta.fields,
                getattr(proto_meta, "data_field", "proto_data"),
            )
        super().__init__(data, *args, **kwargs)
