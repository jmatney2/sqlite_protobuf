"""
DB-backed protobuf descriptor references.

Instead of shipping a compiled ``.pb`` file alongside your code, store the
descriptor once in the database via a migration and reference it by name.

Usage
-----
::

    # In a migration (runs once, stores the bytes in the DB):
    from django_sqlite_protobuf.descriptors import register_descriptor
    from pathlib import Path

    class Migration(migrations.Migration):
        dependencies = [
            ("myapp", "0001_initial"),
            ("django_sqlite_protobuf", "0001_stored_descriptor"),
        ]
        operations = [
            register_descriptor("my_schema", Path("proto/my_schema.pb")),
        ]

    # In your views / ProtoView definitions (resolves from DB on first use):
    from django_sqlite_protobuf.descriptors import DescriptorRef

    DESCRIPTOR = DescriptorRef("my_schema")

    class MyView(ProtoView):
        descriptor   = DESCRIPTOR
        message_type = "mypackage.MyMessage"
        ...
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


class DescriptorRef:
    """
    A lazy, DB-backed reference to a named protobuf descriptor.

    The descriptor bytes are fetched from
    :class:`~django_sqlite_protobuf.models.StoredDescriptor` on first access
    and cached in-process.  Pass instances anywhere a ``descriptor`` argument
    is accepted (e.g. :class:`~django_sqlite_protobuf.expressions.ProtobufExtract`,
    :class:`~django_sqlite_protobuf.proto_view.ProtoView`).

    Parameters
    ----------
    name:
        The ``name`` value passed to :func:`register_descriptor`.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def resolve(self) -> bytes:
        """Return the raw descriptor bytes, fetched from the DB (then cached)."""
        return _fetch(self.name)

    def __repr__(self) -> str:
        return f"DescriptorRef({self.name!r})"


@lru_cache(maxsize=32)
def _fetch(name: str) -> bytes:
    """Fetch descriptor bytes from the database, cached per-process."""
    from django_sqlite_protobuf.models import StoredDescriptor
    try:
        obj = StoredDescriptor.objects.get(name=name)
    except StoredDescriptor.DoesNotExist:
        raise LookupError(
            f"No descriptor named {name!r} in the database. "
            "Did you forget to run migrations that call register_descriptor()?"
        ) from None
    return bytes(obj.data)


def register_descriptor(
    name: str,
    descriptor: str | os.PathLike | bytes,
) -> "migrations.RunPython":
    """
    Return a Django ``RunPython`` migration operation that stores a compiled
    ``.pb`` FileDescriptorSet in the database under *name*.

    Re-running (e.g. after a schema change) updates the stored bytes via
    ``update_or_create``.

    Parameters
    ----------
    name:
        Unique identifier used later by :class:`DescriptorRef`.
    descriptor:
        Path to the compiled ``.pb`` file, or raw bytes.  The bytes are read
        **at migration-generation time** (i.e. when the migration module is
        imported), so the file must be present when ``manage.py migrate`` runs.

    Example
    -------
    ::

        operations = [
            register_descriptor("test_schema", Path("proto/test.pb")),
        ]
    """
    from django.db import migrations

    if isinstance(descriptor, (str, os.PathLike)):
        data = Path(descriptor).read_bytes()
    else:
        data = bytes(descriptor)

    def forwards(apps, schema_editor):
        StoredDescriptor = apps.get_model("django_sqlite_protobuf", "StoredDescriptor")
        StoredDescriptor.objects.update_or_create(
            name=name,
            defaults={"data": data},
        )
        # Invalidate the in-process cache so the new bytes are picked up
        # if this migration runs in the same process as subsequent queries.
        _fetch.cache_clear()

    def backwards(apps, schema_editor):
        StoredDescriptor = apps.get_model("django_sqlite_protobuf", "StoredDescriptor")
        StoredDescriptor.objects.filter(name=name).delete()
        _fetch.cache_clear()

    return migrations.RunPython(forwards, backwards)
