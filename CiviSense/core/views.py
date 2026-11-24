import datetime
import json
import math
from functools import wraps

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ml import mining
from ml.wrangling import clean_text
from .geo_utils import build_points, far_from_ward_km, inside_bounds
from .kannada import nlp_text
from .forms import (AssignForm, CategoryCorrectionForm, ComplaintForm, OverrideForm, RegistrationForm, StatusForm)
from .models import (Assignment, AuditLog, Category, Complaint, Department, DuplicateGroup, DuplicateSuggestion,
                     Notification, StatusHistory, User, Ward)
from . import services

PALETTE = ["#0d6efd", "#20c997", "#fd7e14", "#6f42c1", "#d63384", "#198754", "#ffc107", "#0dcaf0", "#dc3545", "#6c757d"]
PRIORITY_COLORS = {"Critical": "#dc3545", "High": "#fd7e14", "Medium": "#ffc107", "Low": "#20c997"}
ACTIVE = Complaint.ACTIVE_STATUSES
DONE = ["Resolved", "Closed"]


# ------------------------------------------------------------------ access control / helpers
def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(request, *a, **kw):
            if request.user.role not in roles:
                messages.error(request, "You do not have permission to open that page.")
                return redirect(home_for(request.user))
            return fn(request, *a, **kw)
        return wrapper
    return deco


def home_for(user):
    return {"ADMIN": "dashboard", "OFFICER": "officer_home", "CITIZEN": "citizen_home"}.get(user.role, "home")


def notifications_context(request):
    u = getattr(request, "user", None)
    if u is not None and u.is_authenticated:
        qs = Notification.objects.filter(user=u, is_read=False)
        return {"notif_unread": qs.count(), "notif_items": list(qs[:6])}
    return {}


def fig_json(fig, height=320):
    fig.update_layout(height=height, margin=dict(l=40, r=20, t=30, b=40), font=dict(family="Inter, Segoe UI, sans-serif", size=12),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", legend=dict(orientation="h", y=-0.2))
    return json.loads(fig.to_json())


def effective_priority_q(levels):
    return Q(override_priority__in=levels) | (Q(override_priority="") & Q(recommended_priority__in=levels))


def data_version():
    a = Complaint.objects.aggregate(n=Count("id"), m=Max("updated_at"))
    return f"{a['n']}-{int(a['m'].timestamp()) if a['m'] else 0}"


# ------------------------------------------------------------------ public / auth
def home(request):
    return render(request, "home.html", {"total": Complaint.objects.count()})


def role_login(request, role):
    role = role.upper()
    if role not in User.Role.values:
        return redirect("home")
    error = None
    if request.method == "POST":
        user = authenticate(request, username=request.POST.get("username", ""), password=request.POST.get("password", ""))
        if user is None:
            error = "Invalid username or password."
        elif user.role != role:
            error = f"This account is not a {User.Role(role).label.lower()} account. Use the correct login page."
        else:
            login(request, user)
            services.log_audit(user, "LOGIN", user, f"{role} login")
            nxt = request.GET.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else home_for(user))
    return render(request, "login.html", {"role": role, "role_label": User.Role(role).label, "error": error})


