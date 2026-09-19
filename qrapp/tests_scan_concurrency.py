"""Scan-endpoint tests: concurrency safety, IN/OUT integrity, timestamp trust.

Covers the optimizations in qrapp/views.py save_scan()/_lock_scan_target() and
the Attendance indexes added by migration 0020.
"""
import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from qrapp.models import Attendance, Event, Student
from qrapp.views import resolve_scan_timestamp, save_scan


def make_event(**overrides):
    values = {
        "title": "Test Assembly",
        "event_date": date.today(),
        "is_active": True,
    }
    values.update(overrides)
    return Event.objects.create(**values)


def make_student(student_id="2026-0001", **overrides):
    values = {
        "student_id": student_id,
        "name": "Test Student",
        "sex": "F",
        "year": 1,
    }
    values.update(overrides)
    return Student.objects.create(**values)


class ScanFlowTests(TestCase):
    """The existing IN -> OUT -> IN behaviour must be preserved exactly."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("scanner_flow", password="pw123456")
        self.event = make_event()
        self.student = make_student()

    def scan(self, **extra):
        data = {
            "event_id": str(self.event.id),
            "student_id": self.student.student_id,
        }
        data.update(extra)
        request = self.factory.post("/qrapp/save_scan/", data)
        request.user = self.user
        return save_scan(request)

    @staticmethod
    def payload(response):
        return json.loads(response.content)

    def test_first_scan_marks_in(self):
        body = self.payload(self.scan())
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "IN")
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(Attendance.objects.get().status, "IN")

    def test_double_scan_within_an_hour_does_not_create_a_duplicate_in(self):
        self.assertTrue(self.payload(self.scan())["success"])

        # Second scanner, same student, seconds later: the per-student row lock
        # makes this read the IN that was just committed instead of inserting a
        # second IN.
        body = self.payload(self.scan())
        self.assertFalse(body["success"])
        self.assertIn("cannot log OUT yet", body["message"])
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(Attendance.objects.filter(status="IN").count(), 1)

    def test_in_out_in_sequence_is_preserved(self):
        self.scan()
        # Backdate the IN beyond the 1-hour gate so OUT is allowed.
        Attendance.objects.update(timestamp=timezone.now() - timedelta(hours=2))

        out_body = self.payload(self.scan())
        self.assertTrue(out_body["success"])
        self.assertEqual(out_body["status"], "OUT")

        in_body = self.payload(self.scan())
        self.assertTrue(in_body["success"])
        self.assertEqual(in_body["status"], "IN")

        statuses = list(
            Attendance.objects.order_by("timestamp").values_list("status", flat=True)
        )
        self.assertEqual(statuses, ["IN", "OUT", "IN"])

    def test_event_on_another_day_is_still_rejected(self):
        other = make_event(title="Tomorrow", event_date=date.today() + timedelta(days=1))
        request = self.factory.post(
            "/qrapp/save_scan/",
            {"event_id": str(other.id), "student_id": self.student.student_id},
        )
        request.user = self.user
        body = json.loads(save_scan(request).content)
        self.assertFalse(body["success"])
        self.assertIn("not today", body["message"])
        self.assertEqual(Attendance.objects.count(), 0)

    def test_manual_status_is_whitelisted(self):
        # A bogus status must never be written to the choice column.
        self.payload(self.scan(manual_status="BANANA"))
        self.assertEqual(
            list(Attendance.objects.values_list("status", flat=True)), ["IN"]
        )

    def test_manual_status_is_honoured(self):
        body = self.payload(self.scan(manual_status="OUT"))
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "OUT")
        self.assertEqual(Attendance.objects.get().status, "OUT")

    def test_unknown_student_is_rejected(self):
        body = self.payload(self.scan(student_id="does-not-exist"))
        self.assertFalse(body["success"])
        self.assertEqual(body["message"], "Student not found.")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_decision_reads_only_the_latest_record(self):
        """No loading the whole history: one ORDER BY -timestamp LIMIT 1 read."""
        for index in range(6):
            Attendance.objects.create(
                student=self.student,
                event=self.event,
                status="IN" if index % 2 == 0 else "OUT",
                timestamp=timezone.now() - timedelta(hours=10 - index),
            )
        # Outside the 1-hour gate, so the newest record decides the status.
        Attendance.objects.update(timestamp=timezone.now() - timedelta(hours=3))

        with CaptureQueriesContext(connection) as ctx:
            body = self.payload(self.scan())

        self.assertTrue(body["success"])
        sql = " ".join(q["sql"] for q in ctx.captured_queries)
        # The old code ran .exists() over a full history scan and then .last().
        self.assertNotIn("ORDER BY `qrapp_attendance`.`timestamp` ASC", sql)
        self.assertIn("LIMIT 1", sql)

    def test_lock_targets_the_student_row_not_the_attendance_table(self):
        """Concurrency for different students must stay possible."""
        with CaptureQueriesContext(connection) as ctx:
            self.scan()

        locking = [q["sql"] for q in ctx.captured_queries if "FOR UPDATE" in q["sql"]]
        self.assertTrue(locking, "save_scan must use select_for_update()")
        for sql in locking:
            self.assertIn("qrapp_student", sql)
            self.assertNotIn("qrapp_attendance", sql)


class AttendanceIndexTests(TestCase):
    def test_expected_indexes_are_declared(self):
        names = {index.name for index in Attendance._meta.indexes}
        self.assertIn("attend_stu_event_ts_idx", names)
        self.assertIn("attend_ts_idx", names)

    def test_composite_index_covers_the_scan_query(self):
        index = next(
            index
            for index in Attendance._meta.indexes
            if index.name == "attend_stu_event_ts_idx"
        )
        self.assertEqual(index.fields, ["student", "event", "timestamp"])


class ScanTimestampTests(TestCase):
    """The server clock is authoritative; device time is only a hint."""

    def test_missing_device_time_uses_server_time(self):
        server_now = timezone.now()
        self.assertEqual(resolve_scan_timestamp("", server_now), server_now)
        self.assertEqual(resolve_scan_timestamp(None, server_now), server_now)

    def test_garbage_device_time_uses_server_time(self):
        server_now = timezone.now()
        self.assertEqual(resolve_scan_timestamp("not-a-date", server_now), server_now)

    def test_recent_past_device_time_is_kept(self):
        server_now = timezone.now()
        device_time = server_now - timedelta(minutes=30)
        self.assertEqual(
            resolve_scan_timestamp(device_time.isoformat(), server_now), device_time
        )

    def test_future_device_time_is_ignored(self):
        server_now = timezone.now()
        device_time = server_now + timedelta(hours=5)
        self.assertEqual(
            resolve_scan_timestamp(device_time.isoformat(), server_now), server_now
        )

    def test_ancient_device_time_is_ignored(self):
        server_now = timezone.now()
        device_time = server_now - timedelta(days=30)
        self.assertEqual(
            resolve_scan_timestamp(device_time.isoformat(), server_now), server_now
        )

    def test_aware_device_time_is_converted_for_naive_server_time(self):
        server_now = timezone.now()
        device_time = server_now - timedelta(minutes=15)
        aware = timezone.make_aware(device_time, timezone.get_current_timezone())
        resolved = resolve_scan_timestamp(aware.isoformat(), server_now)
        self.assertEqual(resolved, device_time)

    def test_stored_timestamp_cannot_be_forced_into_the_future(self):
        """A doctored phone clock must not future-date an attendance row."""
        factory = RequestFactory()
        user = User.objects.create_user("scanner_clock", password="pw123456")
        event = make_event()
        student = make_student(student_id="2026-7777")

        request = factory.post(
            "/qrapp/save_scan/",
            {
                "event_id": str(event.id),
                "student_id": student.student_id,
                "local_time": (timezone.now() + timedelta(hours=9)).isoformat(),
            },
        )
        request.user = user
        self.assertTrue(json.loads(save_scan(request).content)["success"])

        stored = Attendance.objects.get().timestamp
        self.assertLessEqual(stored, timezone.now() + timedelta(seconds=5))

    def test_backdated_queue_replay_keeps_its_original_time(self):
        """An offline/queued scan replayed later must keep its real time."""
        factory = RequestFactory()
        user = User.objects.create_user("scanner_replay", password="pw123456")
        event = make_event()
        student = make_student(student_id="2026-8888")
        original = timezone.now() - timedelta(minutes=20)

        request = factory.post(
            "/qrapp/save_scan/",
            {
                "event_id": str(event.id),
                "student_id": student.student_id,
                "local_time": original.isoformat(),
            },
        )
        request.user = user
        self.assertTrue(json.loads(save_scan(request).content)["success"])

        stored = Attendance.objects.get().timestamp
        self.assertLessEqual(abs((stored - original).total_seconds()), 1)
class RatelimitedResponseTests(TestCase):
    """A rate-limited scanner must get JSON, not the HTML 429 page."""

    def test_scan_endpoint_gets_json(self):
        from qrapp.views import ratelimited_view

        response = ratelimited_view(RequestFactory().post("/qrapp/save_scan/"))
        self.assertEqual(response.status_code, 429)
        self.assertIn("application/json", response["Content-Type"])
        body = json.loads(response.content)
        self.assertFalse(body["success"])
        self.assertIn("error", body)

    def test_browser_path_still_gets_the_html_page(self):
        from qrapp.views import ratelimited_view

        response = ratelimited_view(RequestFactory().get("/qrapp/dashboard/"))
        self.assertEqual(response.status_code, 429)
        self.assertIn("text/html", response["Content-Type"])


class ServerLocaldateTests(TestCase):
    def test_matches_the_server_clock(self):
        from qrapp.views import server_localdate

        self.assertEqual(server_localdate(), date.today())

    def test_does_not_raise_unlike_timezone_localdate(self):
        from qrapp.views import server_localdate

        self.assertIsInstance(server_localdate(), date)
        # Regression guard: with USE_TZ=False, Django 5.x timezone.localdate()
        # raises for naive datetimes, so it must not be used directly.
        with self.assertRaises(ValueError):
            timezone.localdate()