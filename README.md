# sqlite_protobuf

> **Proof of concept — not officially released.**
> This project is experimental and has no stable API, versioned releases, or
> published package.  It is shared as a reference implementation and starting
> point.  Use it at your own risk in production environments.

A SQLite extension written in Rust that lets you store binary protobuf blobs
and query them directly with SQL — no schema migrations, no duplicate columns.

## SQL functions

| Function | Description |
|---|---|
| `protobuf_extract(data, descriptor, message_type, field_path)` | Extract a field as its native SQLite type. Nested messages, repeated fields, and maps are returned as JSON. Fields in a `oneof` (including proto3 `optional`) return `NULL` when not the active member. |
| `protobuf_which_oneof(data, descriptor, message_type, oneof_name)` | Return the field name of the active `oneof` member, or `NULL` if none is set. |
| `protobuf_to_json(data, descriptor, message_type)` | Convert a blob to its canonical proto3 JSON representation (SQLite JSON subtype, compatible with `json_extract`). |
| `protobuf_valid(data, descriptor, message_type)` | Returns `1` if the blob is a valid encoding of the given message, `0` otherwise. |

`descriptor` is a compiled `FileDescriptorSet` blob (the output of
`protoc --descriptor_set_out --include_imports`).  Pass it as a bound
parameter or store it in a table.

`field_path` uses dot notation and bracket indexing:
- `"name"` — top-level scalar
- `"address.city"` — nested message field
- `"tags[0]"` — first element of a repeated field
- `"previous_addresses[1].city"` — indexed repeated field + nested field

### oneOf fields

Fields belonging to a `oneof` return `NULL` when they are not the active
member (rather than the proto3 default value).  This makes `COALESCE` work
correctly when multiple branches share a field name:

```sql
-- branch_b is set; branch_a.label returns NULL, not ""
SELECT COALESCE(
    protobuf_extract(data, :desc, 'pkg.Record', 'branch_a.label'),
    protobuf_extract(data, :desc, 'pkg.Record', 'branch_b.label')
) AS label
FROM records;
```

Use `protobuf_which_oneof` to find the active branch, or to sort/group rows
by their logical type:

```sql
SELECT
    protobuf_which_oneof(data, :desc, 'pkg.Record', 'source') AS source_type,
    COALESCE(
        protobuf_extract(data, :desc, 'pkg.Record', 'branch_a.label'),
        protobuf_extract(data, :desc, 'pkg.Record', 'branch_b.label')
    ) AS label
FROM records
ORDER BY source_type;   -- groups branch_a rows, then branch_b rows
```

proto3 `optional` fields follow the same rule: an unset `optional string`
returns `NULL`, not `""`.

### Generated columns and indexes

Because `protobuf_extract` is a deterministic function, SQLite accepts it
in `GENERATED ALWAYS AS` expressions and expression indexes.  This lets you
cache frequently-queried fields and make them indexable without duplicating
data in regular columns.

```sql
-- Add a virtual generated column (computed on read, zero storage overhead)
ALTER TABLE records
  ADD COLUMN branch_a_label TEXT
  GENERATED ALWAYS AS (
    protobuf_extract(data, X'<descriptor_hex>', 'pkg.Record', 'branch_a.label')
  ) VIRTUAL;

-- Index it so equality / range queries are fast
CREATE INDEX records_branch_a_label ON records(branch_a_label);

-- Or create a stored generated column (computed on write, stored on disk)
ALTER TABLE records
  ADD COLUMN branch_a_label TEXT
  GENERATED ALWAYS AS (
    protobuf_extract(data, X'<descriptor_hex>', 'pkg.Record', 'branch_a.label')
  ) STORED;

-- Expression index without a generated column
CREATE INDEX records_branch_a_label ON records(
    protobuf_extract(data, X'<descriptor_hex>', 'pkg.Record', 'branch_a.label')
);
```

Pass the descriptor as a hex blob literal (`X'...'`) rather than a bound
parameter — generated column expressions and index definitions must be
self-contained SQL with no placeholders.

## Building

### Prerequisites

