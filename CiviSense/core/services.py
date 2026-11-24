"""Business logic shared by views, management commands and the MCP tools.

Everything the web UI does with complaints goes through the functions in this module, and the MCP server calls the
very same functions, so Claude can never reach behaviour that a normal user could not.
"""
import json
from datetime import datetime

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from ml import mining
from ml.priority import compute_priority
from ml.wrangling import clean_text
from .kannada import nlp_text
from .models import AuditLog, Category, Complaint, DuplicateSuggestion, Notification, StatusHistory, Ward

DONE_STATUSES = ["Resolved", "Closed"]


def dataset_reference_date():
    """Historical waiting time is measured up to the last date in the dataset (not 'today')."""
    try:
        rep = json.loads(settings.WRANGLING_REPORT.read_text(encoding="utf-8"))
        return timezone.make_aware(datetime.fromisoformat(rep["reference_date"]) + pd.Timedelta(days=1))
    except Exception:
        return timezone.now()


def log_audit(user, action, obj=None, details=""):
    AuditLog.objects.create(user=user if getattr(user, "pk", None) else None, action=action[:60],
                            object_type=type(obj).__name__ if obj is not None else "",
                            object_id=str(getattr(obj, "pk", "")) if obj is not None else "", details=details[:2000])


def notify(user, message, complaint=None):
    if user:
        Notification.objects.create(user=user, message=message[:300], complaint=complaint)


# ------------------------------------------------------------------ permissions
def can_view(user, c):
    """May `user` open complaint `c`?  Citizens: own complaints only. Officers: assigned to them or their department."""
    if user.is_admin_role:
        return True
    if user.is_citizen:
        return c.citizen_id == user.id
    return c.assigned_officer_id == user.id or bool(user.department_id and c.department_id == user.department_id)


def effective_priority_q(levels):
    """Filter on the priority people actually see: an officer override wins over the system recommendation."""
    from django.db.models import Q
    return Q(override_priority__in=levels) | (Q(override_priority="") & Q(recommended_priority__in=levels))


def visible_complaints(user):
    """QuerySet of the complaints `user` is allowed to read in full (the queryset form of `can_view`)."""
    from django.db.models import Q
    if user.is_admin_role:
        return Complaint.objects.all()
    if user.is_citizen:
        return Complaint.objects.filter(citizen=user)
    q = Q(assigned_officer=user)
    if user.department_id:
        q |= Q(department_id=user.department_id)
    return Complaint.objects.filter(q)


# ------------------------------------------------------------------ priority / classification / duplicates
def score_complaint(c, ref_date=None):
    """Compute rule-based priority fields on a Complaint instance (does not save)."""
    ref = ref_date or (timezone.now() if c.source == Complaint.Source.LIVE else dataset_reference_date())
    wait = max((ref - c.created_at).total_seconds() / 86400.0, 0)
    weight = c.category.severity_weight if c.category_id else 2
    r = compute_priority(weight, c.severity, c.related_count, wait, c.public_impact, c.comment_count, c.status, c.source)
    c.priority_score, c.recommended_priority, c.priority_factors = r["score"], r["level"], r["factors"]
    return r


def priority_preview(category=None, severity=3, public_impact=2, related_count=0):
    """What priority would a NEW complaint get? Same rule engine, no waiting time yet. Nothing is saved."""
    weight = category.severity_weight if category is not None else 2
    return compute_priority(weight, severity, related_count, 0, public_impact, 0, Complaint.Status.OPEN, Complaint.Source.LIVE)


def live_rows(exclude_pk=None):
    qs = Complaint.objects.filter(source=Complaint.Source.LIVE)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return [(c.pk, c.ref, clean_text(nlp_text(c.title, c.description)), c.latitude, c.longitude,
             c.created_at.timestamp() / 86400.0) for c in qs]


def models_ready():
    """Which trained artefacts exist? (`python manage.py train_models` creates them.)"""
    return {"classifier": mining.CLASSIFIER_FILE.exists(), "duplicate_index": mining.DUP_FILE.exists()}


def suggest_categories(text, top_k=3):
    """Return [(Category, probability)] for the classifier's top predictions.

    Kannada words are mapped to English keywords first (original text untouched) because the model is English-only.
    """
    out = []
    for name, p in mining.predict_category(nlp_text(text), top_k):
        cat = Category.objects.filter(name=name).first()
        if cat:
            out.append((cat, p))
    return out


def find_similar(text, latitude=None, longitude=None, exclude_pk=None, top_n=5):
    """Existing duplicate detector (TF-IDF + cosine, scaled by distance and date) for ad-hoc text.

    Returns [(Complaint, match_dict)] ordered by combined score. Nothing is stored.
    """
    now_days = timezone.now().timestamp() / 86400.0
    matches = mining.find_related(nlp_text(text), latitude, longitude, now_days,
                                  live_rows=live_rows(exclude_pk=exclude_pk), exclude_pk=exclude_pk, top_n=top_n)
    objs = Complaint.objects.filter(pk__in=[m["pk"] for m in matches]).select_related("category", "ward", "department")
    by_pk = {o.pk: o for o in objs}
    return [(by_pk[m["pk"]], m) for m in matches if m["pk"] in by_pk]


