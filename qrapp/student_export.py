import csv
import io
from datetime import date

from django.db.models import Q
from django.http import HttpResponse

from .models import Student

STUDENT_EXPORT_HEADERS = [
    "Student ID",
    "Name",
    "Gender",
    "College",
    "Program",
    "Year",
    "Major",
]


def filter_students_queryset(params):
    # select_related covers every FK the row/QR serializers touch
    # (college_code/program_code/major_name properties) — without "major"
    # each student's major_name access fired its own query (N+1).
    students = Student.objects.select_related("college", "program", "major").all()

    college = (params.get("college") or "").strip()
    program = (params.get("program") or "").strip()
    year = (params.get("year") or "").strip()
    major = (params.get("major") or "").strip()
    gender = (params.get("gender") or "").strip()
    search = (params.get("search") or "").strip()

    if college:
        students = students.filter(college__code=college)
    if program:
        students = students.filter(program__code=program)
    if year:
        students = students.filter(year=year)
    if major:
        students = students.filter(major__name__iexact=major)
    if gender:
        gender_upper = gender.upper()
        if gender_upper in ("M", "MALE"):
            students = students.filter(Q(sex__iexact="M") | Q(sex__icontains="male"))
        elif gender_upper in ("F", "FEMALE"):
            students = students.filter(Q(sex__iexact="F") | Q(sex__icontains="female"))
    if search:
        students = students.filter(
            Q(name__icontains=search)
            | Q(student_id__icontains=search)
            | Q(program__code__icontains=search)
            | Q(college__code__icontains=search)
        )

    return students.order_by("name", "student_id")


def student_row(student):
    return [
        student.student_id,
        student.name,
        student.sex,
        student.college_code,
        student.program_code,
        student.year,
        student.major_name,
    ]


def build_export_filename(params, extension):
    parts = ["students"]
    for key in ("college", "program", "year", "major"):
        value = (params.get(key) or "").strip()
        if value:
            safe = "".join(ch if ch.isalnum() else "_" for ch in str(value))
            parts.append(safe.lower())
    parts.append(date.today().isoformat())
    return f"{'_'.join(parts)}.{extension}"


def export_students_csv(students, filename, include_headers=True):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")  # UTF-8 BOM for Excel
    writer = csv.writer(response)
    if include_headers:
        writer.writerow(STUDENT_EXPORT_HEADERS)
    for student in students:
        writer.writerow(student_row(student))
    return response


def export_students_xlsx(students, filename):
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ImportError("Excel export requires openpyxl. Run: pip install openpyxl") from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Students"
    sheet.append(STUDENT_EXPORT_HEADERS)
    for student in students:
        sheet.append(student_row(student))

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_students_response(params):
    export_format = (params.get("format") or "csv").lower()
    include_headers = (params.get("include_headers") or "1") not in ("0", "false", "False")

    students = filter_students_queryset(params)
    if not students.exists():
        return None, "No students match the selected filters."

    if export_format == "xlsx":
        filename = build_export_filename(params, "xlsx")
        return export_students_xlsx(students, filename), None

    filename = build_export_filename(params, "csv")
    return export_students_csv(students, filename, include_headers=include_headers), None
