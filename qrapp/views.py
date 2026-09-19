from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.db.models import Q, Count, Prefetch
from django.utils import timezone
from django.conf import settings
from django.core.cache import caches
from django.urls import reverse
from django.middleware.csrf import get_token
from django_ratelimit.decorators import ratelimit
from datetime import date, datetime, timedelta
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from PIL import Image, ImageDraw, ImageFont
import os
import io
import base64
import qrcode
import glob

from .models import Student, Attendance, Event, College, Program, Major, ImportIssue
from .forms import StudentForm, StudentUploadForm
from .student_import import import_students_from_file
from .student_export import export_students_response
from .attendance_export import export_attendance_response
from .caching import (
    build_calendar_cache_key,
    build_chart_cache_key,
    build_lookups_cache_key,
    invalidate_calendar_cache,
    invalidate_lookup_caches,
    LOOKUPS_CACHE_TIMEOUT,
    CALENDAR_CACHE_TIMEOUT,
)
from .qr_codes import (
    generate_qr_with_label,
    serialize_qr_list,
    get_filtered_students,
    export_qr_response,
    qr_image_basename,
)


# ---------------- RATE LIMITING ----------------

def client_username(group, request):
    """Rate-limit key: authenticated username, falling back to client IP."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user.username
    return request.META.get("REMOTE_ADDR", "")


def ratelimited_view(request, exception=None):
    """RATELIMIT_VIEW handler: friendly 429 page, JSON for AJAX clients."""
    wants_json = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or request.path.startswith("/ajax/")
            or request.path == "/save_scan/"
    )
    if wants_json:
        return JsonResponse(
            {
                "success": False,
                "error": "Too many requests. Please slow down and try again shortly.",
            },
            status=429,
        )
    return render(request, "qrapp/ratelimited.html", status=429)


def build_calendar_data(today=None):
    """Shared payload for the live calendar widget."""
    from django.urls import reverse

    today = today or date.today()

    # The calendar payload is identical for every user on a given day, so it
    # is cached and shared (invalidated on event/scan changes in
    # invalidate_calendar_cache / short TTL as a safety net).
    calendar_cache = caches["lookups"]
    calendar_key = build_calendar_cache_key(today)
    cached_payload = calendar_cache.get(calendar_key)
    calendar_events = None
    attendance_by_date = None
    if isinstance(cached_payload, dict):
        calendar_events = cached_payload.get("events")
        attendance_by_date = cached_payload.get("attendance_by_date")

    if calendar_events is None or attendance_by_date is None:
        events = Event.objects.filter(is_active=True).order_by("event_date", "start_time", "title")
        calendar_events = [
            {
                "id": event.id,
                "title": event.title,
                "date": event.event_date.isoformat(),
                "start_time": event.start_time.strftime("%H:%M") if event.start_time else "",
                "end_time": event.end_time.strftime("%H:%M") if event.end_time else "",
                "location": event.location or "",
                "is_current": event.is_current,
                "display_window": event.display_window,
            }
            for event in events
        ]

        activity_start = today - timedelta(days=90)
        activity_end = today + timedelta(days=60)
        attendance_days = (
            Attendance.objects.filter(timestamp__date__range=[activity_start, activity_end])
            .values("timestamp__date")
            .annotate(count=Count("id"))
        )
        attendance_by_date = {
            row["timestamp__date"].isoformat(): row["count"]
            for row in attendance_days
            if row["timestamp__date"]
        }

        calendar_cache.set(
            calendar_key,
            {"events": calendar_events, "attendance_by_date": attendance_by_date},
            timeout=CALENDAR_CACHE_TIMEOUT,
        )

    return {
        "today": today.isoformat(),
        "events": calendar_events,
        "attendance_by_date": attendance_by_date,
        "manage_events_url": reverse("manage_events"),
    }, Event.objects.filter(is_current=True, is_active=True).first()


def build_event_analytics(students_qs, scanned_records, report_rows=None):
    """
    Comprehensive analytics for event attendance.
    Prefer report_rows (per-student status) when available for accurate counts.
    """
    expected = students_qs.count() if hasattr(students_qs, "count") else len(students_qs)

    if report_rows is not None:
        attended = sum(1 for r in report_rows if r.get("status") != "ABSENT")
        completed = sum(1 for r in report_rows if r.get("status") == "COMPLETED")
        still_inside = sum(1 for r in report_rows if r.get("status") == "IN")
        absent = sum(1 for r in report_rows if r.get("status") == "ABSENT")
    else:
        in_ids = set(scanned_records.filter(status="IN").values_list("student_id", flat=True))
        out_ids = set(scanned_records.filter(status="OUT").values_list("student_id", flat=True))
        attended = len(in_ids)
        completed = len(in_ids & out_ids)
        still_inside = len(in_ids - out_ids)
        absent = max(expected - attended, 0)

    attendance_rate = round((attended / expected) * 100, 1) if expected else 0.0
    completion_rate = round((completed / attended) * 100, 1) if attended else 0.0

    total_scans = scanned_records.count()
    total_in_scans = scanned_records.filter(status="IN").count()
    total_out_scans = scanned_records.filter(status="OUT").count()

    first_scan = scanned_records.order_by("timestamp").first()
    last_scan = scanned_records.order_by("-timestamp").first()

    # Peak check-in hour
    peak_hour_label = "-"
    peak_hour_count = 0
    hour_counts = {}
    for ts in scanned_records.filter(status="IN").values_list("timestamp", flat=True):
        try:
            local_ts = timezone.localtime(ts)
        except Exception:
            local_ts = ts
        hour = local_ts.hour
        hour_counts[hour] = hour_counts.get(hour, 0) + 1
    if hour_counts:
        peak_hour = max(hour_counts, key=hour_counts.get)
        peak_hour_count = hour_counts[peak_hour]
        suffix = "AM" if peak_hour < 12 else "PM"
        display_hour = peak_hour % 12 or 12
        peak_hour_label = f"{display_hour}:00 {suffix}"

    # Breakdowns from attended students (those with IN)
    attended_student_ids = scanned_records.filter(status="IN").values_list("student_id", flat=True).distinct()
    attended_students = Student.objects.filter(id__in=attended_student_ids)

    college_rows = list(
        attended_students.values("college__code").annotate(count=Count("id")).order_by("-count")[:6]
    )
    year_rows = list(
        attended_students.values("year").annotate(count=Count("id")).order_by("year")
    )
    sex_rows = list(
        attended_students.values("sex").annotate(count=Count("id")).order_by("-count")
    )

    max_college = max((r["count"] for r in college_rows), default=0) or 1
    college_breakdown = [
        {
            "label": r["college__code"] or "Unknown",
            "count": r["count"],
            "percent": round((r["count"] / attended) * 100, 1) if attended else 0,
            "bar": round((r["count"] / max_college) * 100, 1),
        }
        for r in college_rows
    ]

    max_year = max((r["count"] for r in year_rows), default=0) or 1
    year_breakdown = [
        {
            "label": f"Year {r['year']}",
            "count": r["count"],
            "percent": round((r["count"] / attended) * 100, 1) if attended else 0,
            "bar": round((r["count"] / max_year) * 100, 1),
        }
        for r in year_rows
    ]

    sex_map = {"M": "Male", "F": "Female", "Male": "Male", "Female": "Female"}
    sex_breakdown = [
        {
            "label": sex_map.get(r["sex"], r["sex"] or "Other"),
            "count": r["count"],
            "percent": round((r["count"] / attended) * 100, 1) if attended else 0,
        }
        for r in sex_rows
    ]

    def fmt_time(rec):
        if not rec:
            return "-"
        try:
            return timezone.localtime(rec.timestamp).strftime("%I:%M %p")
        except Exception:
            return rec.timestamp.strftime("%I:%M %p")

    return {
        "expected": expected,
        "attended": attended,
        "absent": absent,
        "still_inside": still_inside,
        "completed": completed,
        "attendance_rate": attendance_rate,
        "completion_rate": completion_rate,
        "total_scans": total_scans,
        "total_in_scans": total_in_scans,
        "total_out_scans": total_out_scans,
        "first_checkin": fmt_time(first_scan),
        "last_activity": fmt_time(last_scan),
        "peak_hour": peak_hour_label,
        "peak_hour_count": peak_hour_count,
        "college_breakdown": college_breakdown,
        "year_breakdown": year_breakdown,
        "sex_breakdown": sex_breakdown,
        # backward-compatible keys for older JS
        "present_in": still_inside,
        "present_out": completed,
    }


@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def dashboard_home(request):
    """Clean dashboard with only analytics and shortcuts"""
    today = date.today()

    # Get all students
    students = Student.objects.all()

    # Get today's attendance
    scanned_records = Attendance.objects.filter(timestamp__date=today).order_by('-timestamp')

    # Count stats
    today_scanned_students = scanned_records.values_list("student__id", flat=True).distinct()
    not_scanned = students.exclude(id__in=today_scanned_students)
    scanned_records = scanned_records.order_by("-timestamp", "-id")
    present_in = scanned_records.filter(status="IN")
    present_out = scanned_records.filter(status="OUT")

    # Last 7 days attendance overview (real chart data).
    # Cached + computed with one grouped query (was 14) since dashboards are
    # hit constantly; the chart may lag up to CALENDAR_CACHE_TIMEOUT seconds.
    chart_cache = caches["lookups"]
    chart_key = build_chart_cache_key(today)
    chart_data = chart_cache.get(chart_key)

    if chart_data is None:
        week_start = today - timedelta(days=6)
        week_rows = (
            Attendance.objects.filter(timestamp__date__range=[week_start, today])
            .values("timestamp__date", "status")
            .annotate(count=Count("id"))
        )
        week_counts = {
            (row["timestamp__date"], row["status"]): row["count"] for row in week_rows
        }
        week_labels = []
        week_check_in = []
        week_check_out = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            week_labels.append(day.strftime("%a"))
            week_check_in.append(week_counts.get((day, "IN"), 0))
            week_check_out.append(week_counts.get((day, "OUT"), 0))

        chart_data = {
            "week_labels": week_labels,
            "week_check_in": week_check_in,
            "week_check_out": week_check_out,
            "college_labels": [],
            "college_counts": [],
        }
        chart_cache.set(chart_key, chart_data, timeout=CALENDAR_CACHE_TIMEOUT)

    # College breakdown: unique students who attended today (merged into the
    # cached chart payload; it depends on today's scans, so always fresh).
    college_rows = list(
        scanned_records.values("student__college__code")
        .annotate(count=Count("student_id", distinct=True))
        .order_by("-count")
    )
    if not college_rows:
        # Fallback so chart still reflects roster composition
        college_rows = list(
            students.values("college__code")
            .annotate(count=Count("id"), ).order_by("-count")[:8]
        )
        college_labels = [row["college__code"] or "Unknown" for row in college_rows]
        college_counts = [row["count"] for row in college_rows]
    else:
        college_labels = [row["student__college__code"] or "Unknown" for row in college_rows]
        college_counts = [row["count"] for row in college_rows]

    chart_data["college_labels"] = college_labels
    chart_data["college_counts"] = college_counts

    calendar_data, current_event = build_calendar_data(today)

    return render(request, "qrapp/dashboard_home.html", {
        "students": students,
        "scanned_records": scanned_records,
        "not_scanned": not_scanned,
        "present_in": present_in,
        "present_out": present_out,
        # Raw objects: templates embed them with |json_script (do NOT
        # pre-serialize with json.dumps or JS would parse a string, not object).
        "chart_data": chart_data,
        "calendar_data": calendar_data,
        "current_event": current_event,
    })


@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def admin_dashboard(request):
    # ---------- active section (server-rendered tab) ----------
    # Sidebar deep links (Students / Reports / Approve Users) hit
    # /qrapp/admin_dashboard/?section=... so the correct tab renders even
    # without JS; previously hash-only switching left users on the overview.
    valid_sections = {"dashboard", "students", "reports", "approve"}
    section = (request.GET.get("section") or "dashboard").strip().lower()
    if section not in valid_sections:
        section = "dashboard"
    section_labels = {
        "dashboard": "Dashboard",
        "students": "Students",
        "reports": "Reports",
        "approve": "Approve Users",
    }

    # ---------- filters ----------
    date_filter = request.GET.get("date")
    start_date_filter = request.GET.get("start_date")
    end_date_filter = request.GET.get("end_date")
    start_time_filter = request.GET.get("start_time")
    end_time_filter = request.GET.get("end_time")
    year_filter = request.GET.get("year")
    program_filter = request.GET.get("program")
    college_filter = request.GET.get("college")
    search_query = request.GET.get("search")

    if date_filter:
        try:
            selected_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
        except ValueError:
            selected_date = date.today()
    else:
        selected_date = date.today()

    # ---------- students queryset (optimized) ----------
    students = (
        Student.objects
        .select_related("program", "college", "major")
        .only(
            "id", "name", "student_id", "year", "sex",
            "major_id", "major__code", "major__name",
            "program__code", "program__name",
            "college__code",
        )
    )
    if year_filter:
        students = students.filter(year=year_filter)
    if program_filter:
        students = students.filter(program__code=program_filter)
    if college_filter:
        students = students.filter(college__code=college_filter)
    if search_query:
        students = students.filter(
            Q(name__icontains=search_query)
            | Q(student_id__icontains=search_query)
            | Q(program__code__icontains=search_query)
            | Q(year__icontains=search_query)
        )
    students = students.order_by("name")

    # ---------- attendance queryset (optimized) ----------
    scanned_records = (
        Attendance.objects
        .select_related("student", "student__program", "student__college")
    )

    if date_filter:
        scanned_records = scanned_records.filter(timestamp__date=selected_date)

    if start_date_filter and end_date_filter:
        try:
            start_date = datetime.strptime(start_date_filter, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_filter, "%Y-%m-%d").date()
            scanned_records = scanned_records.filter(
                timestamp__date__range=[start_date, end_date]
            )
        except ValueError:
            pass
    elif not date_filter:
        # Default to today
        scanned_records = scanned_records.filter(timestamp__date=selected_date)

    if start_time_filter and not (start_date_filter and end_date_filter):
        try:
            start_datetime = datetime.combine(
                selected_date, datetime.strptime(start_time_filter, "%H:%M").time()
            )
            scanned_records = scanned_records.filter(timestamp__gte=start_datetime)
        except ValueError:
            pass

    if end_time_filter and not (start_date_filter and end_date_filter):
        try:
            end_datetime = datetime.combine(
                selected_date, datetime.strptime(end_time_filter, "%H:%M").time()
            )
            scanned_records = scanned_records.filter(timestamp__lte=end_datetime)
        except ValueError:
            pass

    if year_filter:
        scanned_records = scanned_records.filter(student__year=year_filter)
    if program_filter:
        scanned_records = scanned_records.filter(student__program__code=program_filter)
    if college_filter:
        scanned_records = scanned_records.filter(student__college__code=college_filter)

    present_in = scanned_records.filter(status="IN")
    present_out = scanned_records.filter(status="OUT")

    # IDs of students who scanned in this window — one query
    scanned_student_ids = set(
        scanned_records.values_list("student_id", flat=True).distinct()
    )
    not_scanned = students.exclude(id__in=scanned_student_ids)

    # ---------- ONE query for all attendance grouped by student ----------
    # Build a dict: student_id -> {"in": dt, "out": dt}
    attendance_map = {}
    for row in (
        scanned_records
        .values("student_id", "status", "timestamp")
        .order_by("student_id", "timestamp")
    ):
        entry = attendance_map.setdefault(row["student_id"], {"in": None, "out": None})
        if row["status"] == "IN" and entry["in"] is None:
            entry["in"] = row["timestamp"]
        elif row["status"] == "OUT":
            entry["out"] = row["timestamp"]

    def to_local(dt):
        if not dt:
            return None
        try:
            return timezone.localtime(dt)
        except Exception:
            return dt

    # Build report data — no DB hit in this loop
    student_report_data = []
    for student in students:
        entry = attendance_map.get(student.id, {"in": None, "out": None})
        time_in = entry["in"]
        time_out = entry["out"]
        if time_in and time_out:
            status = "COMPLETED"
        elif time_in:
            status = "IN"
        else:
            status = "ABSENT"

        time_in_local = to_local(time_in)
        time_out_local = to_local(time_out)
        date_obj = time_out_local or time_in_local

        student_report_data.append({
            "student": student,
            "time_in": time_in_local,
            "time_out": time_out_local,
            "date": date_obj,
            "status": status,
        })

    # ---------- analytics (unchanged call, but now cheap) ----------
    analytics = build_event_analytics(students, scanned_records, student_report_data)

    event_label = selected_date.strftime("%b %d, %Y")
    if start_date_filter and end_date_filter:
        event_label = f"{start_date_filter} → {end_date_filter}"
    elif date_filter:
        event_label = selected_date.strftime("%b %d, %Y")

    calendar_data, current_event = build_calendar_data(date.today())

    # ---------- dropdown data: 3 queries total, no loops ----------
    active_colleges = list(College.objects.filter(is_active=True).order_by("code"))
    college_codes = [c.code for c in active_colleges]

    # All active programs in one query, grouped in Python
    all_programs = (
        Program.objects
        .filter(is_active=True, college__is_active=True)
        .select_related("college")
        .order_by("college__code", "code")
    )
    college_programs = {}
    for p in all_programs:
        college_programs.setdefault(p.college.code, []).append({
            "code": p.code,
            "name": p.name,
        })

    # Student program/college distinct pairs in one query
    student_pairs = (
        Student.objects
        .exclude(college__code__isnull=True)
        .exclude(program__code__isnull=True)
        .values_list("college__code", "program__code")
        .distinct()
    )
    student_programs_by_college = {}
    extra_colleges = set()
    for college_code, program_code in student_pairs:
        extra_colleges.add(college_code)
        lst = student_programs_by_college.setdefault(college_code, [])
        if program_code and program_code not in lst:
            lst.append(program_code)
    for k in student_programs_by_college:
        student_programs_by_college[k].sort()

    unique_colleges = sorted(set(college_codes) | extra_colleges)
    unique_programs = list(
        Program.objects.values_list("code", flat=True).distinct().order_by("code")
    )

    # Events for the export-report dropdown (newest first)
    all_events = list(
        Event.objects.all()
        .order_by("-is_current", "-event_date", "-start_time", "title")
        .values("id", "title", "event_date", "is_current")
    )

    # ---------- users: 3 queries max ----------
    pending_users = list(User.objects.filter(is_active=False))
    all_users = list(User.objects.all().order_by("-date_joined"))
    total_users = len(all_users)
    active_users = sum(1 for u in all_users if u.is_active)

    app_urls = {
        "manageUsers": reverse("manage_users"),
        "pendingUsers": reverse("ajax_pending_users"),
        "addStudent": reverse("add_student"),
        "uploadPdf": reverse("upload_pdf"),
        "ajaxQrCodes": reverse("ajax_qr_codes"),
        "exportQrCodes": reverse("export_qr_codes"),
        "exportAttendance": reverse("export_attendance"),
        "exportStudents": reverse("export_students"),
        "editStudent": reverse("edit_student", args=[0]),
        "deleteStudent": reverse("delete_student", args=[0]),
        "getMajors": reverse("get_majors_json", args=["TEMPLATE"]),
        "scansSince": reverse("scans_since"),
        "ajaxStudentList": reverse("ajax_student_list"),
        "ajaxDashboardData": reverse("ajax_dashboard_data"),
        "ajaxReportsData": reverse("ajax_reports_data"),
        "csrfToken": get_token(request),
    }

    return render(request, "qrapp/admin_dashboard.html", {
        "app_urls": app_urls,
        "today": date.today(),
        "selected_date": selected_date,
        "event_label": event_label,
        "start_date_filter": start_date_filter,
        "end_date_filter": end_date_filter,
        "start_time_filter": start_time_filter,
        "end_time_filter": end_time_filter,
        "year_filter": year_filter,
        "program_filter": program_filter,
        "college_filter": college_filter,
        "students": students,
        "scanned_records": scanned_records,
        "present_in": present_in,
        "search_query": search_query,
        "present_out": present_out,
        "not_scanned": not_scanned,
        "unique_programs": unique_programs,
        "unique_colleges": unique_colleges,
        "student_report_data": student_report_data,
        "college_programs": college_programs,
        "student_programs_by_college": student_programs_by_college,
        "all_events": all_events,
        "pending_users": pending_users,
        "all_users": all_users,
        "total_users": total_users,
        "active_users": active_users,
        "analytics": analytics,
        "calendar_data": calendar_data,
        "current_event": current_event,
        "active_section": section,
        "section_label": section_labels[section],
        "import_issue_count": _import_issue_count(),
    })

# ---------------- SCANNER ----------------
@login_required
def scanner_view(request):
    events = Event.objects.filter(is_active=True).order_by("-is_current", "-event_date", "title")
    current_event = events.filter(is_current=True).first() or events.filter(event_date=date.today()).first()
    scanner_config = {
        "scan_url": reverse("save_scan"),
        "csrfToken": get_token(request),
    }
    return render(request, "qrapp/scanner.html", {
        "events": events,
        "current_event": current_event,
        "scanner_config": scanner_config,
    })


# ---------------- data security ----------------

@login_required
def get_students_data(request):
    user = request.user

    if user.is_staff or user.is_superuser:
        student_data = [
            {
                "id": s.id,

                "student_id": s.student_id,
                "name": s.name,
                "college": s.college_code,
                "program": s.program_code,
                "year": s.year,
                "major": s.major_name,
                # add other fields if needed
            }
            for s in students
        ]
        return JsonResponse({"success": True, "data": student_data})

    # Non-staff: only return their own record (if they have one)
    try:
        student = Student.objects.get(user_profile__user=user)  # adapt to your model relation
    except Student.DoesNotExist:
        return JsonResponse({"success": False, "error": "Not allowed or no student record"}, status=403)

    data = {
        "id": student.id,
        "student_id": student.student_id,
        "name": student.name,
        "college": student.college_code,
        "program": student.program_code,
        "year": student.year,
        "major": student.major_name,
    }
    return JsonResponse({"success": True, "data": data})


# Scan endpoint: generous enough for a queue of students (1 scan / 2s per
# scanner), tight enough to stop scripted flooding of the attendance table.
@ratelimit(key=client_username, rate='30/m', method='POST', block=True)
@login_required
def save_scan(request):
    if request.method != "POST":
        return JsonResponse({
            "success": False,
            "message": "Invalid request method.",
            "color": "warning"
        })

    from .qr_security import resolve_scanned_student_id

    student_id, token_error = resolve_scanned_student_id(
        decoded_text=request.POST.get("qr_payload", ""),
        student_id=request.POST.get("student_id", ""),
        qr_token=request.POST.get("qr_token", ""),
    )
    if token_error:
        return JsonResponse({
            "success": False,
            "message": token_error,
            "color": "warning"
        })

    device_time_str = request.POST.get("local_time")
    manual_status = request.POST.get("manual_status")
    event_label = (request.POST.get("event_label") or "").strip()[:200]
    event_id = (request.POST.get("event_id") or "").strip()
    source = (request.POST.get("source") or "").strip().lower()
    valid_sources = {
        Attendance.SOURCE_CAMERA,
        Attendance.SOURCE_DEVICE,
        Attendance.SOURCE_MANUAL,
    }
    if source not in valid_sources:
        source = Attendance.SOURCE_MANUAL if manual_status else Attendance.SOURCE_CAMERA

    event = None
    if not event_id:
        return JsonResponse({
            "success": False,
            "message": "Please select an event before scanning attendance.",
            "color": "warning",
        })

    event = Event.objects.filter(id=event_id, is_active=True).first()
    if not event:
        return JsonResponse({
            "success": False,
            "message": "Selected event was not found or is inactive.",
            "color": "warning",
        })

    # Scanning is only allowed on the event's own day. Checked against the
    # server's real date (not the device-supplied local_time) so a wrong or
    # doctored phone clock cannot backfill attendance for other days.
    server_today = datetime.now().date()
    if event.event_date != server_today:
        return JsonResponse({
            "success": False,
            "message": (
                f"Event is not today. {event.title} is scheduled for "
                f"{event.event_date.strftime('%b %d, %Y')} — scanning is only "
                "allowed on the event day."
            ),
            "color": "warning",
        })

    if not event_label:
        event_label = event.title

    try:
        student = Student.objects.get(student_id=student_id)
    except Student.DoesNotExist:
        return JsonResponse({
            "success": False,
            "message": "Student not found.",
            "color": "danger"
        })

    now = datetime.now()
    if device_time_str:
        try:
            now = datetime.fromisoformat(device_time_str)
        except ValueError:
            now = datetime.now()

    today = now.date()
    if event:
        scope_records = Attendance.objects.filter(student=student, event=event).order_by("timestamp")
    else:
        scope_records = Attendance.objects.filter(
            student=student,
            timestamp__date=today
        ).order_by("timestamp")

    def create_attendance(status):
        return Attendance.objects.create(
            student=student,
            event=event,
            status=status,
            timestamp=now,
            event_label=event_label,
            scanned_by=request.user if request.user.is_authenticated else None,
            source=source,
        )

    event_note = f" ({event.title})" if event else ""

    if manual_status:
        create_attendance(manual_status)
        return JsonResponse({
            "success": True,
            "message": f"{student.name} manually marked {manual_status}{event_note} at {now.strftime('%I:%M:%S %p')}",
            "status": manual_status,
            "color": "success" if manual_status == "IN" else "info",
            "student_name": student.name,
            "time": now.strftime('%I:%M:%S %p')
        })

    if not scope_records.exists():
        create_attendance("IN")
        return JsonResponse({
            "success": True,
            "message": f"{student.name} marked IN{event_note} at {now.strftime('%I:%M:%S %p')}",
            "status": "IN",
            "color": "success",
            "student_name": student.name,
            "time": now.strftime('%I:%M:%S %p')
        })

    last_record = scope_records.last()

    if last_record.status == "IN":
        if now - last_record.timestamp < timedelta(hours=1):
            return JsonResponse({
                "success": False,
                "message": f"{student.name} cannot log OUT yet. Wait at least 1 hour.",
                "color": "warning",
                "student_name": student.name
            })
        create_attendance("OUT")
        return JsonResponse({
            "success": True,
            "message": f"{student.name} marked OUT{event_note} at {now.strftime('%I:%M:%S %p')}",
            "status": "OUT",
            "color": "info",
            "student_name": student.name,
            "time": now.strftime('%I:%M:%S %p')
        })

    create_attendance("IN")
    return JsonResponse({
        "success": True,
        "message": f"{student.name} marked IN again{event_note} at {now.strftime('%I:%M:%S %p')}",
        "status": "IN",
        "color": "success",
        "student_name": student.name,
        "time": now.strftime('%I:%M:%S %p')
    })


@ratelimit(key=client_username, rate='60/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def edit_student(request, student_id):
    from .models import College
    from .college_program import resolve_college_and_program, resolve_major

    student = get_object_or_404(Student, id=student_id)
    if request.method == "POST":
        # Check if it's an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.POST.get('name'):
            try:
                from .college_program import resolve_college_and_program, resolve_program_and_major
                # Student ID is editable (fixes registrar-file mistakes like
                # generated "-DUP2" numbers). Validate before touching anything.
                new_student_id = (request.POST.get("student_id") or student.student_id).strip()
                if not new_student_id:
                    raise ValueError("Student ID is required.")
                if len(new_student_id) > 20:
                    raise ValueError("Student ID must be 20 characters or fewer.")
                if new_student_id != student.student_id:
                    clash = Student.objects.filter(student_id__iexact=new_student_id).first()
                    if clash is not None:
                        raise ValueError(
                            f"Student ID {new_student_id} is already used by "
                            f"{clash.name}. Choose a different number."
                        )

                college, program = resolve_college_and_program(
                    request.POST.get("college"),
                    request.POST.get("program"),
                )
                # A major value naming a program (e.g. "Animal Science")
                # resolves to that program with no major, never a major.
                program, major = resolve_program_and_major(
                    college, program, request.POST.get("major"), create=True,
                )
                # Program code is authoritative; keep college consistent.
                if program is not None:
                    college = program.college

                id_changed = new_student_id != student.student_id
                old_student_id = student.student_id
                student.student_id = new_student_id
                student.name = request.POST.get("name")
                student.sex = request.POST.get("sex")
                student.college = college
                student.program = program
                student.year = request.POST.get("year")
                student.major = major
                student.save()

                if id_changed:
                    # The old number no longer exists -- drop stale review
                    # entries (e.g. a "-DUP2" shared-number flag that this
                    # rename just fixed) so the issues list stays accurate.
                    # QR codes are generated on demand (no stored files), and
                    # tokens are signed from the ID, so reprints of this
                    # student will automatically use the new number.
                    from .models import ImportIssue
                    ImportIssue.objects.filter(student_id__iexact=old_student_id).delete()

                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'message': 'Student updated successfully!'})
                # Back to the Students tab so the section is preserved.
                return redirect(f"{reverse('admin_dashboard')}?section=students")
            except Exception as e:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': str(e)}, status=400)
                messages.error(request, f'Error updating student: {str(e)}')
        else:
            return redirect(f"{reverse('admin_dashboard')}?section=students")

    active_colleges = College.objects.filter(is_active=True).order_by('code')
    college_choices = [(college.code, f"{college.code} - {college.name}") for college in active_colleges]

    return render(request, "qrapp/edit_student.html", {
        "student": student,
        "college_choices": college_choices
    })


@ratelimit(key=client_username, rate='30/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def delete_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)

    if request.method == "POST":
        password = request.POST.get('adminPassword', '')

        # Confirm with the currently logged-in staff user's password
        if request.user.check_password(password):
            student.delete()
            messages.success(request, f'Student {student.name} has been deleted successfully.')
            return redirect(f"{reverse('admin_dashboard')}?section=students")
        else:
            messages.error(request, 'Incorrect password. Please try again.')

    return render(request, "qrapp/delete_student.html", {"student": student})


@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def ajax_student_list(request):
    """AJAX view to return filtered student list"""
    try:
        if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Invalid request'})

        year_filter = request.GET.get("year")
        program_filter = request.GET.get("program")
        college_filter = request.GET.get("college")
        search_query = request.GET.get("search")
        status_filter = request.GET.get("status")
        sort_filter = request.GET.get("sort", "name")
        today = date.today()

        # Filter students
        students = Student.objects.all()
        if year_filter:
            students = students.filter(year=year_filter)
        if program_filter:
            students = students.filter(program__code=program_filter)
        if college_filter:
            students = students.filter(college__code=college_filter)
        if search_query:
            students = students.filter(
                Q(name__icontains=search_query) |
                Q(student_id__icontains=search_query) |
                Q(program__code__icontains=search_query) |
                Q(year__icontains=search_query)
            )

        # Apply sorting
        if sort_filter == "name":
            students = students.order_by("name")
        elif sort_filter == "id":
            students = students.order_by("student_id")
        elif sort_filter == "program":
            students = students.order_by("program__code")
        elif sort_filter == "year":
            students = students.order_by("year")

        # Get attendance data for today
        scanned_records = Attendance.objects.filter(timestamp__date=today)
        if year_filter:
            scanned_records = scanned_records.filter(student__year=year_filter)
        if program_filter:
            scanned_records = scanned_records.filter(student__program__code=program_filter)
        if college_filter:
            scanned_records = scanned_records.filter(student__college__code=college_filter)

        present_in = scanned_records.filter(status="IN")
        present_out = scanned_records.filter(status="OUT")

        # Get scanned student IDs
        scanned_students = scanned_records.values_list("student__id", flat=True)
        not_scanned = students.exclude(id__in=scanned_students)

        # Analytics against filtered roster (before status narrowing)
        roster_students = students
        stats = build_event_analytics(roster_students, scanned_records)

        # Apply status filter if specified
        if status_filter:
            if status_filter == "present":
                students = students.filter(id__in=scanned_students)
            elif status_filter == "absent":
                students = not_scanned
            elif status_filter == "in":
                students = students.filter(id__in=present_in.values_list('student__id', flat=True))
            elif status_filter == "out":
                students = students.filter(id__in=present_out.values_list('student__id', flat=True))

        # Prepare student data for JSON response
        # ONE query for today's attendance grouped by student (avoids the
        # per-student exists()+iteration N+1 = 2-3 queries per row)
        today_map = {}
        for row in (
            Attendance.objects
            .filter(timestamp__date=today)
            .order_by("student_id", "timestamp")
            .values("student_id", "status", "timestamp")
        ):
            entry = today_map.setdefault(row["student_id"], {"in": None, "out": None})
            if row["status"] == "IN" and entry["in"] is None:
                entry["in"] = row["timestamp"]
            elif row["status"] == "OUT" and entry["out"] is None:
                entry["out"] = row["timestamp"]

        students = (
            students.select_related("college", "program", "major")
        )

        student_data = []
        for student in students:
            entry = today_map.get(student.id)
            time_in = entry["in"] if entry else None
            time_out = entry["out"] if entry else None

            if time_in and time_out:
                status = "COMPLETED"
            elif time_in:
                status = "IN"
            else:
                status = "ABSENT"

            student_data.append({
                'id': student.id,
                'student_id': student.student_id,
                'name': student.name,
                'college': student.college_code,
                'program': student.program_code,
                'year': student.year,
                'major': student.major_name,
                'time_in': time_in.strftime("%I:%M:%S %p") if time_in else None,
                'time_out': time_out.strftime("%H:%M:%S") if time_out else None,
                'status': status
            })

        return JsonResponse({
            'success': True,
            'students': student_data,
            'stats': stats
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def ajax_dashboard_data(request):
    try:
        if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Invalid request'})

        year_filter = request.GET.get("year")
        program_filter = request.GET.get("program")
        college_filter = request.GET.get("college")
        search_query = request.GET.get("search")
        status_filter = request.GET.get("status")
        date_filter = request.GET.get("date")
        start_date_filter = request.GET.get("start_date")
        end_date_filter = request.GET.get("end_date")
        start_time_filter = request.GET.get("start_time")
        end_time_filter = request.GET.get("end_time")

        # Filter students first
        students = Student.objects.all()
        if year_filter:
            students = students.filter(year=year_filter)
        if program_filter:
            students = students.filter(program__code=program_filter)
        if college_filter:
            students = students.filter(college__code=college_filter)
        if search_query:
            students = students.filter(
                Q(name__icontains=search_query) |
                Q(student_id__icontains=search_query) |
                Q(program__code__icontains=search_query) |
                Q(year__icontains=search_query)
            )

        # Date filtering logic
        scanned_records = Attendance.objects.all()
        selected_date = date.today()

        if date_filter:
            try:
                selected_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
                scanned_records = scanned_records.filter(timestamp__date=selected_date)
            except ValueError:
                scanned_records = scanned_records.filter(timestamp__date=date.today())

        if start_date_filter and end_date_filter:
            try:
                start_date = datetime.strptime(start_date_filter, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_filter, "%Y-%m-%d").date()
                scanned_records = scanned_records.filter(
                    timestamp__date__range=[start_date, end_date]
                )
            except ValueError:
                pass
        elif not date_filter:
            scanned_records = scanned_records.filter(timestamp__date=selected_date)

        # Time filtering
        if start_time_filter and not (start_date_filter and end_date_filter):
            try:
                event_date = selected_date
                if date_filter:
                    event_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
                start_datetime = datetime.combine(
                    event_date,
                    datetime.strptime(start_time_filter, "%H:%M").time()
                )
                scanned_records = scanned_records.filter(timestamp__gte=start_datetime)
            except ValueError:
                pass

        if end_time_filter and not (start_date_filter and end_date_filter):
            try:
                event_date = selected_date
                if date_filter:
                    event_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
                end_datetime = datetime.combine(
                    event_date,
                    datetime.strptime(end_time_filter, "%H:%M").time()
                )
                scanned_records = scanned_records.filter(timestamp__lte=end_datetime)
            except ValueError:
                pass

        # Apply year and course filters to attendance records
        if year_filter:
            scanned_records = scanned_records.filter(student__year=year_filter)
        if program_filter:
            scanned_records = scanned_records.filter(student__program__code=program_filter)
        if college_filter:
            scanned_records = scanned_records.filter(student__college__code=college_filter)

        # Apply status filter if specified
        display_records = scanned_records
        if status_filter:
            if status_filter == "in":
                display_records = scanned_records.filter(status="IN")
            elif status_filter == "out":
                display_records = scanned_records.filter(status="OUT")

        # Prepare record data for JSON response
        record_data = []
        for record in display_records:
            record_data.append({
                'student_id': record.student.student_id,
                'name': record.student.name,
                'college': record.student.college_code,
                'program': record.student.program_code,
                'year': record.student.year,
                'major': record.student.major_name,
                'status': record.status,
                'date': record.timestamp.strftime("%Y-%m-%d"),
                'timestamp': record.timestamp.strftime("%I:%M:%S %p")
            })

        # Build report-style rows for accurate analytics
        report_rows = []
        for student in students:
            student_attendance = scanned_records.filter(student=student).order_by("timestamp")
            time_in = None
            time_out = None
            status = "ABSENT"
            if student_attendance.exists():
                for record in student_attendance:
                    if record.status == "IN" and time_in is None:
                        time_in = record.timestamp
                    elif record.status == "OUT":
                        time_out = record.timestamp
                if time_in and time_out:
                    status = "COMPLETED"
                elif time_in:
                    status = "IN"
            report_rows.append({"status": status})

        stats = build_event_analytics(students, scanned_records, report_rows)

        # Add date info for display
        date_info = ""
        if date_filter:
            date_info = f"Showing attendance for {date_filter}"
        elif start_date_filter and end_date_filter:
            date_info = f"Showing attendance from {start_date_filter} to {end_date_filter}"
        else:
            date_info = f"Showing today's attendance - {date.today()}"

        return JsonResponse({
            'success': True,
            'records': record_data,
            'stats': stats,
            'date_info': date_info
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def ajax_reports_data(request):
    """AJAX view to return filtered reports data"""
    try:
        if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Invalid request'})

        year_filter = request.GET.get("year")
        program_filter = request.GET.get("program")
        college_filter = request.GET.get("college")
        search_query = request.GET.get("search")
        status_filter = request.GET.get("status")
        sort_filter = request.GET.get("sort", "name")
        date_filter = request.GET.get("date")
        start_date_filter = request.GET.get("start_date")
        end_date_filter = request.GET.get("end_date")
        start_time_filter = request.GET.get("start_time")
        end_time_filter = request.GET.get("end_time")

        # Filter students
        students = Student.objects.all()
        if year_filter:
            students = students.filter(year=year_filter)
        if program_filter:
            students = students.filter(program__code=program_filter)
        if college_filter:
            students = students.filter(college__code=college_filter)
        if search_query:
            students = students.filter(
                Q(name__icontains=search_query) |
                Q(student_id__icontains=search_query) |
                Q(program__code__icontains=search_query) |
                Q(year__icontains=search_query)
            )

        # Apply sorting
        if sort_filter == "name":
            students = students.order_by("name")
        elif sort_filter == "id":
            students = students.order_by("student_id")
        elif sort_filter == "program":
            students = students.order_by("program__code")
        elif sort_filter == "year":
            students = students.order_by("year")

        # Get attendance records with date/time filtering
        attendance_records = Attendance.objects.all()
        selected_date = date.today()

        # Single date filter
        if date_filter:
            try:
                selected_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
                attendance_records = attendance_records.filter(timestamp__date=selected_date)
            except ValueError:
                attendance_records = attendance_records.filter(timestamp__date=date.today())

        # Date range filter
        if start_date_filter and end_date_filter:
            try:
                start_date = datetime.strptime(start_date_filter, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_filter, "%Y-%m-%d").date()
                attendance_records = attendance_records.filter(
                    timestamp__date__range=[start_date, end_date]
                )
            except ValueError:
                pass
        elif not date_filter:
            attendance_records = attendance_records.filter(timestamp__date=selected_date)

        # Time filtering
        if start_time_filter and date_filter:
            try:
                start_datetime = datetime.combine(
                    datetime.strptime(date_filter, "%Y-%m-%d").date(),
                    datetime.strptime(start_time_filter, "%H:%M").time()
                )
                attendance_records = attendance_records.filter(timestamp__gte=start_datetime)
            except ValueError:
                pass

        if end_time_filter and date_filter:
            try:
                end_datetime = datetime.combine(
                    datetime.strptime(date_filter, "%Y-%m-%d").date(),
                    datetime.strptime(end_time_filter, "%H:%M").time()
                )
                attendance_records = attendance_records.filter(timestamp__lte=end_datetime)
            except ValueError:
                pass

        # Apply year and course filters to attendance records
        if year_filter:
            attendance_records = attendance_records.filter(student__year=year_filter)
        if program_filter:
            attendance_records = attendance_records.filter(student__program__code=program_filter)
        if college_filter:
            attendance_records = attendance_records.filter(student__college__code=college_filter)

        # Prepare report data for JSON response
        # ONE query for the window's attendance grouped by student (avoids the
        # per-student exists()+filter N+1 = ~5 queries per row)
        attendance_map = {}
        for row in (
            attendance_records
            .order_by("student_id", "timestamp")
            .values("student_id", "status", "timestamp")
        ):
            entry = attendance_map.setdefault(row["student_id"], {"in": None, "out": None})
            if row["status"] == "IN" and entry["in"] is None:
                entry["in"] = row["timestamp"]
            elif row["status"] == "OUT" and entry["out"] is None:
                entry["out"] = row["timestamp"]

        students = students.select_related("college", "program", "major")

        report_data = []
        for student in students:
            entry = attendance_map.get(student.id)
            time_in = entry["in"] if entry else None
            time_out = entry["out"] if entry else None

            status = "ABSENT"
            attendance_date = None

            if time_in and time_out:
                status = "COMPLETED"
                attendance_date = time_out.date()
            elif time_in:
                status = "IN"
                attendance_date = time_in.date()

            # Apply status filter if specified
            if status_filter:
                if status_filter == "present" and status == "ABSENT":
                    continue
                elif status_filter == "absent" and status != "ABSENT":
                    continue
                elif status_filter == "in" and status != "IN":
                    continue
                elif status_filter == "out" and status != "COMPLETED":
                    continue

            report_data.append({
                'student_id': student.student_id,
                'name': student.name,
                'college': student.college_code,
                'program': student.program_code,
                'year': student.year,
                'major': student.major_name,
                'time_in': time_in.strftime("%I:%M:%S %p") if time_in else None,
                'time_out': time_out.strftime("%I:%M:%S %p") if time_out else None,
                'date': attendance_date.strftime("%Y-%m-%d") if attendance_date else None,
                'status': status
            })

        # Calculate stats based on filtered data
        stats = build_event_analytics(students, attendance_records, report_data)

        # Add date info for display
        date_info = ""
        if date_filter:
            date_info = f"Showing report for {date_filter}"
        elif start_date_filter and end_date_filter:
            date_info = f"Showing report from {start_date_filter} to {end_date_filter}"
        else:
            date_info = f"Showing today's report - {date.today()}"

        return JsonResponse({
            'success': True,
            'reports': report_data,
            'stats': stats,
            'date_info': date_info
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


# ---------------- AUTH ----------------
def _dashboard_redirect_for(user):
    """Send an already-signed-in user to their home page (staff -> dashboard,
    scanner operators -> scanner)."""
    if user.is_staff or user.is_superuser:
        return redirect('admin_dashboard')
    return redirect('scanner')


@ratelimit(key='ip', rate='10/h', method='POST', block=True)
@ratelimit(key='ip', rate='3/m', method='POST', block=True)
def register_view(request):
    # Opening /register/ in another tab while already signed in should not
    # show the form again — the session (cookie) already identifies the user.
    if request.user.is_authenticated:
        return _dashboard_redirect_for(request.user)

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        password2 = request.POST.get("password2") or ""

        if not username:
            messages.error(request, "Please choose a username.")
        elif User.objects.filter(username__iexact=username).exists():
            messages.error(request, "Username already taken")
        elif len(password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
        elif password != password2:
            messages.error(request, "Passwords do not match.")
        else:
            User.objects.create_user(
                username=username,
                password=password,
                email=email,
                is_active=False  # 🔒 must be approved by admin
            )
            messages.success(
                request,
                "Registration successful! Please wait for an administrator "
                "to approve your account before logging in."
            )
            return redirect("login")

    return render(request, "qrapp/register.html", {
        "username_value": (request.POST.get("username") or "").strip() if request.method == "POST" else "",
        "email_value": (request.POST.get("email") or "").strip() if request.method == "POST" else "",
    })


@ratelimit(key=client_username, rate='60/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def approve_users(request):
    pending_users = User.objects.filter(is_active=False)
    total_users = User.objects.count()

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        try:
            user = User.objects.get(id=user_id)
            user.is_active = True
            user.save()
            messages.success(request, f"User {user.username} approved!")
            return redirect("approve_users")
        except User.DoesNotExist:
            messages.error(request, "User not found.")

    return render(request, "qrapp/approve_users.html", {
        "pending_users": pending_users,
        "total_users": total_users
    })


@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def ajax_pending_users(request):
    """Realtime feed for the Approve Users pages: the pending list plus
    live counts, polled every few seconds so new registrations appear
    without a page refresh."""
    if request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

    pending = list(User.objects.filter(is_active=False).order_by('-date_joined'))
    return JsonResponse({
        "success": True,
        "count": len(pending),
        "active_users": User.objects.filter(is_active=True).count(),
        "total_users": User.objects.count(),
        "pending": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "date_joined": u.date_joined.strftime("%b %d, %Y %H:%M"),
            }
            for u in pending
        ],
    })


# Brute-force protection: strict per-IP burst + hourly caps on login POSTs.
@ratelimit(key='ip', rate='30/h', method='POST', block=True)
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def login_view(request):
    # Already signed in (e.g. the login URL is opened in a second tab)?
    # The Django session cookie already identifies the user — go straight
    # to their dashboard instead of showing the login form again.
    if request.user.is_authenticated:
        return _dashboard_redirect_for(request.user)

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return _dashboard_redirect_for(user)
        else:
            # authenticate() returns None for is_active=False accounts, which
            # made every pending registration look like a wrong password.
            # Tell "not approved yet" apart from bad credentials.
            if username and User.objects.filter(
                username__iexact=username.strip(), is_active=False
            ).exists():
                messages.warning(
                    request,
                    "Your account has not been approved yet. Please wait for "
                    "an administrator to approve it, then try logging in again."
                )
            else:
                messages.error(request, "Invalid username or password")
    else:
        # Arriving here through a login_required redirect while still holding
        # a session cookie that no longer matches a logged-in user means the
        # session aged out or was flushed: explain instead of showing a bare
        # login form. (logout() deletes the cookie, so manual logouts and
        # browser-close logouts never trigger this.)
        if (
            request.GET.get("next")
            and settings.SESSION_COOKIE_NAME in request.COOKIES
            and not request.user.is_authenticated
        ):
            messages.info(
                request,
                "Your session has expired. Please log in again to continue."
            )
    return render(request, "qrapp/login.html")


def logout_view(request):
    logout(request)
    return redirect('login')


# ---------------- QR CODE GENERATION ----------------

@ratelimit(key=client_username, rate='10/m', block=True)
@staff_member_required
def download_qr_pdf(request):
    # request.GET is a QueryDict: {**request.GET} yields lists per key, which
    # broke .strip() downstream. Flatten to plain str values first.
    params = {key: request.GET.get(key) or "" for key in request.GET}
    response, error = export_qr_response({**params, "format": "pdf"})
    if error:
        return HttpResponse(error, status=404)
    return response


# Preview batches are small and user-driven (modal pagination + retries),
# so allow more per minute than the bulk exports.
@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def ajax_qr_codes(request):
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JsonResponse({"success": False, "error": "Invalid request"}, status=400)

    students = get_filtered_students(request.GET)
    total_count = students.count()

    # Optional pagination: ?page=N&page_size=M streams the modal in batches
    # instead of encoding the whole roster (often thousands of base64 PNGs)
    # in one slow response. No page param => legacy full-list behavior.
    page = request.GET.get("page")
    page_size = request.GET.get("page_size")
    if page or page_size:
        try:
            page = max(int(page or 1), 1)
            page_size = min(max(int(page_size or 50), 1), 200)
        except ValueError:
            page, page_size = 1, 50
        students = students[(page - 1) * page_size : page * page_size]

    qr_list = serialize_qr_list(students)
    return JsonResponse(
        {
            "success": True,
            "count": len(qr_list),
            "qr_list": qr_list,
            "total_count": total_count,
            "page": page if page or page_size else None,
        }
    )


# PDF/ZIP generation for the whole roster is CPU-bound: cap it.
@ratelimit(key=client_username, rate='10/m', block=True)
@staff_member_required
def export_qr_codes(request):
    # Flatten QueryDict to plain str values (see download_qr_pdf).
    params = {key: request.GET.get(key) or "" for key in request.GET}
    response, error = export_qr_response(params)
    if error:
        return HttpResponse(error, status=404)
    return response


def safe_strip(value, default="NA"):
    """Helper to safely strip values or return default if None"""
    if value is None:
        return default
    value_str = str(value).strip()
    return value_str if value_str else default


# Bulk import is expensive: cap how often it can run.
@ratelimit(key=client_username, rate='20/h', method='POST', block=True)
@staff_member_required
def upload_pdf(request):
    """Import students from PDF, CSV, or Excel upload."""
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        upload_file = request.FILES.get('student_file') or request.FILES.get('pdf_file')

        if not upload_file:
            error_msg = "Please select a file to upload."
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg}, status=400)
            messages.error(request, error_msg)
            return render(request, 'qrapp/upload_pdf.html', {'form': StudentUploadForm()})

        from django.utils.datastructures import MultiValueDict
        files = MultiValueDict({'student_file': [upload_file]})
        form = StudentUploadForm(request.POST, files)
        if not form.is_valid():
            error_msg = form.errors.get('student_file', ['Invalid file upload'])[0]
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg}, status=400)
            messages.error(request, error_msg)
            return render(request, 'qrapp/upload_pdf.html', {'form': form})

        upload_file = form.cleaned_data['student_file']
        extension = os.path.splitext(upload_file.name)[1].lower() or '.pdf'
        temp_path = os.path.join(settings.MEDIA_ROOT, f"temp_upload{extension}")
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)

        try:
            with open(temp_path, 'wb+') as destination:
                for chunk in upload_file.chunks():
                    destination.write(chunk)

            allow_generated_ids = request.POST.get('allow_generated_ids') in ('on', 'true', '1', 'yes')
            result = import_students_from_file(
                temp_path,
                allow_generated_ids=allow_generated_ids,
                source_file=upload_file.name,
            )
            success_message = result['message']

            if is_ajax:
                return JsonResponse({'success': True, 'message': success_message})
            messages.success(request, success_message)
            return redirect('generate_all_qr')

        except Exception as e:
            error_msg = f"Error processing file: {str(e)}"
            print(error_msg)
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg}, status=400)
            messages.error(request, error_msg)

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        return render(request, 'qrapp/upload_pdf.html', {'form': StudentUploadForm()})

    return render(request, 'qrapp/upload_pdf.html', {'form': StudentUploadForm()})


@ratelimit(key=client_username, rate='10/m', block=True)
@staff_member_required
def export_students(request):
    """Export filtered student roster as CSV or Excel."""
    response, error = export_students_response(request.GET)
    if error:
        return HttpResponse(error, status=404)
    return response


# Attendance report export is DB-bound over the whole roster: cap it.
@ratelimit(key=client_username, rate='10/m', block=True)
@staff_member_required
def export_attendance(request):
    """Export the per-student attendance report (CSV/Excel) with filters."""
    response, error = export_attendance_response(request.GET)
    if error:
        return HttpResponse(error, status=404)
    return response


# ---------------- OTHER ----------------
# Destructive: wipes the roster. Tight cap on POSTs only.
@ratelimit(key=client_username, rate='5/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def delete_all_qr(request):
    students = list(Student.objects.only("student_id", "name"))
    student_count = len(students)

    # Remove each student's QR image while the records still exist to build
    # the filename from (current and legacy suffixes), then delete the roster.
    for student in students:
        base = qr_image_basename(student)
        for suffix in (".png", "_label.png", "_qr.png"):
            path = os.path.join(settings.MEDIA_ROOT, base + suffix)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
    Student.objects.all().delete()

    # Sweep images left over from the old "{id}_{lastname}_label/_qr" naming.
    for pattern in ("*_label.png", "*_qr.png"):
        for f in glob.glob(os.path.join(settings.MEDIA_ROOT, pattern)):
            try:
                os.remove(f)
            except OSError:
                pass

    messages.success(request, f"Deleted {student_count} students and their QR codes!")
    return redirect('generate_all_qr')


@ratelimit(key=client_username, rate='60/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def add_student(request):
    if request.method == 'POST':
        # Only treat as AJAX when the client explicitly sends the header
        # ($.post always does). Checking request.POST.get('student_id') here
        # made the plain add_student page form print raw JSON to the browser
        # instead of rendering, because that form also posts 'student_id'.
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # Handle AJAX request
            try:
                from .college_program import resolve_college_and_program, resolve_program_and_major
                college, program = resolve_college_and_program(
                    request.POST.get('college'),
                    request.POST.get('program'),
                )
                # A major value naming a program (e.g. "Animal Science")
                # resolves to that program with no major, never a major.
                program, major = resolve_program_and_major(
                    college, program, request.POST.get('major'), create=True,
                )
                # Program code is authoritative; keep college consistent.
                if program is not None:
                    college = program.college
                student = Student(
                    student_id=request.POST.get('student_id'),
                    name=request.POST.get('name'),
                    sex=request.POST.get('sex'),
                    college=college,
                    program=program,
                    year=request.POST.get('year'),
                    major=major
                )
                student.save()
                # Send back the new student's QR page so the UI can display
                # and download it immediately after the add.
                return JsonResponse({
                    'success': True,
                    'message': 'Student added successfully!',
                    'student_pk': student.pk,
                    'qr_page_url': reverse('new_student_qr', args=[student.pk]),
                    'qr_download_url': reverse('student_qr_image', args=[student.pk]) + '?download=1',
                })
            except Exception as e:
                return JsonResponse({'success': False, 'error': str(e)}, status=400)
        else:
            # Handle regular form submission
            form = StudentForm(request.POST)
            if form.is_valid():
                student = form.save()
                messages.success(request, "Student added successfully! Their QR code is shown below.")
                # Go straight to the new student's QR page so it can be
                # viewed/downloaded right away.
                return redirect('new_student_qr', student_id=student.pk)
            else:
                messages.error(request, "Please correct the errors below.")
    else:
        form = StudentForm()
    return render(request, 'qrapp/add_student.html', {'form': form})


@staff_member_required
def new_student_qr(request, student_id):
    """Show the QR code of a newly added student, with a download button."""
    student = get_object_or_404(
        Student.objects.select_related("college", "program", "major"), pk=student_id
    )
    return render(request, 'qrapp/new_student_qr.html', {'student': student})


@staff_member_required
def student_qr_image(request, student_id):
    """Serve a student's labeled QR PNG. ?download=1 forces a file download."""
    student = get_object_or_404(
        Student.objects.select_related("college", "program", "major"), pk=student_id
    )
    from .qr_codes import build_qr_labeled_image, qr_image_basename

    buffer = io.BytesIO()
    build_qr_labeled_image(student).save(buffer, format="PNG")
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="image/png")
    filename = f"{qr_image_basename(student)}_qr.png"
    if request.GET.get("download"):
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    else:
        response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


