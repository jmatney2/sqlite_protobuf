"""
Locate the correct pre-compiled libsqlite_protobuf shared library for the
current platform, or honour an explicit override from Django settings.
"""

import platform
from pathlib import Path


_LIBS_DIR = Path(__file__).parent / "libs"


def _candidates() -> list[Path]:
    system = platform.system()
    machine = platform.machine()

    # Normalise machine names (uname reports 'AMD64' on some Windows builds).
    if machine in ("AMD64", "x86_64"):
        machine = "x86_64"
    elif machine in ("aarch64", "arm64"):
        machine = "aarch64"

    if system == "Linux":
        return [
            _LIBS_DIR / f"{machine}-unknown-linux-gnu" / "libsqlite_protobuf.so",
        ]
    if system == "Darwin":
        return [
            _LIBS_DIR / f"{machine}-apple-darwin" / "libsqlite_protobuf.dylib",
        ]
    return []


def find_extension() -> str | None:
    """Return the path to the bundled extension for this platform, or None."""
    for path in _candidates():
        if path.exists():
            return str(path)
    return None


def get_extension_path() -> str:
    """
    Return the extension path to load.

    Resolution order:
    1. ``SQLITE_PROTOBUF_EXTENSION`` in Django settings (explicit path).
    2. A bundled pre-compiled library under ``django_sqlite_protobuf/libs/``.

    Raises ``RuntimeError`` if no library can be found.
    """
    try:
        from django.conf import settings
        explicit = getattr(settings, "SQLITE_PROTOBUF_EXTENSION", None)
        if explicit:
            return explicit
    except Exception:
        pass

    bundled = find_extension()
    if bundled:
        return bundled

    system = platform.system()
    machine = platform.machine()
    raise RuntimeError(
        f"No pre-compiled libsqlite_protobuf found for {system}/{machine}.\n"
        "Either:\n"
        "  • Build from source and set SQLITE_PROTOBUF_EXTENSION in settings.py, or\n"
        "  • Install a wheel that includes a pre-built library for your platform.\n"
        "See the project README for cross-compilation instructions."
    )
