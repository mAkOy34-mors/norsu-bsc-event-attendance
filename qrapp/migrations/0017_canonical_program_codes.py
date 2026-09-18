# Merge canonical program-code aliases into the registrar's list
# (BIT -> CIT|BSIT; stray "BSIT" row under CAS -> CIT|BSIT) and re-point
# affected students. Follows 0016, reusing the same shared merge module --
# this run also covers the canonical-code rules added after 0016 shipped.

from django.db import migrations

from qrapp.legacy_program_merge import merge_legacy_programs


def merge_legacy(apps, schema_editor):
    College = apps.get_model("qrapp", "College")
    Program = apps.get_model("qrapp", "Program")
    Major = apps.get_model("qrapp", "Major")
    Student = apps.get_model("qrapp", "Student")
    merge_legacy_programs(College, Program, Major, Student)


class Migration(migrations.Migration):

    dependencies = [
        ("qrapp", "0016_merge_legacy_programs"),
    ]

    operations = [
        migrations.RunPython(merge_legacy, migrations.RunPython.noop),
    ]
