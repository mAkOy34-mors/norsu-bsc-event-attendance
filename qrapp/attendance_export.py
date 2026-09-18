"""
Server-side attendance report export.

Replaces the old client-side export in dashboard.js, which scraped only the
reports table's current page: rows hidden by pagination never made it into
the file, the date/time/event/gender boxes were collected but never applied,
and "Excel (.xlsx)" was really a renamed CSV. This module exports EVERY
attendance summary row matching the filters, straight from the database.

Row shape mirrors the on-screen reports table (one row per student): first
Time In and first Time Out inside the window, Date, and a COMPLETED / IN /
ABSENT status.
"""

import csv
import io
from datetime import date, datetime

from django.db.models import Q
from django.http import HttpResponse

from .models import Attendance, Event, Student

ATTENDANCE_EXPORT_HEADERS = [
    "Student ID",
    "Name",
    "College",
    "Program",
    "Year",
    "Major",
    "Time In",
    "Time Out",
    "Date",
    "Status",
]


def _parse_date(value):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_time(value):
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except (ValueError, TypeError):
        return None


def _gender_filter(gender):
    """'Male'/'M' -> starts with M, 'Female'/'F' -> starts with F.

    istartswith instead of icontains: "Female" contains "male", so a
    contains-match would put women in the male filter.
    """
    g = str(gender).strip().lower()
    if g in ("m", "male"):
        return Q(sex__istartswith="m")
    if g in ("f", "female"):
        return Q(sex__istartswith="f")
    return None


def build_attendance_report(params):
    """
    Filter students + attendance and build the per-student report rows.

    Returns ``(rows, summary, meta)`` where rows are lists of cell values in
    ATTENDANCE_EXPORT_HEADERS order, summary holds the counts, and meta
    carries the effective filters for the report header.
    """
    event_title = (params.get("event") or "").strip()
    college = (params.get("college") or "").strip()
    program = (params.get("program") or "").strip()
    year = (params.get("year") or "").strip()
    major = (params.get("major") or "").strip()
    gender = (params.get("gender") or "").strip()
    status_filter = (params.get("status") or "").strip().lower()
    date_from = _parse_date(params.get("dateFrom") or params.get("date_from"))
    date_to = _parse_date(params.get("dateTo") or params.get("date_to"))
    time_from = _parse_time(params.get("timeFrom") or params.get("time_from"))
    time_to = _parse_time(params.get("timeTo") or params.get("time_to"))

    # --- the students the report covers -------------------------------
    students = Student.objects.select_related("college", "program", "major").all()
    if college:
        students = students.filter(college__code__iexact=college)
    if program:
        students = students.filter(program__code__iexact=program)
    if year:
        students = students.filter(year=year)
    if major:
        students = students.filter(major__name__iexact=major)
    gender_q = _gender_filter(gender)
    if gender_q is not None:
        students = students.filter(gender_q)
    students = students.order_by("name", "student_id")

    # --- the attendance window ----------------------------------------
    # Dates restrict only when the user actually enters them. An event filter
    # pulls that event's real scans (their actual timestamps, which can fall
    # on a different day than the event's nominal date); blank dates mean all
    # recorded days -- never a silent "today only", which used to blank out
    # the Time In/Out columns when scans happened on another day.
    event = None
    if event_title:
        event = Event.objects.filter(title__iexact=event_title).first()

    records = Attendance.objects.all()
    if event is not None:
        records = records.filter(event=event)
    elif event_title:
        records = records.filter(event__title__iexact=event_title)
    if date_from is not None and date_to is not None:
        records = records.filter(timestamp__date__range=[date_from, date_to])
    elif date_from is not None:
        records = records.filter(timestamp__date=date_from)
    elif date_to is not None:
        records = records.filter(timestamp__date=date_to)
    if time_from is not None:
        records = records.filter(timestamp__time__gte=time_from)
    if time_to is not None:
        records = records.filter(timestamp__time__lte=time_to)

    # First IN and first OUT per student inside the window (one query).
    attendance_map = {}
    for row in records.order_by("student_id", "timestamp").values(
        "student_id", "status", "timestamp"
    ):
        entry = attendance_map.setdefault(row["student_id"], {"in": None, "out": None})
        if row["status"] == "IN" and entry["in"] is None:
            entry["in"] = row["timestamp"]
        elif row["status"] == "OUT" and entry["out"] is None:
            entry["out"] = row["timestamp"]

    # --- assemble rows (one per student, ABSENT included) ---------------
    rows = []
    present = completed = 0
    for student in students:
        entry = attendance_map.get(student.pk) or {}
        time_in = entry.get("in")
        time_out = entry.get("out")

        if time_in and time_out:
            status = "COMPLETED"
            attendance_date = time_out.date()
        elif time_in:
            status = "IN"
            attendance_date = time_in.date()
        else:
            status = "ABSENT"
            attendance_date = None

        if status_filter == "present" and status == "ABSENT":
            continue
        if status_filter == "absent" and status != "ABSENT":
            continue
        if status_filter == "in" and status != "IN":
            continue
        if status_filter == "out" and status != "COMPLETED":
            continue

        if status in ("IN", "COMPLETED"):
            present += 1
        if status == "COMPLETED":
            completed += 1

        rows.append([
            student.student_id,
            student.name,
            student.college_code,
            student.program_code,
            student.year,
            student.major_name,
            time_in.strftime("%I:%M:%S %p") if time_in else "",
            time_out.strftime("%I:%M:%S %p") if time_out else "",
            attendance_date.strftime("%Y-%m-%d") if attendance_date else "",
            status,
        ])

    summary = {
        "total": len(rows),
        "present": present,
        "absent": len(rows) - present,
        "completed": completed,
    }
    meta = {
        "event": event_title,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
    }
    return rows, summary, meta


