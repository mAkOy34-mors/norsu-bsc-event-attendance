"""The Export Report modal's Attendance Status filter.

The dialog sends ``status=present|absent|in|out|out_only`` to
/export_attendance/, which the server maps to the per-student
COMPLETED / IN / OUT / ABSENT status (qrapp/attendance_status.py):

    present   -> a scan exists (COMPLETED, IN or OUT)
    absent    -> no scan in the window
    in        -> checked in, still inside (IN and no OUT yet)
    out       -> checked in AND checked out (COMPLETED)
    out_only  -> checked out with no check-in recorded (OUT)

These tests pin that contract so the dropdown keeps matching the report rows.
"""
import csv
import io
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from qrapp.models import Attendance, College, Program, Student
from qrapp.views import admin_dashboard

STATUS_COLUMN = 9  # ATTENDANCE_EXPORT_HEADERS: Student ID ... Date, Status


def scan(student, day, hour, status):
    """Attendance rows are naive datetimes here (USE_TZ = False)."""
    return Attendance.objects.create(
        student=student,
        status=status,
        timestamp=datetime.combine(day, time(hour, 0)),
    )


class ExportStatusFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(code="EXTC", name="Export Test College")
        cls.program = Program.objects.create(
            college=cls.college, code="EXTP", name="Export Test Program"
        )
        cls.scan_day = date.today() - timedelta(days=3)

        def make_student(student_id, name, sex):
            return Student.objects.create(
                student_id=student_id,
                name=name,
                sex=sex,
                year=1,
                college=cls.college,
                program=cls.program,
            )

        # Names are chosen so the export order (name, student_id) is known.
        cls.completed = make_student("E-0001", "Completed Student", "F")
        cls.inside = make_student("E-0002", "Inside Student", "M")
        cls.absent = make_student("E-0003", "Absent Student", "M")

        scan(cls.completed, cls.scan_day, 8, "IN")
        scan(cls.completed, cls.scan_day, 17, "OUT")
        scan(cls.inside, cls.scan_day, 9, "IN")

        cls.staff = User.objects.create_user(
            "exportstaff", "export@x.com", "pw", is_staff=True
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.staff)

    def export_rows(self, **params):
        response = self.client.get(
            reverse("export_attendance") + "?" + urlencode(params)
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        text = response.content.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def exported_ids(self, **params):
        rows = self.export_rows(**params)
        # Drop the header row; column 0 is the Student ID.
        return [row[0] for row in rows[1:]]

    def test_all_status_exports_the_whole_roster(self):
        self.assertEqual(
            self.exported_ids(),
            ["E-0003", "E-0001", "E-0002"],  # Absent, Completed, Inside
        )

    def test_present_status_drops_students_without_scans(self):
        self.assertEqual(self.exported_ids(status="present"), ["E-0001", "E-0002"])

    def test_absent_status_keeps_only_students_without_scans(self):
        rows = self.export_rows(status="absent")

        self.assertEqual([row[0] for row in rows[1:]], ["E-0003"])
        self.assertEqual(rows[1][STATUS_COLUMN], "ABSENT")

    def test_in_status_keeps_only_students_still_inside(self):
        rows = self.export_rows(status="in")

        self.assertEqual([row[0] for row in rows[1:]], ["E-0002"])
        self.assertEqual(rows[1][STATUS_COLUMN], "IN")
        self.assertEqual(rows[1][7], "")  # no Time Out yet

    def test_out_status_keeps_only_completed_check_ins(self):
        rows = self.export_rows(status="out")

        self.assertEqual([row[0] for row in rows[1:]], ["E-0001"])
        self.assertEqual(rows[1][STATUS_COLUMN], "COMPLETED")
        self.assertNotEqual(rows[1][6], "")  # Time In
        self.assertNotEqual(rows[1][7], "")  # Time Out

    def test_status_composes_with_the_other_filters(self):
        self.assertEqual(
            self.exported_ids(status="absent", gender="Male"), ["E-0003"]
        )
        self.assertEqual(
            self.exported_ids(status="present", gender="Female"), ["E-0001"]
        )

    def test_filter_combination_with_no_matches_reports_an_error(self):
        response = self.client.get(
            reverse("export_attendance")
            + "?"
            + urlencode({"status": "absent", "gender": "Female"})
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"No attendance records match", response.content)

    def test_export_modal_exposes_the_status_dropdown(self):
        request = RequestFactory().get(reverse("admin_dashboard"))
        request.user = self.staff

        html = admin_dashboard(request).content.decode()

        self.assertIn('id="exportStatus"', html)
        self.assertIn('<option value="present">Present</option>', html)
        self.assertIn('<option value="absent">Absent</option>', html)
        self.assertIn('<option value="in">Still Inside</option>', html)
        self.assertIn('<option value="out">Completed</option>', html)
        self.assertIn('<option value="out_only">Checked Out Only</option>', html)
