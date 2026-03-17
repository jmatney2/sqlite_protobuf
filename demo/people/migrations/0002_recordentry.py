from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecordEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("proto_data", models.BinaryField()),
                ("inserted_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-inserted_at"],
            },
        ),
    ]
