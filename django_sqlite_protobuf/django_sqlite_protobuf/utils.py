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

import os
import re
from dataclasses import dataclass
from functools import lru_cache
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
        Django field instance controlling the Python type of the extracted
        value.  When omitted (``None``), the type is inferred automatically
        from the compiled proto descriptor — ``IntegerField`` for integers and
        bools, ``FloatField`` for float/double, ``DateTimeField`` for
        ``google.protobuf.Timestamp``, ``JSONField`` for repeated fields and
        nested messages, and ``TextField`` for everything else.
    verbose_name:
        Human-readable label used as the table column header.  Defaults to
        ``name`` with underscores replaced by spaces, title-cased.
    """

    name: str
    path: str
    output_field: models.Field | None = None  # None → auto-detect from descriptor
    verbose_name: str = ""

    def __post_init__(self) -> None:
        if not self.verbose_name:
            self.verbose_name = self.name.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Descriptor inspection helpers
# ---------------------------------------------------------------------------

@dataclass
class _FieldInfo:
    """Internal: type metadata for a single proto field path."""
    output_field: models.Field
    is_bool: bool = False
    is_enum: bool = False
    is_timestamp: bool = False
    enum_full_name: str = ""  # only set when is_enum=True


@lru_cache(maxsize=32)
def _load_pool(descriptor_bytes: bytes) -> object:
    """Parse a FileDescriptorSet and return a populated DescriptorPool."""
    from google.protobuf.descriptor_pb2 import FileDescriptorSet
    from google.protobuf.descriptor_pool import DescriptorPool

    fds = FileDescriptorSet()
    fds.ParseFromString(descriptor_bytes)
    pool = DescriptorPool()
    for file_proto in fds.file:
        pool.Add(file_proto)
    return pool


@lru_cache(maxsize=256)
def _inspect_proto_field(
    descriptor_bytes: bytes,
    message_type: str,
    field_path: str,
) -> _FieldInfo | None:
    """
    Inspect a single proto field and return its type metadata.

    Returns ``None`` if the path cannot be resolved (unknown message type or
    field name).
    """
    pool = _load_pool(descriptor_bytes)
    msg_desc = pool.FindMessageTypeByName(message_type)
    if msg_desc is None:
        return None

    field_desc = None
    last_has_index = False
    segments = field_path.split(".")
    for i, segment in enumerate(segments):
        last_has_index = "[" in segment
        name = segment.split("[")[0]
        field_desc = msg_desc.fields_by_name.get(name)
        if field_desc is None:
            return None
        # Navigate into the nested message for non-terminal segments.
        if i < len(segments) - 1:
            if field_desc.message_type is not None:
                msg_desc = field_desc.message_type
            else:
                return None

    if field_desc is None:
        return None

    # The UPB C extension (protobuf ≥ 4) exposes is_repeated and is_map as
    # boolean properties rather than label/options inspection.
    is_map = getattr(field_desc, "is_map_field", False) or (
        field_desc.message_type is not None
        and field_desc.message_type.GetOptions().map_entry
    )
    is_repeated = field_desc.is_repeated and not is_map and not last_has_index

    if is_map or is_repeated:
        return _FieldInfo(output_field=models.JSONField())

    # TYPE_* constants live on the class but are accessible through the
    # instance in both the pure-Python and UPB C-extension implementations.
    fd_type = field_desc.type

    if fd_type == field_desc.TYPE_BOOL:
        return _FieldInfo(output_field=models.IntegerField(), is_bool=True)

    if fd_type == field_desc.TYPE_ENUM:
        return _FieldInfo(
            output_field=models.IntegerField(),
            is_enum=True,
            enum_full_name=field_desc.enum_type.full_name,
        )

    if fd_type in (
        field_desc.TYPE_INT32, field_desc.TYPE_SINT32,
        field_desc.TYPE_FIXED32, field_desc.TYPE_SFIXED32,
        field_desc.TYPE_INT64, field_desc.TYPE_SINT64,
        field_desc.TYPE_FIXED64, field_desc.TYPE_SFIXED64,
        field_desc.TYPE_UINT32, field_desc.TYPE_UINT64,
    ):
        return _FieldInfo(output_field=models.IntegerField())

    if fd_type in (field_desc.TYPE_FLOAT, field_desc.TYPE_DOUBLE):
        return _FieldInfo(output_field=models.FloatField())

    if fd_type == field_desc.TYPE_MESSAGE:
        if field_desc.message_type.full_name == "google.protobuf.Timestamp":
            return _FieldInfo(output_field=models.DateTimeField(), is_timestamp=True)
        return _FieldInfo(output_field=models.JSONField())

    return _FieldInfo(output_field=models.TextField())


def _descriptor_bytes(descriptor: "str | os.PathLike | bytes") -> bytes:
    """Normalise a descriptor argument to raw bytes.

    Accepts:

    * :class:`~django_sqlite_protobuf.descriptors.DescriptorRef` — resolved
      from the database (cached per-process).
    * ``str`` / any ``os.PathLike`` — read from the filesystem.
    * Raw ``bytes`` — used directly.
    """
    from django_sqlite_protobuf.descriptors import DescriptorRef
    if isinstance(descriptor, DescriptorRef):
        return descriptor.resolve()
    if isinstance(descriptor, (str, os.PathLike)):
        return Path(descriptor).read_bytes()
    return descriptor


def _enum_auto_prefix(enum_full_name: str) -> str:
    """
    Derive the conventional proto enum value prefix from the enum type name.

    Proto style is to prefix every enum value with the enum name in
    SCREAMING_SNAKE_CASE.  For example ``Status`` → ``STATUS_``,
    ``MyStatus`` → ``MY_STATUS_``.
    """
    simple = enum_full_name.rsplit(".", 1)[-1]
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", simple).upper()
    return snake + "_"


@lru_cache(maxsize=64)
def _enum_choices_cached(descriptor_bytes: bytes, enum_type: str, strip_prefix: str) -> dict[int, str]:
    pool = _load_pool(descriptor_bytes)
    enum_desc = pool.FindEnumTypeByName(enum_type)
    result = {}
    for value_desc in enum_desc.values:
        name = value_desc.name
        if strip_prefix and name.startswith(strip_prefix):
            name = name[len(strip_prefix):]
        result[value_desc.number] = name
    return result


def enum_choices_from_descriptor(
    descriptor: str | Path | bytes,
    enum_type: str,
    strip_prefix: str = "",
) -> dict[int, str]:
    """
    Return a ``{int: str}`` mapping of every value in a proto enum, suitable
    for use with :class:`~django_sqlite_protobuf.tables.ProtobufEnumColumn`.

    Parameters
    ----------
    descriptor:
        Path to (or raw bytes of) the compiled ``.pb`` FileDescriptorSet.
    enum_type:
        Fully-qualified proto enum name, e.g. ``"mypackage.Status"``.
    strip_prefix:
        String to strip from the start of each value name.  The typical proto
        convention is to prefix enum values with the enum name, so
        ``strip_prefix="STATUS_"`` turns ``STATUS_ACTIVE`` into ``ACTIVE``.

    Example
    -------
    ::

        status = ProtobufEnumColumn(
            choices=enum_choices_from_descriptor(DESCRIPTOR, "test.Status", strip_prefix="STATUS_"),
            verbose_name="Status",
        )
    """
    if isinstance(descriptor, (str, Path)):
        descriptor = Path(descriptor).read_bytes()
    return _enum_choices_cached(descriptor, enum_type, strip_prefix)


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
    desc_bytes = _descriptor_bytes(descriptor)
    annotations = {}
    for f in proto_fields:
        if f.output_field is not None:
            output_field = f.output_field
        else:
            info = _inspect_proto_field(desc_bytes, message_type, f.path)
            output_field = info.output_field if info is not None else models.TextField()
        annotations[f.name] = ProtobufExtract(
            data_field, descriptor, message_type, f.path,
            output_field=output_field,
        )
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
