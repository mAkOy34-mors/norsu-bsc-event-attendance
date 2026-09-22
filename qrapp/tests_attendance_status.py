"""A Time Out must never sit next to an "ABSENT" label.

The scanner's Manual Entry can record a check-out for a student who never
checked in (left early, unreadable QR, staff recording exits at the gate).
The report surfaces used to keep their own copies of the status if/elif, and
every one of them fell through to ABSENT when there was no Time In -- so the
Reports tab, the Students list and the CSV/Excel export showed ABSENT rows
that still carried a Time Out, and the Attended count left those students out.

They are now classified OUT by qrapp/attendance_status.py, which every surface
shares, and they count as present (they do have a scan).
"""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from qrapp import attendance_status
from qrapp.models import Attendance, College, Program, Student


def scan(student, day, hour, status):
    """Attendance rows are naive datetimes here (USE_TZ = False)."""
    return Attendance.objects.create(
        student=student,
        status=status,
        timestamp=datetime.combine(day, time(hour, 0)),
    )


class ClassifyTests(TestCase):
    """The one rule set every report surface shares."""

    def test_completed_when_both_scans_exist(self):
        status, attendance_date = attendance_status.classify(
            datetime(2026, 9, 21, 8, 0), datetime(2026, 9, 21, 17, 0)
        )
        self.assertEqual(status, attendance_status.COMPLETED)
        self.assertEqual(attendance_date, date(2026, 9, 21))

    def test_in_when_only_a_check_in_exists(self):
        status, attendance_date = attendance_status.classify(
            datetime(2026, 9, 21, 8, 0), None
        )
        self.assertEqual(status, attendance_status.IN)
        self.assertEqual(attendance_date, date(2026, 9, 21))

    def test_out_when_only_a_check_out_exists(self):
        status, attendance_date = attendance_status.classify(
            None, datetime(2026, 9, 21, 18, 30)
        )
        self.assertEqual(status, attendance_status.OUT)
        self.assertEqual(attendance_date, date(2026, 9, 21))

    def test_absent_only_when_no_scan_exists(self):
        self.assertEqual(
            attendance_status.classify(None, None),
            (attendance_status.ABSENT, None),
        )

    def test_out_students_count_as_present(self):
        self.assertTrue(attendance_status.is_present(attendance_status.OUT))
        self.assertFalse(attendance_status.is_present(attendance_status.ABSENT))

    def test_matches_filter_values(self):
        self.assertTrue(attendance_status.matches_filter(attendance_status.OUT, "present"))
        self.assertFalse(attendance_status.matches_filter(attendance_status.OUT, "absent"))
        self.assertFalse(attendance_status.matches_filter(attendance_status.OUT, "in"))
        self.assertFalse(attendance_status.matches_filter(attendance_status.OUT, "out"))
        self.assertTrue(attendance_status.matches_filter(attendance_status.OUT, "out_only"))
        # Unrecognised values show everything rather than exporting nothing.
        self.assertTrue(attendance_status.matches_filter(attendance_status.OUT, "bogus"))


class OutOnlyStatusIsReportedConsistentlyTests(TestCase):
    """A student with a check-out but no check-in in every surface."""

    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(code="OOC", name="Out Only College")
        cls.program = Program.objects.create(
            college=cls.college, code="OOP", name="Out Only Program"
        )
        cls.scan_day = date.today() - timedelta(days=2)

        cls.out_only = Student.objects.create(
            student_id="O-0001",
            name="Out Only Student",
            sex="M",
            year=1,
            college=cls.college,
            program=cls.program,
        )
        # The bug in one line: a manual check-out with no check-in before it.
        scan(cls.out_only, cls.scan_day, 18, "OUT")

        cls.staff = User.objects.create_user(
            "outstaff", "out@x.com", "pw", is_staff=True
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

    def test_export_never_labels_an_out_only_row_absent(self):
        # The property that was violated: status ABSENT -> both times blank.
        response = self.client.get(reverse("export_attendance"))
        self.assertEqual(response.status_code, 200, response.content[:300])
        import csv
        import io

        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))[1:]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][9], "OUT")
        self.assertEqual(rows[0][6], "")                          # Time In blank
        self.assertEqual(rows[0][7], "06:00:00 PM")               # Time Out shown
        self.assertEqual(rows[0][8], self.scan_day.isoformat())   # Date filled

    def test_export_out_only_filter_selects_the_out_only_student(self):
        response = self.client.get(reverse("export_attendance") + "?status=out_only")
        self.assertEqual(response.status_code, 200, response.content[:300])
        import csv
        import io

        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))[1:]
        self.assertEqual([row[0] for row in rows], ["O-0001"])

    def test_export_absent_filter_is_now_empty_for_an_out_only_roster(self):
        # A manual check-out alone is enough to attend, so nobody is absent.
        response = self.client.get(reverse("export_attendance") + "?status=absent")
        self.assertEqual(response.status_code, 404)

    def test_reports_ajax_labels_the_student_out_not_absent(self):
        payload = self.get_json("ajax_reports_data")

        self.assertEqual(len(payload["reports"]), 1)
        report = payload["reports"][0]
        self.assertEqual(report["status"], "OUT")
        self.assertIsNone(report["time_in"])
        self.assertIsNotNone(report["time_out"])
        self.assertEqual(report["date"], self.scan_day.isoformat())

    def test_reports_ajax_out_only_filter_returns_the_student(self):
        payload = self.get_json("ajax_reports_data", status="out_only")
        self.assertEqual([r["student_id"] for r in payload["reports"]], ["O-0001"])

        payload = self.get_json("ajax_reports_data", status="absent")
        self.assertEqual(payload["reports"], [])

    def test_dashboard_stats_count_out_only_students_as_attended(self):
        payload = self.get_json("ajax_dashboard_data")

        self.assertEqual(payload["stats"]["attended"], 1)
        self.assertEqual(payload["stats"]["absent"], 0)
        self.assertEqual(payload["stats"]["total_scans"], 1)

    def test_admin_dashboard_report_table_labels_the_student_out(self):
        response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        import re

        html = response.content.decode()
        self.assertRegex(html, r'badge-warning">\s*OUT\s*</span>')
        self.assertNotIn("ABSENT", html)

    def test_dashboard_dropdowns_offer_the_out_only_filter(self):
        response = self.client.get(reverse("admin_dashboard"))

        self.assertContains(
            response, '<option value="out_only">Checked Out Only</option>', count=2
        )
