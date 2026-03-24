"""
Django ORM expressions wrapping the sqlite_protobuf SQL functions.

All expressions accept either a path to a compiled ``.pb`` FileDescriptorSet, or
raw ``bytes``.

Example::

    from django_sqlite_protobuf.expressions import ProtobufExtract, ProtobufToJson

    DESCRIPTOR = Path("proto/test.pb")
    MESSAGE = "test.Person"

    queryset = PersonRecord.objects.annotate(
        name=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "name"),
        city=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "address.city"),
        json=ProtobufToJson("proto_data", DESCRIPTOR, MESSAGE),
    ).filter(
        name="Alice"
    ).order_by("-age")

Generated columns
-----------------
Use :func:`make_protobuf_generated_field` to add a Django ``GeneratedField``
backed by a protobuf extraction.  The descriptor bytes are embedded as a SQL
hex literal (required because generated-column DDL cannot use bind parameters)::

    class MyModel(models.Model):
        proto_data = models.BinaryField()
        name = make_protobuf_generated_field(
            "proto_data", DESCRIPTOR, MESSAGE, "name",
            output_field=models.TextField(),
        )

    class Meta:
        indexes = [models.Index(fields=["name"], name="mymodel_name_idx")]

Indexes
-------
Expression indexes on protobuf fields are also supported via
:func:`make_protobuf_index`::

    class Meta:
        indexes = [
            make_protobuf_index(
                "proto_data", DESCRIPTOR, MESSAGE, "name",
                name="mymodel_name_expr_idx",
            )
        ]

Note: for the query planner to *use* an expression index the query must use
the same expression literal.  Indexing a generated column (above) is usually
simpler and more reliable.
"""

import os
from pathlib import Path

from django.db.models import BinaryField, DateTimeField, FloatField, Func, IntegerField, TextField
from django.db.models.expressions import RawSQL, Value


def _descriptor_bytes(descriptor: "str | os.PathLike | bytes | memoryview") -> bytes:
    """Normalise a descriptor argument to raw bytes.

    Accepts:

    * :class:`~django_sqlite_protobuf.descriptors.DescriptorRef` — resolved
      from the database (cached per-process).
    * ``str`` / any ``os.PathLike`` (``pathlib.Path``, ``pathlib.PurePosixPath``,
      etc.) — read from the filesystem.
    * Raw ``bytes`` / ``memoryview`` — used directly.
    """
    # Import here to avoid circular imports at module level.
    from django_sqlite_protobuf.descriptors import DescriptorRef
    if isinstance(descriptor, DescriptorRef):
        return descriptor.resolve()
    if isinstance(descriptor, (str, os.PathLike)):
        return Path(descriptor).read_bytes()
    return bytes(descriptor)


def _descriptor_hex_sql(descriptor_bytes: bytes) -> RawSQL:
    """Return a ``RawSQL`` expression that renders as a SQLite hex blob literal.

    ``X'deadbeef'`` notation is used so that the expression is self-contained
    with no bind parameters — a requirement for generated-column DDL and
    expression index definitions.
    """
    return RawSQL(f"X'{descriptor_bytes.hex()}'", [], output_field=BinaryField())


class ProtobufExtract(Func):
    """
    ``protobuf_extract(data, descriptor, message_type, field_path)``

    Extracts a single field from a binary protobuf blob.  Returns the
    appropriate SQLite type: INTEGER, REAL, TEXT, or BLOB.  Nested messages,
    repeated fields, and maps are returned as JSON text.

    Fields belonging to a ``oneof`` (including proto3 ``optional`` fields)
    return NULL when they are not the active member, so ``COALESCE`` works
    correctly across multiple oneof branches.

    ``output_field`` defaults to ``TextField``; override it for numeric fields::

        age=ProtobufExtract("proto_data", DESC, "test.Person", "age",
                            output_field=IntegerField())
    """

    function = "protobuf_extract"

    def __init__(
        self,
        field,
        descriptor: str | Path | bytes,
        message_type: str,
        path: str,
        output_field=None,
        **kwargs,
    ):
        descriptor_bytes = _descriptor_bytes(descriptor)
        super().__init__(
            field,
            Value(descriptor_bytes, output_field=BinaryField()),
            Value(message_type),
            Value(path),
            output_field=output_field or TextField(),
            **kwargs,
        )


