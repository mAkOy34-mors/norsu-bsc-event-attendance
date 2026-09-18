"""
Merge legacy "major encoded as a program" rows into the proper
College -> Program -> Major hierarchy.

History: data migration 0009 seeded programs like "Agronomy", "Automotive",
or "BSED-Math" -- from an era when the major lived inside the program code.
The ``seed_colleges`` command later introduced the real hierarchy (BSA under
CAF, BIT with majors AUTO/CT/EE, BSED with majors ENG/MATH/SCI), so both
datasets existed side by side and the manage-pages showed duplicates.

This module is shared by:
  * data migration 0016 (passes historical models from apps.get_model)
  * the seed_colleges command (passes the real models)

It is idempotent: once merged, no legacy rows remain and re-running is a
no-op.
"""

# Canonical program codes per the registrar's list. Historical databases may
# hold the same degree under different codes (BIT vs BSIT) or under the wrong
# college (a stray "BSIT" row under CAS created by imports). These are merged
# into the canonical row so every college shows exactly one program per code.
CANONICAL_PROGRAM_CODES = [
    # (college, canonical code, historical aliases) -- aliases may be codes or
    # names, and may live under any college.
    {"college": "CIT", "canonical": "BSIT", "aliases": ["BIT"]},
    {"college": "CAS", "canonical": "BSINT", "aliases": []},
]

LEGACY_PROGRAM_MAP = [
    # (college, legacy program codes/names) -> target program + optional major
    {
        "college": "CAF",
        "legacy_codes": ["Agronomy"],
        "legacy_names": ["Agronomy"],
        "target": ("BSA", "Bachelor of Science in Agronomy"),
        "major": None,
    },
    {
        "college": "CAF",
        "legacy_codes": ["Forestry"],
        "legacy_names": ["Forestry"],
        "target": ("BSF", "Bachelor of Science in Forestry"),
        "major": None,
    },
    {
        "college": "CAF",
        "legacy_codes": ["Animal Science"],
        "legacy_names": ["Animal Science"],
        "target": ("BSAS", "Bachelor of Science in Animal Science"),
        "major": None,
    },
    {
        "college": "CIT",
        "legacy_codes": ["Com. Tech"],
        "legacy_names": ["Computer Technology"],
        "target": ("BSIT", "Bachelor of Science in Industrial Technology"),
        "major": ("CT", "Computer Technology"),
    },
    {
        "college": "CIT",
        "legacy_codes": ["Automotive"],
        "legacy_names": ["Automotive Technology", "Automotive"],
        "target": ("BSIT", "Bachelor of Science in Industrial Technology"),
        "major": ("AUTO", "Automotive Technology"),
    },
    {
        "college": "CIT",
        "legacy_codes": ["Electrical"],
        "legacy_names": ["Electrical Technology", "Electrical"],
        "target": ("BSIT", "Bachelor of Science in Industrial Technology"),
        "major": ("EE", "Electrical Technology"),
    },
    {
        "college": "CIT",
        "legacy_codes": ["Electronics"],
        "legacy_names": ["Electronics Technology", "Electronics"],
        "target": ("BSIT", "Bachelor of Science in Industrial Technology"),
        "major": ("ELX", "Electronics Technology"),
    },
    {
        "college": "CTED",
        "legacy_codes": ["BSED-Math"],
        "legacy_names": ["Bachelor of Secondary Education (Major in Math)"],
        "target": ("BSED", "Bachelor of Secondary Education"),
        "major": ("MATH", "Mathematics"),
    },
    {
        "college": "CTED",
        "legacy_codes": ["BSED-Science"],
        "legacy_names": ["Bachelor of Secondary Education (Major in Science)"],
        "target": ("BSED", "Bachelor of Secondary Education"),
        "major": ("SCI", "Science"),
    },
]