# The page now renders student metadata only — QR PNGs are generated
# on-demand per card by student_qr_image as they scroll into view, so this
# stays fast even for a full roster.
@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def generate_all_qr(request):
    students = (
        Student.objects.select_related("college", "program", "major")
        .order_by("name", "student_id")
    )
    search = (request.GET.get("search") or "").strip()
    if search:
        students = students.filter(
            Q(name__icontains=search) | Q(student_id__icontains=search)
        )

    total_count = Student.objects.count()
    return render(request, "qrapp/all_qr.html", {
        "students": students,
        "search": search,
        "match_count": students.count(),
        "student_count": total_count,
        "import_issue_count": _import_issue_count(),
    })


# ---------------- IMPORT ISSUES ----------------
def _import_issue_count():
    """Badge count for the Students section buttons (one COUNT query)."""
    return ImportIssue.objects.count()


@ratelimit(key=client_username, rate='60/m', block=True)
@staff_member_required
def import_issues(request):
    """Review the students a roster import could not store as printed.

    Backs the "Import Issues" button in the Students section: rows with no
    student number, numbers printed for two different students, students
    listed twice, unreadable rows, and students stored under a generated
    number (see ``ImportIssue``).
    """
    issues = (
        ImportIssue.objects
        .select_related("program", "program__college")
        .order_by("issue_type", "name", "student_id")
    )
    counts = {
        row["issue_type"]: row["total"]
        for row in ImportIssue.objects.values("issue_type").annotate(total=Count("id"))
    }
    # Per-college export buttons: colleges that actually have issues.
    colleges_with_issues = (
        ImportIssue.objects
        .exclude(program__college__code="")
        .values("program__college__code", "program__college__name")
        .annotate(total=Count("id"))
        .order_by("program__college__code")
    )
    return render(request, "qrapp/import_issues.html", {
        "issues": issues,
        "issue_counts": counts,
        "issue_choices": ImportIssue.ISSUE_CHOICES,
        "colleges_with_issues": colleges_with_issues,
        "total_issues": sum(counts.values()),
        "now": timezone.now(),
    })


