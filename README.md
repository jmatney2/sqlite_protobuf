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
| `protobuf_extract(data, descriptor, message_type, field_path)` | Extract a field as its native SQLite type. Nested messages, repeated fields, and maps are returned as JSON. |
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

The demo stores `test.Person` proto blobs and demonstrates filtering,
ordering, and aggregation entirely through `protobuf_extract`.

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

### ORM expressions

```python
from pathlib import Path
from django.db.models import IntegerField, FloatField, JSONField, Avg
from django_sqlite_protobuf.expressions import ProtobufExtract, ProtobufToJson, ProtobufValid

DESCRIPTOR = Path("proto/my_schema.pb")
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
