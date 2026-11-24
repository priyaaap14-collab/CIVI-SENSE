from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        CITIZEN = "CITIZEN", "Citizen"
        OFFICER = "OFFICER", "Department Officer"
        ADMIN = "ADMIN", "Administrator"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CITIZEN)
    department = models.ForeignKey("Department", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="officers", help_text="Officers only")
    phone = models.CharField(max_length=15, blank=True)

    def save(self, *args, **kwargs):
        if self.role == self.Role.ADMIN:
            self.is_staff = True  # allows use of Django admin for user / department / category management
        super().save(*args, **kwargs)

    @property
    def is_citizen(self):
        return self.role == self.Role.CITIZEN

    @property
    def is_officer(self):
        return self.role == self.Role.OFFICER

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN


class Department(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Ward(models.Model):
    ward_id = models.PositiveIntegerField(unique=True, help_text="Ward id used in the source dataset")
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=150, unique=True)
    default_department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL,
                                           related_name="categories",
                                           help_text="Department that most often handled this category in the dataset")
    severity_weight = models.PositiveSmallIntegerField(default=2, help_text="1 (low) - 5 (high); used by the priority engine")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class DuplicateGroup(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    note = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"Group #{self.pk}"


class Complaint(models.Model):
    class Source(models.TextChoices):
        HISTORICAL = "HISTORICAL", "Imported historical record"
        LIVE = "LIVE", "Submitted via CiviSense"

    class Status(models.TextChoices):
        OPEN = "Open", "Open"
        IN_PROGRESS = "On-the-Job", "In progress (On-the-Job)"
        REOPENED = "Re-opened", "Re-opened"
        RESOLVED = "Resolved", "Resolved"
        CLOSED = "Closed", "Closed"
        REJECTED = "Rejected", "Rejected"

    class Priority(models.TextChoices):
        CRITICAL = "Critical", "Critical"
        HIGH = "High", "High"
        MEDIUM = "Medium", "Medium"
        LOW = "Low", "Low"

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        KANNADA = "kn", "Kannada"

    class InputMethod(models.TextChoices):
        TYPED = "TYPED", "Typed"
        VOICE = "VOICE", "Voice (speech-to-text)"
        ASSISTANT = "ASSISTANT", "AI assistant"

    ACTIVE_STATUSES = ["Open", "On-the-Job", "Re-opened"]

    ref = models.CharField(max_length=20, unique=True, editable=False)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.LIVE, db_index=True)
    citizen = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="complaints")
    title = models.CharField(max_length=300)
    description = models.TextField()
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints")
    sub_category = models.CharField(max_length=200, blank=True)
    original_category = models.CharField(max_length=200, blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints")
    ward = models.ForeignKey(Ward, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints")
    address = models.TextField(blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    image = models.ImageField(upload_to="complaints/%Y/%m/", null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    severity = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Citizen-reported severity 1-5")
    public_impact = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1 individual, 2 neighbourhood, 3 wide area")
    comment_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_details = models.TextField(blank=True)
    # language of the citizen's own words (title / description are stored exactly as entered, never translated)
    original_language = models.CharField(max_length=5, choices=Language.choices, default=Language.ENGLISH)
    input_method = models.CharField(max_length=10, choices=InputMethod.choices, default=InputMethod.TYPED)

    # classification
    predicted_category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    prediction_confidence = models.FloatField(null=True, blank=True)
    category_corrected = models.BooleanField(default=False)

    # priority (rule based)
    priority_score = models.FloatField(default=0)
    recommended_priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.LOW)
    priority_factors = models.JSONField(default=dict, blank=True)
    override_priority = models.CharField(max_length=10, choices=Priority.choices, blank=True)
    override_reason = models.CharField(max_length=300, blank=True)
    override_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    related_count = models.PositiveIntegerField(default=0, help_text="Related independent reports found by similarity")
    duplicate_group = models.ForeignKey(DuplicateGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints")
    assigned_officer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="assigned_complaints")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["ward", "category"]), models.Index(fields=["recommended_priority"])]

    def __str__(self):
        return self.ref

    @property
    def priority(self):
        return self.override_priority or self.recommended_priority

    @property
    def priority_class(self):
        return {"Critical": "danger", "High": "warning", "Medium": "info", "Low": "success"}.get(self.priority, "secondary")

    @property
    def status_class(self):
        return {"Open": "warning", "On-the-Job": "primary", "Re-opened": "danger", "Resolved": "success",
                "Closed": "secondary", "Rejected": "dark"}.get(self.status, "secondary")

    @property
    def has_geo(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES


class Assignment(models.Model):
    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="assignments")
    officer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assignments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-assigned_at"]


class StatusHistory(models.Model):
    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="history")
    old_status = models.CharField(max_length=12, blank=True)
    new_status = models.CharField(max_length=12)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class DuplicateSuggestion(models.Model):
    """Suspected duplicate pair found by TF-IDF + cosine similarity. Nothing is merged or deleted."""
    class State(models.TextChoices):
        SUGGESTED = "SUGGESTED", "Suggested"
        CONFIRMED = "CONFIRMED", "Confirmed related"
        REJECTED = "REJECTED", "Not related"

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="dup_suggestions")
    other = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="dup_suggestions_as_other")
    text_similarity = models.FloatField()
    combined_score = models.FloatField()
    distance_m = models.FloatField(null=True, blank=True)
    days_apart = models.FloatField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.SUGGESTED, db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("complaint", "other")]
        ordering = ["-combined_score"]


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=300)
    complaint = models.ForeignKey(Complaint, null=True, blank=True, on_delete=models.CASCADE)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=60)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
