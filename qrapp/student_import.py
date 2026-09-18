from .models import ImportIssue

import csv
import os
import re

import pdfplumber

HEADER_ALIASES = {
    "student_id": [
        "student id",
        "student no id",
        "student no",
        "id number",
        "id",
        "student_id",
        "student number",
        "id no",
    ],
    "name": ["name", "full name", "student name"],
    "sex": ["sex", "gender"],
    "college": ["college"],
    "program": ["program", "course"],
    "year": ["year", "year level", "yr", "yearlevel"],
    "major": ["major", "section", "sec"],
}

SKIP_ROW_MARKERS = {"student id", "id number", "generated", "id no", "student no"}

# Tokens the registrar uses for the Sex column. A trailing single M/F is only
# trusted as Sex when it follows a dotted middle initial -- see
# _parse_course_anchored for why.
SEX_TOKENS = {"m", "f", "male", "female"}
SPELLED_SEX_TOKENS = {"male", "female"}

# Some roster rows carry no student number at all: the registrar prints an em
# dash (2026-2027 BSBA export) or leaves the cell empty. Such rows are kept
# with this prefix instead of being thrown away, so the import can report them
# by name -- and, when the operator opts in, store them under a generated id.
MISSING_ID_PREFIX = "NOID-"
ID_PLACEHOLDERS = {
    "-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\ufffd",
    ".", "n/a", "na", "none", "no id", "no number",
}


def safe_strip(value, default=""):
    if value is None:
        return default
    value_str = str(value).strip()
    return value_str if value_str else default