| Tool | Purpose |
|---|---|
| [Rust](https://rustup.rs) (stable) | Compile the extension |
| [`protoc`](https://grpc.io/docs/protoc-installation/) | Compile `.proto` files for tests |
| [Task](https://taskfile.dev) | Task runner (replaces Make) |
| [uv](https://docs.astral.sh/uv/) | Python package manager (tests + demo) |

### Extension

```sh
# Compile libsqlite_protobuf.so / .dylib
task build

# Run all tests (Rust unit tests + Python integration tests)
task test

# Run just Rust or Python tests
task test:rust
task test:python
```

### Demo app

```sh
task demo        # migrate + runserver at http://127.0.0.1:8000/
```

The demo has two pages:

- **`/`** — stores `test.Person` blobs; demonstrates filtering, ordering,
  and aggregation through `protobuf_extract` and django-tables2.
- **`/records/`** — stores `test.Record` blobs (a `oneof source { BranchA; BranchB; }`
  message); demonstrates `ProtoView`, `protobuf_which_oneof`, COALESCE across
  oneof branches, generated columns, and config serialisation.

## Django integration

The `django_sqlite_protobuf` package provides a custom database backend and
Django ORM expressions.

### Installation

```sh
pip install django-sqlite-protobuf
```

> The package is not published to PyPI.  Install from a locally built wheel
> (see [Deployment](#deployment-airgapped--no-rust-on-site)) or directly from
> the repository:
> ```sh
> pip install "django-sqlite-protobuf @ git+https://github.com/your-org/sqlite_protobuf.git#subdirectory=django_sqlite_protobuf"
> ```

### Settings

```python
DATABASES = {
    "default": {
        "ENGINE": "django_sqlite_protobuf",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Optional: explicit path to the compiled .so / .dylib.
# If unset, the package searches for a bundled library under
# django_sqlite_protobuf/libs/ (populated by the build:* tasks).
SQLITE_PROTOBUF_EXTENSION = "/path/to/libsqlite_protobuf.so"
```

### Storing descriptors in the database

Instead of distributing a `.pb` file alongside your code, store the compiled
descriptor in the database via a migration and reference it by name everywhere.
This approach avoids file-path management and works naturally with Django
migrations.

**1. Add the dependency on `django_sqlite_protobuf` migrations:**

```python
INSTALLED_APPS = [
    ...
    "django_sqlite_protobuf",
    ...
]
```

**2. Create a data migration that stores the descriptor:**

```python
# myapp/migrations/0002_register_descriptors.py
from pathlib import Path
from django.db import migrations
from django_sqlite_protobuf.descriptors import register_descriptor

_DESCRIPTOR_PATH = Path(__file__).parent.parent / "proto" / "my_schema.pb"

class Migration(migrations.Migration):
    dependencies = [
        ("myapp", "0001_initial"),
        ("django_sqlite_protobuf", "0001_stored_descriptor"),
    ]
    operations = [
        register_descriptor("my_schema", _DESCRIPTOR_PATH),
    ]
```

`register_descriptor` reads the `.pb` bytes when the migration module is
imported, stores them via `update_or_create`, and returns a `RunPython`
migration operation.  Re-running after a schema change updates the stored
bytes automatically.

**3. Reference the descriptor by name in your code:**

```python
from django_sqlite_protobuf.descriptors import DescriptorRef

DESCRIPTOR = DescriptorRef("my_schema")
```

`DescriptorRef` is lazy — the database is only queried on first use, and the
result is cached in-process for the lifetime of the worker.  Pass it anywhere
a descriptor is accepted: `ProtobufExtract`, `ProtoView`, generated fields,
and indexes.

> **Alternative:** pass a `pathlib.Path` or raw `bytes` directly — all
> functions accept any of these forms.  `DescriptorRef` is recommended for
> production use to avoid shipping `.pb` files with deployed code.

### ORM expressions

```python
from django.db.models import IntegerField, FloatField, JSONField, Avg
from django_sqlite_protobuf.descriptors import DescriptorRef
from django_sqlite_protobuf.expressions import (
    ProtobufExtract, ProtobufWhichOneof, ProtobufToJson, ProtobufValid,
)

DESCRIPTOR = DescriptorRef("my_schema")
MESSAGE    = "mypackage.Person"

people = PersonRecord.objects.annotate(
    name=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "name"),
    age =ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "age",
                         output_field=IntegerField()),
    city=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "address.city"),
    tags=ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "tags",
                         output_field=JSONField()),
).filter(city="Springfield").order_by("-age")

stats = people.aggregate(avg_age=Avg("age"))
```

#### oneOf expressions

```python
from django.db.models.functions import Coalesce
from django.db.models import TextField
from django_sqlite_protobuf.descriptors import DescriptorRef
from django_sqlite_protobuf.expressions import ProtobufExtract, ProtobufWhichOneof

DESCRIPTOR = DescriptorRef("my_schema")
MESSAGE    = "pkg.Record"

qs = Record.objects.annotate(
    # Which branch is active — useful for sorting, display, and conditional logic
    source_type=ProtobufWhichOneof("proto_data", DESCRIPTOR, MESSAGE, "source"),

    # COALESCE works because inactive branches return NULL, not the proto3 default
    label=Coalesce(
        ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "branch_a.label",
                        output_field=TextField()),
        ProtobufExtract("proto_data", DESCRIPTOR, MESSAGE, "branch_b.label",
                        output_field=TextField()),
    ),
)

# Sort all rows: branch_a first, then branch_b
qs.order_by("source_type")

# Filter to only branch_a rows
qs.filter(source_type="branch_a")
```

#### Generated columns (Django 5.0+)

`make_protobuf_generated_field` and `make_protobuf_which_oneof_generated_field`
wrap Django's `GeneratedField` with the corresponding protobuf expression.  The
descriptor bytes are embedded as a SQL hex literal automatically (required
because generated-column DDL cannot use bind parameters).

```python
from django.db import models
from django_sqlite_protobuf.descriptors import DescriptorRef
from django_sqlite_protobuf.expressions import (
    make_protobuf_generated_field,
    make_protobuf_which_oneof_generated_field,
    make_protobuf_index,
)

DESCRIPTOR = DescriptorRef("my_schema")
MESSAGE    = "pkg.Record"

class Record(models.Model):
    proto_data = models.BinaryField()

    # VIRTUAL (default) — recomputed on read, no extra disk space
    branch_a_label = make_protobuf_generated_field(
        "proto_data", DESCRIPTOR, MESSAGE, "branch_a.label",
        output_field=models.TextField(),
    )

    # STORED (db_persist=True) — written at insert/update, can be indexed
    branch_a_value = make_protobuf_generated_field(
        "proto_data", DESCRIPTOR, MESSAGE, "branch_a.value",
        output_field=models.IntegerField(),
        db_persist=True,
    )

    # Stored generated column for the active oneof branch name
    # e.g. "branch_a", "branch_b", or NULL — useful for filtering/grouping
    source_type = make_protobuf_which_oneof_generated_field(
        "proto_data", DESCRIPTOR, MESSAGE, "source",
        db_persist=True,
    )

    class Meta:
        indexes = [
            # Index the stored generated column directly
            models.Index(fields=["branch_a_value"],
                         name="record_branch_a_value_idx"),
            models.Index(fields=["source_type"],
                         name="record_source_type_idx"),

            # Or an expression index without a generated column
            make_protobuf_index(
                "proto_data", DESCRIPTOR, MESSAGE, "branch_b.category",
                name="record_branch_b_category_idx",
            ),
        ]
```

Once the column exists, filter and sort by name like any regular field:

```python
Record.objects.filter(branch_a_label="alpha").order_by("branch_a_value")
Record.objects.filter(source_type="branch_a")
```

### ProtoView — column and filter profiles

`ProtoView` bundles a set of columns, fixed-scope filters, and user-supplied
dynamic filters into a single reusable definition.  It is designed for large
schemas where different `oneof` branches represent logically distinct record
types that each need their own columns and search fields.

```python
from django_sqlite_protobuf.descriptors import DescriptorRef
from django_sqlite_protobuf.proto_view import (
    ProtoView, ProtoColumn, OneofColumn, ModelColumn,
    OneofFilter, FieldFilter, ModelFilter,
    DynamicFilter, ModelDynamicFilter,
)
from django.db.models import IntegerField

DESCRIPTOR = DescriptorRef("my_schema")
MESSAGE    = "pkg.Event"

class ClickEventView(ProtoView):
    descriptor   = DESCRIPTOR
    message_type = MESSAGE
    blob_field   = "proto_data"   # default

    # Fixed filters — always applied; define the logical "type" this view covers
    fixed_filters = [
        OneofFilter("payload", "click"),            # oneof branch must be "click"
        # FieldFilter("status", 1, output_field=IntegerField()),  # scalar field value
        # ModelFilter("source_type", "click"),      # plain model column
    ]

    # Columns — annotated onto each queryset row
    columns = [
        OneofColumn("event_type", "payload",        # "click" | "view" | NULL; sortable
                    badges={"click": "badge-blue", "view": "badge-green"}),
        ProtoColumn("url",     "payload.click.url"),
        ProtoColumn("user_id", "payload.click.user_id",
                    output_field=IntegerField()),
        # ModelColumn reads a regular model field (no annotation added)
        ModelColumn("flagged", verbose_name="Flagged"),
    ]

    # Dynamic filters — applied only when the user supplies a value
    dynamic_filters = [
        DynamicFilter("url",      "payload.click.url",      lookup="icontains"),
        DynamicFilter("user_id",  "payload.click.user_id",  lookup="exact",
                      output_field=IntegerField()),
        # choices renders a <select> dropdown so users don't type raw values
        DynamicFilter("min_uid",  "payload.click.user_id",  lookup="gte",
                      output_field=IntegerField(), label="Min user ID"),
        # ModelDynamicFilter targets a model field (or annotation) directly —
        # no extra protobuf_extract call is made
        ModelDynamicFilter(
            "type", "event_type",
            choices=[("click", "Click"), ("view", "View")],
            multiple=True,       # renders <select multiple>; applies __in filter
            label="Event type",
        ),
    ]
```

**Column types:**

| Class | Source | Use case |
|---|---|---|
| `ProtoColumn` | `protobuf_extract` annotation | Scalar protobuf fields |
| `OneofColumn` | `protobuf_which_oneof` annotation | Active branch name for sorting/display |
| `ModelColumn` | Model field (no annotation) | Regular columns or generated columns already on the model |

**Filter types:**

| Class | Applied | Targets |
|---|---|---|
| `OneofFilter` | Always (fixed) | Oneof branch — restricts to one logical type |
| `FieldFilter` | Always (fixed) | Protobuf scalar field value |
| `ModelFilter` | Always (fixed) | Plain model field |
| `DynamicFilter` | When param present | Protobuf field via `protobuf_extract` |
| `ModelDynamicFilter` | When param present | Model field or existing annotation |

**`choices` and `multiple`** can be added to any `DynamicFilter` or
`ModelDynamicFilter` to render a dropdown instead of a text input:

```python
DynamicFilter("status", "status", output_field=IntegerField(),
              choices=[(1, "Active"), (2, "Pending"), (3, "Closed")])

ModelDynamicFilter("branch", "source_type",
                   choices=["branch_a", "branch_b"],
                   multiple=True)   # multi-select; applies __in lookup
```

**`badges`** on `ProtoColumn`, `OneofColumn`, and `ModelColumn` maps a cell
value to a CSS class string.  The built-in `proto_table.html` renders matching
values as `<span class="badge <css-class>">`:

```python
OneofColumn("source_type", "source",
            badges={"branch_a": "my-badge-blue", "branch_b": "my-badge-green"})
```

#### Using `build_proto_view_context` with the built-in template

`build_proto_view_context` is the easiest way to wire a `ProtoView` into a
Django view.  It applies the view, handles sorting, materialises rows, and
returns a ready-to-use template context dict.

```python
from django_sqlite_protobuf.proto_view import build_proto_view_context

def click_events(request):
    ctx = build_proto_view_context(
        ClickEventView(),
        Event.objects.all(),
        request.GET,
    )
    return render(request, "myapp/events.html", ctx)
```

In the template, include the built-in table widget:

```django
{% include "django_sqlite_protobuf/proto_table.html" %}
```

The template renders a filter form (when dynamic filters are configured) and a
sortable data table.  All CSS classes have Bootstrap defaults but can be
overridden via the `with` clause:

```django
{% include "django_sqlite_protobuf/proto_table.html" with
    proto_table_class="my-table"
    proto_filter_class="my-filter-form"
    proto_btn_search_class="my-btn my-btn--primary"
    proto_btn_clear_class="my-btn my-btn--outline"
    proto_null_class="my-null-cell"
    proto_empty_message="No results." %}
```

Available CSS class overrides: `proto_table_class`, `proto_table_container_class`,
`proto_thead_class`, `proto_sort_link_class`, `proto_null_class`,
`proto_filter_class`, `proto_input_class`, `proto_select_class`,
`proto_btn_search_class`, `proto_btn_clear_class`.

`build_proto_view_context` also accepts optional keyword arguments:

```python
build_proto_view_context(
    view,
    queryset,
    request.GET,
    # Add ORM annotations beyond those declared in the view (e.g. COALESCE)
    extra_annotations={"combined_label": coalesce_expr},
    # Pass the extra column metadata to the template so it renders a header
    # (extra_columns is added to the ctx dict you spread into the render call)
    sort_param="sort",   # default; query-string key for column ordering
    limit=100,           # default; max rows materialised
)
```

Columns added via `extra_annotations` get automatic sort URLs.  Pass their
display metadata as `extra_columns` in the template context:

```python
ctx = build_proto_view_context(view, qs, request.GET,
                               extra_annotations={"combined_label": expr})
return render(request, "myapp/list.html", {
    **ctx,
    "extra_columns": [
        {"name": "combined_label", "verbose_name": "Label", "sortable": True},
    ],
})
```

#### Using a ProtoView manually (without `build_proto_view_context`)

```python
def click_events(request):
    view = ClickEventView()
    qs = view.apply(Event.objects.all(), request.GET)
    # qs rows carry .event_type, .url, .user_id annotations

    qs = qs.order_by("event_type", "-user_id")

    form_fields = view.filter_form_fields()
    # → [{"name": "url", "label": "Url", "lookup": "icontains"}, ...]
    ...
```

`apply(queryset, params)` performs three steps in one call:
1. Annotates the queryset with all column expressions (including `OneofColumn`
   → `protobuf_which_oneof`; `ModelColumn` adds no annotation).
2. Applies all `fixed_filters` unconditionally.
3. Applies each dynamic filter whose key is present and non-empty in `params`.

#### User-defined views (serialisation)

The user-customisable parts of a view (columns and dynamic filters) can be
round-tripped through JSON.  Fixed filters, descriptor, and message type are
always preserved from the subclass definition.

```python
import json

# Serialise the current column/filter setup
config = view.serialize_config()
# → {"columns": [...], "dynamic_filters": [...]}
json.dumps(config)   # safe to store in a database / session

# Restore — fixed_filters are kept from the class definition unchanged
view2 = ClickEventView.from_config(json.loads(stored_config))
qs    = view2.apply(Event.objects.all(), request.GET)
```

This lets end-users save their preferred column layout and active search
fields, while developers retain control over which records each view can
access.

## Deployment (airgapped / no Rust on-site)

Build platform-specific libraries ahead of time and ship them inside the wheel.

### 1. Compile the native library

| Target | Command | Runs on |
|---|---|---|
| CentOS 7 / RHEL 7 (glibc 2.17) | `task build:centos7` | CentOS 7, RHEL 7, RHEL 8, RHEL 9, any Linux |
| RHEL 9 / modern Linux | `task build:linux-gnu` | glibc ≥ 2.34 |

`build:centos7` builds inside a `manylinux2014` Docker container (glibc 2.17)
and copies the result into `django_sqlite_protobuf/libs/x86_64-unknown-linux-gnu/`.
Because it targets the oldest supported glibc, this binary also runs on all
newer distributions — making it the recommended build for airgapped deployment.

> **Note on musl:** Rust's `x86_64-unknown-linux-musl` target only supports
> static binaries, not shared libraries (`cdylib`).  SQLite extensions must be
> loadable `.so` files, so a musl build is not possible.  Use `build:centos7`
> for maximum portability instead.

### 2. Build the wheel

```sh
task wheel
# → dist/django_sqlite_protobuf-*.whl
```

The wheel bundles the compiled `.so` so deployers only need `pip install`.
No `SQLITE_PROTOBUF_EXTENSION` setting is required.

### 3. Demo on CentOS 7

```sh
task build:centos7
task wheel
task demo:centos7    # runs the demo at http://localhost:8000/ inside centos:7
```
