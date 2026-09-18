# Convert Student.major from CharField to a ForeignKey(Major), preserving data.
#
# Existing text values are migrated: for every student with a non-empty major
# string, a Major row is created (or reused) under that student's program and
# linked. This mirrors the college_fk/program_fk approach in 0010.

import django.db.models.deletion
from django.db import migrations, models


def promote_major_text_to_fk(apps, schema_editor):
    Student = apps.get_model("qrapp", "Student")
    Major = apps.get_model("qrapp", "Major")

    for student in Student.objects.select_related("program").iterator():
        major_raw = (getattr(student, "major", "") or "").strip()
        if not major_raw or major_raw.upper() in ("NA", "N/A", "NONE"):
            continue
        if student.program_id is None:
            # A major needs a program; without one the text cannot be linked.
            continue

        major_code = major_raw[:50]
        major = (
            Major.objects.filter(program_id=student.program_id, code__iexact=major_code).first()
            or Major.objects.filter(program_id=student.program_id, name__iexact=major_raw).first()
        )
        if not major:
            major = Major.objects.create(
                program_id=student.program_id,
                code=major_code,
                name=major_raw,
                is_active=True,
            )

        student.major_fk_id = major.id
        student.save(update_fields=["major_fk"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("qrapp", "0013_rename_section_to_major"),
    ]

    operations = [
        migrations.CreateModel(
            name="Major",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(help_text="Major code (e.g., HRM, AUTO)", max_length=50)),
                ("name", models.CharField(help_text="Full major name (e.g., Human Resource Management)", max_length=200)),
                ("is_active", models.BooleanField(default=True, help_text="Active majors appear in dropdowns")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("program", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="majors", to="qrapp.program")),
            ],
            options={
                "ordering": ["program__code", "code"],
                "verbose_name": "Major",
                "verbose_name_plural": "Majors",
            },
        ),
        migrations.AddField(
            model_name="student",
            name="major_fk",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional — only set if the chosen program has majors",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="students",
                to="qrapp.major",
            ),
        ),
        migrations.RunPython(promote_major_text_to_fk, noop_reverse),
        migrations.RemoveField(
            model_name="student",
            name="major",
        ),
        migrations.RenameField(
            model_name="student",
            old_name="major_fk",
            new_name="major",
        ),
        migrations.AlterField(
            model_name="student",
            name="major",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional — only set if the chosen program has majors",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="students",
                to="qrapp.major",
            ),
        ),
        migrations.AlterModelOptions(
            name="major",
            options={"ordering": ["program__code", "code"], "verbose_name": "Major", "verbose_name_plural": "Majors"},
        ),
    ]
