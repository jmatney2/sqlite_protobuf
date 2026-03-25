from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0004_update_test_descriptor"),
    ]

    operations = [
        migrations.CreateModel(
            name="SavedRecordConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("config", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
    ]
