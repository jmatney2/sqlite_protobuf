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
from urllib.parse import urlencode

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
    sortable: bool = False
    badges: dict | None = None  # {value: "bg-primary"} → renders a Bootstrap badge

    def __post_init__(self) -> None:
        if not self.verbose_name:
            self.verbose_name = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        d = {"type": "proto", "name": self.name, "path": self.path,
             "verbose_name": self.verbose_name, "sortable": self.sortable}
        if self.badges is not None:
            d["badges"] = self.badges
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ProtoColumn":
        return cls(name=d["name"], path=d["path"],
                   verbose_name=d.get("verbose_name", ""),
                   sortable=d.get("sortable", True),
                   badges=d.get("badges"))


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
    sortable: bool = False
    badges: dict | None = None  # {value: "bg-primary"} → renders a Bootstrap badge

    def __post_init__(self) -> None:
        if not self.verbose_name:
            self.verbose_name = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        d = {"type": "oneof", "name": self.name, "oneof_name": self.oneof_name,
             "verbose_name": self.verbose_name, "sortable": self.sortable}
        if self.badges is not None:
            d["badges"] = self.badges
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OneofColumn":
        return cls(name=d["name"], oneof_name=d["oneof_name"],
                   verbose_name=d.get("verbose_name", ""),
                   sortable=d.get("sortable", True),
                   badges=d.get("badges"))


@dataclass
class CoalesceColumn:
    """
    A column that returns the first non-NULL value across multiple protobuf
    field paths, using SQL ``COALESCE``.

    The primary use case is showing a "combined" value for a field that appears
    in several branches of a ``oneof``.  Because inactive oneof branches return
    ``NULL`` (not the proto3 default), ``COALESCE`` correctly picks the active
    branch's value.

    Example — show whichever branch's ``label`` is set::

        CoalesceColumn(
            "combined_label",
            paths=["branch_a.label", "branch_b.label"],
            verbose_name="Label",
        )

    Parameters
    ----------
    name:
        Annotation name on the queryset row.
    paths:
        Ordered list of protobuf field paths.  The first non-NULL value wins.
    output_field:
        Django field instance controlling the Python type.  ``None`` triggers
        auto-detection from the first path in the descriptor.
    verbose_name:
        Column header label.  Defaults to ``name`` title-cased.
    sortable:
        Whether to allow ordering by this column.
    """

    name: str
    paths: list
    output_field: Any = None
    verbose_name: str = ""
    sortable: bool = False
    badges: dict | None = None

    def __post_init__(self) -> None:
        if not self.verbose_name:
            self.verbose_name = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        d = {"type": "coalesce", "name": self.name, "paths": list(self.paths),
             "verbose_name": self.verbose_name, "sortable": self.sortable}
        if self.badges is not None:
            d["badges"] = self.badges
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CoalesceColumn":
        return cls(name=d["name"], paths=d["paths"],
                   verbose_name=d.get("verbose_name", ""),
                   sortable=d.get("sortable", True),
                   badges=d.get("badges"))


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
    choices: list | None = None  # renders a <select>; list of str or (value, label) tuples
    multiple: bool = False       # True → <select multiple> + __in lookup

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        d = {"type": "proto", "name": self.name, "path": self.path,
             "label": self.label, "lookup": self.lookup}
        if self.choices is not None:
            d["choices"] = _normalize_choices(self.choices)
            d["multiple"] = self.multiple
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DynamicFilter":
        return cls(name=d["name"], path=d["path"],
                   label=d.get("label", ""),
                   lookup=d.get("lookup", "exact"),
                   choices=d.get("choices"),
                   multiple=d.get("multiple", False))


# ---------------------------------------------------------------------------
# Model-field column and filter descriptors
# ---------------------------------------------------------------------------


@dataclass
class ModelColumn:
    """
    A column backed by a regular Django model field (not a protobuf extraction).

    Use this for fields that already live on the model — either a real database
    column or a generated column created with
    :func:`~django_sqlite_protobuf.expressions.make_protobuf_generated_field`
    or
    :func:`~django_sqlite_protobuf.expressions.make_protobuf_which_oneof_generated_field`.

    No ORM annotation is added; the value is read directly from the model row.

    Parameters
    ----------
    name:
        Model field name (also used as the column accessor).
    verbose_name:
        Column header label.  Defaults to ``name`` title-cased.
    sortable:
        Whether to allow ordering by this column.

    Example
    -------
    ::

        class RecordView(ProtoView):
            columns = [
                # A generated column that already exists on the model
                ModelColumn("source_type", verbose_name="Type"),
                ProtoColumn("label", "branch_a.label"),
            ]
    """

    name: str
    verbose_name: str = ""
    sortable: bool = True
    badges: dict | None = None  # {value: "bg-primary"} → renders a Bootstrap badge

    def __post_init__(self) -> None:
        if not self.verbose_name:
            self.verbose_name = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        d = {"type": "model", "name": self.name,
             "verbose_name": self.verbose_name, "sortable": self.sortable}
        if self.badges is not None:
            d["badges"] = self.badges
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ModelColumn":
        return cls(name=d["name"],
                   verbose_name=d.get("verbose_name", ""),
                   sortable=d.get("sortable", True),
                   badges=d.get("badges"))