def register(request):
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome to CiviSense! Your citizen account has been created.")
        return redirect("citizen_home")
    return render(request, "register.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("home")


@login_required
@require_POST
def notifications_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect(request.META.get("HTTP_REFERER") or home_for(request.user))


# ------------------------------------------------------------------ administrator dashboard
@role_required("ADMIN")
def dashboard(request):
    source = request.GET.get("source", "")
    qs = Complaint.objects.all()
    if source in ("HISTORICAL", "LIVE"):
        qs = qs.filter(source=source)
    total = qs.count()
    dup_ids = set(DuplicateSuggestion.objects.filter(state="SUGGESTED", complaint__in=qs).values_list("complaint_id", flat=True)) | \
        set(DuplicateSuggestion.objects.filter(state="SUGGESTED", other__in=qs).values_list("other_id", flat=True))
    kpis = [
        ("Total complaints", total, "bi-collection", "primary"),
        ("Open complaints", qs.filter(status__in=ACTIVE).count(), "bi-folder2-open", "warning"),
        ("Resolved / closed", qs.filter(status__in=DONE).count(), "bi-check2-circle", "success"),
        ("High-priority (active)", qs.filter(status__in=ACTIVE).filter(effective_priority_q(["Critical", "High"])).count(),
         "bi-exclamation-triangle", "danger"),
        ("Suspected duplicates", len(dup_ids), "bi-files", "info"),
        ("Wards covered", qs.exclude(ward=None).values("ward").distinct().count(), "bi-geo-alt", "secondary"),
    ]

    cat = list(qs.values("category__name").annotate(n=Count("id")).order_by("-n")[:12])
    f1 = go.Figure(go.Bar(x=[c["n"] for c in cat][::-1], y=[c["category__name"] or "Uncategorised" for c in cat][::-1],
                          orientation="h", marker_color=PALETTE[0]))
    f1.update_layout(xaxis_title="Complaints")

    monthly = list(qs.annotate(m=TruncMonth("created_at")).values("m", "source").annotate(n=Count("id")).order_by("m"))
    f2 = go.Figure()
    for s, col in (("HISTORICAL", PALETTE[0]), ("LIVE", PALETTE[2])):
        rows = [r for r in monthly if r["source"] == s]
        if rows:
            f2.add_trace(go.Scatter(x=[r["m"].strftime("%Y-%m") for r in rows], y=[r["n"] for r in rows], mode="lines+markers",
                                    name="Imported historical" if s == "HISTORICAL" else "Submitted via CiviSense", line=dict(color=col)))
    f2.update_layout(yaxis_title="Complaints per month")

    dept = list(qs.values("department__code").annotate(n=Count("id")).order_by("-n"))
    f3 = go.Figure(go.Bar(x=[d["department__code"] or "Unassigned" for d in dept], y=[d["n"] for d in dept], marker_color=PALETTE[1]))
    f3.update_layout(yaxis_title="Complaints")

    st = list(qs.values("status").annotate(n=Count("id")).order_by("-n"))
    f4 = go.Figure(go.Pie(labels=[s["status"] for s in st], values=[s["n"] for s in st], hole=0.55, marker=dict(colors=PALETTE)))

    pr = {lv: qs.filter(effective_priority_q([lv])).count() for lv in ("Critical", "High", "Medium", "Low")}
    f5 = go.Figure(go.Bar(x=list(pr), y=list(pr.values()), marker_color=[PRIORITY_COLORS[k] for k in pr]))
    f5.update_layout(yaxis_title="Complaints (all statuses)")

    charts = {"cat": fig_json(f1, 380), "monthly": fig_json(f2), "dept": fig_json(f3), "status": fig_json(f4), "prio": fig_json(f5)}
    ctx = {
        "kpis": kpis, "charts": charts, "source": source,
        "live_total": Complaint.objects.filter(source="LIVE").count(),
        "needs_assignment": Complaint.objects.filter(source="LIVE", assigned_officer=None, status__in=ACTIVE).select_related("category", "ward")[:6],
        "audit": AuditLog.objects.select_related("user")[:8],
    }
    return render(request, "dashboard.html", ctx)


# ------------------------------------------------------------------ complaint list (admin / officer / citizen)
def filter_complaints(qs, g):
    q = g.get("q", "").strip()
    if q:
        qs = qs.filter(Q(ref__icontains=q) | Q(title__icontains=q) | Q(description__icontains=q) | Q(address__icontains=q))
    for key, field in (("department", "department_id"), ("category", "category_id"), ("ward", "ward_id")):
        val = g.get(key, "")
        if val.isdigit():
            qs = qs.filter(**{field: int(val)})
    if g.get("status") in Complaint.Status.values:
        qs = qs.filter(status=g["status"])
    if g.get("priority") in Complaint.Priority.values:
        qs = qs.filter(effective_priority_q([g["priority"]]))
    if g.get("source") in ("HISTORICAL", "LIVE"):
        qs = qs.filter(source=g["source"])
    if g.get("dups") == "1":
        ids = set(DuplicateSuggestion.objects.filter(state="SUGGESTED").values_list("complaint_id", flat=True)) | \
            set(DuplicateSuggestion.objects.filter(state="SUGGESTED").values_list("other_id", flat=True))
        qs = qs.filter(pk__in=ids)
    if g.get("assigned") == "none":
        qs = qs.filter(assigned_officer=None)
    sort = g.get("sort", "new")
    return qs.order_by({"new": "-created_at", "old": "created_at", "priority": "-priority_score"}.get(sort, "-created_at"))


def list_context(request, qs, mode):
    qs = filter_complaints(qs.select_related("category", "ward", "department", "assigned_officer"), request.GET)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return {
        "page": page, "mode": mode, "qs_string": params.urlencode(), "total_found": page.paginator.count,
        "departments": Department.objects.all(), "categories": Category.objects.all(), "wards": Ward.objects.all(),
        "statuses": Complaint.Status.choices, "priorities": Complaint.Priority.choices, "g": request.GET,
    }


@role_required("ADMIN")
def complaint_list(request):
    return render(request, "complaint_list.html", list_context(request, Complaint.objects.all(), "admin"))


# ------------------------------------------------------------------ complaint detail (all roles)
can_view = services.can_view   # permission rule lives in services so the MCP tools use exactly the same one


def merge_group(a, b, user):
    ga, gb = a.duplicate_group, b.duplicate_group
    if ga and gb and ga.pk != gb.pk:
        Complaint.objects.filter(duplicate_group=gb).update(duplicate_group=ga)
        gb.delete()
        return ga
    g = ga or gb or DuplicateGroup.objects.create(created_by=user, note="Confirmed by officer review")
    for c in (a, b):
        if c.duplicate_group_id != g.pk:
            c.duplicate_group = g
            c.save(update_fields=["duplicate_group"])
    return g


@login_required
def complaint_detail(request, ref):
    c = get_object_or_404(Complaint.objects.select_related("category", "ward", "department", "assigned_officer", "citizen",
                                                           "predicted_category", "override_by", "duplicate_group"), ref=ref)
    user = request.user
    if not can_view(user, c):
        messages.error(request, "You do not have access to that complaint.")
        return redirect(home_for(user))
    staff = user.is_admin_role or user.is_officer
    can_update = staff

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "assign" and user.is_admin_role:
            f = AssignForm(request.POST)
            if f.is_valid():
                with transaction.atomic():
                    c.assignments.filter(is_active=True).update(is_active=False)
                    Assignment.objects.create(complaint=c, officer=f.cleaned_data["officer"], assigned_by=user)
                    c.assigned_officer = f.cleaned_data["officer"]
                    if not c.department_id and c.assigned_officer.department_id:
                        c.department = c.assigned_officer.department
                    c.save()
                    services.notify(c.assigned_officer, f"Complaint {c.ref} was assigned to you.", c)
                    services.log_audit(user, "ASSIGN", c, f"Assigned to {c.assigned_officer.username}")
                messages.success(request, f"{c.ref} assigned to {c.assigned_officer.username}.")
            else:
                messages.error(request, "Choose an officer to assign.")
        elif action == "status" and can_update:
            f = StatusForm(request.POST)
            if f.is_valid():
                new = f.cleaned_data["status"]
                services.apply_status_update(c, user, new, f.cleaned_data["remarks"], f.cleaned_data["resolution_details"])
                messages.success(request, f"Status updated to {new}.")
            else:
                messages.error(request, "; ".join(e for errs in f.errors.values() for e in errs))
        elif action == "override" and can_update:
            f = OverrideForm(request.POST)
            if f.is_valid():
                c.override_priority = f.cleaned_data["level"]
                c.override_reason = f.cleaned_data["reason"] if f.cleaned_data["level"] else ""
                c.override_by = user if f.cleaned_data["level"] else None
                c.save()
                services.log_audit(user, "PRIORITY_OVERRIDE", c, f"{c.recommended_priority} -> {c.override_priority or 'recommendation'}: {c.override_reason}")
                messages.success(request, "Priority updated." if c.override_priority else "Override removed - system recommendation restored.")
            else:
                messages.error(request, "; ".join(e for errs in f.errors.values() for e in errs))
        elif action == "dup_review" and can_update:
            s = get_object_or_404(DuplicateSuggestion.objects.filter(Q(complaint=c) | Q(other=c)), pk=request.POST.get("suggestion"))
            decision = request.POST.get("decision")
            if decision in ("confirm", "reject"):
                s.state = "CONFIRMED" if decision == "confirm" else "REJECTED"
                s.reviewed_by, s.reviewed_at = user, timezone.now()
                s.save()
                if decision == "confirm":
                    merge_group(s.complaint, s.other, user)
                services.log_audit(user, "DUPLICATE_REVIEW", c, f"{s.complaint.ref} ~ {s.other.ref}: {s.state}")
                messages.success(request, "Duplicate review saved. Both complaints remain separate and traceable.")
        elif action == "correct_category" and (staff or c.citizen_id == user.id):
            f = CategoryCorrectionForm(request.POST)
            if f.is_valid():
                old = c.category
                c.category = f.cleaned_data["category"]
                c.category_corrected = True
                c.department = c.category.default_department or c.department
                services.score_complaint(c)
                c.save()
                services.log_audit(user, "CATEGORY_CORRECTION", c, f"{old} -> {c.category} (predicted: {c.predicted_category})")
                messages.success(request, f"Category corrected to {c.category}. The correction is kept for future model training.")
        return redirect("complaint_detail", ref=c.ref)

    suggestions = DuplicateSuggestion.objects.filter(Q(complaint=c) | Q(other=c)).select_related("complaint", "other")
    group_members = c.duplicate_group.complaints.exclude(pk=c.pk) if c.duplicate_group_id else []
    ctx = {
        "c": c, "staff": staff, "can_update": can_update, "history": c.history.select_related("changed_by"),
        "suggestions": [(s, s.other if s.complaint_id == c.pk else s.complaint) for s in suggestions[:25]],
        "group_members": group_members, "assign_form": AssignForm(), "status_form": StatusForm(initial={"status": c.status}),
        "override_form": OverrideForm(initial={"level": c.override_priority, "reason": c.override_reason}),
        "correct_form": CategoryCorrectionForm(initial={"category": c.category}),
        "assignments": c.assignments.select_related("officer", "assigned_by"),
        "can_correct": staff or c.citizen_id == user.id,
    }
    return render(request, "complaint_detail.html", ctx)


# ------------------------------------------------------------------ citizen portal
@role_required("CITIZEN")
def citizen_home(request):
    qs = Complaint.objects.filter(citizen=request.user)
    ctx = list_context(request, qs, "citizen")
    ctx["stats"] = {"total": qs.count(), "active": qs.filter(status__in=ACTIVE).count(), "done": qs.filter(status__in=DONE).count()}
    ctx["recent_notifications"] = Notification.objects.filter(user=request.user)[:8]
    return render(request, "citizen_home.html", ctx)


@role_required("CITIZEN")
def citizen_submit(request):
    form = ComplaintForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        c, _matches = services.register_complaint(form.save(commit=False), request.user, via="web form")
        messages.success(request, f"Complaint submitted. Your complaint ID is {c.ref}.")
        return redirect("complaint_detail", ref=c.ref)
    return render(request, "submit.html", {"form": form})


# ------------------------------------------------------------------ officer portal
@role_required("OFFICER")
def officer_home(request):
    u = request.user
    mine = Complaint.objects.filter(assigned_officer=u)
    ctx = list_context(request, mine, "officer")
    ctx["stats"] = {
        "assigned": mine.count(), "active": mine.filter(status__in=ACTIVE).count(),
        "urgent": mine.filter(status__in=ACTIVE).filter(effective_priority_q(["Critical", "High"])).count(),
        "resolved": mine.filter(status__in=DONE).count(),
    }
    dept = u.department
    if dept:
        dq = Complaint.objects.filter(department=dept)
        by_status = {r["status"]: r["n"] for r in dq.values("status").annotate(n=Count("id"))}
        top_cat = list(dq.values("category__name").annotate(n=Count("id")).order_by("-n")[:6])
        f = go.Figure(go.Bar(x=list(by_status), y=list(by_status.values()), marker_color=PALETTE[0]))
        f.update_layout(yaxis_title="Complaints")
        ctx["dept"] = {"obj": dept, "total": dq.count(), "active": dq.filter(status__in=ACTIVE).count(),
                       "live": dq.filter(source="LIVE").count(), "top_cat": top_cat,
                       "urgent": dq.filter(status__in=ACTIVE).filter(effective_priority_q(["Critical", "High"])).count(),
                       "chart": fig_json(f, 260)}
    ctx["pending_dups"] = DuplicateSuggestion.objects.filter(state="SUGGESTED", complaint__assigned_officer=u).select_related("complaint", "other")[:6]
    return render(request, "officer_home.html", ctx)


# ------------------------------------------------------------------ JSON APIs
@login_required
def api_suggest_category(request):
    text = request.GET.get("text", "")
    if len(clean_text(nlp_text(text))) < 8:      # Kannada words count through their English keyword hints
        return JsonResponse({"suggestions": []})
    out = [{"id": cat.pk, "name": cat.name, "probability": round(p, 3),
            "department": cat.default_department.code if cat.default_department else None}
           for cat, p in services.suggest_categories(text, 3)]
    return JsonResponse({"suggestions": out})


@login_required
def api_ward_from_point(request):
    try:
        lat, lon = float(request.GET["lat"]), float(request.GET["lon"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "lat and lon required"}, status=400)
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return JsonResponse({"error": "lat and lon must be finite numbers"}, status=400)
    # `id` is the Ward primary key (what the form's <select> uses). Previously this looked the ward up with the
    # *dataset* ward_id column using a primary key, which could pre-select a different ward than the name shown.
    ward = services.nearest_ward(lat, lon)
    return JsonResponse({"ward": ward,
                         "note": "Estimated from the usual location of this ward's complaints - not an official ward "
                                 "boundary. Please confirm." if ward else "No ward could be estimated for this point."})


def geo_scope(user, qs):
    """Officers only see their own department's complaints on maps; administrators see everything."""
    if user.role == "OFFICER":
        return qs.filter(department_id=user.department_id) if user.department_id else qs.none()
    return qs


def _geo_filters(qs, category="", ward="", source=""):
    if category.isdigit():
        qs = qs.filter(category_id=int(category))
    if ward.isdigit():
        qs = qs.filter(ward_id=int(ward))
    if source in ("HISTORICAL", "LIVE"):
        qs = qs.filter(source=source)
    return qs


def geo_dataframe(user, category="", ward="", source=""):
    """Complaints with REAL stored coordinates inside the Bengaluru box. Returns (df, total_matching, outside_bounds)."""
    everything = geo_scope(user, _geo_filters(Complaint.objects.all(), category, ward, source))
    qs = everything.exclude(latitude=None).exclude(longitude=None)
    rows = list(qs.values_list("pk", "ref", "latitude", "longitude", "category__name", "ward__name", "ward_id", "status", "source"))
    df = pd.DataFrame(rows, columns=["pk", "ref", "latitude", "longitude", "category", "ward", "ward_pk", "status", "source"])
    inside = inside_bounds(df)
    outside = int((~inside).sum())
    df = df[inside].reset_index(drop=True)
    df["category"] = df["category"].fillna("Uncategorised")
    df["ward"] = df["ward"].fillna("Unknown")
    return df, everything.count(), outside


@role_required("ADMIN", "OFFICER")
def api_geo(request):
    g = request.GET
    try:
        eps = min(max(int(g.get("eps", 300)), 50), 3000)
        ms = min(max(int(g.get("min_samples", 15)), 3), 200)
    except ValueError:
        return JsonResponse({"error": "invalid parameters"}, status=400)
    scope = f"{request.user.role}-{request.user.department_id}"
    key = f"geo2:{scope}:{g.get('category','')}:{g.get('ward','')}:{g.get('source','')}:{eps}:{ms}:{data_version()}".replace(" ", "_")
    out = cache.get(key)
    if out is None:
        df, total, outside = geo_dataframe(request.user, g.get("category", ""), g.get("ward", ""), g.get("source", ""))
        if len(df):
            res = mining.run_dbscan(df, eps, ms)          # real DBSCAN over every complaint with coordinates
            labels = res.pop("labels") or [-1] * len(df)
            df["label"] = labels
            df["far_km"] = far_from_ward_km(df, {c["ward_pk"]: c for c in services.ward_centroids()})
            pts = build_points(df)
            bounds = [[float(df.latitude.min()), float(df.longitude.min())], [float(df.latitude.max()), float(df.longitude.max())]]
        else:
            res = {"n_clusters": 0, "noise_points": 0, "clusters": [], "silhouette": None, "eps_m": eps, "min_samples": ms, "n_points": 0}
            pts, bounds = [], None
        wards = df.groupby("ward").size().sort_values(ascending=False).head(10) if len(df) else pd.Series(dtype=int)
        out = {"points": pts, "result": res, "bounds": bounds, "total_matching_filter": total,
               "without_coordinates": total - len(df) - outside, "outside_bengaluru": outside,
               "stacked_points": int(sum(1 for p in pts if p[3] > 1)),
               "top_wards": [{"ward": w, "n": int(n)} for w, n in wards.items()]}
        cache.set(key, out, 600)
    return JsonResponse(out)


@role_required("ADMIN", "OFFICER")
def api_geo_point(request):
    """Details of the complaints stored at ONE map point (used by marker popups). Same visibility rules as the map."""
    try:
        lat, lon = float(request.GET["lat"]), float(request.GET["lon"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "lat and lon required"}, status=400)
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return JsonResponse({"error": "invalid coordinates"}, status=400)
    g, tol = request.GET, 0.000006
    qs = geo_scope(request.user, _geo_filters(Complaint.objects.all(), g.get("category", ""), g.get("ward", ""), g.get("source", "")))
    qs = qs.filter(latitude__range=(lat - tol, lat + tol), longitude__range=(lon - tol, lon + tol))
    total = qs.count()
    items = qs.select_related("category", "ward").order_by("-priority_score")[:25]
    return JsonResponse({"total": total, "shown": min(total, 25), "complaints": [
        {"ref": c.ref, "title": c.title[:140], "category": c.category.name if c.category_id else "Uncategorised",
         "ward": c.ward.name if c.ward_id else "Unknown", "status": c.status, "priority": c.priority,
         "reported": c.created_at.date().isoformat(), "address": c.address[:140], "source": c.source} for c in items]})


@role_required("ADMIN", "OFFICER")
def geo_page(request):
    live = geo_scope(request.user, Complaint.objects.filter(source="LIVE")).exclude(latitude=None).count()
    return render(request, "geo.html", {"categories": Category.objects.all(), "wards": Ward.objects.all(), "live_count": live,
                                       "scope_note": "your department's complaints" if request.user.role == "OFFICER" else "all complaints"})


# ------------------------------------------------------------------ data mining & intelligence page
@role_required("ADMIN")
def mining_page(request):
    res = mining.load_results()
    cls, dup = res.get("classification"), res.get("duplicates")
    ctx = {"res": res, "cls": cls, "dup": dup, "dup_mining": res.get("duplicate_mining")}
    try:
        ctx["wr"] = json.loads(settings.WRANGLING_REPORT.read_text(encoding="utf-8"))
    except Exception:
        ctx["wr"] = None

    charts = {}
    if cls:
        labels = cls["labels"]
        cm = np.array(cls["confusion_matrix"], dtype=float)
        norm = (cm / cm.sum(axis=1, keepdims=True).clip(min=1)).round(2)
        f = go.Figure(go.Heatmap(z=norm.tolist(), x=labels, y=labels, colorscale="Blues", zmin=0, zmax=1,
                                 hovertemplate="Actual: %{y}<br>Predicted: %{x}<br>Share of actual class: %{z}<extra></extra>"))
        f.update_layout(xaxis_title="Predicted", yaxis_title="Actual", yaxis=dict(autorange="reversed"), xaxis=dict(tickangle=-45))
        charts["cm"] = fig_json(f, 600)
        ctx["per_class"] = sorted(((k, {**v, "f1": v["f1-score"]}) for k, v in cls["per_class"].items()), key=lambda kv: -kv[1]["support"])
    if dup:
        th = dup["thresholds"]
        f = go.Figure()
        for k, col in (("precision", PALETTE[0]), ("recall", PALETTE[2]), ("f1", PALETTE[5])):
            f.add_trace(go.Scatter(x=[t["threshold"] for t in th], y=[t[k] for t in th], mode="lines+markers",
                                   name="F1" if k == "f1" else k.title(), line=dict(color=col)))
        f.add_vline(x=dup["operating_threshold"], line_dash="dash", annotation_text="operating threshold")
        f.update_layout(xaxis_title="Similarity threshold", yaxis_title="Score", yaxis=dict(range=[0, 1.02]))
        charts["dup"] = fig_json(f)
        ctx["top_pairs"] = DuplicateSuggestion.objects.select_related("complaint", "other", "complaint__category") \
            .filter(state="SUGGESTED").order_by("-combined_score")[:12]

    hist = Complaint.objects.filter(source="HISTORICAL")
    df = pd.DataFrame(list(hist.exclude(latitude=None).values_list("pk", "latitude", "longitude", "category__name", "ward__name")),
                      columns=["pk", "latitude", "longitude", "category", "ward"]).fillna("Unknown")
    key = f"mining-dbscan:{data_version()}"
    db = cache.get(key)
    if db is None and len(df):
        db = mining.run_dbscan(df, 300, 15)
        db.pop("labels", None)
        cache.set(key, db, 900)
    ctx["dbscan"] = db

    kdf = pd.DataFrame(list(hist.exclude(ward=None).values_list("ward__name", "category__name", "status", "comment_count", "created_at")),
                       columns=["ward", "category", "status", "comment_count", "created_at"])
    km = cache.get(f"mining-km:{data_version()}")
    if km is None and len(kdf):
        kdf["created_at"] = pd.to_datetime(kdf["created_at"], utc=True).dt.tz_localize(None)
        kdf["category"] = kdf["category"].fillna("Uncategorised")
        km = mining.ward_clustering(kdf, services.dataset_reference_date().astimezone(datetime.timezone.utc).replace(tzinfo=None))
        cache.set(f"mining-km:{data_version()}", km, 900)
    ctx["km"] = km
    if km:
        ks = km["k_range"]
        f = go.Figure(go.Scatter(x=ks, y=[km["inertia"][k] for k in ks], mode="lines+markers", line=dict(color=PALETTE[0])))
        f.update_layout(xaxis_title="k (clusters)", yaxis_title="Inertia (elbow method)")
        charts["elbow"] = fig_json(f, 280)
        f = go.Figure(go.Scatter(x=ks, y=[km["silhouette"][k] for k in ks], mode="lines+markers", line=dict(color=PALETTE[2])))
        f.update_layout(xaxis_title="k (clusters)", yaxis_title="Silhouette score")
        charts["sil"] = fig_json(f, 280)
        f = go.Figure()
        for cl in sorted({p["cluster"] for p in km["pca"]}):
            pts = [p for p in km["pca"] if p["cluster"] == cl]
            f.add_trace(go.Scatter(x=[p["x"] for p in pts], y=[p["y"] for p in pts], mode="markers", text=[p["ward"] for p in pts],
                                   name=f"Cluster {cl}", marker=dict(size=9, color=PALETTE[cl % 10])))
        f.update_layout(xaxis_title="PCA 1", yaxis_title="PCA 2")
        charts["pca"] = fig_json(f, 340)

    monthly = list(hist.annotate(m=TruncMonth("created_at")).values("m").annotate(n=Count("id")).order_by("m"))
    if monthly:
        s = pd.Series([r["n"] for r in monthly], index=[r["m"].strftime("%Y-%m") for r in monthly])
        f = go.Figure()
        f.add_trace(go.Bar(x=s.index.tolist(), y=s.tolist(), name="Monthly complaints", marker_color="#9ec5fe"))
        f.add_trace(go.Scatter(x=s.index.tolist(), y=s.rolling(3, min_periods=1).mean().round(1).tolist(),
                               name="3-month moving average", line=dict(color=PALETTE[8])))
        charts["trend"] = fig_json(f)
        w = pd.Series([timezone.localtime(x).strftime("%a") for x in hist.values_list("created_at", flat=True)]) \
            .value_counts().reindex(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]).fillna(0)
        f = go.Figure(go.Bar(x=w.index.tolist(), y=w.astype(int).tolist(), marker_color=PALETTE[3]))
        f.update_layout(yaxis_title="Complaints")
        charts["weekday"] = fig_json(f, 260)
    ctx["charts"] = charts
    return render(request, "mining.html", ctx)
