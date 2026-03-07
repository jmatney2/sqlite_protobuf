"""
Custom SQLite database backend that loads the sqlite_protobuf extension on
every new connection.

Usage in settings.py::

    DATABASES = {
        "default": {
            "ENGINE": "django_sqlite_protobuf",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

    # Optional — override the auto-detected extension path:
    SQLITE_PROTOBUF_EXTENSION = "/path/to/libsqlite_protobuf.so"
"""

from django.db.backends.sqlite3.base import DatabaseWrapper as _SQLite3Wrapper

from .loader import get_extension_path


class DatabaseWrapper(_SQLite3Wrapper):
    def get_new_connection(self, conn_params):
        conn = super().get_new_connection(conn_params)
        ext = get_extension_path()
        conn.enable_load_extension(True)
        conn.load_extension(ext)
        conn.enable_load_extension(False)
        return conn
