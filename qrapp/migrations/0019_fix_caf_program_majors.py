# Repair the CAF catalog: imports stored program names ("Agronomy",
# "Animal Science") as majors under BSA, even though Animal Science is its
# own program (BSAS) and CAF programs have no majors. Moves the affected
# students to their real program and deletes the bogus majors.

from django.db import migrations

from qrapp.caf_program_fix import fix_caf_program_majors


def fix_caf(apps, schema_editor):
    College = apps.get_model("qrapp", "College")
    Program = apps.get_model("qrapp", "Program")
    Major = apps.get_model("qrapp", "Major")
    Student = apps.get_model("qrapp", "Student")
    fix_caf_program_majors(College, Program, Major, Student)


class Migration(migrations.Migration):

    dependencies = [
        ("qrapp", "0018_importissue"),
    ]

    operations = [
        migrations.RunPython(fix_caf, migrations.RunPython.noop),
    ]
