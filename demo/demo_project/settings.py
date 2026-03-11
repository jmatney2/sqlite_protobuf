from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "demo-secret-key-do-not-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "django_sqlite_protobuf",
    "django_tables2",
    "people",
]

DJANGO_TABLES2_TEMPLATE = "django_tables2/bootstrap5.html"

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "demo_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django_sqlite_protobuf",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# When running from the repo, prefer the local cargo build output so that
# `task build && cd demo && uv run python manage.py runserver` works without
# any extra steps.  In a real deployment this block is absent entirely and
# the loader finds the .so bundled inside the installed wheel automatically.
_local_so = BASE_DIR.parent / "target" / "release" / "libsqlite_protobuf.so"
if _local_so.exists():
    SQLITE_PROTOBUF_EXTENSION = str(_local_so)

STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
