# -----------------------------------------------------------------------------
# INTEGRATION:
# Place this file at:
#   <your_app>/management/commands/seed_colleges.py
# (create "management" and "management/commands" folders if they don't exist
# yet — each needs an empty __init__.py file alongside the command)
#
# Then replace "yourapp" below with the name of the app where your
# College / Program / Major models actually live, and run:
#   python manage.py seed_colleges
#
# Safe to re-run: existing rows are updated, not duplicated.
# -----------------------------------------------------------------------------

from django.core.management.base import BaseCommand
from django.db import transaction

from qrapp.models import College, Program, Major, Student


# Each program entry: (code, name, [ (major_code, major_name), ... ])
# An empty majors list means the program has no majors.
COLLEGES_DATA = [
    {
        "code": "CAF",
        "name": "College of Agriculture and Forestry",
        "programs": [
            ("BSA", "Bachelor of Science in Agronomy", []),
            ("BSAS", "Bachelor of Science in Animal Science", []),
            ("BSF", "Bachelor of Science in Forestry", []),
        ],
    },
    {
        "code": "CAS",
        "name": "College of Arts and Sciences",
        "programs": [
            ("BSCS", "Bachelor of Science in Computer Science", []),
            ("BSINT", "Bachelor of Science in Information Technology", []),
        ],
    },
    {
        "code": "CBA",
        "name": "College of Business Administration",
        "programs": [
            ("BSBA", "Bachelor of Science in Business Administration", [
                ("HRM", "Human Resource Management"),
            ]),
            ("BSHM", "Bachelor of Science in Hospitality Management", []),
            ("BSOA", "Bachelor of Science in Office Administration", []),
        ],
    },
    {
        "code": "CCJE",
        "name": "College of Criminal Justice Education",
        "programs": [
            ("BSCRIM", "Bachelor of Science in Criminology", []),
        ],
    },
    {
        "code": "CIT",
        "name": "College of Industrial Technology",
        "programs": [
            ("BSIT", "Bachelor of Science in Industrial Technology", [
                ("AUTO", "Automotive Technology"),
                ("CT", "Computer Technology"),
                ("EE", "Electrical Technology"),
                ("ELX", "Electronics Technology"),
            ]),
        ],
    },
    {
        "code": "CTED",
        "name": "College of Teacher Education",
        "programs": [
            ("BEED", "Bachelor of Elementary Education", [
                ("GEN", "General Curriculum"),
            ]),
            ("BSED", "Bachelor of Secondary Education", [
                ("ENG", "English"),
                ("MATH", "Mathematics"),
                ("SCI", "Science"),
            ]),
        ],
    },
]


class Command(BaseCommand):
    help = "Seed (or update) Colleges, Programs, and Majors from a fixed reference list."

    @transaction.atomic
    def handle(self, *args, **options):
        college_count = program_count = major_count = 0

        # Fold any legacy "major encoded as a program" rows (Agronomy,
        # Automotive, BSED-Math, ...) into the real hierarchy before seeding,
        # so old databases don't end up with duplicate program listings.
        from qrapp.legacy_program_merge import merge_legacy_programs
        report = merge_legacy_programs(College, Program, Major, Student)
        if report["removed_programs"] or report.get("removed_majors"):
            self.stdout.write(
                self.style.WARNING(
                    f"Merged legacy entries: {report['removed_programs']} programs removed "
                    f"({report['moved_students']} students re-pointed), "
                    f"{report.get('removed_majors', 0)} duplicate majors removed."
                )
            )

        # Repair CAF rows where imports stored program names ("Agronomy",
        # "Animal Science") as majors under BSA -- CAF programs have no majors.
        from qrapp.caf_program_fix import fix_caf_program_majors
        caf_report = fix_caf_program_majors(College, Program, Major, Student)
        if caf_report["removed_majors"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Fixed CAF program/major mix-ups: {caf_report['moved_students']} students "
                    f"moved to their real program, {caf_report['cleared_students']} majors cleared, "
                    f"{caf_report['removed_majors']} bogus majors removed."
                )
            )

        for entry in COLLEGES_DATA:
            college, created = College.objects.update_or_create(
                code=entry["code"],
                defaults={"name": entry["name"], "is_active": True},
            )
            college_count += 1
            self.stdout.write(
                self.style.SUCCESS(f"{'Created' if created else 'Updated'} college: {college}")
            )

            for prog_code, prog_name, majors in entry["programs"]:
                program, created = Program.objects.update_or_create(
                    college=college,
                    code=prog_code,
                    defaults={"name": prog_name, "is_active": True},
                )
                program_count += 1
                self.stdout.write(f"  {'Created' if created else 'Updated'} program: {program}")

                for major_code, major_name in majors:
                    major, created = Major.objects.update_or_create(
                        program=program,
                        code=major_code,
                        defaults={"name": major_name, "is_active": True},
                    )
                    major_count += 1
                    self.stdout.write(f"    {'Created' if created else 'Updated'} major: {major}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {college_count} colleges, {program_count} programs, "
                f"{major_count} majors seeded."
            )
        )