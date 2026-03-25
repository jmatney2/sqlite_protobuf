"""
Update the stored test descriptor after adding Person to BranchA
and count/score/enabled to BranchB.
"""

from pathlib import Path

from django.db import migrations

from django_sqlite_protobuf.descriptors import register_descriptor

_DESCRIPTOR_PATH = Path(__file__).parent.parent / "descriptors" / "test.pb"


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0003_register_descriptors"),
    ]

    operations = [
        register_descriptor("test", _DESCRIPTOR_PATH),
    ]
