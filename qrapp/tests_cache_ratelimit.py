"""Tests for qrapp caching + rate limiting."""
import json
import os
import re
from datetime import date

from django.contrib.auth.models import AnonymousUser, User
from django.core.cache import caches
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, TestCase

from qrapp import caching
from qrapp.models import Event
from qrapp.views import build_calendar_data, client_username


class CacheHelperTests(TestCase):
    def test_calendar_key_is_versioned_and_dated(self):
        self.assertEqual(
            caching.build_calendar_cache_key(date(2026, 9, 17)),
            "v1:calendar:2026-09-17",
        )

    def test_lookup_keys_stable_and_user_scoped(self):
        ka = caching.build_lookups_cache_key("colleges", 1, "active=True")
        kb = caching.build_lookups_cache_key("colleges", 1, "active=True")
        kc = caching.build_lookups_cache_key("colleges", 2, "active=True")
        self.assertEqual(ka, kb)
        self.assertNotEqual(ka, kc)

    def test_calendar_payload_served_from_cache(self):
        Event.objects.create(
            title="Live Event", event_date=date.today(), is_active=True, is_current=True
        )
        payload1, _ = build_calendar_data()
        self.assertEqual(payload1["events"][0]["title"], "Live Event")

        caches["lookups"].set(
            caching.build_calendar_cache_key(date.today()),
            {"events": [{"id": 999, "title": "CACHED"}], "attendance_by_date": {}},
            timeout=60,
        )
        payload2, _ = build_calendar_data()
        self.assertEqual(payload2["events"][0]["title"], "CACHED")

    def test_calendar_invalidation_freshens_payload(self):
        Event.objects.create(
            title="Live Event", event_date=date.today(), is_active=True, is_current=True
        )
        caches["lookups"].set(
            caching.build_calendar_cache_key(date.today()),
            {"events": [{"id": 999, "title": "CACHED"}], "attendance_by_date": {}},
            timeout=60,
        )
        caching.invalidate_calendar_cache()
        payload, _ = build_calendar_data()
        self.assertEqual(payload["events"][0]["title"], "Live Event")

    def test_lookup_invalidation_clears_bin(self):
        caches["lookups"].set("v1:lookups:whatever", {"x": 1}, timeout=60)
        caching.invalidate_lookup_caches()
        self.assertIsNone(caches["lookups"].get("v1:lookups:whatever"))


class RateLimitKeyTests(TestCase):
    def _request(self, user):
        request = HttpRequest()
        request.user = user
        request.META = {"REMOTE_ADDR": "1.2.3.4"}
        return request

    def test_key_uses_username_when_authenticated(self):
        self.assertEqual(
            client_username(None, self._request(User(username="admin1"))), "admin1"
        )

    def test_key_falls_back_to_ip_when_anonymous(self):
        self.assertEqual(client_username(None, self._request(AnonymousUser())), "1.2.3.4")


class DashboardChartCacheTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="staff", password="pw12345", is_staff=True
        )

    def test_dashboard_home_serves_cached_chart(self):
        from django.test import RequestFactory

        from qrapp.caching import build_chart_cache_key
        from qrapp.views import dashboard_home

        factory = RequestFactory()

        def call_view():
            request = factory.get("/")
            request.user = self.staff
            return dashboard_home(request)

        first = call_view()
        self.assertEqual(first.status_code, 200)

        # Warm the cache with a marker payload; next render must use it.
        caches["lookups"].set(
            build_chart_cache_key(date.today()),
            {
                "week_labels": ["X"],
                "week_check_in": [7],
                "week_check_out": [3],
                "college_labels": [],
                "college_counts": [],
            },
            timeout=60,
        )
        second = call_view()
        self.assertEqual(second.status_code, 200)
        # Chart JSON is embedded (json-escaped) in a script tag on the page:
        # the marker week label and value must both be present.
        content = second.content.decode()
        self.assertIn("week_labels", content)
        self.assertIn("[7]", content)


class LoginRateLimitTests(TestCase):
    def setUp(self):
        User.objects.create_user(username="victim", password="secret12345")

    def test_login_burst_hits_429(self):
        client = Client()
        statuses = [
            client.post("/login/", {"username": "victim", "password": "wrong"}).status_code
            for _ in range(8)
        ]
        self.assertIn(429, statuses)

    def test_ratelimit_template_renders(self):
        html = render_to_string("qrapp/ratelimited.html")
        self.assertIn("Too Many Requests", html)


