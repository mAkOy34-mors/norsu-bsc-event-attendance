"""Concurrent load test for the QR scan endpoint (opt-in).

Runs against a real HTTP server and the real database engine, so the MySQL
row lock, the connection handling and the rate limiter are all exercised the
way they are on event day:

    set RUN_LOAD_TESTS=1
    python manage.py test qrapp.tests_load_scan -v 2

Per concurrency level it reports wall time, throughput, p50/p95 latency, the
HTTP status spread and the number of attendance rows actually written (so a
duplicate IN shows up as count > 1).

Skipped entirely unless RUN_LOAD_TESTS=1, so the normal test suite stays fast.
"""
import os
import statistics
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.db.models import Count
from django.test import LiveServerTestCase

from qrapp.models import Attendance, Event, Student

CONCURRENCY_LEVELS = (10, 30, 50)
MAX_STUDENTS = max(CONCURRENCY_LEVELS) * 2
SCAN_PATH = "/qrapp/save_scan/"
SESSION_COOKIE = settings.SESSION_COOKIE_NAME


def make_session_cookie(user):
    """Log a scanner in without going through the login form (and its limiter)."""
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return session.session_key


class LoadHarness(LiveServerTestCase):
    """Shared fixtures: one student per scanner slot.

    LiveServerTestCase is a TransactionTestCase, which TRUNCATES the database
    between test methods -- the event, users and DB-backed sessions created in
    setUpClass would be gone before the second test ran (its POSTs then bounce
    off the login page as HTML 200s, or hit 'Selected event was not found').
    Everything is therefore created per-test in setUp(); setUpClass only builds
    the students, which each test re-creates on demand via its own harness.
    """

    def setUp(self):
        """Rebuild per-test fixtures (TransactionTestCase truncates between tests)."""
        super().setUp()
        # Students too: setUpClass data does not survive the truncation.
        self.students = Student.objects.bulk_create(
            [
                Student(
                    student_id=f"LOAD{i:05d}",
                    name=f"Load Tester {i}",
                    sex="F",
                    year=1,
                )
                for i in range(MAX_STUDENTS)
            ]
        )
        # The event row is also wiped between test methods, and save_scan
        # refuses to record anything for a missing event -- so it must be
        # re-created here, not in setUpClass. A fresh event also means no
        # stale attendance rows, so each test starts from a clean state.
        self.event = Event.objects.create(
            title="Load Test Event", event_date=date.today(), is_active=True
        )
        Attendance.objects.all().delete()
        # One account per scanner, mirroring the real deployment (the CSRF
        # token is fetched here, outside the timed bursts). setUpClass data
        # does NOT survive the truncation, hence the per-test rebuild.
        self.scanners = []
        for index in range(max(CONCURRENCY_LEVELS)):
            user = User.objects.create_user(
                f"loadscanner{index}", password="loadtest12345"
            )
            session_key = make_session_cookie(user)
            self.scanners.append((user, session_key, self.fetch_csrf(session_key)))

    @classmethod
    def fetch_csrf(cls, session_key):
        """The scanner page renders {% csrf_token %}, so it sets the cookie."""
        response = requests.get(
            cls.live_server_url + "/qrapp/scanner/",
            cookies={SESSION_COOKIE: session_key},
            timeout=30,
        )
        return response.cookies.get("csrftoken", "")

    def scan(self, scanner_index, student_id):
        _user, session_key, csrf = self.scanners[scanner_index]
        # The csrftoken cookie MUST travel with the POST: CsrfViewMiddleware
        # compares the X-CSRFToken header against that cookie.
        cookies = {SESSION_COOKIE: session_key, "csrftoken": csrf}
        started = time.perf_counter()
        try:
            response = requests.post(
                self.live_server_url + SCAN_PATH,
                data={"event_id": self.event.id, "student_id": student_id},
                cookies=cookies,
                headers={
                    "X-CSRFToken": csrf,
                    "Referer": self.live_server_url + "/qrapp/scanner/",
                },
                timeout=60,
            )
            status = response.status_code
            try:
                body = response.json()
            except ValueError:
                # A non-JSON response means the request never reached save_scan
                # (e.g. a redirect to the login page). Record what came back so
                # the report can say so instead of printing a blank message.
                return {
                    "status": status,
                    "success": False,
                    "message": f"non-JSON {status}: {response.text[:120].strip()}",
                    "elapsed": time.perf_counter() - started,
                }
        except requests.RequestException as exc:  # network-level failure
            return {"status": "network-error", "error": str(exc),
                    "elapsed": time.perf_counter() - started, "success": False}
        return {
            "status": status,
            "success": bool(body.get("success")),
            "message": body.get("message", ""),
            "elapsed": time.perf_counter() - started,
        }

    def run_burst(self, concurrency, students):
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(self.scan, index % concurrency, students[index])
                for index in range(concurrency)
            ]
            return [future.result() for future in futures]