@dataclass
class ModelFilter:
    """
    Fixed filter on a regular model field.

    Applied unconditionally (like :class:`OneofFilter` and
    :class:`FieldFilter`) but targets a plain model column instead of a
    protobuf path.

    Parameters
    ----------
    field_name:
        Django model field name (or double-underscore lookup path).
    value:
        Value to match.
    lookup:
        ORM lookup suffix (``"exact"``, ``"gte"``, etc.).

    Example
    -------
    ::

        fixed_filters = [ModelFilter("source_type", "branch_a")]
    """

    field_name: str
    value: Any
    lookup: str = "exact"


@dataclass
class ModelDynamicFilter:
    """
    A user-supplied filter on a regular model field.

    Like :class:`DynamicFilter` but targets a plain model column rather than
    a protobuf extraction path, so no ORM annotation is required.

    Parameters
    ----------
    name:
        Key in the params dict.
    field_name:
        Django model field name to filter on.
    label:
        Human-readable form label.  Defaults to ``name`` title-cased.
    lookup:
        ORM lookup suffix.

    Example
    -------
    ::

        dynamic_filters = [
            ModelDynamicFilter("type", "source_type", lookup="exact",
                               label="Record type"),
        ]
    """

    name: str
    field_name: str
    label: str = ""
    lookup: str = "exact"
    choices: list | None = None  # renders a <select>; list of str or (value, label) tuples
    multiple: bool = False       # True → <select multiple> + __in lookup

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        d = {"type": "model", "name": self.name, "field_name": self.field_name,
             "label": self.label, "lookup": self.lookup}
        if self.choices is not None:
            d["choices"] = _normalize_choices(self.choices)
            d["multiple"] = self.multiple
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ModelDynamicFilter":
        return cls(name=d["name"], field_name=d["field_name"],
                   label=d.get("label", ""),
                   lookup=d.get("lookup", "exact"),
                   choices=d.get("choices"),
                   multiple=d.get("multiple", False))


# ---------------------------------------------------------------------------
# ProtoView
# ---------------------------------------------------------------------------

_COLUMN_TYPES = {
    "proto": ProtoColumn,
    "oneof": OneofColumn,
    "model": ModelColumn,
    "coalesce": CoalesceColumn,
}
_DYNAMIC_FILTER_TYPES = {"proto": DynamicFilter, "model": ModelDynamicFilter}


def _normalize_choices(choices: list) -> list[tuple[str, str]]:
    """Normalise choices to a list of (value, label) string tuples."""
    result = []
    for c in choices:
        if isinstance(c, (list, tuple)):
            result.append((str(c[0]), str(c[1])))
        else:
            result.append((str(c), str(c)))
    return result


