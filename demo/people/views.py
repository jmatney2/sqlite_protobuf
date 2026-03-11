from pathlib import Path

from django.db.models import Avg, FloatField, IntegerField
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django_tables2 import RequestConfig

from django_sqlite_protobuf.expressions import ProtobufExtract

from .models import PersonRecord
from .proto_helpers import make_random_person
from .tables import DESCRIPTOR, MESSAGE, PersonTable


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