class ProtobufWhichOneof(Func):
    """
    ``protobuf_which_oneof(data, descriptor, message_type, oneof_name)``

    Returns the **field name** of the currently active member of the named
    ``oneof``, or NULL when no member is set (or when ``data`` is NULL).
    Raises an SQL error when ``message_type`` is unknown or ``oneof_name``
    does not exist in the message.

    Example — tag each row with which branch is active and extract the shared
    label field regardless of branch::

        qs = Record.objects.annotate(
            active_branch=ProtobufWhichOneof("proto_data", DESC, "pkg.Record", "source"),
            label=Coalesce(
                ProtobufExtract("proto_data", DESC, "pkg.Record", "branch_a.label"),
                ProtobufExtract("proto_data", DESC, "pkg.Record", "branch_b.label"),
            ),
        )
    """

    function = "protobuf_which_oneof"

    def __init__(
        self,
        field,
        descriptor: str | Path | bytes,
        message_type: str,
        oneof_name: str,
        **kwargs,
    ):
        descriptor_bytes = _descriptor_bytes(descriptor)
        super().__init__(
            field,
            Value(descriptor_bytes, output_field=BinaryField()),
            Value(message_type),
            Value(oneof_name),
            output_field=TextField(),
            **kwargs,
        )


class ProtobufToJson(Func):
    """
    ``protobuf_to_json(data, descriptor, message_type)``

    Converts a binary protobuf blob to its canonical proto3 JSON string.
    The result carries SQLite's JSON subtype and is compatible with
    ``json_extract()`` and other SQLite JSON functions.
    """

    function = "protobuf_to_json"

    def __init__(
        self,
        field,
        descriptor: str | Path | bytes,
        message_type: str,
        **kwargs,
    ):
        descriptor_bytes = _descriptor_bytes(descriptor)
        super().__init__(
            field,
            Value(descriptor_bytes, output_field=BinaryField()),
            Value(message_type),
            output_field=TextField(),
            **kwargs,
        )


class ProtobufTimestamp(Func):
    """
    Extracts an ``int64`` Unix-seconds field from a protobuf blob and returns
    it as a Python ``datetime`` object.

    Internally this renders as::

        datetime(protobuf_extract(data, descriptor, message_type, path), 'unixepoch')

    which SQLite converts to an ISO 8601 string that Django's ``DateTimeField``
    can parse.  The result is a **naive** datetime (UTC implied).  If your
    project uses ``USE_TZ = True`` wrap the queryset value with
    ``django.utils.timezone.make_aware()`` or use ``Cast`` with a timezone-aware
    expression.

    Example::

        from django_sqlite_protobuf.expressions import ProtobufTimestamp

        qs = Event.objects.annotate(
            created=ProtobufTimestamp("proto_data", DESCRIPTOR, "pkg.Event", "created_at"),
        ).filter(created__gte=datetime(2024, 1, 1))
    """

    function = "datetime"
    template = "%(function)s(%(expressions)s, 'unixepoch')"

    def __init__(
        self,
        field,
        descriptor: str | Path | bytes,
        message_type: str,
        path: str,
        **kwargs,
    ):
        kwargs.setdefault("output_field", DateTimeField())
        inner = ProtobufExtract(field, descriptor, message_type, path, output_field=IntegerField())
        super().__init__(inner, **kwargs)


