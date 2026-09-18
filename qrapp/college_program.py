"""Helpers to resolve/create College, Program, and Major from string codes."""

from .models import College, Program, Major


def normalize_code(value, fallback="NA", max_length=50):
    code = (str(value).strip() if value is not None else "") or fallback
    return code[:max_length]


def resolve_college(code_or_name, create=True):
    """
    Resolve a College from a code (preferred) or name.
    Creates an active college when missing if create=True.
    """
    raw = (str(code_or_name).strip() if code_or_name is not None else "") or "UNK"
    code = normalize_code(raw, fallback="UNK", max_length=20)

    college = College.objects.filter(code__iexact=code).first()
    if college:
        return college

    college = College.objects.filter(name__iexact=raw).first()
    if college:
        return college

    if not create:
        return None

    college, _ = College.objects.get_or_create(
        code=code,
        defaults={"name": raw, "is_active": True},
    )
    return college


def resolve_program(college, program_code_or_name, create=True):
    """
    Resolve a Program; create when missing if create=True.

    Program codes are unique across colleges in practice (the registrar's
    canonical list: BSINT -> CAS, BSIT -> CIT, ...), so the code is looked up
    GLOBALLY first. Only when no program with that code exists do we fall
    back to the college column of the spreadsheet -- this keeps imports from
    creating a stray "BSIT" under CAS when the sheet's college column is
    wrong or stale, and lets BSINT/BSIT land in their correct colleges
    regardless of what the import file says.
    """
    raw = (str(program_code_or_name).strip() if program_code_or_name is not None else "") or "NA"
    code = normalize_code(raw, fallback="NA", max_length=50)

    # 1. Program code is authoritative: search across all colleges.
    program = Program.objects.filter(code__iexact=code).first()
    if program:
        return program

    # 2. Fall back to the college context (code or full-name match).
    if college is not None:
        program = Program.objects.filter(college=college, code__iexact=code).first()
        if program:
            return program

        program = Program.objects.filter(college=college, name__iexact=raw).first()
        if program:
            return program

    if not create:
        return None

    # 3. New code: create under the given college (or nowhere without one).
    if college is None:
        return None

    program, _ = Program.objects.get_or_create(
        college=college,
        code=code,
        defaults={"name": raw, "is_active": True},
    )
    return program


def resolve_college_and_program(college_value, program_value, create=True):
    college = resolve_college(college_value, create=create)
    program = resolve_program(college, program_value, create=create) if college else None
    return college, program


def _singularize_word(word):
    """Crude singular form so "Sciences" and "Science" compare equal.
    Deterministic on both sides of the comparison, which is all fuzzy
    matching needs (Mathematics -> Mathematic consistently)."""
    low = word.lower()
    if len(low) > 3 and low.endswith("s") and not low.endswith("ss"):
        return low[:-1]
    return low


def _normalized_major_label(value):
    """Normalize a major label for fuzzy matching: drop the generic word
    "Technology", collapse whitespace, lowercase, ignore trailing plurals.
    "Automotive Technology", "Sciences" and "Automotive" / "Science" then
    compare equal."""
    words = [
        _singularize_word(w) for w in str(value).split()
        if w.lower() not in ("technology", "tech")
    ]
    return " ".join(words).strip()


def resolve_major(program, major_value, create=False):
    """
    Resolve a Major under a Program. Majors are optional: an empty value
    always returns None. Existing majors are matched by code, name, then a
    normalized name (so "Automotive Technology" matches canonical "AUTO" /
    "Automotive" instead of creating a duplicate); with create=True a still-
    missing major is created under the program.
    """
    if program is None:
        return None

    raw = (str(major_value).strip() if major_value is not None else "")
    if not raw or raw.upper() in ("NA", "N/A", "NONE"):
        return None

    code = normalize_code(raw, fallback="", max_length=50)

    major = Major.objects.filter(program=program, code__iexact=code).first()
    if major:
        return major

    major = Major.objects.filter(program=program, name__iexact=raw).first()
    if major:
        return major

    # Fuzzy match: "Automotive Technology" ~ "Automotive" (and vice versa).
    wanted = _normalized_major_label(raw)
    if wanted:
        for candidate in Major.objects.filter(program=program):
            if _normalized_major_label(candidate.name) == wanted:
                return candidate
            if candidate.code and _normalized_major_label(candidate.code) == wanted:
                return candidate

    if not create:
        return None

    major, _ = Major.objects.get_or_create(
        program=program,
        code=code,
        defaults={"name": raw, "is_active": True},
    )
    return major


def _label_words(value):
    """Singularized lowercase words of a label, for name comparisons."""
    return [_singularize_word(w) for w in str(value).split()]


def program_name_tail_matches(program_name, label):
    """
    True when a program's full name ends with ``label`` as whole words AND
    what remains is a degree prefix ("Bachelor of Science in"). This is how
    "Agronomy" tails "Bachelor of Science in Agronomy" (the program itself)
    while a bare "Science" does NOT tail "Bachelor of Science in Animal
    Science" (the remainder "... Animal" is not a degree prefix).
    """
    label_words = _label_words(label)
    if not label_words:
        return False
    name_words = _label_words(program_name)
    if len(label_words) >= len(name_words):
        return False
    return name_words[-len(label_words):] == label_words and name_words[-len(label_words) - 1] == "in"


def find_program_in_major_label(college, major_value, program_model=None):
    """
    Detect a "major" value that actually names a program.

    The registrar's CAF sheets repeat the program in the major column
    ("BSA / Agronomy") or name a sibling program ("BSA / Animal Science",
    which is its own program BSAS). Resolving those as majors planted every
    CAF student under BSA with a bogus major. A value identifies a program
    when it matches one by code, by full name, or as the distinctive tail of
    its full name (see program_name_tail_matches). Tail matches are only
    trusted when exactly one program matches. Scoped to ``college`` first,
    global second (mirroring resolve_program).
    """
    raw = (str(major_value).strip() if major_value is not None else "")
    if not raw or raw.upper() in ("NA", "N/A", "NONE"):
        return None

    program_model = program_model or Program

    # Exact code, then exact full name; college scope first, global second.
    for lookup in (
        dict(code__iexact=raw),
        dict(name__iexact=raw),
    ):
        if college is not None:
            program = program_model.objects.filter(college=college, **lookup).first()
            if program:
                return program
        program = program_model.objects.filter(**lookup).first()
        if program:
            return program

    # Distinctive tail of a program's full name, only when unambiguous.
    for candidates in (
        program_model.objects.filter(college=college) if college is not None else program_model.objects.none(),
        program_model.objects.all(),
    ):
        matches = [p for p in candidates if program_name_tail_matches(p.name, raw)]
        if len(matches) == 1:
            return matches[0]

    return None


def resolve_program_and_major(college, program, major_value, create=True):
    """
    Resolve one roster row's program + major, treating a major value that
    names a program (see find_program_in_major_label) as a program reference:
    the row resolves to that program with NO major. Returns (program, major).
    """
    if program is None:
        return program, None

    candidate = find_program_in_major_label(college, major_value)
    if candidate is not None:
        return candidate, None

    return program, resolve_major(program, major_value, create=create)
