import csv
import io
import json
from urllib.parse import urlencode

from django.db.models import Avg, FloatField, IntegerField
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_tables2 import RequestConfig

from django_sqlite_protobuf.expressions import ProtobufExtract
from django_sqlite_protobuf.proto_view import build_proto_view_context

from .models import PersonRecord, RecordEntry, SavedRecordConfig
from .proto_helpers import make_random_person, make_random_record
from .record_views import (
    COLUMN_BY_NAME, COLUMN_GROUPS, DEFAULT_COLUMN_NAMES, RecordView,
)
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
    # URL param ?config=<name> loads a saved server config (and updates session).
    config_name = request.GET.get("config")
    if config_name:
        try:
            saved = SavedRecordConfig.objects.get(name=config_name)
            loaded_view = RecordView.from_config(saved.config)
            col_names = [col.name for col in loaded_view.columns
                         if col.name in COLUMN_BY_NAME]
            request.session["record_col_names"] = col_names
        except SavedRecordConfig.DoesNotExist:
            col_names = request.session.get("record_col_names") or list(DEFAULT_COLUMN_NAMES)
    else:
        col_names = request.session.get("record_col_names") or list(DEFAULT_COLUMN_NAMES)

    view = RecordView()
    view.columns = [COLUMN_BY_NAME[n] for n in col_names]

    ctx = build_proto_view_context(view, RecordEntry.objects.all(), request.GET)

    selected_columns = [COLUMN_BY_NAME[n] for n in col_names]
    saved_configs = list(SavedRecordConfig.objects.values("name"))

    return render(request, "people/records.html", {
        **ctx,
        "column_groups": COLUMN_GROUPS,
        "selected_columns": selected_columns,
        "selected_col_names": col_names,
        "config_json": json.dumps(view.serialize_config(), indent=2),
        "total_scoped": ctx["total"],
        "total_all": RecordEntry.objects.count(),
        "saved_configs": saved_configs,
        "active_config_name": config_name,
    })


def records_csv(request):
    config_name = request.GET.get("config")
    if config_name:
        try:
            saved = SavedRecordConfig.objects.get(name=config_name)
            loaded_view = RecordView.from_config(saved.config)
            col_names = [col.name for col in loaded_view.columns
                         if col.name in COLUMN_BY_NAME]
        except SavedRecordConfig.DoesNotExist:
            col_names = request.session.get("record_col_names") or list(DEFAULT_COLUMN_NAMES)
    else:
        col_names = request.session.get("record_col_names") or list(DEFAULT_COLUMN_NAMES)

    view = RecordView()
    view.columns = [COLUMN_BY_NAME[n] for n in col_names]

    qs = view.apply(RecordEntry.objects.all(), request.GET)

    sort = request.GET.get("sort", "")
    if sort.lstrip("-") in view.sortable_columns():
        qs = qs.order_by(sort)

    columns = view.columns

    def _rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([col.verbose_name for col in columns])
        yield buf.getvalue()
        for record in qs.iterator(chunk_size=500):
            buf.seek(0)
            buf.truncate()
            writer.writerow([getattr(record, col.name, "") for col in columns])
            yield buf.getvalue()

    response = StreamingHttpResponse(_rows(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="records.csv"'
    return response


@require_POST
def save_record_columns(request):
    cols = request.POST.get("cols", "")
    col_names = [n for n in cols.split(",") if n in COLUMN_BY_NAME]
    if col_names:
        request.session["record_col_names"] = col_names
    return redirect("records")


@require_POST
def save_named_config(request):
    name = request.POST.get("name", "").strip()
    if not name:
        return redirect("records")
    col_names = request.session.get("record_col_names") or list(DEFAULT_COLUMN_NAMES)
    view = RecordView()
    view.columns = [COLUMN_BY_NAME[n] for n in col_names]
    SavedRecordConfig.objects.update_or_create(
        name=name,
        defaults={"config": view.serialize_config()},
    )
    return redirect(reverse("records") + "?" + urlencode({"config": name}))


@require_POST
def delete_named_config(request):
    name = request.POST.get("name", "").strip()
    if name:
        SavedRecordConfig.objects.filter(name=name).delete()
    return redirect("records")


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
