import json

from pathlib import Path

from django.db.models import Avg, FloatField, IntegerField, TextField
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django_tables2 import RequestConfig

from django_sqlite_protobuf.expressions import ProtobufExtract

from .models import PersonRecord, RecordEntry
from .proto_helpers import make_random_person, make_random_record
from .record_views import DESCRIPTOR as RECORD_DESCRIPTOR
from .record_views import MESSAGE as RECORD_MESSAGE
from .record_views import VIEW_LABELS, VIEWS
from .tables import DESCRIPTOR, MESSAGE, PersonTable


# ---------------------------------------------------------------------------
# People (existing demo)
# ---------------------------------------------------------------------------


def _stats():
    qs = PersonRecord.objects.annotate(
        _age=ProtobufExtract(
            "proto_data", DESCRIPTOR, MESSAGE, "age", output_field=IntegerField()
        ),
        _score=ProtobufExtract(
            "proto_data", DESCRIPTOR, MESSAGE, "score", output_field=FloatField()
        ),
        _active=ProtobufExtract(
            "proto_data", DESCRIPTOR, MESSAGE, "active", output_field=IntegerField()
        ),
    )
    total = PersonRecord.objects.count()
    agg = qs.aggregate(avg_age=Avg("_age"), avg_score=Avg("_score"))
    return {
        "total": total,
        "active": qs.filter(_active=1).count(),
        "avg_age": round(agg["avg_age"] or 0, 1),
        "avg_score": round(agg["avg_score"] or 0, 2),
    }


def index(request):
    filter_active = request.GET.get("active")
    qs = PersonRecord.objects.all()
    if filter_active == "1":
        qs = qs.filter(
            pk__in=PersonRecord.objects.annotate(
                _active=ProtobufExtract(
                    "proto_data", DESCRIPTOR, MESSAGE, "active",
                    output_field=IntegerField(),
                )
            ).filter(_active=1).values("pk")
        )
    elif filter_active == "0":
        qs = qs.filter(
            pk__in=PersonRecord.objects.annotate(
                _active=ProtobufExtract(
                    "proto_data", DESCRIPTOR, MESSAGE, "active",
                    output_field=IntegerField(),
                )
            ).filter(_active=0).values("pk")
        )

    table = PersonTable(qs)
    RequestConfig(request, paginate={"per_page": 25}).configure(table)

    return render(
        request,
        "people/index.html",
        {
            "table": table,
            "stats": _stats(),
            "filter_active": filter_active,
        },
    )


@require_POST
def add_random(request):
    count = int(request.POST.get("count", 1))
    for _ in range(min(count, 50)):
        blob = make_random_person()
        PersonRecord.objects.create(proto_data=blob)
    return redirect("index")


@require_POST
def clear_all(request):
    PersonRecord.objects.all().delete()
    return redirect("index")


# ---------------------------------------------------------------------------
# Records — ProtoView demo
# ---------------------------------------------------------------------------


def records(request):
    """
    Demonstrates:
    - ProtoView: fixed filters scope to a oneof branch; dynamic filters let
      the user search within that scope
    - OneofColumn: annotates each row with its active branch name, enabling
      sorting/grouping by type
    - COALESCE across oneof branches: works because inactive branches now
      return NULL instead of the proto3 default value
    - Config serialization: the view's column/filter setup can be round-tripped
      through JSON, enabling user-saved view configurations
    """
    view_name = request.GET.get("view", "all")
    if view_name not in VIEWS:
        view_name = "all"

    # Restore a user-saved config from the session if present, otherwise
    # use the class defaults.
    view_cls = VIEWS[view_name]
    saved_config = request.session.get(f"record_view_config_{view_name}")
    proto_view = view_cls.from_config(saved_config) if saved_config else view_cls()

    # apply() annotates the queryset with column expressions and applies
    # fixed + active dynamic filters in one call.
    qs = proto_view.apply(RecordEntry.objects.all(), request.GET)

    # COALESCE: picks the label from whichever branch is active.
    # This relies on the new NULL behavior: protobuf_extract returns NULL
    # for an inactive oneof member, not the proto3 default empty string.
    qs = qs.annotate(
        combined_label=Coalesce(
            ProtobufExtract(
                "proto_data", RECORD_DESCRIPTOR, RECORD_MESSAGE, "branch_a.label",
                output_field=TextField(),
            ),
            ProtobufExtract(
                "proto_data", RECORD_DESCRIPTOR, RECORD_MESSAGE, "branch_b.label",
                output_field=TextField(),
            ),
        )
    )

    # Sorting: any sortable column name, optionally prefixed with "-".
    sort = request.GET.get("sort", "")
    valid_sort_cols = proto_view.sortable_columns() + ["combined_label"]
    if sort.lstrip("-") in valid_sort_cols:
        qs = qs.order_by(sort)

    # Materialise up to 100 rows as plain dicts for the template.
    columns = proto_view.columns
    rows = []
    for record in qs[:100]:
        row = {"_id": record.id}
        for col in columns:
            row[col.name] = getattr(record, col.name, None)
        row["combined_label"] = getattr(record, "combined_label", None)
        rows.append(row)

    # Serialised config for the "user-defined views" panel.
    config_json = json.dumps(proto_view.serialize_config(), indent=2)

    # Build toggle-sort URLs for each column header.
    def sort_url(col_name):
        new_sort = f"-{col_name}" if sort == col_name else col_name
        params = request.GET.copy()
        params["sort"] = new_sort
        return "?" + params.urlencode()

    col_sort_urls = {col.name: sort_url(col.name) for col in columns}
    col_sort_urls["combined_label"] = sort_url("combined_label")

    return render(
        request,
        "people/records.html",
        {
            "rows": rows,
            "columns": columns,
            "proto_view": proto_view,
            "active_view": view_name,
            "view_labels": VIEW_LABELS,
            "filter_form_fields": proto_view.filter_form_fields(),
            "active_params": {
                k: v for k, v in request.GET.items()
                if k not in ("view", "sort")
            },
            "sort": sort,
            "col_sort_urls": col_sort_urls,
            "config_json": config_json,
            "total_scoped": qs.count(),
            "total_all": RecordEntry.objects.count(),
        },
    )


@require_POST
def add_random_record(request):
    count = int(request.POST.get("count", 1))
    for _ in range(min(count, 50)):
        blob = make_random_record()
        RecordEntry.objects.create(proto_data=blob)
    return redirect("records")


@require_POST
def clear_all_records(request):
    RecordEntry.objects.all().delete()
    return redirect("records")
