import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import (AuditLog, Category, Complaint, Department, DuplicateGroup, DuplicateSuggestion, Notification,
                     StatusHistory, User, Ward)

TMP_MEDIA = tempfile.mkdtemp()


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class CiviSenseTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.dept = Department.objects.create(code="BBMP", name="BBMP")
        self.cat = Category.objects.create(name="Garbage & Sanitation", default_department=self.dept, severity_weight=3)
        Category.objects.create(name="Street Lighting", default_department=self.dept, severity_weight=3)
        self.ward = Ward.objects.create(ward_id=1, name="Test Ward")
        self.admin = User.objects.create_user("adm", password="Pass@12345", role="ADMIN")
        self.officer = User.objects.create_user("off", password="Pass@12345", role="OFFICER", department=self.dept)
        self.citizen = User.objects.create_user("cit", password="Pass@12345", role="CITIZEN", email="c@x.com")
        self.other = User.objects.create_user("cit2", password="Pass@12345", role="CITIZEN", email="c2@x.com")

    def submit(self, client, title="Garbage overflow", desc="Garbage is overflowing near my apartment and smells bad", **extra):
        data = {"title": title, "description": desc, "ward": self.ward.pk, "address": "1st Cross", "severity": 4,
                "public_impact": 2, "latitude": "12.9716", "longitude": "77.5946", **extra}
        return client.post(reverse("citizen_submit"), data, follow=True)

    # ---- authentication / registration
    def test_registration_and_login(self):
        r = self.client.post(reverse("register"), {"username": "newcit", "first_name": "New Cit", "email": "n@x.com", "phone": "",
                                                    "password1": "Str0ng!Pass99", "password2": "Str0ng!Pass99"}, follow=True)
        self.assertEqual(r.status_code, 200)
        u = User.objects.get(username="newcit")
        self.assertEqual(u.role, "CITIZEN")
        self.assertNotEqual(u.password, "Str0ng!Pass99")  # hashed

    def test_role_specific_login(self):
        r = self.client.post(reverse("login", args=["officer"]), {"username": "cit", "password": "Pass@12345"})
        self.assertContains(r, "not a department officer account")
        r = self.client.post(reverse("login", args=["officer"]), {"username": "off", "password": "Pass@12345"})
        self.assertRedirects(r, reverse("officer_home"))
        self.client.logout()
        r = self.client.post(reverse("login", args=["admin"]), {"username": "adm", "password": "wrong"})
        self.assertContains(r, "Invalid username or password")

    def test_cannot_self_register_as_officer(self):
        self.client.post(reverse("register"), {"username": "sneaky", "first_name": "S", "email": "s@x.com", "role": "ADMIN",
                                                "password1": "Str0ng!Pass99", "password2": "Str0ng!Pass99"})
        self.assertEqual(User.objects.get(username="sneaky").role, "CITIZEN")

    # ---- role based access
    def test_rbac(self):
        self.client.login(username="cit", password="Pass@12345")
        for name in ("dashboard", "complaints", "mining", "officer_home", "geo"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)
        self.assertEqual(self.client.get(reverse("citizen_home")).status_code, 200)
        self.client.logout()
        self.client.login(username="off", password="Pass@12345")
        for name in ("dashboard", "complaints", "mining", "citizen_home", "citizen_submit"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)
        self.assertEqual(self.client.get(reverse("officer_home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("geo")).status_code, 200)
        self.client.logout()
        self.client.login(username="adm", password="Pass@12345")
        for name in ("dashboard", "complaints", "mining", "geo"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get("/django-admin/").status_code, 200)

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)
        self.assertEqual(self.client.get(reverse("api_suggest")).status_code, 302)

    # ---- complaint submission + classification
    def test_submission_classifies_and_persists(self):
        self.client.login(username="cit", password="Pass@12345")
        r = self.submit(self.client)
        c = Complaint.objects.get(citizen=self.citizen)
        self.assertRegex(c.ref, r"^CS-\d{4}-\d{6}$")
        self.assertEqual(c.source, "LIVE")
        self.assertEqual(c.category.name, "Garbage & Sanitation")   # model suggestion used (no category chosen)
        self.assertEqual(c.predicted_category.name, "Garbage & Sanitation")
        self.assertGreater(c.prediction_confidence, 0.3)
        self.assertEqual(c.department, self.dept)
        self.assertIn(c.recommended_priority, ["Critical", "High", "Medium", "Low"])
        self.assertTrue(c.priority_factors)
        self.assertEqual(StatusHistory.objects.filter(complaint=c).count(), 1)
        self.assertTrue(Notification.objects.filter(user=self.citizen, complaint=c).exists())
        self.assertContains(r, c.ref)

    def test_validation_errors(self):
        self.client.login(username="cit", password="Pass@12345")
        r = self.submit(self.client, title="ab", desc="short")
        self.assertEqual(Complaint.objects.count(), 0)
        self.assertContains(r, "at least")
        r = self.submit(self.client, latitude="40.0", longitude="10.0")
        self.assertContains(r, "outside the Bengaluru area")
        self.assertEqual(Complaint.objects.count(), 0)

    def test_image_upload_validation(self):
        self.client.login(username="cit", password="Pass@12345")
        bad = SimpleUploadedFile("evil.exe", b"MZ....", content_type="application/octet-stream")
        r = self.submit(self.client, image=bad)
        self.assertEqual(Complaint.objects.count(), 0)
        fake = SimpleUploadedFile("x.png", b"not really an image", content_type="image/png")
        self.submit(self.client, image=fake)
        self.assertEqual(Complaint.objects.count(), 0)
        ok = SimpleUploadedFile("photo.png", png_bytes(), content_type="image/png")
        self.submit(self.client, image=ok)
        c = Complaint.objects.get()
        self.assertTrue(c.image.name.endswith(".png"))
        self.assertNotIn("photo", c.image.name)  # client file name is not trusted

    def test_suggest_api(self):
        self.client.login(username="cit", password="Pass@12345")
        d = self.client.get(reverse("api_suggest"), {"text": "Garbage is overflowing near my apartment"}).json()
        self.assertTrue(d["suggestions"])
        self.assertEqual(d["suggestions"][0]["name"], "Garbage & Sanitation")

    # ---- duplicates remain traceable
    def test_duplicate_detection_and_review(self):
        self.client.login(username="cit", password="Pass@12345")
        self.submit(self.client)
        self.client.logout()
        self.client.login(username="cit2", password="Pass@12345")
        self.submit(self.client, title="Garbage overflowing", desc="Garbage is overflowing near my apartment, it smells very bad")
        first, second = Complaint.objects.order_by("created_at")
        s = DuplicateSuggestion.objects.get(complaint=second, other=first)
        self.assertGreater(s.text_similarity, 0.5)
        self.assertEqual(second.related_count, 1)
        self.client.logout()
        self.client.login(username="off", password="Pass@12345")
        first.assigned_officer = self.officer
        first.save()
        r = self.client.post(reverse("complaint_detail", args=[first.ref]), {"action": "dup_review", "suggestion": s.pk, "decision": "confirm"}, follow=True)
        s.refresh_from_db()
        self.assertEqual(s.state, "CONFIRMED")
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.duplicate_group_id, second.duplicate_group_id)
        self.assertEqual(Complaint.objects.count(), 2)  # nothing merged or deleted
        self.assertTrue(AuditLog.objects.filter(action="DUPLICATE_REVIEW").exists())

    # ---- assignment, status updates, history
    def test_assignment_status_flow(self):
        self.client.login(username="cit", password="Pass@12345")
        self.submit(self.client)
        c = Complaint.objects.get()
        self.client.logout()
        self.client.login(username="adm", password="Pass@12345")
        self.client.post(reverse("complaint_detail", args=[c.ref]), {"action": "assign", "officer": self.officer.pk})
        c.refresh_from_db()
        self.assertEqual(c.assigned_officer, self.officer)
        self.assertTrue(Notification.objects.filter(user=self.officer).exists())
        self.client.logout()
        self.client.login(username="off", password="Pass@12345")
        self.assertContains(self.client.get(reverse("officer_home")), c.ref)
        # resolving without details is rejected
        self.client.post(reverse("complaint_detail", args=[c.ref]), {"action": "status", "status": "Resolved"})
        c.refresh_from_db()
        self.assertEqual(c.status, "Open")
        self.client.post(reverse("complaint_detail", args=[c.ref]), {"action": "status", "status": "On-the-Job", "remarks": "Crew dispatched"})
        self.client.post(reverse("complaint_detail", args=[c.ref]), {"action": "status", "status": "Resolved", "resolution_details": "Garbage cleared"})
        c.refresh_from_db()
        self.assertEqual(c.status, "Resolved")
        self.assertIsNotNone(c.resolved_at)
        self.assertEqual(list(c.history.values_list("new_status", flat=True)), ["Open", "On-the-Job", "Resolved"])
        self.client.logout()
        self.client.login(username="cit", password="Pass@12345")
        page = self.client.get(reverse("complaint_detail", args=[c.ref]))
        self.assertContains(page, "Crew dispatched")
        self.assertContains(page, "Garbage cleared")
        self.assertNotContains(page, "Update status")   # citizens cannot update

    def test_officer_cannot_open_other_department_complaint(self):
        other_dept = Department.objects.create(code="BWSSB")
        c = Complaint.objects.create(ref="CS-X", source="LIVE", title="t", description="d", department=other_dept, created_at=timezone.now())
        self.client.login(username="off", password="Pass@12345")
        self.assertEqual(self.client.get(reverse("complaint_detail", args=[c.ref])).status_code, 302)

    def test_citizen_cannot_see_others_complaint(self):
        self.client.login(username="cit", password="Pass@12345")
        self.submit(self.client)
        c = Complaint.objects.get()
        self.client.logout()
        self.client.login(username="cit2", password="Pass@12345")
        self.assertEqual(self.client.get(reverse("complaint_detail", args=[c.ref])).status_code, 302)

    # ---- priority override + category correction
    def test_priority_override_and_correction(self):
        self.client.login(username="cit", password="Pass@12345")
        self.submit(self.client)
        c = Complaint.objects.get()
        self.client.logout()
        self.client.login(username="adm", password="Pass@12345")
        self.client.post(reverse("complaint_detail", args=[c.ref]), {"action": "override", "level": "Critical", "reason": ""})
        c.refresh_from_db(); self.assertEqual(c.override_priority, "")
        self.client.post(reverse("complaint_detail", args=[c.ref]), {"action": "override", "level": "Critical", "reason": "Blocks school entrance"})
        c.refresh_from_db(); self.assertEqual(c.priority, "Critical"); self.assertEqual(c.override_by, self.admin)
        self.client.post(reverse("complaint_detail", args=[c.ref]), {"action": "override", "level": "", "reason": ""})
        c.refresh_from_db(); self.assertEqual(c.priority, c.recommended_priority)
        light = Category.objects.get(name="Street Lighting")
        self.client.post(reverse("complaint_detail", args=[c.ref]), {"action": "correct_category", "category": light.pk})
        c.refresh_from_db(); self.assertEqual(c.category, light); self.assertTrue(c.category_corrected)

    # ---- geography / dashboards
    def test_geo_api_dbscan_and_dashboard(self):
        base = timezone.now()
        for i in range(12):   # tight cluster of historical complaints (test fixtures only)
            Complaint.objects.create(ref=f"H-{i}", source="HISTORICAL", title="x", description="y", category=self.cat, ward=self.ward,
                                     latitude=12.9700 + i * 0.00005, longitude=77.5900, created_at=base, status="Open")
        Complaint.objects.create(ref="H-far", source="HISTORICAL", title="x", description="y", category=self.cat, ward=self.ward,
                                 latitude=13.10, longitude=77.70, created_at=base)
        Complaint.objects.create(ref="H-nogeo", source="HISTORICAL", title="x", description="y", category=self.cat, ward=self.ward, created_at=base)
        self.client.login(username="adm", password="Pass@12345")
        d = self.client.get(reverse("api_geo"), {"eps": 200, "min_samples": 5}).json()
        self.assertEqual(d["result"]["n_clusters"], 1)
        self.assertEqual(d["result"]["noise_points"], 1)
        self.assertEqual(d["without_coordinates"], 1)
        self.assertEqual(d["result"]["clusters"][0]["size"], 12)
        d = self.client.get(reverse("api_geo"), {"category": self.cat.pk, "eps": 200, "min_samples": 5}).json()
        self.assertEqual(len(d["points"]), 13)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Total complaints")
        self.assertEqual(page.context["kpis"][0][1], 14)
        self.assertEqual(self.client.get(reverse("dashboard"), {"source": "LIVE"}).context["kpis"][0][1], 0)

    def test_complaint_filters(self):
        self.client.login(username="cit", password="Pass@12345")
        self.submit(self.client)
        self.client.logout()
        self.client.login(username="adm", password="Pass@12345")
        r = self.client.get(reverse("complaints"), {"q": "overflowing", "status": "Open", "category": self.cat.pk, "ward": self.ward.pk})
        self.assertEqual(r.context["total_found"], 1)
        r = self.client.get(reverse("complaints"), {"q": "nonexistent-zzz"})
        self.assertEqual(r.context["total_found"], 0)