def normalize_header(value):
    normalized = safe_strip(value).lower().replace("_", " ")
    normalized = re.sub(r"[./]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def is_header_row(row):
    if not row:
        return False
    first = normalize_header(row[0])
    if first in SKIP_ROW_MARKERS:
        return True
    normalized = [normalize_header(cell) for cell in row if safe_strip(cell)]
    for aliases in HEADER_ALIASES.values():
        if any(alias in normalized for alias in aliases):
            return True
    return False


def build_column_map(header_row):
    column_map = {}
    for index, cell in enumerate(header_row):
        header = normalize_header(cell)
        if not header:
            continue
        for field, aliases in HEADER_ALIASES.items():
            if header in aliases and field not in column_map:
                column_map[field] = index
                break
    return column_map


def parse_student_row(row, column_map=None, fallback_number=None):
    """Parse one spreadsheet/PDF row into a student dict.

    ``fallback_number`` is the row's position in the file. It is only used when
    the row carries no student number at all: the row is kept with a
    ``NOID-<position>`` stand-in so the import can report it by name (and store
    it under a generated id when the operator opts in) instead of silently
    dropping the student.
    """
    if not row:
        return None

    first_cell = normalize_header(row[0])
    if first_cell in SKIP_ROW_MARKERS:
        return None

    if column_map:
        def get_field(field, default=""):
            index = column_map.get(field)
            if index is None or index >= len(row):
                return default
            return safe_strip(row[index], default)

        student_id = get_field("student_id", "0")
        name = get_field("name")
        sex = get_field("sex")
        college = get_field("college", "CAS")
        program = get_field("program")
        year = get_field("year", "0")
        major = get_field("major", "NA")
    else:
        # The first column is a spreadsheet row number, not the student ID.
        student_id = safe_strip(row[1], "0") if len(row) > 1 else "0"
        name = safe_strip(row[2]) if len(row) > 2 else ""
        sex = safe_strip(row[3]) if len(row) > 3 else ""
        college = "CAS"
        program = safe_strip(row[4]) if len(row) > 4 else ""
        year = safe_strip(row[5], "0") if len(row) > 5 else "0"
        major = safe_strip(row[6], "NA") if len(row) > 6 else "NA"

    if name in ("NA", ""):
        return None

    if not is_missing_student_number(student_id) and (
        student_id in ("0", "NA", "") or _is_id_placeholder(student_id)
    ):
        # No student number at all: keep the row under a positional stand-in.
        # The PDF parser records the printed line number itself ("NOID-202");
        # spreadsheets fall back to the row's position in the file.
        if fallback_number is None:
            return None
        student_id = f"{MISSING_ID_PREFIX}{fallback_number}"

    try:
        year_int = int(float(str(year).replace(",", "")))
    except (ValueError, TypeError):
        year_int = 0

    return {
        "student_id": student_id,
        "name": name,
        "sex": sex,
        "college": college,
        "program": program,
        "year": year_int,
        "major": major,
    }


def _generated_student_id(program, year, position):
    """Stand-in number for a roster row that has none, e.g. ``NOID-BSBA-3-202``.

    The program, year level and the row's position in the file keep the
    generated id traceable back to the registrar's document.
    """
    code = re.sub(r"[^A-Za-z0-9]", "", str(getattr(program, "code", "") or "")).upper()[:6]
    return f"{MISSING_ID_PREFIX}{code or 'NA'}-{year}-{position}"


def _unique_generated_id(Student, program, year, position):
    base = _generated_student_id(program, year, position)
    for suffix in range(1, 10):
        candidate = base if suffix == 1 else f"{base}-{suffix}"
        if len(candidate) > 20:
            return None
        if not Student.objects.filter(student_id=candidate).exists():
            return candidate
    return None


def _unique_variant_id(Student, base):
    """``202600303-DUP2`` for a second student printed with the same number."""
    for suffix in range(2, 10):
        candidate = f"{base}-DUP{suffix}"
        if len(candidate) > 20:
            return None
        if not Student.objects.filter(student_id=candidate).exists():
            return candidate
    return None


def _already_stored_generated(Student, base, name):
    """True when this roster row was already stored under a generated id.

    Keeps re-importing the same file from creating a second record for a
    student whose number is missing (``NOID-BSBA-3-202``/``-2``) or shared
    (``202600303-DUP2``).
    """
    return Student.objects.filter(
        student_id__regex=rf"^{re.escape(base)}(-DUP\d+|-\d+)?$",
        name__iexact=name,
    ).exists()


def import_students_from_rows(rows, column_map=None, stats=None, allow_generated_ids=False):
    """Create students from parsed spreadsheet/PDF rows.

    Returns ``(created, skipped)``. When ``stats`` (a dict) is supplied it is
    filled with the breakdown of the skipped rows, so callers can tell the
    user *why* a roster came in short instead of only how many rows were
    skipped: rows that could not be read at all vs. students that already
    exist (``student_id`` is unique across the whole roster).

    ``allow_generated_ids`` stores the rows the roster cannot identify -- no
    student number at all (``NOID-BSBA-3-202``) or a number already used by a
    different student (``202600303-DUP2``) -- instead of skipping them. It is
    opt-in because the registrar's number is the key behind every QR code.
    """
    from .college_program import resolve_college_and_program, resolve_program_and_major
    from .models import ImportIssue, Student

    created = 0
    skipped = 0
    unreadable = 0
    already_exists = 0
    conflicts = []
    missing_numbers = []
    generated_ids = []
    conflict_skipped = 0
    missing_skipped = 0
    duplicate_ids = set()
    # Numbers printed more than once *within this file* (used to flag true
    # duplicate listings; meeting the stored roster is not an issue).
    seen_numbers = set() if stats is not None else None
    # Rows the roster cannot identify, recorded for the "Import Issues"
    # review page so nothing gets lost in a console warning.
    issues = []

    for position, row in enumerate(rows, start=1):
        data = parse_student_row(row, column_map, fallback_number=position)
        if not data:
            skipped += 1
            unreadable += 1
            issues.append({
                "issue_type": ImportIssue.UNREADABLE_ROW,
                "name": "",
                "student_id": "",
                "program": None,
                "year": None,
                "detail": _row_preview(row),
            })
            continue

        college, program = resolve_college_and_program(data["college"], data["program"])
        # A major column that actually names a program ("BSA / Animal
        # Science") resolves to that program with no major, never a major.
        program, major = resolve_program_and_major(college, program, data["major"], create=True)
        # The program is resolved by CODE first (registrar's canonical list),
        # so the program's own college is authoritative. Overriding the
        # sheet's college column keeps BSIT students under CIT even when the
        # sheet says CAS, and prevents college/program mismatches.
        if program is not None:
            college = program.college

        student_id = data["student_id"]
        generated = False
        # Detail pieces kept for the import-issue report below.
        missing_line_number = None
        shared_with_name = None
        if seen_numbers is not None:
            if student_id in seen_numbers:
                duplicate_ids.add(student_id)
            else:
                seen_numbers.add(student_id)

        if is_missing_student_number(student_id):
            # "202 - BARIDO, MERFE BSBA 3 HRM": no student number in the file.
            line_number = student_id[len(MISSING_ID_PREFIX):]
            missing_numbers.append({"position": line_number, "name": data["name"]})
            missing_line_number = line_number
            if not allow_generated_ids:
                skipped += 1
                missing_skipped += 1
                issues.append(_issue_dict(
                    ImportIssue.MISSING_NUMBER, data, program,
                    detail=f"Roster line {line_number}: no student number in the file (not stored)",
                ))
                continue
            base_id = _generated_student_id(program, data["year"], line_number)
            if _already_stored_generated(Student, base_id, data["name"]):
                skipped += 1
                already_exists += 1
                continue
            student_id = _unique_generated_id(Student, program, data["year"], line_number)
            if student_id is None:
                skipped += 1
                missing_skipped += 1
                issues.append(_issue_dict(
                    ImportIssue.MISSING_NUMBER, data, program,
                    detail=f"Roster line {line_number}: no student number in the file (not stored)",
                ))
                continue
            generated = True
        else:
            existing = Student.objects.filter(student_id=student_id).first()
            if existing is not None:
                duplicate_ids.add(student_id)
                printed_number = student_id
                same_person = (
                    (existing.name or "").strip().lower() == (data["name"] or "").strip().lower()
                )
                if same_person:
                    # The registrar listed the same student twice.
                    skipped += 1
                    already_exists += 1
                    if seen_numbers is not None and student_id in seen_numbers:
                        # Only a duplicate listing inside this file is an
                        # import issue; the same student meeting the stored
                        # roster on a re-import is normal idempotent behaviour.
                        issues.append(_issue_dict(
                            ImportIssue.DUPLICATE_ROW, data, program,
                            student_id=student_id,
                            detail=f"Student number {student_id} is listed more than once in the file",
                        ))
                    continue

                # Two students share one number: report it, and store the
                # second one under a variant id when the operator opted in.
                conflicts.append(
                    {"student_id": student_id, "existing": existing.name, "incoming": data["name"]}
                )
                shared_with_name = existing.name
                already_stored_variant = _already_stored_generated(
                    Student, student_id, data["name"]
                )
                if not allow_generated_ids or already_stored_variant:
                    skipped += 1
                    conflict_skipped += 1
                    if not already_stored_variant:
                        # The variant's "stored as ..." note was recorded when
                        # it was first stored; a re-import must not resurrect a
                        # "(not stored)" warning for that student.
                        issues.append(_issue_dict(
                            ImportIssue.SHARED_NUMBER, data, program,
                            student_id=printed_number,
                            detail=(
                                f"Number {printed_number} is also printed for "
                                f"'{shared_with_name}' (not stored)"
                            ),
                        ))
                    continue
                student_id = _unique_variant_id(Student, printed_number)
                if student_id is None:
                    skipped += 1
                    conflict_skipped += 1
                    issues.append(_issue_dict(
                        ImportIssue.SHARED_NUMBER, data, program,
                        student_id=printed_number,
                        detail=(
                            f"Number {printed_number} is also printed for "
                            f"'{shared_with_name}' (not stored)"
                        ),
                    ))
                    continue
                generated = True

        _, was_created = Student.objects.get_or_create(
            student_id=student_id,
            defaults={
                "name": data["name"],
                "sex": data["sex"],
                "college": college,
                "program": program,
                "year": data["year"],
                "major": major,
            },
        )
        if was_created:
            created += 1
            if generated:
                generated_ids.append(student_id)
            if missing_line_number is not None:
                # Stored under a generated number the registrar will not
                # recognise; flag it for review.
                issues.append(_issue_dict(
                    ImportIssue.GENERATED_NUMBER, data, program,
                    student_id=student_id,
                    detail=(
                        f"Roster line {missing_line_number}: no student number in "
                        f"the file -- stored as {student_id}"
                    ),
                ))
            elif shared_with_name is not None:
                issues.append(_issue_dict(
                    ImportIssue.SHARED_NUMBER, data, program,
                    student_id=student_id,
                    detail=(
                        f"Number {printed_number} is also printed for "
                        f"'{shared_with_name}' -- stored as {student_id}"
                    ),
                ))
        else:
            skipped += 1
            already_exists += 1

    if stats is not None:
        stats["created"] = created
        stats["skipped"] = skipped
        stats["unreadable"] = unreadable
        stats["already_exists"] = already_exists
        stats["conflict_count"] = len(conflicts)
        stats["conflict_skipped"] = conflict_skipped
        stats["conflicts"] = conflicts[:20]
        stats["missing_number_count"] = len(missing_numbers)
        stats["missing_number_skipped"] = missing_skipped
        stats["missing_numbers"] = missing_numbers[:20]
        stats["generated_id_count"] = len(generated_ids)
        stats["generated_ids"] = generated_ids[:20]
        stats["duplicate_id_count"] = len(duplicate_ids)
        stats["duplicate_ids"] = sorted(duplicate_ids)[:20]
        stats["issues"] = issues

    return created, skipped


def _issue_dict(issue_type, data, program, student_id="", detail=""):
    """Build one import-issue record for the review page."""
    return {
        "issue_type": issue_type,
        "name": data.get("name", ""),
        "student_id": student_id,
        "program": program,
        "year": data.get("year"),
        "detail": detail,
    }


def _row_preview(row, limit=200):
    """Summarize a row that could not be parsed, for the review page."""
    if isinstance(row, str):
        text = row
    else:
        try:
            text = " ".join(str(cell) for cell in row if str(cell).strip())
        except Exception:
            text = str(row)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "Unreadable row (no text found)"


def _persist_import_issues(issues, source_file=""):
    """Store the import-issue report so staff can review these students.

    Uses ``get_or_create`` on the issue content so importing the same roster
    again does not duplicate the review list.
    """
    if not issues:
        return
    from .models import ImportIssue

    for issue in issues:
        ImportIssue.objects.get_or_create(
            issue_type=issue["issue_type"],
            name=issue["name"],
            student_id=issue["student_id"],
            detail=issue["detail"],
            defaults={
                "program": issue["program"],
                "year": issue["year"],
                "source_file": source_file or "",
            },
        )


_COURSE_CODE_MEMO = None


def _known_course_codes():
    """Memoized set of Program codes, used to anchor PDF roster rows on the
    course token instead of a sex token (registrar exports often have a
    blank Sex column)."""
    global _COURSE_CODE_MEMO
    if _COURSE_CODE_MEMO is None:
        from .models import Program

        _COURSE_CODE_MEMO = {
            str(code).strip().upper()
            for code in Program.objects.values_list("code", flat=True)
        }
    return _COURSE_CODE_MEMO


def _is_student_id_token(token):
    """Registrar student numbers look like 202600297 or 2025-0001: a token
    of at least 5 characters, starting and ending with a digit, containing
    only digits, spaces, and hyphens. Row numbers (1-10) never match."""
    return re.fullmatch(r"\d[\d -]{3,}\d", token) is not None


def is_missing_student_number(student_id):
    """True for the stand-in id given to a row that has no student number."""
    return str(student_id).startswith(MISSING_ID_PREFIX)


def _is_row_number(token):
    """A printed roster line number (1-4 digits) -- never a student number."""
    return re.fullmatch(r"\d{1,4}", token.strip()) is not None


def _is_id_placeholder(token):
    """True for the dash the registrar prints where the Student No. should be
    ("202 - BARIDO, MERFE BSBA 3 HRM")."""
    stripped = token.strip()
    if not stripped:
        return False
    if stripped.lower() in ID_PLACEHOLDERS:
        return True
    # Any other short symbol (an unmapped dash glyph, for instance).
    return len(stripped) <= 2 and not any(char.isalnum() for char in stripped)


def _looks_like_course(token, following):
    """True when a token is a known program code, or (structural fallback) a
    short uppercase code immediately followed by a numeric year level."""
    t = token.upper()
    if t in _known_course_codes():
        return True
    if re.fullmatch(r"[A-Z]{2,6}", t) and following:
        return re.fullmatch(r"[1-6]", following[0]) is not None
    return False


def _parse_course_anchored(tokens):
    """Anchor roster rows on the course code:

        [No.] StudentNo Name... COURSE Year [Major]

    The name may contain single-letter middle initials and the registrar's
    Sex column is frequently blank, so row location by an M/F token is
    unreliable; splitting around the known course code handles both styles.

    Rows whose Student No. cell holds a dash keep the printed line number as a
    ``NOID-<line>`` stand-in instead of being discarded.
    """
    id_index = None
    name_start = 1
    missing_number = None
    if _is_student_id_token(tokens[0]):
        id_index = 0
    elif len(tokens) > 1 and tokens[0].isdigit() and _is_student_id_token(tokens[1]):
        id_index = 1
        name_start = 2
    elif len(tokens) > 2 and _is_row_number(tokens[0]) and _is_id_placeholder(tokens[1]):
        name_start = 2
        missing_number = tokens[0]
    else:
        return None

    course_index = next(
        (
            index
            for index in range(name_start + 1, len(tokens))
            if _looks_like_course(tokens[index], tokens[index + 1:])
        ),
        None,
    )
    if course_index is None:
        return None

    year_index = next(
        (
            index
            for index in range(course_index + 1, len(tokens))
            if re.fullmatch(r"[1-6]", tokens[index])
        ),
        None,
    )
    if year_index is None:
        return None

    # The Sex value sits between the name and the course code, but several
    # rosters (the BSOA/BSHM/BSBA/CCJE exports) leave the Sex column blank and
    # print the middle initial there instead: "DUMAGO, NASH ADRIEL F BSOA 4".
    # Only take the trailing token as Sex when it is an unambiguous
    # "Male"/"Female", or a single M/F that follows a dotted middle initial
    # ("CASIPONG,JHONREY C. M BSCS 2") -- the shape of every row in the rosters
    # that really do carry a Sex column. Otherwise the letter stays part of
    # the name, so no student is dropped or mislabelled.
    name_tokens = tokens[name_start:course_index]
    sex = ""
    if name_tokens and name_tokens[-1].lower() in SEX_TOKENS:
        trailing = name_tokens[-1]
        follows_dotted_initial = len(name_tokens) > 1 and name_tokens[-2].endswith(".")
        if trailing.lower() in SPELLED_SEX_TOKENS or follows_dotted_initial:
            sex = trailing
            name_tokens = name_tokens[:-1]
    name = " ".join(name_tokens)
    if not name:
        return None

    return [
        tokens[id_index] if id_index is not None else f"{MISSING_ID_PREFIX}{missing_number}",
        name,
        sex,
        tokens[course_index],
        tokens[year_index],
        " ".join(tokens[year_index + 1:]),
    ]


def _parse_sex_anchored(tokens):
    """Legacy fallback: locate the row by an M/F sex token (used by older
    exports that do not include a recognizable course code)."""
    sex_index = next(
        (index for index, token in enumerate(tokens) if token.lower() in SEX_TOKENS),
        None,
    )
    if sex_index is None or sex_index < 2:
        return None

    year_index = next(
        (
            index
            for index in range(sex_index + 1, len(tokens))
            if re.fullmatch(r"\d{1,2}", tokens[index])
        ),
        None,
    )
    if year_index is None or year_index <= sex_index + 1:
        return None

    # A row number may precede the student ID in the requested PDF format.
    id_index = 1 if tokens[0].isdigit() and sex_index >= 3 else 0
    if id_index >= sex_index - 1:
        return None

    return [
        tokens[id_index],
        " ".join(tokens[id_index + 1:sex_index]),
        tokens[sex_index],
        " ".join(tokens[sex_index + 1:year_index]),
        tokens[year_index],
        " ".join(tokens[year_index + 1:]),
    ]


def parse_pdf_text_row(line):
    tokens = safe_strip(line).split()
    if len(tokens) < 5:
        return None

    row = _parse_course_anchored(tokens)
    if row is not None:
        return row

    return _parse_sex_anchored(tokens)


def looks_like_student_line(tokens):
    """True when a text line is a roster row rather than a title/footer.

    Used to report rows the parser could not read (e.g. a student whose
    Student No. cell is a dash) instead of dropping them silently.
    """
    if any(_is_student_id_token(token) for token in tokens):
        return True
    return any(
        _looks_like_course(token, tokens[index + 1:])
        for index, token in enumerate(tokens)
    )


def iter_pdf_rows(file_path, unreadable=None):
    """Return (rows, column_map) for a roster PDF.

    When a list is passed as ``unreadable``, roster-looking text lines that
    could not be turned into a row are appended to it so the caller can report
    them (the registrar's exports include rows with a dash instead of a
    Student No., which must not vanish without a trace).
    """
    rows = []
    column_map = None
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            if is_header_row(table[0]):
                if column_map is None:
                    column_map = build_column_map(table[0])
                rows.extend(table[1:])
            else:
                rows.extend(table)

        if rows:
            return rows, column_map

        text_column_map = {
            "student_id": 0,
            "name": 1,
            "sex": 2,
            "program": 3,
            "year": 4,
            "major": 5,
        }
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                tokens = line.split()
                if is_header_row(tokens) or normalize_header(line).startswith("generated"):
                    continue
                parsed_row = parse_pdf_text_row(line)
                if parsed_row:
                    rows.append(parsed_row)
                elif unreadable is not None and looks_like_student_line(tokens):
                    unreadable.append(line.strip())
        if rows:
            return rows, text_column_map
    return rows, column_map


def iter_csv_rows(file_path):
    rows = []
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(file_path, newline="", encoding=encoding) as handle:
                reader = csv.reader(handle)
                for row in reader:
                    if any(safe_strip(cell) for cell in row):
                        rows.append(row)
            break
        except UnicodeDecodeError:
            rows = []
            continue

    if not rows:
        raise ValueError("Could not read CSV file. Please save it as UTF-8.")

    column_map = None
    data_rows = rows
    if is_header_row(rows[0]):
        column_map = build_column_map(rows[0])
        data_rows = rows[1:]

    return data_rows, column_map


def iter_xlsx_rows(file_path):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ImportError("Excel support requires openpyxl. Run: pip install openpyxl") from exc

    workbook = load_workbook(file_path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = []
    for row in sheet.iter_rows(values_only=True):
        row_values = [cell if cell is not None else "" for cell in row]
        if any(safe_strip(cell) for cell in row_values):
            rows.append(row_values)
    workbook.close()

    if not rows:
        return [], None

    column_map = None
    data_rows = rows
    if is_header_row(rows[0]):
        column_map = build_column_map(rows[0])
        data_rows = rows[1:]

    return data_rows, column_map


def import_students_from_file(file_path, allow_generated_ids=False, source_file=""):
    """Import a roster file and remember the rows that need review.

    ``source_file`` is the original file name shown on the Import Issues
    review page (``file_path`` is usually a temporary upload path).
    """
    extension = os.path.splitext(file_path)[1].lower()
    unreadable_lines = []

    if extension == ".pdf":
        rows, column_map = iter_pdf_rows(file_path, unreadable=unreadable_lines)
        label = "PDF"
    elif extension == ".csv":
        rows, column_map = iter_csv_rows(file_path)
        label = "CSV"
    elif extension in (".xlsx", ".xlsm"):
        rows, column_map = iter_xlsx_rows(file_path)
        label = "Excel"
    else:
        raise ValueError("Unsupported file type. Use PDF, CSV, or Excel (.xlsx).")

    if not rows:
        raise ValueError(f"No student rows found in the {label} file.")

    stats = {}
    created, skipped = import_students_from_rows(
        rows, column_map, stats=stats, allow_generated_ids=allow_generated_ids
    )

    # Keep the rows the roster could not identify on the Import Issues
    # review page (the toast below disappears; this list stays).
    for line in unreadable_lines:
        stats["issues"].append({
            "issue_type": ImportIssue.UNREADABLE_ROW,
            "name": "",
            "student_id": "",
            "program": None,
            "year": None,
            "detail": " ".join(line.split())[:200],
        })
    _persist_import_issues(stats["issues"], source_file=source_file)

    unreadable_total = stats["unreadable"] + len(unreadable_lines)
    conflicts = stats["conflicts"]
    missing_numbers = stats["missing_numbers"]

    breakdown = []
    if stats["already_exists"]:
        breakdown.append(f"{stats['already_exists']} already in the roster")
    if stats["conflict_skipped"]:
        breakdown.append(f"{stats['conflict_skipped']} shared student numbers")
    if stats["missing_number_skipped"]:
        breakdown.append(f"{stats['missing_number_skipped']} rows with no student number")
    if unreadable_total:
        breakdown.append(f"{unreadable_total} unreadable")

    message = f"Added {created} students from {label}!"
    if breakdown:
        message += f" Skipped {skipped + len(unreadable_lines)} rows: {', '.join(breakdown)}."

    if stats["generated_id_count"]:
        shown = ", ".join(stats["generated_ids"][:4])
        if stats["generated_id_count"] > 4:
            shown += f" (+{stats['generated_id_count'] - 4} more)"
        message += (
            f" {stats['generated_id_count']} students were stored with generated"
            f" student numbers ({shown}) -- please review them."
        )
    elif conflicts:
        shown = "; ".join(
            f"{item['student_id']} = {item['existing']} vs {item['incoming']}"
            for item in conflicts[:3]
        )
        message += f" Conflicting numbers: {shown}"
        if len(conflicts) > 3:
            message += f" (+{len(conflicts) - 3} more)"
        message += "."
    elif missing_numbers:
        shown = "; ".join(f"line {item['position']} = {item['name']}" for item in missing_numbers[:3])
        message += f" Rows without a student number: {shown}"
        if len(missing_numbers) > 3:
            message += f" (+{len(missing_numbers) - 3} more)"
        message += "."

    if unreadable_lines:
        shown = " | ".join(unreadable_lines[:3])
        message += f" Unreadable rows: {shown}"
        if len(unreadable_lines) > 3:
            message += f" (+{len(unreadable_lines) - 3} more)"
        message += "."

    if stats["issues"]:
        message += " See Students → Import Issues for the full list."

    # The toast is small: log the full list so the details stay recoverable.
    for line in unreadable_lines:
        print(f"Import warning ({label}): unreadable roster row: {line}")
    for item in missing_numbers:
        print(
            f"Import warning ({label}): line {item['position']} has no student number: {item['name']}"
        )
    for item in conflicts:
        print(
            f"Import warning ({label}): student number {item['student_id']} is used "
            f"for '{item['existing']}' and '{item['incoming']}'"
        )
    for student_id in stats["generated_ids"]:
        print(f"Import note ({label}): generated student number {student_id}")

    return {
        "created": created,
        "skipped": skipped,
        "label": label,
        "already_exists": stats["already_exists"],
        "conflicts": conflicts,
        "conflict_count": stats["conflict_count"],
        "conflict_skipped": stats["conflict_skipped"],
        "missing_numbers": missing_numbers,
        "missing_number_count": stats["missing_number_count"],
        "missing_number_skipped": stats["missing_number_skipped"],
        "generated_ids": stats["generated_ids"],
        "generated_id_count": stats["generated_id_count"],
        "unreadable": unreadable_total,
        "unreadable_rows": unreadable_lines[:20],
        "duplicate_ids": stats["duplicate_ids"],
        "message": message,
    }