def _find_legacy_programs(Program, college, entry):
    """Programs under ``college`` matching the legacy codes/names."""
    from django.db.models import Q
    from functools import reduce

    query = reduce(
        lambda a, b: a | b,
        [Q(code__iexact=c) for c in entry["legacy_codes"]]
        + [Q(name__iexact=n) for n in entry["legacy_names"]],
    )
    return Program.objects.filter(college=college).filter(query)


def _get_or_create_target(Program, college, entry):
    code, name = entry["target"]
    target = Program.objects.filter(college=college, code__iexact=code).first()
    if target is None:
        target = Program.objects.create(college=college, code=code, name=name, is_active=True)
    return target


def _get_or_create_mapped_major(Major, target_program, major_entry):
    code, name = major_entry
    major = Major.objects.filter(program=target_program, code__iexact=code).first()
    if major is None:
        major = Major.objects.filter(program=target_program, name__iexact=name).first()
    if major is None:
        major = Major.objects.create(program=target_program, code=code, name=name, is_active=True)
    return major


def _merge_orphan_major(Major, Student, target_program, orphan):
    """
    Re-home a Major that was created under a legacy program (typically by
    migration 0014 promoting student major text). Merge into an equivalent
    major under the target when one exists; otherwise move it.
    """
    match = Major.objects.filter(program=target_program, code__iexact=orphan.code).first()
    if match is None:
        match = Major.objects.filter(program=target_program, name__iexact=orphan.name).first()
    if match is not None and match.pk != orphan.pk:
        Student.objects.filter(major=orphan).update(major=match)
        orphan.delete()
        return
    orphan.program = target_program
    orphan.save(update_fields=["program"])


def _find_equivalent_major(Major, target_program, major):
    """Find an equivalent major under ``target_program``: code, name, then
    normalized name (so "Automotive Technology" matches "Automotive")."""
    match = Major.objects.filter(program=target_program, code__iexact=major.code).first()
    if match is None:
        match = Major.objects.filter(program=target_program, name__iexact=major.name).first()
    if match is None and major.code:
        match = Major.objects.filter(program=target_program, name__iexact=major.code).first()
    if match is None:
        wanted = _normalized_major_name(major.name)
        for candidate in Major.objects.filter(program=target_program):
            if _normalized_major_name(candidate.name) == wanted:
                return candidate
    return match


def _merge_program_into(Program, Major, Student, source, target, mapped_major=None):
    """
    Move majors and students from ``source`` onto ``target`` and delete the
    source. Majors merge into equivalent ones under the target (matched by
    code, then name) or are re-homed. Students keep their major when it
    survives the move; those whose major maps elsewhere are re-assigned.
    Returns the number of students moved.
    """
    if source.pk == target.pk:
        return 0

    # Re-home majors under the source program.
    for orphan in source.majors.all():
        match = _find_equivalent_major(Major, target, orphan)
        if match is not None and match.pk != orphan.pk:
            Student.objects.filter(major=orphan).update(major=match)
            orphan.delete()
        else:
            orphan.program = target
            orphan.save(update_fields=["program"])

    moved = Student.objects.filter(program=source).update(
        program=target, college=target.college
    )
    if mapped_major is not None:
        # The source program itself represented a specialization: students
        # with no specific major adopt the mapped one.
        Student.objects.filter(program=target, major__isnull=True).update(
            major=mapped_major
        )
    source.delete()
    return moved


def merge_canonical_codes(College, Program, Major, Student):
    """
    Collapse historical code aliases into the canonical program rows
    (BIT -> CIT|BSIT; a stray BSIT row under CAS -> CIT|BSIT). Idempotent.
    """
    moved = 0
    for entry in CANONICAL_PROGRAM_CODES:
        college = College.objects.filter(code__iexact=entry["college"]).first()
        if college is None:
            continue
        target = Program.objects.filter(college=college, code__iexact=entry["canonical"]).first()
        if target is None:
            continue
        aliases = [entry["canonical"].lower(), entry["canonical"].upper()]
        aliases += entry["aliases"]
        from django.db.models import Q
        from functools import reduce
        query = reduce(lambda a, b: a | b, [Q(code__iexact=a) for a in aliases])
        sources = Program.objects.filter(query).exclude(pk=target.pk)
        for source in sources:
            moved += _merge_program_into(Program, Major, Student, source, target)
    return moved