class ProtobufValid(Func):
    """
    ``protobuf_valid(data, descriptor, message_type)``

    Returns 1 when the blob is a valid protobuf encoding of the given message
    type, 0 otherwise.  Raises an SQL error for unknown message types.
    """

    function = "protobuf_valid"

    def __init__(
        self,
        field,
        descriptor: str | Path | bytes,
        message_type: str,
        **kwargs,
    ):
        descriptor_bytes = _descriptor_bytes(descriptor)
        super().__init__(
            field,
            Value(descriptor_bytes, output_field=BinaryField()),
            Value(message_type),
            output_field=IntegerField(),
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Generated columns
# ---------------------------------------------------------------------------


def make_protobuf_generated_field(
    blob_field: str,
    descriptor: str | Path | bytes,
    message_type: str,
    path: str,
    output_field=None,
    db_persist: bool = False,
):
    """
    Return a Django ``GeneratedField`` whose value is
    ``protobuf_extract(<blob_field>, <descriptor>, <message_type>, <path>)``.

    Requires **Django 5.0+** (when ``GeneratedField`` was introduced).

    The descriptor bytes are embedded as a SQL hex literal (``X'...'``) so
    that the expression is valid inside a ``GENERATED ALWAYS AS (...)``
    column definition, which does not permit bind parameters.

    Parameters
    ----------
    blob_field:
        Name of the model field that stores the protobuf blob (e.g.
        ``"proto_data"``).
    descriptor:
        Path to (or raw bytes of) the compiled ``.pb`` FileDescriptorSet.
    message_type:
        Fully-qualified protobuf message name, e.g. ``"mypackage.Person"``.
    path:
        Dot-separated field path, e.g. ``"name"`` or ``"address.city"``.
    output_field:
        Django field instance controlling the column type.  Defaults to
        ``TextField``.
    db_persist:
        ``True`` for a STORED generated column, ``False`` (default) for
        VIRTUAL.

    Example
    -------
    ::

        from django_sqlite_protobuf.expressions import make_protobuf_generated_field

        class MyModel(models.Model):
            proto_data = models.BinaryField()
            name = make_protobuf_generated_field(
                "proto_data", DESCRIPTOR, "pkg.Person", "name",
            )
            age = make_protobuf_generated_field(
                "proto_data", DESCRIPTOR, "pkg.Person", "age",
                output_field=models.IntegerField(),
                db_persist=True,  # STORED — can be indexed directly
            )

        class Meta:
            indexes = [models.Index(fields=["name"], name="mymodel_name_idx")]
    """
    try:
        from django.db.models import GeneratedField
    except ImportError as exc:
        raise ImportError(
            "GeneratedField requires Django 5.0 or later."
        ) from exc

    descriptor_bytes = _descriptor_bytes(descriptor)
    output_field = output_field or TextField()
    expression = Func(
        blob_field,
        _descriptor_hex_sql(descriptor_bytes),
        Value(message_type),
        Value(path),
        function="protobuf_extract",
        output_field=output_field,
    )
    return GeneratedField(expression=expression, output_field=output_field, db_persist=db_persist)


# ---------------------------------------------------------------------------
# Expression indexes
# ---------------------------------------------------------------------------


def make_protobuf_index(
    blob_field: str,
    descriptor: str | Path | bytes,
    message_type: str,
    path: str,
    name: str,
    **index_kwargs,
):
    """
    Return a Django ``Index`` on
    ``protobuf_extract(<blob_field>, <descriptor>, <message_type>, <path>)``.

    The descriptor bytes are embedded as a SQL hex literal so the index
    definition is self-contained.

    .. note::
        For the query planner to *use* this index a query must contain the
        **exact same expression literal**, including the identical descriptor
        hex string.  In practice, indexing a generated column (via
        :func:`make_protobuf_generated_field` + a normal ``Meta.indexes``
        entry) is simpler and more reliably used by the planner.

    Parameters
    ----------
    blob_field, descriptor, message_type, path:
        Same as :func:`make_protobuf_generated_field`.
    name:
        Required index name (Django requirement for expression indexes).
    **index_kwargs:
        Extra keyword arguments forwarded to ``Index`` (e.g. ``condition``).

    Example
    -------
    ::

        class Meta:
            indexes = [
                make_protobuf_index(
                    "proto_data", DESCRIPTOR, "pkg.Person", "name",
                    name="mymodel_pb_name_idx",
                ),
            ]
    """
    from django.db.models import Index

    descriptor_bytes = _descriptor_bytes(descriptor)
    expression = Func(
        blob_field,
        _descriptor_hex_sql(descriptor_bytes),
        Value(message_type),
        Value(path),
        function="protobuf_extract",
        output_field=TextField(),
    )
    return Index(expression, name=name, **index_kwargs)


# ---------------------------------------------------------------------------
# which_oneof generated columns and indexes
# ---------------------------------------------------------------------------


def make_protobuf_which_oneof_generated_field(
    blob_field: str,
    descriptor: str | Path | bytes,
    message_type: str,
    oneof_name: str,
    db_persist: bool = False,
):
    """
    Return a Django ``GeneratedField`` whose value is
    ``protobuf_which_oneof(<blob_field>, <descriptor>, <message_type>, <oneof_name>)``.

    Requires **Django 5.0+**.

    The result is a ``TEXT`` column containing the active oneof member's field
    name (e.g. ``"branch_a"`` or ``"branch_b"``), or ``NULL`` when no member
    is set.  This lets you sort/group/index by logical record type without
    parsing the protobuf at query time.

    Parameters
    ----------
    blob_field:
        Name of the model field that stores the protobuf blob.
    descriptor:
        Path to (or raw bytes of) the compiled ``.pb`` FileDescriptorSet.
    message_type:
        Fully-qualified protobuf message name, e.g. ``\"mypackage.Record\"``.
    oneof_name:
        Name of the ``oneof`` in the message, e.g. ``\"source\"``.
    db_persist:
        ``True`` for a STORED generated column, ``False`` (default) for
        VIRTUAL.

    Example
    -------
    ::

        from django_sqlite_protobuf.expressions import (
            make_protobuf_which_oneof_generated_field,
        )

        class Record(models.Model):
            proto_data  = models.BinaryField()
            source_type = make_protobuf_which_oneof_generated_field(
                \"proto_data\", DESCRIPTOR, \"pkg.Record\", \"source\",
                db_persist=True,  # STORED — can be indexed directly
            )

            class Meta:
                indexes = [
                    models.Index(fields=[\"source_type\"],
                                 name=\"record_source_type_idx\"),
                ]
    """
    try:
        from django.db.models import GeneratedField
    except ImportError as exc:
        raise ImportError(
            "GeneratedField requires Django 5.0 or later."
        ) from exc

    descriptor_bytes = _descriptor_bytes(descriptor)
    expression = Func(
        blob_field,
        _descriptor_hex_sql(descriptor_bytes),
        Value(message_type),
        Value(oneof_name),
        function="protobuf_which_oneof",
        output_field=TextField(),
    )
    return GeneratedField(expression=expression, output_field=TextField(), db_persist=db_persist)


def make_protobuf_which_oneof_index(
    blob_field: str,
    descriptor: str | Path | bytes,
    message_type: str,
    oneof_name: str,
    name: str,
    **index_kwargs,
):
    """
    Return a Django ``Index`` on
    ``protobuf_which_oneof(<blob_field>, <descriptor>, <message_type>, <oneof_name>)``.

    The descriptor bytes are embedded as a SQL hex literal so the index
    definition is self-contained.

    .. note::
        Indexing a generated column (via
        :func:`make_protobuf_which_oneof_generated_field` + a normal
        ``Meta.indexes`` entry) is usually simpler and more reliably used by
        the query planner.

    Parameters
    ----------
    blob_field, descriptor, message_type, oneof_name:
        Same as :func:`make_protobuf_which_oneof_generated_field`.
    name:
        Required index name.
    **index_kwargs:
        Extra keyword arguments forwarded to ``Index`` (e.g. ``condition``).

    Example
    -------
    ::

        class Meta:
            indexes = [
                make_protobuf_which_oneof_index(
                    \"proto_data\", DESCRIPTOR, \"pkg.Record\", \"source\",
                    name=\"record_source_type_expr_idx\",
                ),
            ]
    """
    from django.db.models import Index

    descriptor_bytes = _descriptor_bytes(descriptor)
    expression = Func(
        blob_field,
        _descriptor_hex_sql(descriptor_bytes),
        Value(message_type),
        Value(oneof_name),
        function="protobuf_which_oneof",
        output_field=TextField(),
    )
    return Index(expression, name=name, **index_kwargs)