def _get_param(params, name: str, multiple: bool = False):
    """
    Extract a value from *params*, handling QueryDict multi-values.

    Returns a non-empty list when *multiple* is True, a non-empty string
    otherwise, or ``None`` when the key is absent / blank.
    """
    if multiple:
        vals = params.getlist(name) if hasattr(params, "getlist") else (
            [params[name]] if params.get(name) else []
        )
        vals = [v for v in vals if v]
        return vals or None
    val = params.get(name)
    return val if val else None


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
    columns: list[ProtoColumn | OneofColumn | ModelColumn | CoalesceColumn] = []
    fixed_filters: list[OneofFilter | FieldFilter | ModelFilter] = []
    dynamic_filters: list[DynamicFilter | ModelDynamicFilter] = []

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

        # Column annotations (ModelColumn is already on the model — no annotation)
        for col in self.columns:
            if isinstance(col, ProtoColumn):
                annotations[col.name] = self._extract(col.path, col.output_field)
            elif isinstance(col, OneofColumn):
                annotations[col.name] = self._which_oneof(col.oneof_name)
            elif isinstance(col, CoalesceColumn):
                from django.db.models.functions import Coalesce
                of = col.output_field or self._auto_output_field(col.paths[0])
                exprs = [self._extract(p, of) for p in col.paths]
                annotations[col.name] = Coalesce(*exprs, output_field=of)

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
            # ModelFilter: no annotation needed

        # Dynamic-filter annotations (only when the param is supplied)
        for df in self.dynamic_filters:
            if not _get_param(params, df.name, getattr(df, "multiple", False)):
                continue
            if isinstance(df, ModelDynamicFilter):
                continue  # model field — no annotation needed
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
            elif isinstance(f, ModelFilter):
                key = f.field_name if f.lookup == "exact" else f"{f.field_name}__{f.lookup}"
                kwargs[key] = f.value

        for df in self.dynamic_filters:
            multiple = getattr(df, "multiple", False)
            value = _get_param(params, df.name, multiple)
            if not value:
                continue
            lookup = "in" if multiple else df.lookup
            if isinstance(df, ModelDynamicFilter):
                key = df.field_name if lookup == "exact" else f"{df.field_name}__{lookup}"
                kwargs[key] = value
            else:
                ann = f"_df_{df.name}"
                key = ann if lookup == "exact" else f"{ann}__{lookup}"
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

        Returns a list of dicts with keys ``name``, ``label``, ``lookup``,
        and optionally ``choices`` (list of ``(value, label)`` tuples) and
        ``multiple`` (bool) for select-based filters.
        """
        result = []
        for df in self.dynamic_filters:
            d = {"name": df.name, "label": df.label, "lookup": df.lookup}
            if getattr(df, "choices", None) is not None:
                d["choices"] = _normalize_choices(df.choices)
                d["multiple"] = getattr(df, "multiple", False)
            result.append(d)
        return result

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

        dyn_filters = []
        for d in config.get("dynamic_filters", []):
            df_cls = _DYNAMIC_FILTER_TYPES.get(d.get("type", "proto"), DynamicFilter)
            dyn_filters.append(df_cls.from_dict(d))
        instance.dynamic_filters = dyn_filters
        return instance


# ---------------------------------------------------------------------------
# View context builder
# ---------------------------------------------------------------------------


def _compute_page_range(page: int, total_pages: int, window: int = 2) -> list:
    """Return page numbers to display, with ``None`` as an ellipsis marker."""
    if total_pages <= 9:
        return list(range(1, total_pages + 1))
    pages: set[int] = set()
    pages.update([1, 2])
    pages.update(range(max(1, page - window), min(total_pages, page + window) + 1))
    pages.update([total_pages - 1, total_pages])
    result: list = []
    prev: int | None = None
    for p in sorted(pages):
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result


def build_proto_view_context(
    proto_view: "ProtoView",
    queryset,
    params,
    *,
    sort_param: str = "sort",
    extra_annotations: dict | None = None,
    extra_sort_columns: list[str] | None = None,
    page_size: int = 100,
    page_param: str = "page",
) -> dict:
    """
    Apply *proto_view* to *queryset*, sort, materialise rows, and return a
    template context dict ready for use with the built-in
    ``django_sqlite_protobuf/proto_table.html`` template (or any custom
    template that expects the same variables).

    Parameters
    ----------
    proto_view:
        A :class:`ProtoView` instance already configured with columns and
        filters.
    queryset:
        Base queryset to annotate and filter (e.g.
        ``MyModel.objects.all()``).
    params:
        Dict of user-supplied parameters — typically ``request.GET``
        (a Django ``QueryDict`` is accepted too).
    sort_param:
        Name of the query-string key that controls ordering.  Defaults to
        ``"sort"``.  The value is a column name, optionally prefixed with
        ``"-"`` for descending order.
    extra_annotations:
        Additional ORM annotations to apply after :meth:`ProtoView.apply`.
        Keys are annotation names; values are Django expression objects.
        These names are also materialised into each row dict and get
        sortable column-header URLs.
    extra_sort_columns:
        Names of extra sortable columns that are not declared on the view
        (e.g. columns added by *extra_annotations*).  These are included
        when validating the current sort value.  If *extra_annotations* is
        provided its keys are implicitly included, so this parameter is only
        needed when sorting a column that comes from elsewhere (e.g. already
        on the model).
    page_size:
        Number of rows per page.  Defaults to 100.
    page_param:
        Name of the query-string key that controls the current page.
        Defaults to ``"page"``.

    Returns
    -------
    dict
        Keys:

        ``columns``
            The view's column objects (for template iteration).
        ``rows``
            List of plain dicts, one per materialised row.  Keys are column
            names plus any *extra_annotations* names.
        ``col_sort_urls``
            ``{col_name: url_string}`` mapping for sortable column headers.
            Clicking toggles between ascending and descending.
        ``sort``
            Current sort value from *params* (may be empty string).
        ``filter_form_fields``
            Output of :meth:`ProtoView.filter_form_fields` — list of dicts
            with ``name``, ``label``, ``lookup`` keys.
        ``active_params``
            *params* with the sort key removed; useful for passing filter
            values back to a search form via hidden inputs.
        ``total``
            Total count of matching rows (before pagination).
        ``page``
            Current page number (1-based).
        ``page_size``
            Rows per page.
        ``total_pages``
            Total number of pages.
        ``page_range``
            List of page numbers (and ``None`` for ellipsis) to render in
            the pagination control.
        ``page_urls``
            ``{page_num: url}`` mapping for every page number in
            *page_range*.
        ``prev_url``
            URL for the previous page, or ``""`` on page 1.
        ``next_url``
            URL for the next page, or ``""`` on the last page.

    Example
    -------
    ::

        def my_view(request):
            view = MyProtoView()
            ctx = build_proto_view_context(
                view,
                MyModel.objects.all(),
                request.GET,
            )
            return render(request, \"myapp/list.html\", ctx)

    Template (minimal)::

        {% include \"django_sqlite_protobuf/proto_table.html\" %}
    """
    # Keep the original params for apply() so QueryDict.getlist() works for
    # multi-select filters.  Build a flat dict for scalar lookups and a
    # multi-value dict (values may be lists) for URL construction.
    if hasattr(params, "dict"):
        params_flat = params.dict()
    else:
        params_flat = dict(params)

    # Start with the flat dict, then overlay list values for every
    # multiple-choice filter so sort URLs preserve all selected options.
    params_multi: dict = dict(params_flat)
    for df in proto_view.dynamic_filters:
        if getattr(df, "multiple", False):
            vals = _get_param(params, df.name, multiple=True)
            if vals:
                params_multi[df.name] = vals

    # Pass original params so get_annotations / get_filter_kwargs can call
    # getlist() for multiple-choice filters.
    qs = proto_view.apply(queryset, params)

    if extra_annotations:
        qs = qs.annotate(**extra_annotations)

    extra_names = list(extra_annotations or {})
    extra_sort = list(extra_sort_columns or []) + extra_names

    sort = params_flat.get(sort_param, "")
    valid_sort = proto_view.sortable_columns() + extra_sort
    if sort.lstrip("-") in valid_sort:
        qs = qs.order_by(sort)

    total = qs.count()

    # Pagination
    total_pages = max(1, (total + page_size - 1) // page_size)
    try:
        page = max(1, int(params_flat.get(page_param) or 1))
    except (ValueError, TypeError):
        page = 1
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    columns = proto_view.columns
    rows = []
    for record in qs[offset:offset + page_size]:
        row = {}
        for col in columns:
            row[col.name] = getattr(record, col.name, None)
        for name in extra_names:
            row[name] = getattr(record, name, None)
        rows.append(row)

    def _sort_url(col_name: str) -> str:
        new_sort = f"-{col_name}" if sort == col_name else col_name
        # Changing sort resets to page 1, so strip page_param.
        p = {k: v for k, v in params_multi.items() if k != page_param}
        p[sort_param] = new_sort
        return "?" + urlencode(p, doseq=True)

    def _page_url(p: int) -> str:
        pars = {**params_multi, page_param: p}
        return "?" + urlencode(pars, doseq=True)

    col_sort_urls = {col.name: _sort_url(col.name) for col in columns}
    for name in extra_names:
        col_sort_urls[name] = _sort_url(name)

    page_range = _compute_page_range(page, total_pages)
    page_urls = {p: _page_url(p) for p in page_range if p is not None}
    prev_url = _page_url(page - 1) if page > 1 else ""
    next_url = _page_url(page + 1) if page < total_pages else ""

    active_params = {k: v for k, v in params_flat.items() if k != sort_param}

    # Hidden params: active_params minus filter-field names and page_param
    # (filter fields have their own inputs; page resets to 1 on new searches).
    filter_names = {df.name for df in proto_view.dynamic_filters}
    hidden_params = {
        k: v for k, v in active_params.items()
        if k not in filter_names and k != page_param
    }

    # Enrich filter_form_fields with current selected value(s) so the template
    # can pre-select options without extra dict lookups.
    form_fields = proto_view.filter_form_fields()
    for f in form_fields:
        df = next((d for d in proto_view.dynamic_filters if d.name == f["name"]), None)
        if df and getattr(df, "multiple", False):
            f["selected"] = _get_param(params, f["name"], multiple=True) or []
        else:
            f["selected"] = params_flat.get(f["name"], "")

    return {
        "columns": columns,
        "rows": rows,
        "col_sort_urls": col_sort_urls,
        "sort": sort,
        "filter_form_fields": form_fields,
        "active_params": active_params,
        "hidden_params": hidden_params,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "page_range": page_range,
        "page_urls": page_urls,
        "prev_url": prev_url,
        "next_url": next_url,
    }