class CalendarDataEmbedTests(TestCase):
    """
    Regression: views used to pass json.dumps() strings that templates then
    re-encoded with |json_script, so JS parsed a string instead of an object
    ("Calendar data unavailable" / "Loading events..." forever).
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            username="embedstaff", password="pw12345", is_staff=True
        )

    def _dashboard_html(self):
        from django.test import RequestFactory
        from qrapp.views import dashboard_home

        request = RequestFactory().get("/dashboard/")
        request.user = self.staff
        return dashboard_home(request).content.decode()

    def test_calendar_json_island_holds_object_not_string(self):
        html = self._dashboard_html()
        match = re.search(
            r'<script id="live-calendar-data" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "live-calendar-data script island missing")
        payload = json.loads(match.group(1))
        # Must be a dict (not a JSON-encoded string of a dict).
        self.assertIsInstance(payload, dict)
        self.assertIn("today", payload)
        self.assertIn("events", payload)

    def test_chart_json_island_holds_object_not_string(self):
        html = self._dashboard_html()
        match = re.search(
            r'<script id="dashboard-chart-data" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard-chart-data script island missing")
        payload = json.loads(match.group(1))
        self.assertIsInstance(payload, dict)
        self.assertIn("week_labels", payload)

    def test_events_url_attribute_has_no_stray_quotes(self):
        html = self._dashboard_html()
        self.assertIn('data-events-url="/ajax/get-events/"', html)
        self.assertNotIn('data-events-url=\\"', html)


class RootUrlRoutingTests(TestCase):
    """dashboard_home was shadowed at "" by the project root redirect."""

    def test_dashboard_home_is_reachable_via_named_route(self):
        from django.urls import reverse

        # Named route must reverse (templates depend on it).
        url = reverse("dashboard_home")
        self.assertEqual(url, "/dashboard/")

        staff = User.objects.create_user(
            username="routestaff", password="pw12345", is_staff=True
        )
        client = Client()
        client.force_login(staff)
        response = client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_root_still_redirects_to_login(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("/login/"))


class NotificationPartialTests(TestCase):
    """The SweetAlert2 bridge partial must render and include the scripts."""

    def test_base_template_renders_with_notifications(self):
        html = render_to_string("qrapp/base.html")
        self.assertIn("sweetalert2", html.lower())
        # WhiteNoise appends a hash to the static name in rendered output.
        self.assertRegex(html, r"notify\.[0-9a-f]+\.js")

    def test_django_messages_render_as_toast_script(self):
        from django.contrib.messages.storage.fallback import FallbackStorage

        request = HttpRequest()
        request.session = "session"
        request._messages = FallbackStorage(request)
        from django.contrib import messages as dj_messages

        dj_messages.success(request, "College added successfully!")
        html = render_to_string("qrapp/partials/notifications.html", request=request)
        self.assertIn("College added successfully!", html)
        # Messages are passed via data attributes (no embedded JS); notify.js
        # parses them and calls notify.toast at runtime.
        self.assertIn('data-tag-0="success"', html)
        self.assertIn('class="django-messages"', html)
        self.assertNotIn("<script>", html.replace('<script src=', '<!--src-->' ))


class ProgramResolutionTests(TestCase):
    """Imports resolve programs by code first: BSINT -> CAS, BSIT -> CIT."""

    @classmethod
    def setUpTestData(cls):
        # CAS/CIT already exist via data migrations 0007/0009.
        from qrapp.models import College, Program

        cls.cas = College.objects.get(code="CAS")
        cls.cit = College.objects.get(code="CIT")
        cls.bsint, _ = Program.objects.get_or_create(
            college=cls.cas, code="BSINT",
            defaults={"name": "Bachelor of Science in Information Technology"},
        )
        cls.bsit, _ = Program.objects.get_or_create(
            college=cls.cit, code="BSIT",
            defaults={"name": "Bachelor of Science in Industrial Technology"},
        )

    def test_program_code_resolves_globally_before_college(self):
        from qrapp.college_program import resolve_college_and_program

        # Sheet says college=CAS + program=BSIT -> still lands in CIT (code wins)
        college, program = resolve_college_and_program("CAS", "BSIT", create=False)
        self.assertEqual(program.college.code, "CIT")

        # BSINT resolves to CAS even if the sheet said CIT
        college, program = resolve_college_and_program("CIT", "BSINT", create=False)
        self.assertEqual(program.college.code, "CAS")

    def test_unknown_code_falls_back_to_college_column(self):
        from qrapp.college_program import resolve_college_and_program
        from qrapp.models import Program

        Program.objects.filter(code="BSCS-TEST").delete()
        college, program = resolve_college_and_program("CAS", "BSCS-TEST", create=True)
        self.assertEqual(college.code, "CAS")
        self.assertEqual(program.code, "BSCS-TEST")

    def test_merge_absorbs_stray_bsit_under_cas(self):
        from qrapp.legacy_program_merge import merge_legacy_programs
        from qrapp.models import Major, Program, Student

        # A stray "BSIT" under CAS (created by old imports with a wrong
        # college column) -- must be absorbed into CIT|BSIT by the merge.
        stray = Program.objects.create(college=self.cas, code="BSIT", name="BSIT")
        auto, _ = Major.objects.get_or_create(program=self.bsit, code="AUTO", defaults={"name": "Automotive"})
        stray_auto = Major.objects.create(program=stray, code="Automotive Technology", name="Automotive Technology")
        student = Student.objects.create(
            student_id="202699999", name="Merge Test Student", sex="M",
            college=self.cas, program=stray, year=1, major=stray_auto,
        )

        from qrapp.models import College as _College, Program as _Program
        merge_legacy_programs(_College, _Program, Major, Student)

        student.refresh_from_db()
        self.assertFalse(Program.objects.filter(pk=stray.pk).exists())
        self.assertEqual(student.program.pk, self.bsit.pk)
        self.assertEqual(student.college.code, "CIT")
        self.assertEqual(student.major.pk, auto.pk)  # name-normalized merge

    def test_import_stores_program_college_not_sheet_college(self):
        from qrapp.student_import import import_students_from_rows

        # Sheet says college=CAS but program=BSIT: student must land in CIT.
        column_map = {"student_id": 0, "name": 1, "sex": 2, "college": 3, "program": 4, "year": 5, "major": 6}
        rows = [
            ["202688888", "Import Test", "F", "CAS", "BSIT", "1", ""],
        ]
        created, skipped = import_students_from_rows(rows, column_map)
        self.assertEqual(created, 1)
        from qrapp.models import Student as _Student
        student = _Student.objects.get(student_id="202688888")
        self.assertEqual(student.program.code, "BSIT")
        self.assertEqual(student.college.code, "CIT")
        student.delete()

    def test_import_fuzzy_matches_major_without_duplicates(self):
        from qrapp.student_import import import_students_from_rows
        from qrapp.models import Major

        # Canonical majors exist with short codes (seed-created).
        auto, _ = Major.objects.get_or_create(
            program=self.bsit, code="AUTO", defaults={"name": "Automotive Technology"},
        )
        ee, _ = Major.objects.get_or_create(
            program=self.bsit, code="EE", defaults={"name": "Electrical Technology"},
        )
        major_count_before = Major.objects.filter(program=self.bsit).count()

        column_map = {"student_id": 0, "name": 1, "sex": 2, "college": 3, "program": 4, "year": 5, "major": 6}
        rows = [
            # Sheet spells them slightly differently than the canonical names.
            ["202677771", "Fuzzy A", "M", "CIT", "BSIT", "1", "Automotive"],
            ["202677772", "Fuzzy B", "F", "CIT", "BSIT", "1", "Electrical"],
        ]
        created, _ = import_students_from_rows(rows, column_map)
        self.assertEqual(created, 2)

        from qrapp.models import Student as _Student
        self.assertEqual(_Student.objects.get(student_id="202677771").major_id, auto.pk)
        self.assertEqual(_Student.objects.get(student_id="202677772").major_id, ee.pk)
        # No duplicate majors were created for the variant spellings.
        self.assertEqual(Major.objects.filter(program=self.bsit).count(), major_count_before)
        _Student.objects.filter(student_id__in=["202677771", "202677772"]).delete()

    def test_dedupe_majors_merges_technology_variants(self):
        from qrapp.legacy_program_merge import dedupe_majors
        from qrapp.models import Major, Student as _Student

        auto, _ = Major.objects.get_or_create(
            program=self.bsit, code="AUTO", defaults={"name": "Automotive Technology"},
        )
        long_named = Major.objects.create(program=self.bsit, code="Automotive Tech.", name="Automotive")
        student = _Student.objects.create(
            student_id="202666666", name="Dedupe Test", sex="M",
            college=self.cit, program=self.bsit, year=1, major=long_named,
        )

        removed = dedupe_majors(Major, _Student)

        self.assertEqual(removed, 1)
        self.assertFalse(Major.objects.filter(pk=long_named.pk).exists())
        student.refresh_from_db()
        self.assertEqual(student.major_id, auto.pk)
        student.delete()

    def test_import_fuzzy_matches_plural_major_without_duplicates(self):
        from qrapp.student_import import import_students_from_rows
        from qrapp.models import College, Major, Program

        # Canonical BSED/SCI pair as created by seed_colleges.
        cted, _ = College.objects.get_or_create(code="CTED", defaults={"name": "College of Teacher Education", "is_active": True})
        bsed, _ = Program.objects.get_or_create(
            college=cted, code="BSED",
            defaults={"name": "Bachelor of Secondary Education"},
        )
        sci, _ = Major.objects.get_or_create(
            program=bsed, code="SCI", defaults={"name": "Science"},
        )
        major_count_before = Major.objects.filter(program=bsed).count()

        column_map = {"student_id": 0, "name": 1, "sex": 2, "college": 3, "program": 4, "year": 5, "major": 6}
        rows = [
            # Sheet says "Sciences"; canonical row is SCI / "Science".
            ["202688881", "Plural A", "F", "CTED", "BSED", "3", "Sciences"],
        ]
        created, _ = import_students_from_rows(rows, column_map)
        self.assertEqual(created, 1)

        from qrapp.models import Student as _Student
        self.assertEqual(_Student.objects.get(student_id="202688881").major_id, sci.pk)
        # No duplicate "Sciences" major was created.
        self.assertEqual(Major.objects.filter(program=bsed).count(), major_count_before)
        _Student.objects.filter(student_id="202688881").delete()

    def test_dedupe_majors_merges_plural_variants(self):
        from qrapp.legacy_program_merge import dedupe_majors
        from qrapp.models import College, Major, Program, Student as _Student

        cted, _ = College.objects.get_or_create(code="CTED", defaults={"name": "College of Teacher Education", "is_active": True})
        bsed, _ = Program.objects.get_or_create(
            college=cted, code="BSED",
            defaults={"name": "Bachelor of Secondary Education"},
        )
        sci, _ = Major.objects.get_or_create(
            program=bsed, code="SCI", defaults={"name": "Science"},
        )
        plural = Major.objects.create(program=bsed, code="Sciences", name="Sciences")
        student = _Student.objects.create(
            student_id="202688882", name="Plural Dedupe", sex="F",
            college=cted, program=bsed, year=2, major=plural,
        )

        removed = dedupe_majors(Major, _Student)

        self.assertEqual(removed, 1)
        self.assertFalse(Major.objects.filter(pk=plural.pk).exists())
        student.refresh_from_db()
        self.assertEqual(student.major_id, sci.pk)
        student.delete()


class RosterTextParsingTests(TestCase):
    """Registrar text-PDF rosters must be parsed by anchoring on the course
    code, not on an M/F sex token.

    The BSOA/BSHM/BSBA/CCJE exports leave the Sex column blank and print the
    middle initial in its place ("DUMAGO, NASH ADRIEL F BSOA 4"), so an
    M/F-anchored parser only found the 4 BSOA rows whose middle initial
    happened to be M or F instead of all 142.
    """

    @classmethod
    def setUpTestData(cls):
        from qrapp.models import College, Program

        cba, _ = College.objects.get_or_create(
            code="CBA",
            defaults={"name": "College of Business Administration", "is_active": True},
        )
        cls.bsao, _ = Program.objects.get_or_create(
            college=cba,
            code="BSOA",
            defaults={
                "name": "Bachelor of Science in Office Administration",
                "is_active": True,
            },
        )
        cls.bshm, _ = Program.objects.get_or_create(
            college=cba,
            code="BSHM",
            defaults={
                "name": "Bachelor of Science in Hospitality Management",
                "is_active": True,
            },
        )
        cls.text_column_map = {
            "student_id": 0,
            "name": 1,
            "sex": 2,
            "program": 3,
            "year": 4,
            "major": 5,
        }
        # Lines copied from BSOA-2026-2027.pdf (blank Sex column). The trailing
        # "F"/"M" tokens are middle initials, not sex values.
        cls.bsao_lines = [
            "1 202600193 ADVINCULA, ALAYSSA MARIE BSOA 1",
            "11 202600253 DECAFINO, ASHLEY MARIE BSOA 1",
            "120 202300643 DUMAGO, NASH ADRIEL F BSOA 4",
            "127 202300122 JAUDIAN, ROSE M BSOA 4",
            "132 202300047 OMISON, KATE NICOLE M BSOA 4",
            "136 202200771 SATINITIGAN, KAYE M BSOA 4",
        ]

    def test_rows_with_blank_sex_column_are_parsed(self):
        from qrapp.student_import import parse_pdf_text_row

        self.assertEqual(
            parse_pdf_text_row("1 202600193 ADVINCULA, ALAYSSA MARIE BSOA 1"),
            ["202600193", "ADVINCULA, ALAYSSA MARIE", "", "BSOA", "1", ""],
        )

    def test_middle_initial_is_not_reported_as_sex(self):
        from qrapp.student_import import parse_pdf_text_row

        # These are exactly the rows the old M/F-anchored parser found.
        self.assertEqual(
            parse_pdf_text_row("120 202300643 DUMAGO, NASH ADRIEL F BSOA 4"),
            ["202300643", "DUMAGO, NASH ADRIEL F", "", "BSOA", "4", ""],
        )
        self.assertEqual(
            parse_pdf_text_row("127 202300122 JAUDIAN, ROSE M BSOA 4"),
            ["202300122", "JAUDIAN, ROSE M", "", "BSOA", "4", ""],
        )

    def test_sex_column_after_dotted_initial_is_kept(self):
        from qrapp.student_import import parse_pdf_text_row

        # Rosters that do carry a Sex column keep it: the value follows the
        # dotted middle initial.
        self.assertEqual(
            parse_pdf_text_row("1 202500967 CASIPONG,JHONREY C. M BSCS 2"),
            ["202500967", "CASIPONG,JHONREY C.", "M", "BSCS", "2", ""],
        )
        self.assertEqual(
            parse_pdf_text_row("1 202600543 ABELLANA,RAZEL S. M BSIT 1 Automotive Technology"),
            ["202600543", "ABELLANA,RAZEL S.", "M", "BSIT", "1", "Automotive Technology"],
        )

    def test_whole_bsao_roster_imports(self):
        from qrapp.student_import import import_students_from_rows, parse_pdf_text_row
        from qrapp.models import Student

        rows = [parse_pdf_text_row(line) for line in self.bsao_lines]
        self.assertTrue(all(rows), rows)

        created, skipped = import_students_from_rows(rows, self.text_column_map)

        self.assertEqual((created, skipped), (len(self.bsao_lines), 0))
        self.assertEqual(
            Student.objects.filter(program=self.bsao).count(), len(self.bsao_lines)
        )

    def test_pdf_row_without_student_number_is_reported_not_dropped(self):
        import tempfile

        from reportlab.pdfgen import canvas

        from qrapp.student_import import import_students_from_file

        # The registrar's BSBA export prints a dash instead of a Student No.
        # for four students (e.g. "202 - BARIDO, MERFE BSBA 3 HRM"). Those rows
        # used to disappear without a trace; they must now be reported, and
        # listed by name rather than as raw text.
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "roster.pdf")
            pdf = canvas.Canvas(path)
            y = 800
            for line in [
                "No. Student No. Name Sex Course Year Major",
                "1 202600143 ABACAL, JEAN MARIE M BSBA 1 HRM",
                "2 202600034 ADALIA, ZAIRYN JANE R BSBA 1 HRM",
                "202 \u2014 BARIDO, MERFE BSBA 3 HRM",
            ]:
                pdf.drawString(40, y, line)
                y -= 14
            pdf.save()

            result = import_students_from_file(path)

        self.assertEqual(result["created"], 2)
        self.assertEqual(result["missing_number_skipped"], 1)
        self.assertEqual(
            result["missing_numbers"], [{"position": "202", "name": "BARIDO, MERFE"}]
        )
        self.assertIn("no student number", result["message"])
        self.assertIn("BARIDO, MERFE", result["message"])

    def test_rows_without_student_numbers_are_imported_when_opted_in(self):
        import tempfile

        from reportlab.pdfgen import canvas

        from qrapp.student_import import import_students_from_file
        from qrapp.models import Student

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "roster.pdf")
            pdf = canvas.Canvas(path)
            y = 800
            for line in [
                "No. Student No. Name Sex Course Year Major",
                "1 202600143 ABACAL, JEAN MARIE M BSBA 1 HRM",
                "202 \u2014 BARIDO, MERFE BSBA 3 HRM",
            ]:
                pdf.drawString(40, y, line)
                y -= 14
            pdf.save()

            first = import_students_from_file(path, allow_generated_ids=True)
            second = import_students_from_file(path, allow_generated_ids=True)

        self.assertEqual(first["created"], 2)
        self.assertEqual(first["generated_ids"], ["NOID-BSBA-3-202"])
        self.assertIn("generated", first["message"])

        student = Student.objects.get(student_id="NOID-BSBA-3-202")
        self.assertEqual(student.name, "BARIDO, MERFE")
        self.assertEqual(student.program.code, "BSBA")
        self.assertEqual(student.college.code, "CBA")
        self.assertEqual(student.year, 3)

        # Re-importing the same roster must not create the student twice.
        self.assertEqual(second["created"], 0)
        self.assertEqual(Student.objects.filter(name="BARIDO, MERFE").count(), 1)

    def test_conflicting_student_numbers_are_reported_by_name(self):
        from qrapp.student_import import import_students_from_rows
        from qrapp.models import Student

        rows = [
            ["202600303", "CANOY, JANICE Y", "", "BSHM", "1", ""],
            # Same number printed for a different student (registrar typo).
            ["202600303", "ERSAN, GERALDINE A", "", "BSHM", "1", ""],
            ["202600042", "PEBIDA, LORAINE GRACE A", "", "BSHM", "1", ""],
            # Same student listed twice.
            ["202600042", "PEBIDA, LORAINE GRACE A", "", "BSHM", "1", ""],
        ]
        stats = {}

        created, skipped = import_students_from_rows(
            rows, self.text_column_map, stats=stats
        )

        self.assertEqual((created, skipped), (2, 2))
        self.assertEqual(stats["already_exists"], 1)
        self.assertEqual(stats["conflict_count"], 1)
        self.assertEqual(stats["conflict_skipped"], 1)
        self.assertEqual(
            stats["conflicts"],
            [
                {
                    "student_id": "202600303",
                    "existing": "CANOY, JANICE Y",
                    "incoming": "ERSAN, GERALDINE A",
                }
            ],
        )
        self.assertEqual(Student.objects.count(), 2)

    def test_shared_student_numbers_get_variant_ids_when_opted_in(self):
        from qrapp.student_import import import_students_from_rows
        from qrapp.models import Student

        rows = [
            ["202600303", "CANOY, JANICE Y", "", "BSHM", "1", ""],
            # Same number printed for a different student (registrar typo).
            ["202600303", "ERSAN, GERALDINE A", "", "BSHM", "1", ""],
        ]
        stats = {}

        created, _ = import_students_from_rows(
            rows, self.text_column_map, stats=stats, allow_generated_ids=True
        )

        self.assertEqual(created, 2)
        self.assertEqual(stats["generated_ids"], ["202600303-DUP2"])
        self.assertEqual(stats["conflict_skipped"], 0)
        self.assertEqual(
            Student.objects.get(student_id="202600303").name, "CANOY, JANICE Y"
        )
        variant = Student.objects.get(student_id="202600303-DUP2")
        self.assertEqual(variant.name, "ERSAN, GERALDINE A")
        self.assertEqual(variant.program, self.bshm)

        # A second run of the same roster must not add a third student.
        again, _ = import_students_from_rows(
            rows, self.text_column_map, allow_generated_ids=True
        )
        self.assertEqual(again, 0)
        self.assertEqual(Student.objects.count(), 2)

    def test_import_stats_separate_existing_from_unreadable_rows(self):
        from qrapp.student_import import import_students_from_rows, parse_pdf_text_row
        from qrapp.models import Student

        rows = [
            parse_pdf_text_row("1 202600193 ADVINCULA, ALAYSSA MARIE BSOA 1"),
            ["202600193", "ADVINCULA, ALAYSSA MARIE", "", "BSOA", "1", ""],  # duplicate
            ["202699999", "", "", "BSOA", "1", ""],  # no name -> unreadable
        ]
        stats = {}

        created, skipped = import_students_from_rows(
            rows, self.text_column_map, stats=stats
        )

        self.assertEqual((created, skipped), (1, 2))
        self.assertEqual(stats["already_exists"], 1)
        self.assertEqual(stats["unreadable"], 1)
        self.assertEqual(stats["duplicate_ids"], ["202600193"])
        self.assertEqual(Student.objects.filter(program=self.bsao).count(), 1)

class ImportIssueReportTests(TestCase):
    """Roster rows the import cannot identify must reach the review page.

    ``student_import`` used to print these rows to the console only; they are
    now stored as ``ImportIssue`` rows and shown by the "Import Issues"
    button in the Students section.
    """

    @classmethod
    def setUpTestData(cls):
        from qrapp.models import College, Program

        cba, _ = College.objects.get_or_create(
            code="CBA",
            defaults={"name": "College of Business Administration", "is_active": True},
        )
        cls.bsba, _ = Program.objects.get_or_create(
            college=cba,
            code="BSBA",
            defaults={"name": "Bachelor of Science in Business Administration", "is_active": True},
        )
        cls.text_column_map = {
            "student_id": 0,
            "name": 1,
            "sex": 2,
            "program": 3,
            "year": 4,
            "major": 5,
        }

    def _write_pdf(self, folder, name="roster.pdf", lines=()):
        from reportlab.pdfgen import canvas

        path = os.path.join(folder, name)
        pdf = canvas.Canvas(path)
        y = 800
        for line in lines:
            pdf.drawString(40, y, line)
            y -= 14
        pdf.save()
        return path

    def test_missing_numbers_are_persisted_for_review(self):
        import tempfile

        from qrapp.student_import import import_students_from_file
        from qrapp.models import ImportIssue

        with tempfile.TemporaryDirectory() as folder:
            path = self._write_pdf(
                folder,
                lines=[
                    "No. Student No. Name Sex Course Year Major",
                    "202 \u2014 BARIDO, MERFE BSBA 3 HRM",
                ],
            )
            import_students_from_file(path, source_file="BSBA-2026-2027.pdf")

        issue = ImportIssue.objects.get()
        self.assertEqual(issue.issue_type, ImportIssue.MISSING_NUMBER)
        self.assertEqual(issue.name, "BARIDO, MERFE")
        self.assertEqual(issue.program, self.bsba)
        self.assertEqual(issue.year, 3)
        self.assertEqual(issue.source_file, "BSBA-2026-2027.pdf")
        self.assertIn("202", issue.detail)
        self.assertIn("no student number", issue.detail)

    def test_generated_numbers_are_flagged_for_review(self):
        import tempfile

        from qrapp.student_import import import_students_from_file
        from qrapp.models import ImportIssue

        with tempfile.TemporaryDirectory() as folder:
            path = self._write_pdf(
                folder,
                lines=[
                    "No. Student No. Name Sex Course Year Major",
                    "202 \u2014 BARIDO, MERFE BSBA 3 HRM",
                ],
            )
            import_students_from_file(path, allow_generated_ids=True)

        issue = ImportIssue.objects.get()
        self.assertEqual(issue.issue_type, ImportIssue.GENERATED_NUMBER)
        self.assertEqual(issue.student_id, "NOID-BSBA-3-202")
        self.assertEqual(issue.name, "BARIDO, MERFE")
        self.assertIn("stored as NOID-BSBA-3-202", issue.detail)

        # Re-importing the same roster must not duplicate the review entry.
        import_students_from_file(path, allow_generated_ids=True)
        self.assertEqual(ImportIssue.objects.count(), 1)

    def test_shared_numbers_and_duplicates_are_persisted(self):
        from qrapp.student_import import import_students_from_rows
        from qrapp.models import ImportIssue

        rows = [
            ["202600303", "CANOY, JANICE Y", "", "BSBA", "1", ""],
            # Same number printed for a different student (registrar typo).
            ["202600303", "ERSAN, GERALDINE A", "", "BSBA", "1", ""],
            ["202600042", "PEBIDA, LORAINE GRACE A", "", "BSBA", "1", ""],
            # Same student listed twice in the file.
            ["202600042", "PEBIDA, LORAINE GRACE A", "", "BSBA", "1", ""],
        ]
        stats = {}
        import_students_from_rows(rows, self.text_column_map, stats=stats)

        types = sorted(ImportIssue.objects.values_list("issue_type", flat=True))
        self.assertEqual(types, ["duplicate_row", "shared_number"])
        conflict = ImportIssue.objects.get(issue_type=ImportIssue.SHARED_NUMBER)
        self.assertEqual(conflict.name, "ERSAN, GERALDINE A")
        self.assertEqual(conflict.student_id, "202600303")
        self.assertIn("CANOY, JANICE Y", conflict.detail)
        self.assertIn("not stored", conflict.detail)
        dup = ImportIssue.objects.get(issue_type=ImportIssue.DUPLICATE_ROW)
        self.assertEqual(dup.name, "PEBIDA, LORAINE GRACE A")

        # A re-import meets the stored roster, not a duplicate listing, so the
        # review list must stay at two issues.
        import_students_from_rows(rows, self.text_column_map, stats={})
        self.assertEqual(ImportIssue.objects.count(), 2)

    def test_variant_ids_are_reported_as_shared_number(self):
        from qrapp.student_import import import_students_from_rows
        from qrapp.models import ImportIssue

        rows = [
            ["202600303", "CANOY, JANICE Y", "", "BSBA", "1", ""],
            ["202600303", "ERSAN, GERALDINE A", "", "BSBA", "1", ""],
        ]
        import_students_from_rows(
            rows, self.text_column_map, stats={}, allow_generated_ids=True
        )

        issue = ImportIssue.objects.get()
        self.assertEqual(issue.issue_type, ImportIssue.SHARED_NUMBER)
        self.assertEqual(issue.student_id, "202600303-DUP2")
        self.assertIn("stored as 202600303-DUP2", issue.detail)
        self.assertIn("CANOY, JANICE Y", issue.detail)

    def test_review_page_lists_issues_and_supports_dismiss_and_clear(self):
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from qrapp.views import import_issues, dismiss_import_issue, clear_import_issues
        from qrapp.models import ImportIssue

        ImportIssue.objects.create(
            issue_type=ImportIssue.MISSING_NUMBER,
            name="BARIDO, MERFE",
            program=self.bsba,
            year=3,
            detail="Roster line 202: no student number in the file (not stored)",
            source_file="BSBA-2026-2027.pdf",
        )
        ImportIssue.objects.create(
            issue_type=ImportIssue.SHARED_NUMBER,
            name="ERSAN, GERALDINE A",
            student_id="202600303",
            program=self.bsba,
            year=1,
            detail="Number 202600303 is also printed for 'CANOY, JANICE Y' (not stored)",
            source_file="BSBA-2026-2027.pdf",
        )
        admin = User.objects.create_superuser("issuesadmin", "i@x.com", "pw12345")
        request = RequestFactory().get("/dashboard/import_issues/")
        request.user = admin

        response = import_issues(request)
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("BARIDO, MERFE", html)
        self.assertIn("ERSAN, GERALDINE A", html)
        self.assertIn("No student number in the file: 1", html)
        self.assertIn("202600303", html)

        # Dismiss one issue: it disappears, the other stays.
        first = ImportIssue.objects.order_by("id").first()
        dismiss_request = RequestFactory().get(
            f"/dashboard/import_issues/dismiss/{first.id}/"
        )
        dismiss_request.user = admin
        response = dismiss_import_issue(dismiss_request, issue_id=first.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ImportIssue.objects.count(), 1)

        # Clear the list: everything goes.
        clear_request = RequestFactory().get("/dashboard/import_issues/clear/")
        clear_request.user = admin
        response = clear_import_issues(clear_request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ImportIssue.objects.count(), 0)

    def test_review_page_is_staff_only(self):
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from qrapp.views import import_issues

        requester = User.objects.create_user(
            "regular", "r@x.com", "pw12345", is_staff=False
        )
        request = RequestFactory().get("/dashboard/import_issues/")
        request.user = requester
        with self.assertRaises(PermissionError):
            import_issues(request)


class DashboardQueryBudgetTest(TestCase):
    """The admin dashboard must stay O(1) in queries, not N+1 per student."""

    def test_admin_dashboard_query_count_is_bounded(self):
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from qrapp.views import admin_dashboard
        from qrapp.models import College, Program, Major, Student

        college = College.objects.order_by("id").first()
        program = Program.objects.filter(college=college).order_by("id").first()
        if program is None:
            program = Program.objects.create(college=college, code="TSTP", name="Test Program")
        User.objects.create_superuser("dashadmin", "d@x.com", "pw")
        majors = [
            Major.objects.create(program=program, code=f"M{i}", name=f"Major {i}")
            for i in range(3)
        ]
        for i in range(60):
            Student.objects.create(
                student_id=f"T{i:04d}",
                name=f"Test Student {i}",
                sex="M" if i % 2 else "F",
                year=1,
                college=college,
                program=program,
                major=majors[i % 3],
            )

        admin = User.objects.get(username="dashadmin")
        request = RequestFactory().get("/admin_dashboard/")
        request.user = admin
        with CaptureQueriesContext(connection) as ctx:
            admin_dashboard(request)
        # 60 students: a per-student N+1 (sex/major deferred fields) would
        # blow well past this bound; the optimized view needs ~20.
        self.assertLess(len(ctx.captured_queries), 60, "Dashboard regressed to N+1 queries")
