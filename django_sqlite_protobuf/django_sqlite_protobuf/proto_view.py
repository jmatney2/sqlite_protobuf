"""
ProtoView: column-and-filter profiles for protobuf-backed querysets.

The central idea is that a large protobuf schema typically contains many
logical "types" of record living in one table, distinguished by a ``oneof``
field.  Rather than writing bespoke annotation/filter logic everywhere, you
define a :class:`ProtoView` subclass once and call ``apply()`` in any view or
API handler.

Quick start
-----------
::

    # myapp/proto_views.py
    from django_sqlite_protobuf.proto_view import (
        ProtoView, ProtoColumn, OneofColumn,
        OneofFilter, FieldFilter, DynamicFilter,
    )
    from django.db.models import IntegerField
    from pathlib import Path

    DESCRIPTOR = Path("proto/events.pb")

    class ClickEventView(ProtoView):
        descriptor   = DESCRIPTOR
        message_type = "pkg.Event"
        blob_field   = "proto_data"

        # Scope: only records where the "payload" oneof is set to "click"
        fixed_filters = [OneofFilter("payload", "click")]

        columns = [
            OneofColumn("event_type", "payload"),          # sortable branch name
            ProtoColumn("url",     "payload.click.url"),
            ProtoColumn("user_id", "payload.click.user_id",
                        output_field=IntegerField()),
        ]

        dynamic_filters = [
            DynamicFilter("url",     "payload.click.url",     lookup="icontains"),
            DynamicFilter("user_id", "payload.click.user_id", lookup="exact",
                          output_field=IntegerField()),
        ]

    # myapp/views.py
    from .proto_views import ClickEventView

    def click_events(request):
        view = ClickEventView()
        qs = view.apply(Event.objects.all(), request.GET)
        # qs rows carry .event_type, .url, .user_id attributes
        ...

User-defined views
------------------
``ProtoView`` supports (de)serialization of its user-customisable parts
(columns and dynamic filters) so that end-users can save and restore their
own column/filter configurations::

    # Serialize a user's column selection to store in the database
    config = view.serialize_config()     # → {"columns": [...], "dynamic_filters": [...]}
    view2 = ClickEventView.from_config(config)   # fixed_filters are preserved
    qs = view2.apply(qs, request.GET)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.db.models import TextField

from .expressions import ProtobufExtract, ProtobufWhichOneof
from .utils import _descriptor_bytes, _inspect_proto_field


# ---------------------------------------------------------------------------
# Column descriptors
# ---------------------------------------------------------------------------


@dataclass
class ProtoColumn:
    """
    A column that extracts a protobuf field value.

    Parameters
    ----------
    name:
        Annotation name on the queryset row and key in serialized configs.
    path:
        Dot-separated protobuf field path, e.g. ``"address.city"`` or
        ``"payload.click.url"``.
    output_field:
        Django field instance controlling the Python type.  ``None`` triggers
        auto-detection from the descriptor.
    verbose_name:
        Column header label.  Defaults to ``name`` title-cased.
    sortable:
        Whether to allow ordering by this column.
    """

    name: str
    path: str
    output_field: Any = None
    verbose_name: str = ""
    sortable: bool = True

    def __post_init__(self) -> None:
        if not self.verbose_name:
            self.verbose_name = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        return {"type": "proto", "name": self.name, "path": self.path,
                "verbose_name": self.verbose_name, "sortable": self.sortable}

    @classmethod
    def from_dict(cls, d: dict) -> "ProtoColumn":
        return cls(name=d["name"], path=d["path"],
                   verbose_name=d.get("verbose_name", ""),
                   sortable=d.get("sortable", True))


@dataclass
class OneofColumn:
    """
    A column that shows which branch of a ``oneof`` is currently set.

    The annotation value is a string (the field name) or NULL.  This column
    is primarily useful for sorting records by their logical "type" and for
    conditional display logic in templates.

    Parameters
    ----------
    name:
        Annotation name on the queryset row.
    oneof_name:
        Name of the ``oneof`` as declared in the ``.proto`` file.
    verbose_name:
        Column header label.  Defaults to ``name`` title-cased.
    sortable:
        Whether to allow ordering by this column.
    """

    name: str
    oneof_name: str
    verbose_name: str = ""
    sortable: bool = True

    def __post_init__(self) -> None:
        if not self.verbose_name:
            self.verbose_name = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        return {"type": "oneof", "name": self.name, "oneof_name": self.oneof_name,
                "verbose_name": self.verbose_name, "sortable": self.sortable}

    @classmethod
    def from_dict(cls, d: dict) -> "OneofColumn":
        return cls(name=d["name"], oneof_name=d["oneof_name"],
                   verbose_name=d.get("verbose_name", ""),
                   sortable=d.get("sortable", True))


# ---------------------------------------------------------------------------
# Filter descriptors
# ---------------------------------------------------------------------------


@dataclass
class OneofFilter:
    """
    Fixed filter: only include records where ``oneof_name`` is set to
    ``branch``.

    Example — scope a view to records whose "payload" oneof is "click"::

        fixed_filters = [OneofFilter("payload", "click")]
    """

    oneof_name: str
    branch: str


@dataclass
class FieldFilter:
    """
    Fixed filter: only include records where a protobuf field matches
    ``value`` under the given ``lookup``.

    Example — scope to active records::

        fixed_filters = [FieldFilter("status", 1, output_field=IntegerField())]
    """

    path: str
    value: Any
    output_field: Any = None
    lookup: str = "exact"


@dataclass
class DynamicFilter:
    """
    A user-supplied filter read from a params dict (e.g. ``request.GET``).

    The filter is only applied when ``params[name]`` is present and non-empty.

    Parameters
    ----------
    name:
        Key in the params dict.
    path:
        Protobuf field path to filter on.
    output_field:
        Django field instance controlling type coercion.  ``None`` triggers
        auto-detection.
    label:
        Human-readable label for a search form.  Defaults to ``name``
        title-cased.
    lookup:
        Django ORM lookup suffix: ``"exact"``, ``"icontains"``, ``"gte"``,
        ``"lte"``, etc.
    """

    name: str
    path: str
    output_field: Any = None
    label: str = ""
    lookup: str = "exact"

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        return {"name": self.name, "path": self.path,
                "label": self.label, "lookup": self.lookup}

    @classmethod
    def from_dict(cls, d: dict) -> "DynamicFilter":
        return cls(name=d["name"], path=d["path"],
                   label=d.get("label", ""),
                   lookup=d.get("lookup", "exact"))


# ---------------------------------------------------------------------------
# ProtoView
# ---------------------------------------------------------------------------

_COLUMN_TYPES = {"proto": ProtoColumn, "oneof": OneofColumn}


def _safe_name(path: str) -> str:
    """Convert a dotted/indexed path to a safe Python identifier fragment."""
    return path.replace(".", "_").replace("[", "_").replace("]", "")


class ProtoView:
    """
    A reusable column-and-filter profile for a protobuf-backed queryset.

    Subclass and declare class-level attributes; then call :meth:`apply` from
    any Django view or API handler.

    Class attributes
    ----------------
    descriptor:
        Path to (or raw bytes of) the compiled ``.pb`` FileDescriptorSet.
    message_type:
        Fully-qualified protobuf message name, e.g. ``"mypackage.Event"``.
    blob_field:
        Model field name that stores the binary protobuf blob.  Defaults to
        ``"proto_data"``.
    columns:
        Ordered list of :class:`ProtoColumn` and/or :class:`OneofColumn`
        instances to annotate and display.
    fixed_filters:
        Filters that always narrow the queryset to this view's logical scope
        (e.g. restrict to a specific oneof branch).  These are never affected
        by user params.
    dynamic_filters:
        Filters that are applied only when the user supplies a corresponding
        value in the params dict.
    """

    descriptor: str | Path | bytes
    message_type: str
    blob_field: str = "proto_data"
    columns: list[ProtoColumn | OneofColumn] = []
    fixed_filters: list[OneofFilter | FieldFilter] = []
    dynamic_filters: list[DynamicFilter] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _desc_bytes(self) -> bytes:
        return _descriptor_bytes(self.descriptor)

    def _auto_output_field(self, path: str):
        info = _inspect_proto_field(self._desc_bytes(), self.message_type, path)
        return info.output_field if info is not None else TextField()

    def _extract(self, path: str, output_field=None) -> ProtobufExtract:
        of = output_field or self._auto_output_field(path)
        return ProtobufExtract(
            self.blob_field, self.descriptor, self.message_type, path,
            output_field=of,
        )

    def _which_oneof(self, oneof_name: str) -> ProtobufWhichOneof:
        return ProtobufWhichOneof(
            self.blob_field, self.descriptor, self.message_type, oneof_name,
        )

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def get_annotations(self, params: dict | None = None) -> dict:
        """
        Build the full dict of ORM annotations needed for columns and active
        filters.  Deduplicates: the same annotation expression is never added
        twice.

        Parameters
        ----------
        params:
            User-supplied filter values (e.g. ``request.GET``).  Used to
            determine which dynamic-filter annotations are needed.
        """
        params = params or {}
        annotations: dict = {}

        # Column annotations
        for col in self.columns:
            if isinstance(col, ProtoColumn):
                annotations[col.name] = self._extract(col.path, col.output_field)
            elif isinstance(col, OneofColumn):
                annotations[col.name] = self._which_oneof(col.oneof_name)

        # Fixed-filter annotations (may share a name with a column — skip if so)
        for f in self.fixed_filters:
            if isinstance(f, OneofFilter):
                ann = f"_oneof_{_safe_name(f.oneof_name)}"
                if ann not in annotations:
                    annotations[ann] = self._which_oneof(f.oneof_name)
            elif isinstance(f, FieldFilter):
                ann = f"_ff_{_safe_name(f.path)}"
                if ann not in annotations:
                    annotations[ann] = self._extract(f.path, f.output_field)

        # Dynamic-filter annotations (only when the param is supplied)
        for df in self.dynamic_filters:
            if not params.get(df.name):
                continue
            ann = f"_df_{df.name}"
            if ann not in annotations:
                annotations[ann] = self._extract(df.path, df.output_field)

        return annotations

    def get_filter_kwargs(self, params: dict | None = None) -> dict:
        """
        Build the ``**kwargs`` dict for a ``.filter()`` call, combining fixed
        filters and any active dynamic filters.

        Parameters
        ----------
        params:
            User-supplied filter values.  Missing or blank values are skipped.
        """
        params = params or {}
        kwargs: dict = {}

        for f in self.fixed_filters:
            if isinstance(f, OneofFilter):
                ann = f"_oneof_{_safe_name(f.oneof_name)}"
                kwargs[ann] = f.branch
            elif isinstance(f, FieldFilter):
                ann = f"_ff_{_safe_name(f.path)}"
                key = ann if f.lookup == "exact" else f"{ann}__{f.lookup}"
                kwargs[key] = f.value

        for df in self.dynamic_filters:
            value = params.get(df.name)
            if not value:
                continue
            ann = f"_df_{df.name}"
            key = ann if df.lookup == "exact" else f"{ann}__{df.lookup}"
            kwargs[key] = value

        return kwargs

    def apply(self, queryset, params: dict | None = None):
        """
        Annotate *queryset* with column expressions, apply fixed filters, and
        apply any dynamic filters whose values appear in *params*.

        Parameters
        ----------
        queryset:
            Base queryset to start from (e.g. ``MyModel.objects.all()``).
        params:
            Dict of user-supplied filter values, typically ``request.GET``.
            Only :class:`DynamicFilter` entries whose ``name`` key is present
            and non-empty are applied.

        Returns
        -------
        QuerySet
            Annotated and filtered.  Column annotations are accessible as
            attributes on each row object.
        """
        annotations = self.get_annotations(params)
        filter_kwargs = self.get_filter_kwargs(params)
        qs = queryset.annotate(**annotations)
        if filter_kwargs:
            qs = qs.filter(**filter_kwargs)
        return qs

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def column_names(self) -> list[str]:
        """Ordered list of annotation names for the configured columns."""
        return [col.name for col in self.columns]

    def sortable_columns(self) -> list[str]:
        """Annotation names for columns that permit ordering."""
        return [col.name for col in self.columns if col.sortable]

    def filter_form_fields(self) -> list[dict]:
        """
        Metadata for each dynamic filter, suitable for rendering a search form.

        Returns a list of dicts with keys ``name``, ``label``, ``lookup``.
        """
        return [
            {"name": df.name, "label": df.label, "lookup": df.lookup}
            for df in self.dynamic_filters
        ]

    # ------------------------------------------------------------------
    # Serialization — user-defined column/filter customisation
    # ------------------------------------------------------------------

    def serialize_config(self) -> dict:
        """
        Serialize the user-customisable parts of this view (columns and
        dynamic filters) to a JSON-compatible dict.

        The fixed parts (``descriptor``, ``message_type``, ``blob_field``,
        ``fixed_filters``) are intentionally omitted: they belong to the
        developer-defined subclass and should not be overridden by end-users.

        Example::

            config = view.serialize_config()
            # Store config as JSON in a UserPreference model, then later:
            view2 = MyView.from_config(config)
        """
        return {
            "columns": [col.to_dict() for col in self.columns],
            "dynamic_filters": [df.to_dict() for df in self.dynamic_filters],
        }

    @classmethod
    def from_config(cls, config: dict) -> "ProtoView":
        """
        Create a new instance of this view with columns and dynamic filters
        replaced by those in *config* (as produced by :meth:`serialize_config`).

        The subclass's ``descriptor``, ``message_type``, ``blob_field``, and
        ``fixed_filters`` are preserved unchanged.

        Example::

            # User saved their preferred columns to the database:
            saved = json.loads(user_preference.view_config)
            view = ClickEventView.from_config(saved)
            qs = view.apply(Event.objects.all(), request.GET)
        """
        instance = cls.__new__(cls)
        # Copy class-level attributes so fixed parts are available.
        instance.descriptor = cls.descriptor
        instance.message_type = cls.message_type
        instance.blob_field = getattr(cls, "blob_field", "proto_data")
        instance.fixed_filters = list(getattr(cls, "fixed_filters", []))

        columns = []
        for d in config.get("columns", []):
            col_cls = _COLUMN_TYPES.get(d.get("type", "proto"))
            if col_cls is not None:
                columns.append(col_cls.from_dict(d))
        instance.columns = columns

        instance.dynamic_filters = [
            DynamicFilter.from_dict(d) for d in config.get("dynamic_filters", [])
        ]
        return instance