def process_new_complaint(c, actor=None):
    """Run the live workflow for a newly saved complaint: department routing, duplicate detection, priority."""
    if c.category_id and not c.department_id:
        c.department = c.category.default_department
    matches = mining.find_related(nlp_text(c.title, c.description), c.latitude, c.longitude,
                                  c.created_at.timestamp() / 86400.0,
                                  live_rows=live_rows(exclude_pk=c.pk), exclude_pk=c.pk)
    # ignore index entries whose complaint no longer exists (e.g. history re-imported after training)
    existing = set(Complaint.objects.filter(pk__in=[m["pk"] for m in matches]).values_list("pk", flat=True))
    matches = [m for m in matches if m["pk"] in existing]
    created = 0
    for m in matches:
        _, was_new = DuplicateSuggestion.objects.get_or_create(
            complaint=c, other_id=m["pk"],
            defaults={"text_similarity": m["text_similarity"], "combined_score": m["combined"],
                      "distance_m": m["distance_m"], "days_apart": m["days_apart"]})
        created += was_new
    c.related_count = len(matches)
    score_complaint(c)
    c.save()
    StatusHistory.objects.create(complaint=c, old_status="", new_status=c.status, changed_by=c.citizen,
                                 remarks="Complaint submitted")
    notify(c.citizen, f"Complaint {c.ref} received. Status: {c.get_status_display()}.", c)
    return matches


def register_complaint(c, user, via="web"):
    """THE complaint submission workflow (web form, voice form and the AI assistant all end up here).

    `c` is an unsaved Complaint built from validated form data. A category the citizen chose is kept; otherwise the
    classifier's top prediction is used. Returns (complaint, matches).
    """
    chosen = c.category if c.category_id else None
    preds = suggest_categories(f"{c.title} {c.description}", 3)
    with transaction.atomic():
        c.source, c.citizen, c.created_at = Complaint.Source.LIVE, user, timezone.now()
        c.ref = f"TMP-{c.created_at.timestamp():.6f}"[:20]
        if preds:
            c.predicted_category, c.prediction_confidence = preds[0]
        if not chosen:
            c.category = c.predicted_category
        c.category_corrected = bool(chosen and c.predicted_category and chosen != c.predicted_category)
        c.save()
        c.ref = f"CS-{c.created_at.year}-{c.pk:06d}"
        matches = process_new_complaint(c, user)
        log_audit(user, "COMPLAINT_SUBMITTED", c,
                  f"category={c.category}, related={len(matches)}, via={via}, lang={c.original_language}, "
                  f"input={c.input_method}")
    return c, matches


def apply_status_update(c, user, new_status, remarks="", resolution_details="", via="web"):
    """Change a complaint's status (history, notification, priority re-score, audit). Caller checks permissions
    and validates the input (see `core.forms.StatusForm`). Returns the previous status."""
    with transaction.atomic():
        old = c.status
        c.status = new_status
        if new_status in DONE_STATUSES:
            c.resolved_at = timezone.now()
            c.resolution_details = resolution_details
        elif old in DONE_STATUSES:
            c.resolved_at = None
        score_complaint(c)
        c.save()
        StatusHistory.objects.create(complaint=c, old_status=old, new_status=new_status, changed_by=user,
                                     remarks=remarks or resolution_details)
        notify(c.citizen, f"Complaint {c.ref} status changed: {old} -> {new_status}.", c)
        log_audit(user, "STATUS_UPDATE", c, f"{old} -> {new_status} (via {via})")
    return old


def rescore_bulk(qs, batch=2000):
    """Recompute priority for many complaints and save only the priority fields."""
    qs = qs.select_related("category")
    items, n = [], 0
    for c in qs.iterator(chunk_size=batch):
        score_complaint(c)
        items.append(c)
        if len(items) >= batch:
            Complaint.objects.bulk_update(items, ["priority_score", "recommended_priority", "priority_factors"])
            n += len(items)
            items = []
    if items:
        Complaint.objects.bulk_update(items, ["priority_score", "recommended_priority", "priority_factors"])
        n += len(items)
    return n


# ------------------------------------------------------------------ wards (estimated, never an official boundary)
def ward_centroids():
    """Typical location of each ward = MEDIAN coordinate of its historical complaints (robust to mis-geocoded rows).

    The dataset has no ward boundary polygons, so this is only an estimate used to suggest a ward for a pin.
    """
    c = cache.get("ward_centroids_v2")
    if c is None:
        rows = list(Complaint.objects.filter(source="HISTORICAL").exclude(latitude=None).exclude(ward=None)
                    .values_list("ward_id", "ward__name", "latitude", "longitude"))
        if rows:
            df = pd.DataFrame(rows, columns=["ward_pk", "name", "lat", "lon"])
            g = df.groupby(["ward_pk", "name"])[["lat", "lon"]].median().reset_index()
            c = [{"ward_pk": int(r.ward_pk), "name": r.name, "lat": float(r.lat), "lon": float(r.lon)} for r in g.itertuples()]
        else:
            c = []
        cache.set("ward_centroids_v2", c, 3600)
    return c


def nearest_ward(latitude, longitude, max_m=8000):
    """Estimate the ward for a point: {'id': <Ward pk>, 'name', 'distance_m'} or None. Clearly an estimate."""
    cs = ward_centroids()
    if not cs:
        return None
    d = mining.haversine_m(latitude, longitude, np.array([x["lat"] for x in cs]), np.array([x["lon"] for x in cs]))
    i = int(np.argmin(d))
    if d[i] > max_m:
        return None
    return {"id": cs[i]["ward_pk"], "name": cs[i]["name"], "distance_m": round(float(d[i]))}


def find_ward(value):
    """Resolve a ward from a Ward pk (int / digit string) or a name. Returns Ward or None (never guesses on ties)."""
    if value in (None, ""):
        return None
    if isinstance(value, int) or str(value).isdigit():
        return Ward.objects.filter(pk=int(value)).first()
    name = str(value).strip()
    w = Ward.objects.filter(name__iexact=name).first()
    if w:
        return w
    hits = list(Ward.objects.filter(name__icontains=name)[:2])
    return hits[0] if len(hits) == 1 else None
