import random
import time
from pathlib import Path

from django.db.models import Avg, FloatField, IntegerField, JSONField
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from django_sqlite_protobuf.expressions import ProtobufExtract, ProtobufToJson

from .models import PersonRecord
from .proto_helpers import make_random_person

DESCRIPTOR = Path(__file__).parent / "descriptors" / "test.pb"
MESSAGE = "test.Person"


def _annotated_queryset(filter_active=None, sort_by=None):
    """Return a PersonRecord queryset with all proto fields annotated."""
    qs = PersonRecord.objects.annotate(
        name=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "name"),
        age=ProtobufExtract(
            "proto_data", DESCRIPTOR, MESSAGE, "age", output_field=IntegerField()
        ),
        score=ProtobufExtract(
            "proto_data", DESCRIPTOR, MESSAGE, "score", output_field=FloatField()
        ),
        temperature=ProtobufExtract(
            "proto_data", DESCRIPTOR, MESSAGE, "temperature", output_field=FloatField()
        ),
        active=ProtobufExtract(
            "proto_data", DESCRIPTOR, MESSAGE, "active", output_field=IntegerField()
        ),
        status=ProtobufExtract(
            "proto_data", DESCRIPTOR, MESSAGE, "status", output_field=IntegerField()
        ),
        city=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "address.city"),
        nickname=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "nickname"),
        tags=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "tags",
                             output_field=JSONField()),
    )

    if filter_active is not None:
        qs = qs.filter(active=1 if filter_active else 0)

    sort_map = {
        "name": "name",
        "-name": "-name",
        "age": "age",
        "-age": "-age",
        "score": "-score",   # default score sort: highest first
    }
    order = sort_map.get(sort_by, "-inserted_at")
    return qs.order_by(order)


STATUS_LABELS = {0: "Unknown", 1: "Active", 2: "Inactive"}


def index(request):
    filter_active = request.GET.get("active")
    sort_by = request.GET.get("sort", "")

    if filter_active == "1":
        filter_active = True
    elif filter_active == "0":
        filter_active = False
    else:
        filter_active = None

    people = _annotated_queryset(filter_active=filter_active, sort_by=sort_by)

    # Aggregate stats across ALL records (no active filter applied here)
    all_qs = PersonRecord.objects.annotate(
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
    active_count = all_qs.filter(_active=1).count()
    agg = all_qs.aggregate(
        avg_age=Avg("_age"), avg_score=Avg("_score")
    )

    stats = {
        "total": total,
        "active": active_count,
        "avg_age": round(agg["avg_age"] or 0, 1),
        "avg_score": round(agg["avg_score"] or 0, 2),
    }

    return render(
        request,
        "people/index.html",
        {
            "people": people,
            "stats": stats,
            "STATUS_LABELS": STATUS_LABELS,
            "filter_active": filter_active,
            "sort_by": sort_by,
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
