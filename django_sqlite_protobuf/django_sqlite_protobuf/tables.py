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

from .utils import annotate_proto


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

        # Auto-generate a tables.Column for each ProtoField not already
        # explicitly declared on this class.  Proto fields are prepended so
        # they appear in ProtobufMeta.fields order before any custom columns.
        auto = {
            f.name: tables.Column(verbose_name=f.verbose_name)
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
