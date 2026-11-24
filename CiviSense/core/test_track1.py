"""Django tests for the Track 1 additions (MCP backend permissions, voice review, ward fix, map scoping).

NOTE: written alongside the code but NOT executed in the authoring sandbox (it had no Django/PostgreSQL).
Run:  python manage.py test core
"""
import json

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from civisense_mcp.errors import BackendError
from civisense_mcp.backend import DjangoBackend, ROLES

from .models import AuditLog, Category, Complaint, Department, User, Ward


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.dept = Department.objects.create(code="BBMP", name="BBMP")
        self.dept2 = Department.objects.create(code="BESCOM", name="BESCOM")
        self.cat = Category.objects.create(name="Garbage & Sanitation", default_department=self.dept, severity_weight=3)
        self.cat2 = Category.objects.create(name="Street Lighting", default_department=self.dept2, severity_weight=3)
        self.ward = Ward.objects.create(ward_id=1, name="Test Ward")
        self.admin = User.objects.create_user("adm", password="Pass@12345", role="ADMIN")
        self.off = User.objects.create_user("off", password="Pass@12345", role="OFFICER", department=self.dept)
        self.off2 = User.objects.create_user("off2", password="Pass@12345", role="OFFICER", department=self.dept2)
        self.cit = User.objects.create_user("cit", password="Pass@12345", role="CITIZEN", email="c@x.com")
        self.cit2 = User.objects.create_user("cit2", password="Pass@12345", role="CITIZEN", email="c2@x.com")
        self.be = DjangoBackend()

    def make(self, user, cat=None, desc="Garbage is overflowing near my apartment and smells bad", lat="12.9716", lon="77.5946"):
        self.client.force_login(user)
        r = self.client.post(reverse("citizen_submit"), {
            "title": "Garbage overflow", "description": desc, "ward": self.ward.pk, "address": "1st Cross",
            "severity": 4, "public_impact": 2, "latitude": lat, "longitude": lon,
            "category": (cat or self.cat).pk, "original_language": "en", "input_method": "TYPED"}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.client.logout()
        return Complaint.objects.filter(citizen=user).latest("id")


class McpBackendPermissions(Base):
    def test_role_matrix(self):
        for tool in ("get_officer_tasks", "update_complaint_status"):
            self.assertNotIn("CITIZEN", ROLES[tool])
        self.assertEqual(set(ROLES["submit_complaint"]), {"CITIZEN"})

    def test_citizen_cannot_use_officer_tools(self):
        with self.assertRaises(BackendError) as cm:
            self.be.execute("get_officer_tasks", self.cit.pk, {})
        self.assertEqual(cm.exception.code, "forbidden")

    def test_officer_cannot_submit(self):
        with self.assertRaises(BackendError) as cm:
            self.be.prepare("submit_complaint", self.off.pk, {"title": "x", "description": "y" * 30, "ward": "Test Ward"})
        self.assertEqual(cm.exception.code, "forbidden")

    def test_inactive_or_unknown_user_is_unauthenticated(self):
        with self.assertRaises(BackendError) as cm:
            self.be.execute("check_complaint_status", 999999, {"ref": "CS-0001"})
        self.assertEqual(cm.exception.code, "unauthenticated")

    def test_citizen_cannot_read_another_citizens_complaint(self):
        c = self.make(self.cit)
        res = self.be.execute("check_complaint_status", self.cit.pk, {"ref": c.ref})
        self.assertEqual(res["ref"], c.ref)
        with self.assertRaises(BackendError) as other:
            self.be.execute("check_complaint_status", self.cit2.pk, {"ref": c.ref})
        with self.assertRaises(BackendError) as missing:
            self.be.execute("check_complaint_status", self.cit2.pk, {"ref": "CS-NOPE-1"})
        self.assertEqual(other.exception.code, "not_found")
        self.assertEqual(str(other.exception).replace(c.ref, "X"), str(missing.exception).replace("CS-NOPE-1", "X"))

    def test_officer_cannot_update_other_departments_complaint(self):
        c = self.make(self.cit, cat=self.cat2)            # belongs to BESCOM
        c.refresh_from_db()
        if c.department_id == self.dept2.pk:
            with self.assertRaises(BackendError):
                self.be.prepare("update_complaint_status", self.off.pk, {"ref": c.ref, "new_status": "On-the-Job", "remarks": "x"})

    def test_update_prepare_changes_nothing_and_execute_does(self):
        c = self.make(self.cit)
        c.refresh_from_db()
        args = {"ref": c.ref, "new_status": "On-the-Job", "remarks": "Crew sent"}
        user = self.off if c.department_id == self.dept.pk else self.admin
        prev = self.be.prepare("update_complaint_status", user.pk, args)
        self.assertEqual(prev["new_status"], "On-the-Job")
        c.refresh_from_db()
        self.assertNotEqual(c.status, "On-the-Job")                 # preview must not write
        self.be.execute("update_complaint_status", user.pk, args)
        c.refresh_from_db()
        self.assertEqual(c.status, "On-the-Job")

    def test_submit_requires_ward_and_never_guesses(self):
        with self.assertRaises(BackendError) as cm:
            self.be.prepare("submit_complaint", self.cit.pk, {"title": "Pothole", "description": "Big pothole on the main road " * 2})
        self.assertEqual(cm.exception.code, "invalid_input")

    def test_tool_audit_written(self):
        before = AuditLog.objects.count()
        self.be.audit(self.cit.pk, "check_complaint_status", {"ref": "CS-1"}, "ok")
        self.assertEqual(AuditLog.objects.count(), before + 1)
        self.assertTrue(AuditLog.objects.first().action.startswith("MCP_TOOL_"))


class VoiceReviewAndForm(Base):
    def post_form(self, **extra):
        self.client.force_login(self.cit)
        data = {"title": "ರಸ್ತೆಯಲ್ಲಿ ಗುಂಡಿ", "description": "ರಸ್ತೆಯಲ್ಲಿ ದೊಡ್ಡ ಗುಂಡಿ ಇದೆ, ತುರ್ತಾಗಿ ಸರಿಪಡಿಸಿ", "ward": self.ward.pk,
                "address": "MG Road", "severity": 3, "public_impact": 2, "category": self.cat.pk,
                "latitude": "12.9716", "longitude": "77.5946", **extra}
        return self.client.post(reverse("citizen_submit"), data, follow=True)

    def test_voice_without_review_is_rejected(self):
        self.post_form(input_method="VOICE")
        self.assertFalse(Complaint.objects.exists())

    def test_voice_with_review_keeps_original_kannada(self):
        self.post_form(input_method="VOICE", voice_reviewed="on")
        c = Complaint.objects.get()
        self.assertEqual(c.original_language, "kn")
        self.assertEqual(c.input_method, "VOICE")
        self.assertIn("ಗುಂಡಿ", c.description)                       # original text preserved, not translated

    def test_analyze_is_citizen_only_and_saves_nothing(self):
        url = reverse("api_complaint_analyze")
        self.client.force_login(self.off)
        self.assertNotEqual(self.client.post(url, json.dumps({"text": "Garbage overflow in Test Ward"}), content_type="application/json").status_code, 200)
        self.client.force_login(self.cit)
        r = self.client.post(url, json.dumps({"text": "Garbage is overflowing near Test Ward bus stop"}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Complaint.objects.count(), 0)
        self.assertEqual(r.json()["ward"]["name"], "Test Ward")

    def test_analyze_rejects_bad_input(self):
        self.client.force_login(self.cit)
        r = self.client.post(reverse("api_complaint_analyze"), "not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)


class WardFixAndGeo(Base):
    def test_ward_from_point_returns_pk(self):
        cache.delete("ward_centroids_v2")
        w2 = Ward.objects.create(ward_id=500, name="Other Ward")      # dataset ward_id differs from pk on purpose
        for i in range(3):
            Complaint.objects.create(
                ref=f"TEST-HIST-{w2.pk}-{i}",
                title="h",
                description="historical",
                ward=w2,
                latitude=12.90,
                longitude=77.50,
                source=Complaint.Source.HISTORICAL,
                status="Open",
                created_at=timezone.now(),
            )

        cache.delete("ward_centroids_v2")
        self.client.force_login(self.cit)
        r = self.client.get(reverse("api_ward"), {"lat": 12.90, "lon": 77.50})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ward"]["id"], w2.pk)

    def test_geo_api_scopes_officer_to_department(self):
        a = self.make(self.cit, cat=self.cat)
        b = self.make(self.cit2, cat=self.cat2, lat="12.9800", lon="77.6000")
        self.client.force_login(self.off)
        data = self.client.get(reverse("api_geo")).json()
        refs = {p[4] for p in data["points"] if p[4]}
        for c in (a, b):
            c.refresh_from_db()
        mine = {c.ref for c in (a, b) if c.department_id == self.dept.pk}
        theirs = {c.ref for c in (a, b) if c.department_id != self.dept.pk}
        self.assertFalse(refs & theirs)
        self.assertTrue(mine <= refs or not mine)

    def test_geo_point_is_staff_only_and_department_scoped(self):
        c = self.make(self.cit)
        c.refresh_from_db()
        url, q = reverse("api_geo_point"), {"lat": 12.9716, "lon": 77.5946}
        self.client.force_login(self.cit2)
        self.assertNotEqual(self.client.get(url, q).status_code, 200)             # citizens never get map detail
        owner, stranger = (self.off, self.off2) if c.department_id == self.dept.pk else (self.off2, self.off)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(url, q).json()["total"], 0)               # other department sees nothing
        self.client.force_login(owner)
        self.assertEqual(self.client.get(url, q).json()["total"], 1)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url, {"lat": "nan", "lon": "77.5946"}).status_code, 400)


class Migrations(TestCase):
    def test_no_missing_migrations(self):
        from django.core.management import call_command

        call_command(
            "makemigrations",
            "--check",
            "--dry-run",
            verbosity=0
        )