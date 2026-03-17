from django.db import models


class PersonRecord(models.Model):
    """
    Stores a single serialised ``test.Person`` protobuf blob.

    All queryable fields (name, age, score, …) are read directly from the
    blob at query time via protobuf_extract() — no duplicated columns.
    """

    proto_data = models.BinaryField()
    inserted_at = models.DateTimeField(auto_now_add=True)
    flagged = models.BooleanField(default=False)

    class Meta:
        ordering = ["-inserted_at"]


class RecordEntry(models.Model):
    """
    Stores a single serialised ``test.Record`` protobuf blob.

    ``test.Record`` has a ``oneof source { BranchA branch_a; BranchB branch_b; }``
    field — each row is one of two distinct "types" stored in the same table.

    Demonstrates:
    - ``ProtoView``: per-type column/filter profiles scoped to one branch
    - ``protobuf_which_oneof()``: sort/group rows by their active oneof branch
    - COALESCE across oneof branches: inactive branches now return NULL instead
      of the proto3 default, so COALESCE correctly skips them
    - Generated columns + indexes via ``make_protobuf_generated_field()``:
      see the "How it works" panel on the /records/ page
    """

    proto_data = models.BinaryField()
    inserted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-inserted_at"]
