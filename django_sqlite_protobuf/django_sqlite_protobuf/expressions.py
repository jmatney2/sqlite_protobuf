"""
Django ORM expressions wrapping the three sqlite_protobuf SQL functions.

All three accept either a path to a compiled ``.pb`` FileDescriptorSet, or
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
"""

from pathlib import Path

from django.db.models import BinaryField, DateTimeField, FloatField, Func, IntegerField, TextField
from django.db.models.expressions import Value


def _descriptor_bytes(descriptor: str | Path | bytes | memoryview) -> bytes:
    if isinstance(descriptor, (str, Path)):
        return Path(descriptor).read_bytes()
    return bytes(descriptor)


class ProtobufExtract(Func):
    """
    ``protobuf_extract(data, descriptor, message_type, field_path)``

    Extracts a single field from a binary protobuf blob.  Returns the
    appropriate SQLite type: INTEGER, REAL, TEXT, or BLOB.  Nested messages,
    repeated fields, and maps are returned as JSON text.

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
