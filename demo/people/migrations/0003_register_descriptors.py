"""
Data migration: store the compiled protobuf descriptor in the database.

After this migration runs, code can reference the descriptor by name via
``DescriptorRef("test")`` instead of a file path.
"""

from pathlib import Path

from django.db import migrations

from django_sqlite_protobuf.descriptors import register_descriptor

_DESCRIPTOR_PATH = Path(__file__).parent.parent / "descriptors" / "test.pb"


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0002_recordentry"),
        ("django_sqlite_protobuf", "0001_stored_descriptor"),
    ]

    operations = [
        register_descriptor("test", _DESCRIPTOR_PATH),
    ]
