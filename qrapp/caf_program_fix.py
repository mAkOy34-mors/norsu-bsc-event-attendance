"""
Repair the CAF catalog after imports stored program names as majors.

History: the registrar's CAF rosters put the program in the major column
("BSA / Agronomy", "BSA / Animal Science"), so the import planted every CAF
student under BSA with a bogus major -- even though "Animal Science" is its
own program (BSAS) and "Agronomy" merely repeats BSA's own name. The resolver
now rejects those values (see college_program.find_program_in_major_label);
this module fixes the data such imports already stored.

Per the registrar's catalog, CAF programs have NO majors:
    BSA  - Bachelor of Science in Agronomy
    BSAS - Bachelor of Science in Animal Science
    BSF  - Bachelor of Science in Forestry

For every major under a CAF program whose label tails a CAF program's full
name, its students move to that program with no major (staying put when it
tails the program they are already under) and the major is deleted. Labels
that tail nothing are left for manual review.

This module is shared by:
  * data migration 0019 (passes historical models from apps.get_model)
  * the seed_colleges command (passes the real models)

It is idempotent: once fixed, no CAF tail-named majors remain.
"""

from .college_program import program_name_tail_matches


def fix_caf_program_majors(College, Program, Major, Student):
    """
    Re-home students held by bogus CAF majors and delete those majors.
    Returns a small report dict for logging.
    """
    moved_students = 0
    cleared_students = 0
    removed_majors = 0

    caf = College.objects.filter(code__iexact="CAF").first()
    if caf is None:
        return {"moved_students": 0, "cleared_students": 0, "removed_majors": 0}

    caf_programs = list(Program.objects.filter(college=caf))

    for major in list(Major.objects.filter(program__college=caf)):
        label = (major.name or major.code or "").strip()
        if not label:
            continue

        # Which CAF program does the label tail? ("Agronomy" -> BSA itself,
        # "Animal Science" -> BSAS.) Unambiguous single tail match only.
        tails = [p for p in caf_programs if program_name_tail_matches(p.name, label)]
        if len(tails) != 1:
            continue
        target = tails[0]

        if major.program_id == target.pk:
            # The major merely repeats its own program's name.
            cleared_students += Student.objects.filter(major=major).update(major=None)
        else:
            moved_students += Student.objects.filter(major=major).update(
                program=target, college=target.college, major=None,
            )
        major.delete()
        removed_majors += 1

    return {
        "moved_students": moved_students,
        "cleared_students": cleared_students,
        "removed_majors": removed_majors,
    }