@ratelimit(key=client_username, rate='30/m', block=True)
@staff_member_required
def dismiss_import_issue(request, issue_id):
    """Remove one reviewed issue from the list (e.g. the student was fixed)."""
    issue = get_object_or_404(ImportIssue, pk=issue_id)
    label = str(issue)
    issue.delete()
    messages.success(request, f"Dismissed: {label}")
    return redirect("import_issues")


@ratelimit(key=client_username, rate='30/m', block=True)
@staff_member_required
def export_import_issues_pdf(request):
    """Download the import-issue review list as a labelled PDF.

    One row per issue; a number the registrar printed for two different
    students gets a row for both students. The student-number column shows
    ``no_id`` for rows the file carried no number for.
    """
    from .import_issue_export import export_import_issues_pdf as build_pdf

    college_code = (request.GET.get("college") or "").strip()
    response, error = build_pdf(college_code)
    if error:
        messages.error(request, error)
        return redirect("import_issues")
    return response


@ratelimit(key=client_username, rate='5/m', block=True)
@staff_member_required
def clear_import_issues(request):
    """Empty the whole review list (the next import repopulates it)."""
    removed = ImportIssue.objects.count()
    ImportIssue.objects.all().delete()
    messages.success(request, f"Cleared {removed} import issue(s).")
    return redirect("import_issues")


