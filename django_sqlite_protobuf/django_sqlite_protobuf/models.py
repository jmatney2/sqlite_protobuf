from django.db import models


class StoredDescriptor(models.Model):
    """
    A compiled protobuf ``FileDescriptorSet`` blob stored in the database.

    Instead of shipping a ``.pb`` file alongside your code, register your
    descriptor once via a data migration using
    :func:`~django_sqlite_protobuf.descriptors.register_descriptor`, then
    reference it by name with
    :class:`~django_sqlite_protobuf.descriptors.DescriptorRef`.

    Example migration::

        from django_sqlite_protobuf.descriptors import register_descriptor
        from pathlib import Path

        class Migration(migrations.Migration):
            dependencies = [
                ("myapp", "0001_initial"),
                ("django_sqlite_protobuf", "0001_stored_descriptor"),
            ]
            operations = [
                register_descriptor("my_schema", Path("proto/my_schema.pb")),
            ]
    """

    name = models.CharField(max_length=200, unique=True)
    data = models.BinaryField()

    class Meta:
        app_label = "django_sqlite_protobuf"

    def __str__(self) -> str:
        return self.name
