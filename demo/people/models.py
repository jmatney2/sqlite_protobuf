from django.db import models


class PersonRecord(models.Model):
    """
    Stores a single serialised ``test.Person`` protobuf blob.

    All queryable fields (name, age, score, …) are read directly from the
    blob at query time via protobuf_extract() — no duplicated columns.
    """

    proto_data = models.BinaryField()
    inserted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-inserted_at"]