# ---------------- COLLEGE MANAGEMENT ----------------
@ratelimit(key=client_username, rate='60/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def manage_colleges(request):
    """View to list and manage colleges"""
    from .models import College
    import json

    colleges = College.objects.annotate(
        student_count=Count('students', distinct=True)
    ).order_by('code')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            code = request.POST.get('code', '').strip().upper()
            name = request.POST.get('name', '').strip()

            if code and name:
                if not College.objects.filter(code=code).exists():
                    College.objects.create(code=code, name=name, is_active=True)
                    messages.success(request, f'College {code} added successfully!')
                else:
                    messages.error(request, f'College code {code} already exists!')
            else:
                messages.error(request, 'Both code and name are required!')

        elif action == 'edit':
            college_id = request.POST.get('college_id')
            code = request.POST.get('code', '').strip().upper()
            name = request.POST.get('name', '').strip()
            is_active = request.POST.get('is_active') == 'on'

            try:
                college = College.objects.get(id=college_id)
                # Check if code is being changed and if new code already exists
                if code != college.code and College.objects.filter(code=code).exists():
                    messages.error(request, f'College code {code} already exists!')
                else:
                    college.code = code
                    college.name = name
                    college.is_active = is_active
                    college.save()
                    messages.success(request, f'College {code} updated successfully!')
            except College.DoesNotExist:
                messages.error(request, 'College not found!')

        elif action == 'delete':
            college_id = request.POST.get('college_id')
            try:
                college = College.objects.get(id=college_id)
                # Check if any students are using this college
                student_count = Student.objects.filter(college=college).count()
                if student_count > 0:
                    messages.warning(request,
                                     f'Cannot delete {college.code}! {student_count} students are still enrolled in this college. Please reassign them first.')
                else:
                    college.delete()
                    messages.success(request, f'College {college.code} deleted successfully!')
            except College.DoesNotExist:
                messages.error(request, 'College not found!')

        # College changes feed lookup dropdowns: drop caches.
        invalidate_lookup_caches()
        return redirect('manage_colleges')

    # Get active colleges for the dropdown
    active_colleges = College.objects.filter(is_active=True).order_by('code')

    # Count students per college. Built from the annotation above (single
    # query, always present even for colleges with zero students) instead of
    # a per-college Student.objects.filter(...).count() loop, which used to
    # leave codes with no students out of the dict / stuck on "Loading...".
    college_stats = {college.code: college.student_count for college in colleges}
    total_students = sum(college_stats.values())

    return render(request, 'qrapp/manage_colleges.html', {
        'colleges': colleges,
        'active_colleges': active_colleges,
        'total_students': total_students,
        # Raw object: template embeds it via |json_script
        'college_stats': college_stats
    })


@ratelimit(key=client_username, rate='120/m', block=True)
@staff_member_required
def get_colleges_json(request):
    """AJAX endpoint to get colleges for dropdowns (cached)."""
    from .models import College

    active_only = request.GET.get('active_only', 'true').lower() == 'true'

    lookup_cache = caches["lookups"]
    cache_key = build_lookups_cache_key("colleges", request.user.pk, f"active={active_only}")
    payload = lookup_cache.get(cache_key)

    if payload is None:
        if active_only:
            colleges = College.objects.filter(is_active=True).order_by('code')
        else:
            colleges = College.objects.all().order_by('code')

        college_list = [
            {
                'id': college.id,
                'code': college.code,
                'name': college.name,
                'is_active': college.is_active
            }
            for college in colleges
        ]
        payload = {
            'success': True,
            'colleges': college_list
        }
        lookup_cache.set(cache_key, payload, timeout=LOOKUPS_CACHE_TIMEOUT)

    return JsonResponse(payload)


@ratelimit(key=client_username, rate='120/m', block=True)
@staff_member_required
def get_programs_json(request, college_code):
    """AJAX endpoint to get programs for a specific college (cached)."""
    from .models import College, Program

    try:
        college = College.objects.get(code=college_code)
    except College.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'College not found'
        }, status=404)

    active_only = request.GET.get('active_only', 'true').lower() == 'true'

    lookup_cache = caches["lookups"]
    cache_key = build_lookups_cache_key(
        "programs", request.user.pk, f"college={college_code};active={active_only}"
    )
    payload = lookup_cache.get(cache_key)

    if payload is None:
        if active_only:
            programs = Program.objects.filter(college=college, is_active=True).order_by('code')
        else:
            programs = Program.objects.filter(college=college).order_by('code')

        program_list = [
            {
                'id': program.id,
                'code': program.code,
                'name': program.name,
                'is_active': program.is_active
            }
            for program in programs
        ]
        payload = {
            'success': True,
            'programs': program_list
        }
        lookup_cache.set(cache_key, payload, timeout=LOOKUPS_CACHE_TIMEOUT)

    return JsonResponse(payload)


