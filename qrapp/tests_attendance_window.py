"""The attendance views must fall back to the newest day that HAS scans.

Regression tests for the "I cannot fetch the attendance records" bug: the
dashboard analytics cards, the records table, the reports tab and the Live
Logs feed all used to apply ``timestamp__date=date.today()`` whenever the Date
box was left empty. Scans are stamped with the event's own date, so on the day
after an event every one of those views showed "Expected 4396 / Attended 0 /
Absent 4396 / Scans 0" and "No attendance records found" while 4,373 rows sat
untouched in ``qrapp_attendance``.
"""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from qrapp.models import Attendance, College, Program, Student
from qrapp.views import admin_dashboard, latest_attendance_date


def make_event_day_scan(student, day, hour, status="IN"):
    """Attendance rows are naive datetimes here (USE_TZ = False)."""
    return Attendance.objects.create(
        student=student,
        status=status,
        timestamp=datetime.combine(day, time(hour, 0)),
    )


class LatestAttendanceDateTests(TestCase):
    """The helper that decides which day an unfiltered view shows."""

    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(code="TSTC", name="Test College")
        cls.program = Program.objects.create(
            college=cls.college, code="TSTP", name="Test Program"
        )
        cls.student = Student.objects.create(
            student_id="T-0001",
            name="Test Student",
            sex="M",
            year=1,
            college=cls.college,
            program=cls.program,
        )

    def test_returns_the_newest_day_that_has_scans(self):
        older = date.today() - timedelta(days=4)
        newest = date.today() - timedelta(days=1)
        make_event_day_scan(self.student, older, 8)
        make_event_day_scan(self.student, newest, 9)

        self.assertEqual(latest_attendance_date(), newest)

    def test_no_date_filter_defaults_to_the_scan_day_not_today(self):
        scan_day = date.today() - timedelta(days=1)
        make_event_day_scan(self.student, scan_day, 8)

        self.assertNotEqual(latest_attendance_date(), date.today())
        self.assertEqual(latest_attendance_date(), scan_day)

    def test_explicit_fallback_is_used(self):
        fallback = date(2020, 1, 1)

        self.assertEqual(latest_attendance_date(fallback=fallback), fallback)


class EmptyAttendanceFallbackTests(TestCase):
    """With no scans at all the views keep behaving like before (today)."""

    def test_falls_back_to_today(self):
        self.assertEqual(latest_attendance_date(), date.today())


class AttendanceWindowViewTests(TestCase):
    """Unfiltered requests show the newest scan day; a typed date still wins."""

    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(code="TSTC2", name="Test College 2")
        cls.program = Program.objects.create(
            college=cls.college, code="TSTP2", name="Test Program 2"
        )
        cls.student = Student.objects.create(
            student_id="T-0002",
            name="Scanned Student",
            sex="F",
            year=2,
            college=cls.college,
            program=cls.program,
        )
        cls.scan_day = date.today() - timedelta(days=1)
        make_event_day_scan(cls.student, cls.scan_day, 8, status="IN")
        make_event_day_scan(cls.student, cls.scan_day, 17, status="OUT")

        cls.staff = User.objects.create_user(
            "windowstaff", "window@x.com", "pw", is_staff=True
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.staff)

    def get_json(self, url_name, **params):
        url = reverse(url_name)
        if params:
            url = f"{url}?{urlencode(params)}"
        response = self.client.get(
            url, headers={"x-requested-with": "XMLHttpRequest"}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_dashboard_ajax_defaults_to_the_latest_scan_day(self):
        payload = self.get_json("ajax_dashboard_data")

        self.assertTrue(payload["success"])
        # Both scans of the event day come back, even though it is not today.
        self.assertEqual(len(payload["records"]), 2)
        self.assertEqual(payload["stats"]["attended"], 1)
        self.assertEqual(payload["stats"]["absent"], 0)
        self.assertEqual(payload["stats"]["total_scans"], 2)
        self.assertIn(self.scan_day.isoformat(), payload["date_info"])
        self.assertNotIn(date.today().isoformat(), payload["date_info"])

    def test_dashboard_ajax_explicit_date_of_the_scan_day_is_unchanged(self):
        payload = self.get_json("ajax_dashboard_data", date=self.scan_day.isoformat())

        self.assertEqual(len(payload["records"]), 2)
        self.assertEqual(
            payload["date_info"], f"Showing attendance for {self.scan_day}"
        )

    def test_explicit_date_without_scans_still_shows_nothing(self):
        payload = self.get_json("ajax_dashboard_data", date=date.today().isoformat())

        self.assertEqual(payload["records"], [])
        self.assertEqual(payload["stats"]["attended"], 0)

    def test_reports_ajax_defaults_to_the_latest_scan_day(self):
        payload = self.get_json("ajax_reports_data")

        rows = {row["student_id"]: row for row in payload["reports"]}
        scanned = rows["T-0002"]
        self.assertEqual(scanned["status"], "COMPLETED")
        self.assertIsNotNone(scanned["time_in"])
        self.assertIsNotNone(scanned["time_out"])
        self.assertIn(self.scan_day.isoformat(), payload["date_info"])

    def test_sidebar_logs_defaults_to_the_latest_scan_day(self):
        payload = self.get_json("ajax_sidebar_logs")

        self.assertTrue(payload["success"])
        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(payload["rows"][0]["student_id"], "T-0002")

    def test_scans_since_defaults_to_the_latest_scan_day(self):
        payload = self.get_json("scans_since", since=0)

        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(payload["rows"][0]["student_id"], "T-0002")

    def test_admin_dashboard_renders_the_latest_scan_day_server_side(self):
        request = RequestFactory().get(reverse("admin_dashboard"))
        request.user = self.staff

        response = admin_dashboard(request)
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # The table ships the day's rows instead of the empty-state row ...
        self.assertIn('data-attendance-id="', html)
        self.assertNotIn("No attendance records found", html)
        # ... the Date box shows which day is on screen ...
        self.assertIn(f'value="{self.scan_day.isoformat()}"', html)
        # ... and the card no longer claims to be showing "today".
        self.assertIn("Attendance \u2014", html)
        self.assertNotIn("Today's Attendance", html)


class DashboardAjaxQueryBudgetTests(TestCase):
    """The dashboard JSON must stay O(1) in queries, not O(students).

    The view used to run exists() + filter() per student (~2 queries each) to
    build the analytics rows: 4,408 queries / 6s on a quiet day, 17k+ on a day
    that actually has scans.
    """

    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(code="TSTC3", name="Test College 3")
        cls.program = Program.objects.create(
            college=cls.college, code="TSTP3", name="Test Program 3"
        )
        cls.scan_day = date.today() - timedelta(days=2)
        for i in range(25):
            student = Student.objects.create(
                student_id=f"T-1{i:03d}",
                name=f"Roster Student {i}",
                sex="M" if i % 2 else "F",
                year=1,
                college=cls.college,
                program=cls.program,
            )
            make_event_day_scan(student, cls.scan_day, 8, status="IN")
            make_event_day_scan(student, cls.scan_day, 17, status="OUT")

        cls.staff = User.objects.create_user(
            "budgetstaff", "budget@x.com", "pw", is_staff=True
        )

    def test_dashboard_ajax_query_count_is_bounded(self):
        client = Client()
        client.force_login(self.staff)

        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                reverse("ajax_dashboard_data"),
                headers={"x-requested-with": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stats"]["attended"], 25)
        # 25 students x 2 scans: a per-student N+1 would be 60+ queries.
        self.assertLess(
            len(ctx.captured_queries),
            40,
            "ajax_dashboard_data regressed to an N+1 over students",
        )