class ScanLoadTests(LoadHarness):
    """Only runs with RUN_LOAD_TESTS=1."""

    @classmethod
    def setUpClass(cls):
        if os.getenv("RUN_LOAD_TESTS", "").strip() != "1":
            raise unittest.SkipTest("set RUN_LOAD_TESTS=1 to run scan load tests")
        super().setUpClass()

    def test_concurrent_unique_scans(self):
        student_ids = [student.student_id for student in self.students]
        cursor = 0
        for concurrency in CONCURRENCY_LEVELS:
            batch = student_ids[cursor:cursor + concurrency]
            cursor += concurrency
            Attendance.objects.all().delete()

            started = time.perf_counter()
            results = self.run_burst(concurrency, batch)
            wall = time.perf_counter() - started

            self.report(f"{concurrency} unique scans", concurrency, results, wall)

            accepted = [r for r in results if r["success"]]
            written = Attendance.objects.filter(event=self.event).count()
            # Any scan the server ACCEPTED must have produced exactly one row.
            # Requests the harness itself could not deliver (Django's dev/test
            # WSGI server refuses connections past ~30 simultaneous ones) are
            # counted in the report but cannot be an app assertion.
            self.assertEqual(
                written,
                len(accepted),
                f"{len(accepted)} accepted scans wrote {written} rows",
            )
            duplicated = (
                Attendance.objects.filter(event=self.event)
                .values("student_id")
                .annotate(rows=Count("id"))
                .filter(rows__gt=1)
                .count()
            )
            self.assertEqual(
                duplicated, 0, "a single scan produced more than one row"
            )

    def test_concurrent_same_student_burst_creates_one_in(self):
        """The race that used to produce duplicate INs."""
        for concurrency in CONCURRENCY_LEVELS:
            Attendance.objects.all().delete()
            student_id = self.students[0].student_id

            started = time.perf_counter()
            results = self.run_burst(concurrency, [student_id] * concurrency)
            wall = time.perf_counter() - started

            self.report(f"{concurrency} scans of ONE student", concurrency, results, wall)

            rows = Attendance.objects.filter(event=self.event)
            in_rows = rows.filter(status="IN").count()
            self.assertEqual(
                in_rows, 1,
                f"{concurrency} simultaneous scans of one student created "
                f"{in_rows} IN rows (expected exactly 1)",
            )
            # The others must ALL have been refused (never "silently
            # accepted and also written"). Refusals are the correct outcome:
            # a duplicate IN is the bug, a queue of scans for a student who is
            # already inside is not.
            refusals = [r for r in results if not r["success"]]
            self.assertEqual(
                len(refusals),
                concurrency - 1,
                f"expected {concurrency - 1} refusals and one IN, "
                f"got {len(refusals)} refusals "
                f"({refusals[:3]!r})",
            )
            self.assertEqual(rows.count(), 1)

    def report(self, label, concurrency, results, wall):
        """Print the per-burst metrics and every refusal reason.

        The absolute latency is dominated by Django's test WSGI server
        (wsgiref), which handles bursts far worse than the Waitress server the
        app actually runs on and starts refusing connections past ~30-35
        simultaneous ones (seen as network-error above). Treat the numbers as
        relative (how the burst scales, whether any scan was duplicated or
        lost), not as production latency figures.
        """
        elapsed = sorted(r["elapsed"] for r in results)
        statuses = {}
        for result in results:
            key = str(result["status"])
            statuses[key] = statuses.get(key, 0) + 1
        busy = sum(1 for r in results if "busy" in (r.get("message") or ""))
        failures = {}
        for result in results:
            if not result["success"]:
                key = (result.get("message") or result.get("error") or "?")[:50]
                failures[key] = failures.get(key, 0) + 1
        p50 = statistics.median(elapsed)
        p95 = elapsed[max(0, int(len(elapsed) * 0.95) - 1)]
        worst = elapsed[-1]
        rate = len(results) / wall if wall else 0
        print(
            "\n--- {0} ---\n"
            "  wall: {1:.2f}s  throughput: {2:.1f} req/s\n"
            "  latency p50: {3:.0f}ms  p95: {4:.0f}ms  max: {5:.0f}ms\n"
            "  statuses: {6}\n"
            "  saved: {7}  not-saved: {8}\n"
            "  failure messages: {9}\n"
            "  lock-contention retries returned: {10}".format(
                label,
                wall,
                rate,
                p50 * 1000,
                p95 * 1000,
                worst * 1000,
                statuses,
                sum(1 for r in results if r["success"]),
                sum(1 for r in results if not r["success"]),
                failures,
                busy,
            )
        )