@ratelimit(key=client_username, rate='60/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def manage_programs(request, college_id):
    """View to manage programs (and their majors) for a specific college"""
    from .models import College, Program, Major

    college = get_object_or_404(College, id=college_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()

            if code and name:
                if not Program.objects.filter(college=college, code=code).exists():
                    Program.objects.create(college=college, code=code, name=name, is_active=True)
                    messages.success(request, f'Program {code} added successfully to {college.code}!')
                else:
                    messages.error(request, f'Program code {code} already exists in {college.code}!')
            else:
                messages.error(request, 'Both code and name are required!')

        elif action == 'edit':
            program_id = request.POST.get('program_id')
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()
            is_active = request.POST.get('is_active') == 'on'

            try:
                program = Program.objects.get(id=program_id, college=college)
                # Check if code is being changed and if new code already exists
                if code != program.code and Program.objects.filter(college=college, code=code).exists():
                    messages.error(request, f'Program code {code} already exists in {college.code}!')
                else:
                    program.code = code
                    program.name = name
                    program.is_active = is_active
                    program.save()
                    messages.success(request, f'Program {code} updated successfully!')
            except Program.DoesNotExist:
                messages.error(request, 'Program not found!')

        elif action == 'delete':
            program_id = request.POST.get('program_id')
            try:
                program = Program.objects.get(id=program_id, college=college)
                # Check if any students are using this program
                student_count = Student.objects.filter(program=program).count()
                if student_count > 0:
                    messages.warning(request,
                                     f'Cannot delete {program.code}! {student_count} students are enrolled in this program. Please reassign them first.')
                else:
                    program.delete()
                    messages.success(request, f'Program {program.code} deleted successfully!')
            except Program.DoesNotExist:
                messages.error(request, 'Program not found!')

        elif action == 'add_major':
            program_id = request.POST.get('program_id')
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()

            try:
                program = Program.objects.get(id=program_id, college=college)
                if code and name:
                    if not Major.objects.filter(program=program, code=code).exists():
                        Major.objects.create(program=program, code=code, name=name, is_active=True)
                        messages.success(request, f'Major {code} added successfully to {program.code}!')
                    else:
                        messages.error(request, f'Major code {code} already exists in {program.code}!')
                else:
                    messages.error(request, 'Both code and name are required!')
            except Program.DoesNotExist:
                messages.error(request, 'Program not found!')

        elif action == 'edit_major':
            major_id = request.POST.get('major_id')
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()
            is_active = request.POST.get('is_active') == 'on'

            try:
                major = Major.objects.get(id=major_id, program__college=college)
                if code != major.code and Major.objects.filter(program=major.program, code=code).exists():
                    messages.error(request, f'Major code {code} already exists in {major.program.code}!')
                else:
                    major.code = code
                    major.name = name
                    major.is_active = is_active
                    major.save()
                    messages.success(request, f'Major {code} updated successfully!')
            except Major.DoesNotExist:
                messages.error(request, 'Major not found!')

        elif action == 'delete_major':
            major_id = request.POST.get('major_id')
            try:
                major = Major.objects.get(id=major_id, program__college=college)
                # Check if any students are using this major
                student_count = Student.objects.filter(major=major).count()
                if student_count > 0:
                    messages.warning(request,
                                     f'Cannot delete {major.code}! {student_count} students are enrolled in this major. Please reassign them first.')
                else:
                    code = major.code
                    major.delete()
                    messages.success(request, f'Major {code} deleted successfully!')
            except Major.DoesNotExist:
                messages.error(request, 'Major not found!')

        # Program/major changes feed lookup dropdowns: drop caches.
        invalidate_lookup_caches()
        return redirect('manage_programs', college_id=college_id)

    # Pull programs together with their majors in as few queries as
    # possible, and annotate real (always-present, including zero) student
    # counts at every level of the College -> Program -> Major hierarchy
    # instead of the old per-object Student.objects.filter(...).count()
    # loop, which silently dropped codes with no students and never
    # surfaced majors at all.
    programs = Program.objects.filter(college=college).annotate(
        student_count=Count('students', distinct=True)
    ).order_by('code').prefetch_related(
        Prefetch(
            'majors',
            queryset=Major.objects.annotate(
                student_count=Count('students', distinct=True)
            ).order_by('code'),
        )
    )

    program_stats = {}
    major_stats = {}
    programs_with_majors = []

    for program in programs:
        majors = list(program.majors.all())
        majors_student_total = sum(major.student_count for major in majors)
        # Students attached to the program directly (no major chosen) --
        # only meaningful when the program actually has majors defined.
        direct_count = program.student_count - majors_student_total

        program_stats[program.code] = program.student_count
        for major in majors:
            major_stats[major.code] = major.student_count

        programs_with_majors.append({
            'program': program,
            'majors': majors,
            'direct_count': direct_count,
        })

    # Overall total for this college: sum of its programs' student counts.
    # (Equal to Student.objects.filter(college=college).count() when data
    # is consistent, since Student.clean() enforces program.college ==
    # student.college.)
    college_total = sum(program_stats.values())

    return render(request, 'qrapp/manage_programs.html', {
        'college': college,
        'college_total': college_total,
        'programs': programs,
        'programs_with_majors': programs_with_majors,
        # Raw objects: template embeds them via |json_script
        'program_stats': program_stats,
        'major_stats': major_stats,
    })


# ---------------- USER MANAGEMENT ----------------
@ratelimit(key=client_username, rate='60/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def manage_users(request):
    """AJAX handler for user management operations"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})

    action = request.POST.get('action')

    if action == 'add':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        is_staff = request.POST.get('is_staff') == 'true'
        is_active = request.POST.get('is_active') == 'true'

        if not username or not password:
            return JsonResponse({'success': False, 'error': 'Username and password are required'})

        if User.objects.filter(username=username).exists():
            return JsonResponse({'success': False, 'error': f'Username {username} already exists'})

        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_staff=is_staff,
                is_active=is_active
            )
            return JsonResponse({
                'success': True,
                'message': f'User {username} created successfully',
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'is_staff': user.is_staff,
                    'is_active': user.is_active,
                    'date_joined': user.date_joined.strftime('%b %d, %Y %H:%M')
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    elif action == 'edit':
        user_id = request.POST.get('user_id')
        email = request.POST.get('email', '').strip()
        is_staff = request.POST.get('is_staff') == 'true'
        is_active = request.POST.get('is_active') == 'true'
        new_password = request.POST.get('new_password', '').strip()

        try:
            user = User.objects.get(id=user_id)

            # Don't allow editing superuser
            if user.is_superuser and not request.user.is_superuser:
                return JsonResponse({'success': False, 'error': 'Cannot edit superuser'})

            user.email = email
            user.is_staff = is_staff
            user.is_active = is_active

            if new_password:
                user.set_password(new_password)

            user.save()

            return JsonResponse({
                'success': True,
                'message': f'User {user.username} updated successfully',
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'is_staff': user.is_staff,
                    'is_active': user.is_active,
                    'date_joined': user.date_joined.strftime('%b %d, %Y %H:%M')
                }
            })
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'User not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    elif action == 'delete':
        user_id = request.POST.get('user_id')

        try:
            user = User.objects.get(id=user_id)

            # Don't allow deleting superuser or self
            if user.is_superuser:
                return JsonResponse({'success': False, 'error': 'Cannot delete superuser'})
            if user.id == request.user.id:
                return JsonResponse({'success': False, 'error': 'Cannot delete yourself'})

            username = user.username
            user.delete()

            return JsonResponse({
                'success': True,
                'message': f'User {username} deleted successfully'
            })
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'User not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    elif action == 'approve':
        user_id = request.POST.get('user_id')

        try:
            user = User.objects.get(id=user_id)
            user.is_active = True
            user.save()

            return JsonResponse({
                'success': True,
                'message': f'User {user.username} approved successfully',
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'is_staff': user.is_staff,
                    'is_active': user.is_active,
                    'date_joined': user.date_joined.strftime('%b %d, %Y %H:%M')
                }
            })
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'User not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({'success': False, 'error': 'Invalid action'})


# ---------------- EVENT MANAGEMENT ----------------
def _parse_optional_time(value):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


@ratelimit(key=client_username, rate='60/m', method=ratelimit.UNSAFE, block=True)
@staff_member_required
def manage_events(request):
    """Create and manage attendance events/sessions."""
    events = Event.objects.all().order_by("-is_current", "-event_date", "-start_time", "title")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add":
            title = (request.POST.get("title") or "").strip()
            description = (request.POST.get("description") or "").strip()
            location = (request.POST.get("location") or "").strip()
            event_date_raw = (request.POST.get("event_date") or "").strip()
            start_time = _parse_optional_time(request.POST.get("start_time"))
            end_time = _parse_optional_time(request.POST.get("end_time"))
            is_current = request.POST.get("is_current") == "on"

            if not title:
                messages.error(request, "Event title is required.")
            else:
                try:
                    event_date = datetime.strptime(event_date_raw,
                                                   "%Y-%m-%d").date() if event_date_raw else date.today()
                except ValueError:
                    event_date = date.today()

                event = Event(
                    title=title,
                    description=description,
                    location=location,
                    event_date=event_date,
                    start_time=start_time,
                    end_time=end_time,
                    is_active=True,
                    is_current=is_current,
                    created_by=request.user,
                )
                picture = request.FILES.get("picture")
                if picture:
                    event.picture = picture
                event.save()
                messages.success(request, f'Event "{title}" created successfully!')

        elif action == "edit":
            event_id = request.POST.get("event_id")
            title = (request.POST.get("title") or "").strip()
            description = (request.POST.get("description") or "").strip()
            location = (request.POST.get("location") or "").strip()
            event_date_raw = (request.POST.get("event_date") or "").strip()
            start_time = _parse_optional_time(request.POST.get("start_time"))
            end_time = _parse_optional_time(request.POST.get("end_time"))
            is_active = request.POST.get("is_active") == "on"
            is_current = request.POST.get("is_current") == "on"

            try:
                event = Event.objects.get(id=event_id)
                if not title:
                    messages.error(request, "Event title is required.")
                else:
                    try:
                        event_date = datetime.strptime(event_date_raw,
                                                       "%Y-%m-%d").date() if event_date_raw else event.event_date
                    except ValueError:
                        event_date = event.event_date

                    event.title = title
                    event.description = description
                    event.location = location
                    event.event_date = event_date
                    event.start_time = start_time
                    event.end_time = end_time
                    event.is_active = is_active
                    event.is_current = is_current
                    picture = request.FILES.get("picture")
                    if picture:
                        if event.picture:
                            event.picture.delete(save=False)
                        event.picture = picture
                    elif request.POST.get("clear_picture") == "on":
                        if event.picture:
                            event.picture.delete(save=False)
                        event.picture = None
                    event.save()
                    messages.success(request, f'Event "{title}" updated successfully!')
            except Event.DoesNotExist:
                messages.error(request, "Event not found!")

        elif action == "set_current":
            event_id = request.POST.get("event_id")
            try:
                event = Event.objects.get(id=event_id)
                event.is_active = True
                event.is_current = True
                event.save()
                messages.success(request, f'"{event.title}" is now the current scanning event.')
            except Event.DoesNotExist:
                messages.error(request, "Event not found!")

        elif action == "delete":
            event_id = request.POST.get("event_id")
            try:
                event = Event.objects.get(id=event_id)
                scan_count = event.attendance_records.count()
                title = event.title
                if event.picture:
                    event.picture.delete(save=False)
                event.delete()
                messages.success(
                    request,
                    f'Event "{title}" deleted.'
                    + (f" {scan_count} related scans were unlinked." if scan_count else ""),
                )
            except Event.DoesNotExist:
                messages.error(request, "Event not found!")

        # Event data feeds the calendar and lookup dropdowns: drop caches.
        invalidate_calendar_cache()
        invalidate_lookup_caches()
        return redirect("manage_events")

    event_stats = {
        str(event.id): event.attendance_records.count()
        for event in events
    }

    return render(request, "qrapp/manage_events.html", {
        "events": events,
        "today": date.today().isoformat(),
        # Raw object: template embeds it via |json_script
        "event_stats": event_stats,
    })


@ratelimit(key=client_username, rate='120/m', block=True)
@staff_member_required
def get_events_json(request):
    """AJAX endpoint for active events (scanner / filters), cached."""
    active_only = request.GET.get("active_only", "true").lower() == "true"

    lookup_cache = caches["lookups"]
    cache_key = build_lookups_cache_key("events", request.user.pk, f"active={active_only}")
    payload = lookup_cache.get(cache_key)

    if payload is None:
        events = Event.objects.all().order_by("-is_current", "-event_date", "title")
        if active_only:
            events = events.filter(is_active=True)

        event_payload = [
            {
                "id": event.id,
                "title": event.title,
                "event_date": event.event_date.isoformat(),
                "location": event.location,
                "is_current": event.is_current,
                "is_active": event.is_active,
                "display_window": event.display_window,
                "picture_url": event.picture.url if event.picture else "",
            }
            for event in events
        ]
        payload = {"success": True, "events": event_payload}
        lookup_cache.set(cache_key, payload, timeout=LOOKUPS_CACHE_TIMEOUT)

    return JsonResponse(payload)


@ratelimit(key=client_username, rate='120/m', block=True)
@staff_member_required
def get_majors_json(request, program_code):
    """AJAX endpoint to get majors for a specific program (cached)."""
    program = Program.objects.filter(code__iexact=program_code).select_related("college").first()
    if not program:
        return JsonResponse({"success": True, "majors": []})

    active_only = request.GET.get("active_only", "true").lower() == "true"

    lookup_cache = caches["lookups"]
    cache_key = build_lookups_cache_key(
        "majors", request.user.pk, f"program={program.code};active={active_only}"
    )
    payload = lookup_cache.get(cache_key)

    if payload is None:
        majors = Major.objects.filter(program=program)
        if active_only:
            majors = majors.filter(is_active=True)

        payload = {
            "success": True,
            "majors": [
                {
                    "id": major.id,
                    "code": major.code,
                    "name": major.name,
                    "is_active": major.is_active,
                }
                for major in majors.order_by("code")
            ],
        }
        lookup_cache.set(cache_key, payload, timeout=LOOKUPS_CACHE_TIMEOUT)

    return JsonResponse(payload)

# ---------------- REALTIME: scans since a given id ----------------

@ratelimit(key=client_username, rate='180/m', block=True)
@staff_member_required
def ajax_sidebar_logs(request):
    """
    Realtime feed for the sidebar "Live Logs" widget. Returns the most recent
    attendance records (time-in / time-out) for today, newest first, so the
    sidebar updates without a page refresh.

    Optional GET params:
      limit   – max rows to return (default 15, capped at 50)
      since_id – only rows newer than this id (avoids re-pushing old rows)
    """
    try:
        limit = min(max(int(request.GET.get("limit", 15) or 15), 1), 50)
    except (TypeError, ValueError):
        limit = 15

    try:
        since_id = int(request.GET.get("since_id", 0) or 0)
    except (TypeError, ValueError):
        since_id = 0

    records = (
        Attendance.objects.select_related("student", "student__program", "student__college")
        .filter(timestamp__date=date.today())
    )
    if since_id:
        records = records.filter(id__gt=since_id)

    records = records.order_by("-timestamp", "-id")[:limit]

    rows = [
        {
            "id": r.id,
            "name": r.student.name,
            "student_id": r.student.student_id,
            "status": r.status,
            "time": r.timestamp.strftime("%I:%M:%S %p"),
            "event": (r.event_label or (r.event.title if r.event else "")),
            "source": r.source,
            "college": r.student.college_code,
        }
        for r in records
    ]

    return JsonResponse({
        "success": True,
        "rows": rows,
        # USE_TZ is False, so datetime.now() is already local (Asia/Manila).
        "server_time": datetime.now().strftime("%I:%M:%S %p"),
    })

@ratelimit(key=client_username, rate='180/m', block=True)
@staff_member_required
def scans_since(request):
    """
    Returns attendance rows with id > since, respecting the same filters
    used by the dashboard table. Polled every few seconds by the browser.
    """
    try:
        since_id = int(request.GET.get("since", 0) or 0)
    except (TypeError, ValueError):
        since_id = 0

    # ---- filters (mirror ajax_dashboard_data) ----
    year_filter = request.GET.get("year")
    program_filter = request.GET.get("program")
    college_filter = request.GET.get("college")
    search_query = request.GET.get("search")
    status_filter = request.GET.get("status")
    date_filter = request.GET.get("date")
    start_date_filter = request.GET.get("start_date")
    end_date_filter = request.GET.get("end_date")

    records = Attendance.objects.select_related(
        "student", "student__program", "student__college"
    )

    # Date scoping
    selected_date = date.today()
    if date_filter:
        try:
            selected_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
            records = records.filter(timestamp__date=selected_date)
        except ValueError:
            records = records.filter(timestamp__date=selected_date)
    elif start_date_filter and end_date_filter:
        try:
            sd = datetime.strptime(start_date_filter, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date_filter, "%Y-%m-%d").date()
            records = records.filter(timestamp__date__range=[sd, ed])
        except ValueError:
            records = records.filter(timestamp__date=selected_date)
    else:
        records = records.filter(timestamp__date=selected_date)

    # Filter by year / program / college
    if year_filter:
        records = records.filter(student__year=year_filter)
    if program_filter:
        records = records.filter(student__program__code=program_filter)
    if college_filter:
        records = records.filter(student__college__code=college_filter)
    if search_query:
        records = records.filter(
            Q(student__name__icontains=search_query)
            | Q(student__student_id__icontains=search_query)
        )
    if status_filter in ("in", "out"):
        records = records.filter(status=status_filter.upper())

    # Only rows newer than the client's last known id
    new_records = records.filter(id__gt=since_id).order_by("id")

    rows = [
        {
            "id": r.id,
            "student_id": r.student.student_id,
            "name": r.student.name,
            "college": r.student.college_code,
            "program": r.student.program_code,
            "year": r.student.year,
            "major": r.student.major_name,
            "status": r.status,
            "date": r.timestamp.strftime("%d/%m/%Y"),
            "time": r.timestamp.strftime("%I:%M %p"),
        }
        for r in new_records[:50]     # cap to avoid huge bursts
    ]

    # Highest id we've seen, so client can advance its cursor
    latest_id = since_id
    if rows:
        latest_id = rows[-1]["id"]
    else:
        # If no new rows, report the current max in the filtered window
        # so the client doesn't re-query the entire table every tick.
        highest = (
            records.order_by("-id").values_list("id", flat=True).first()
        )
        if highest:
            latest_id = max(since_id, highest) if since_id else highest

    return JsonResponse({
        "success": True,
        "rows": rows,
        "latest_id": latest_id,
    })