def _slugify(value):
    return "".join(ch if ch.isalnum() else "_" for ch in str(value)).strip("_").lower()


def build_export_filename(params, extension):
    parts = ["attendance"]
    for key in ("college", "program", "year", "major"):
        value = (params.get(key) or "").strip()
        if value:
            parts.append(_slugify(value))
    event = (params.get("event") or "").strip()
    if event:
        parts.append(_slugify(event))
    meta_from = _parse_date(params.get("dateFrom") or params.get("date_from"))
    meta_to = _parse_date(params.get("dateTo") or params.get("date_to"))
    if meta_from and meta_to:
        parts.append(f"{meta_from:%Y%m%d}-{meta_to:%Y%m%d}")
    elif meta_from:
        parts.append(f"{meta_from:%Y%m%d}")
    parts.append(date.today().isoformat())
    return f"{'_'.join(parts)}.{extension}"


def export_attendance_csv(rows, summary, meta, filename, include_headers=True, include_summary=False):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")  # UTF-8 BOM so Excel opens it as UTF-8
    writer = csv.writer(response)

    if include_summary:
        if meta["event"]:
            writer.writerow([f"Event/Occasion: {meta['event']}"])
        writer.writerow([f"Generated: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"])
        if meta["date_from"] or meta["date_to"]:
            writer.writerow([f"Date Range: {meta['date_from']} to {meta['date_to']}"])
        writer.writerow([])
        writer.writerow(["SUMMARY STATISTICS"])
        writer.writerow(["Total Records", summary["total"]])
        writer.writerow(["Present", summary["present"]])
        writer.writerow(["Absent", summary["absent"]])
        writer.writerow(["Completed", summary["completed"]])
        writer.writerow([])

    if include_headers:
        writer.writerow(ATTENDANCE_EXPORT_HEADERS)
    writer.writerows(rows)
    return response


def export_attendance_xlsx(rows, summary, filename, include_headers=True, include_summary=False):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:
        raise ImportError("Excel export requires openpyxl. Run: pip install openpyxl") from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Attendance"
    if include_headers:
        sheet.append(ATTENDANCE_EXPORT_HEADERS)
    for row in rows:
        sheet.append(row)

    if rows:
        # Auto-arrange: size every column to its longest cell so nothing is
        # cut off, freeze the header, and add an Excel filter dropdown per
        # column for instant sorting/filtering.
        if include_headers:
            sheet.freeze_panes = "A2"
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            sheet.auto_filter.ref = sheet.dimensions
        for index, header in enumerate(ATTENDANCE_EXPORT_HEADERS, start=1):
            longest = len(str(header)) if include_headers else 0
            for row in rows:
                longest = max(longest, len(str(row[index - 1])))
            sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = min(longest + 3, 45)

    if include_summary:
        summary_sheet = workbook.create_sheet("Summary")
        summary_sheet.append(["SUMMARY STATISTICS"])
        for label, value in (
            ["Total Records", summary["total"]],
            ["Present", summary["present"]],
            ["Absent", summary["absent"]],
            ["Completed", summary["completed"]],
        ):
            summary_sheet.append([label, value])
        summary_sheet.column_dimensions["A"].width = max(
            16, max(len(str(r[0])) for r in summary_sheet.iter_rows(values_only=True))
        )
        summary_sheet.column_dimensions["B"].width = 12

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_attendance_response(params):
    include_headers = (params.get("includeHeaders") or params.get("include_headers") or "1") not in (
        "0", "false", "False",
    )
    include_summary = (params.get("includeSummary") or params.get("include_summary") or "") in (
        "1", "true", "True", "on",
    )
    export_format = (params.get("format") or "csv").lower()

    rows, summary, meta = build_attendance_report(params)
    if summary["total"] == 0:
        return None, "No attendance records match the selected filters."

    filename = build_export_filename(params, "xlsx" if export_format == "xlsx" else "csv")
    if export_format == "xlsx":
        return export_attendance_xlsx(rows, summary, filename, include_headers, include_summary), None
    return (
        export_attendance_csv(rows, summary, meta, filename, include_headers, include_summary),
        None,
    )