def _normalized_major_name(name):
    """Normalize for matching: "Automotive Technology" ~ "Automotive" and
    "Sciences" ~ "Science" (drop the generic "Technology", ignore plurals)."""
    from qrapp.college_program import _singularize_word

    return " ".join(
        _singularize_word(word) for word in str(name).split()
        if word.lower() != "technology"
    ).strip()


def dedupe_majors(Major, Student):
    """
    Merge duplicate majors within the same program that differ only by the
    generic word "Technology" (e.g. AUTO/"Automotive" vs "Automotive
    Technology"). Students move to the canonical row -- the one with the
    shortest code (seed-created rows like AUTO/CT/EE win over long-named
    import rows). Returns the number of majors removed.
    """
    from collections import defaultdict

    removed = 0
    for program_id in Major.objects.values_list("program_id", flat=True).distinct():
        groups = defaultdict(list)
        for major in Major.objects.filter(program_id=program_id):
            key = _normalized_major_name(major.name) or major.code.lower()
            groups[key].append(major)

        for majors in groups.values():
            if len(majors) < 2:
                continue
            # Canonical row: shortest code (AUTO beats "Automotive Technology").
            majors.sort(key=lambda m: (len(m.code or ""), m.pk))
            keep = majors[0]
            for duplicate in majors[1:]:
                Student.objects.filter(major=duplicate).update(major=keep)
                duplicate.delete()
                removed += 1
    return removed


def merge_legacy_programs(College, Program, Major, Student):
    """
    Fold every legacy program row into its target Program (creating the
    target/major when missing), re-point students, and delete the leftovers.
    Returns a small report dict for logging.
    """
    moved_students = 0
    removed_programs = 0

    # 0. Canonical code aliases (BIT vs BSIT; stray BSIT under CAS).
    moved_students += merge_canonical_codes(College, Program, Major, Student)

    # Drop junk rows created by old imports where the "program" text merely
    # mirrored the college (e.g. code=CAS under college CAS). Only when the
    # program has no students and no majors.
    from django.db.models import Count, F
    junk = (
        Program.objects.annotate(n=Count("students"))
        .filter(code__iexact=F("college__code"), n=0, majors__isnull=True)
    )
    removed_programs += junk.count()
    junk.delete()

    for entry in LEGACY_PROGRAM_MAP:
        college = College.objects.filter(code__iexact=entry["college"]).first()
        if college is None:
            continue

        target = _get_or_create_target(Program, college, entry)
        mapped_major = (
            _get_or_create_mapped_major(Major, target, entry["major"])
            if entry["major"] else None
        )

        for legacy in _find_legacy_programs(Program, college, entry):
            if legacy.pk == target.pk:
                continue
            # Re-home majors that lived under the legacy program first.
            for orphan in legacy.majors.all():
                _merge_orphan_major(Major, Student, target, orphan)
            # Re-point its students (assigning the mapped major when the
            # legacy program itself represented a specialization).
            qs = Student.objects.filter(program=legacy)
            moved_students += qs.count()
            if mapped_major is not None:
                qs.update(program=target, major=mapped_major)
            else:
                qs.update(program=target)
            legacy.delete()
            removed_programs += 1

    # Final pass: collapse "X Technology" duplicates that earlier imports
    # created next to canonical seed majors (AUTO vs Automotive Technology).
    removed_majors = dedupe_majors(Major, Student)

    return {
        "moved_students": moved_students,
        "removed_programs": removed_programs,
        "removed_majors": removed_majors,
    }
