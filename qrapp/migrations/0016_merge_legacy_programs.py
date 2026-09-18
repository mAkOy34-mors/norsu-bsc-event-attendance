# Merge legacy "major encoded as a program" rows into the proper
# College -> Program -> Major hierarchy, then remove the leftovers.
#
# Migration 0009 seeded programs like "Agronomy", "Automotive", or
# "BSED-Math" -- at the time the major lived inside the program code. The
# seed_colleges management command later introduced the real hierarchy
# (BSA under CAF, BIT with majors AUTO/CT/EE, BSED with majors ENG/MATH/SCI),
# so both datasets existed side by side and the manage pages showed every
# program twice (e.g. CIT listing BIT *and* Automotive/Com. Tech/Electrical).
#
# The merge logic lives in qrapp/legacy_program_merge.py so the seed command
# can reuse the exact same behavior.

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
        ("qrapp", "0015_alter_major_unique_together"),
    ]

    operations = [
        migrations.RunPython(merge_legacy, migrations.RunPython.noop),
    ]
