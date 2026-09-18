"""PDF export of the Import Issues review list.

One row per issue (and per stored Student when a number was printed for two
different students, so both appear), labelled with the student number -- or
``no_id`` when the roster carried none -- plus year level, program, and major
(only when the program actually has one). Optionally filtered by college.
"""

import io
import re
from datetime import date

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import ImportIssue, Student

NO_ID_LABEL = "no_id"
_STORED_STUDENT_LABEL = "Shared number (stored student)"

_DUP_SUFFIX = re.compile(r"^(.*?)-DUP\d+$", re.IGNORECASE)

HEADERS = ["Student ID", "Name", "Year Level", "Program", "Major", "Issue"]


def _issue_label(issue):
    """Short tag shown in the Issue column."""
    labels = dict(ImportIssue.ISSUE_CHOICES)
    return labels.get(issue.issue_type, issue.issue_type)


def _resolve_major(issue):
    """Major of the stored student this issue refers to, when we can find one.

    ``ImportIssue`` does not store the major, but generated-number students
    (NOID-... / -DUP2) exist in the roster, so match by number first and by
    name as a fallback.
    """
    if issue.student_id:
        student = Student.objects.filter(student_id=issue.student_id).first()
        if student:
            return student.major_name
    if issue.name:
        student = Student.objects.filter(name__iexact=issue.name).first()
        if student:
            return student.major_name
    return ""


def _program_major(issue):
    """(program_code, major) -- major only when the program has majors."""
    if not issue.program_id:
        return "", ""
    program_code = issue.program.code
    major = _resolve_major(issue)
    if major:
        return program_code, major
    # Only report the major column for programs that actually offer majors.
    has_majors = issue.program.majors.filter(is_active=True).exists()
    return program_code, major if has_majors else ""


def _number_base(number):
    """``202500656`` for ``202500656`` and ``202500656-DUP2`` alike.

    Shared-number issues may carry the generated variant as printed
    (``-DUP2``); the students sharing the number are matched on the base.
    """
    match = _DUP_SUFFIX.match(number or "")
    return match.group(1) if match else (number or "")


def _shared_number_students(issue):
    """Every stored student holding the number this issue flags.

    A shared-number issue means the registrar printed one number for two
    different students; the export must show both, not just the flagged row.
    Matching is on the base number, so a ``-DUP2`` issue also surfaces the
    student holding the original number -- in any college.
    """
    if issue.issue_type != ImportIssue.SHARED_NUMBER or not issue.student_id:
        return []
    base = _number_base(issue.student_id)
    if not base:
        return []
    return list(
        Student.objects.filter(student_id__startswith=base)
        .order_by("student_id")
    )

def _student_row(student_id_label, name, year, program_code, major, issue_label):
    return [student_id_label, name or "", year or "", program_code or "", major or "", issue_label]


def _college_code(issue):
    """College code of the program an issue was recorded under."""
    return issue.program.college.code if issue.program_id and issue.program.college_id else ""


def build_issue_rows(college_code=""):
    """Table rows for the review list, optionally filtered by one college.

    One row per issue; every stored student holding a flagged shared number
    gets a row too (matched on the base number, so a ``-DUP2`` issue also
    surfaces the original holder). Each student is emitted only once per
    table, identified by base number + name.

    With a college filter the export covers everything involving that
    college: its own issues, plus any number flagged on *another* college's
    roster when one of its own students holds that number. So a CAS student
    and a CCJE student printed with the same number are exported by both
    CAS and CCJE, each export showing both students.
    """
    wanted = college_code.lower() if college_code else ""
    issues = (
        ImportIssue.objects.select_related("program", "program__college")
        .order_by("issue_type", "name", "student_id")
    )

    rows = []
    seen = set()
    for issue in issues:
        issue_college = _college_code(issue)
        holders = _shared_number_students(issue)

        if wanted:
            issue_own = issue_college.lower() == wanted
            # Cross-college shared numbers: another college's flagged issue
            # still belongs here when one of this college's students holds
            # the number (or vice versa).
            holder_here = any(
                s.college_id and s.college.code.lower() == wanted
                for s in holders
            )
            if not issue_own and not holder_here:
                continue

        program_code, major = _program_major(issue)
        seen.add((_number_base(issue.student_id or NO_ID_LABEL),
                  (issue.name or "").upper()))
        rows.append(_student_row(
            issue.student_id or NO_ID_LABEL,
            issue.name,
            issue.year,
            program_code,
            major,
            _issue_label(issue),
        ))

        for student in holders:
            key = (_number_base(student.student_id), (student.name or "").upper())
            if key in seen:
                continue
            seen.add(key)
            rows.append(_shared_row(student, program_code, issue_college))
    return rows


def _shared_row(student, issue_program_code, issue_college_code):
    """Row for a stored student holding a flagged shared number.

    Program/major come from the student's own record; the issue's program is
    only the fallback when the student carries none. When the student belongs
    to a different college than the flagged roster, the label says which
    roster flagged the number.
    """
    label = _STORED_STUDENT_LABEL
    if (issue_college_code and student.college_id
            and student.college.code.upper() != issue_college_code.upper()):
        label = f"Shared number (from {issue_college_code.upper()} roster)"
    return _student_row(
        student.student_id,
        student.name,
        student.year,
        student.program.code if student.program_id else issue_program_code,
        student.major_name or "",
        label,
    )



def export_import_issues_pdf(college_code=""):
    """PDF of the import-issue review list, optionally one college, or (None, error)."""
    if not ImportIssue.objects.exists():
        return None, "There are no import issues to export."

    rows = build_issue_rows(college_code)
    if not rows:
        return None, "There are no import issues to export."

    college_note = f"College: {college_code.upper()} | " if college_code else ""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="Import Issues",
    )

    styles = getSampleStyleSheet()
    story = [
        Paragraph("Import Issues - Students for Review", styles["Title"]),
        Paragraph(
            f"Exported {date.today().strftime('%B %d, %Y')} | {college_note}"
            f"{len(rows)} row(s). "
            "'no_id' means the roster file had no student number for that row.",
            styles["Normal"],
        ),
        Spacer(1, 6 * mm),
    ]

    table = Table([HEADERS] + rows, repeatRows=1, colWidths=[
        42 * mm,  # Student ID (fits NOID-... and -DUP2 variants)
        78 * mm,  # Name
        20 * mm,  # Year Level
        28 * mm,  # Program
        55 * mm,  # Major
        65 * mm,  # Issue
    ])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f7")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)

    doc.build(story)
    buffer.seek(0)

    parts = ["import_issues"]
    if college_code:
        parts.append("".join(ch if ch.isalnum() else "_" for ch in college_code).lower())
    parts.append(date.today().isoformat())
    filename = "_".join(parts) + ".pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response, None

