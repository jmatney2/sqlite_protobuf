"""
Pytest fixtures shared across all test modules.

The session-scoped fixtures compile the Rust extension and the test .proto file
once, then make a fresh in-memory SQLite connection available to each test.
"""

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PROTO_DIR = REPO_ROOT / "proto"

# ---------------------------------------------------------------------------
# Django setup (needed by tests that import django_sqlite_protobuf)
# ---------------------------------------------------------------------------

# Make the Django integration package importable from the source tree.
sys.path.insert(0, str(REPO_ROOT / "django_sqlite_protobuf"))

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[],
        USE_TZ=False,
    )
    django.setup()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def extension_path():
    """Build the Rust extension and return the path to the shared library."""
    subprocess.run(
        ["cargo", "build", "--release"],
        cwd=REPO_ROOT,
        check=True,
    )
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    return str(REPO_ROOT / "target" / "release" / f"libsqlite_protobuf{suffix}")


# ---------------------------------------------------------------------------
# Protobuf compilation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def proto_out(tmp_path_factory):
    """Compile test.proto to a descriptor set and Python stubs."""
    out = tmp_path_factory.mktemp("proto_out")
    subprocess.run(
        [
            "protoc",
            f"-I{PROTO_DIR}",
            f"--descriptor_set_out={out / 'test.pb'}",
            "--include_imports",
            f"--python_out={out}",
            str(PROTO_DIR / "test.proto"),
        ],
        check=True,
    )
    return out


@pytest.fixture(scope="session")
def descriptor_bytes(proto_out):
    return (proto_out / "test.pb").read_bytes()


@pytest.fixture(scope="session")
def pb2(proto_out):
    """Return the generated test_pb2 module."""
    sys.path.insert(0, str(proto_out))
    spec = importlib.util.spec_from_file_location("test_pb2", proto_out / "test_pb2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture
def db(extension_path):
    """Fresh in-memory SQLite connection with the extension loaded."""
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    conn.load_extension(extension_path)
    conn.enable_load_extension(False)
    yield conn
    conn.close()
