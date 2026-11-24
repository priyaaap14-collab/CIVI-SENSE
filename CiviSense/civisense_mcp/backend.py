"""Django-backed implementation of the CiviSense MCP tools.

Every tool here calls the SAME services / ML code the website uses (`core.services`, `ml.mining`, `ml.priority`,
`core.forms`), reads the real database, and re-checks the signed-in user's role and department on every call.
`django.setup()` must have been called before this module is imported (see `__main__.py`).
"""
import json
import math
import threading
from datetime import timedelta

import numpy as np
import pandas as pd
from django.core.cache import cache
from django.db import close_old_connections as _close_old_connections
from django.db.models import Count, Q
from django.utils import timezone

from core import services
from core.forms import ComplaintForm, StatusForm
from core.kannada import detect_language
from core.models import Category, Complaint, User, Ward
from ml import mining

from .errors import BackendError

ALL_ROLES = {"CITIZEN", "OFFICER", "ADMIN"}
ROLES = {
    "list_wards": ALL_ROLES,
    "submit_complaint": {"CITIZEN"},
    "check_complaint_status": ALL_ROLES,
    "find_duplicate_complaints": ALL_ROLES,
    "classify_complaint": ALL_ROLES,
    "calculate_priority": ALL_ROLES,
    "get_nearby_complaints": ALL_ROLES,
    "get_civic_hotspots": ALL_ROLES,
    "get_ward_analytics": ALL_ROLES,
    "get_officer_tasks": {"OFFICER", "ADMIN"},
    "update_complaint_status": {"OFFICER", "ADMIN"},
}
MAX_SUBMISSIONS_PER_HOUR = 10
VIA = "AI assistant (MCP)"


# ------------------------------------------------------------------ small helpers
def close_old_connections():
    """The MCP layer runs every tool in a worker thread (asyncio.to_thread); each thread owns a DB connection that must
    be recycled. On the main thread (e.g. inside Django's TestCase transaction) it must NOT be touched."""
    if threading.current_thread() is not threading.main_thread():
        _close_old_connections()


def _redact(args: dict) -> str:
    out = {}
    for k, v in args.items():
        if k == "approval_token":
            out[k] = "<present>"
        elif isinstance(v, str) and len(v) > 80:
            out[k] = v[:80] + f"...(+{len(v) - 80} chars)"
        else:
            out[k] = v
    return json.dumps(out, ensure_ascii=False, default=str)[:900]


def _text(value: str, field: str) -> str:
    value = (value or "").strip()
    if any(ord(ch) < 32 and ch not in "\n\t\r" for ch in value):
        raise BackendError("invalid_input", f"{field} contains control characters.")
    return value


def _resolve_category(name):
    if not name:
        return None
    cat = Category.objects.filter(name__iexact=name.strip()).first()
    if cat:
        return cat
    hits = list(Category.objects.filter(name__icontains=name.strip())[:5])
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(c.name for c in (hits or Category.objects.all()[:30]))
    raise BackendError("invalid_input", f"Unknown or ambiguous category '{name}'. Known/similar categories: {names}")


def _resolve_ward(value, required=False):
    if value in (None, ""):
        if required:
            raise BackendError("invalid_input", "A ward is required.")
        return None
    ward = services.find_ward(value)
    if ward is None:
        sample = ", ".join(Ward.objects.filter(name__icontains=str(value).strip()[:30]).values_list("name", flat=True)[:8])
        raise BackendError("invalid_input", f"Unknown or ambiguous ward '{value}'." + (f" Similar wards: {sample}" if sample else ""))
    return ward


def _haversine_rows(lat, lon, lats, lons):
    return mining.haversine_m(lat, lon, np.asarray(lats, dtype=float), np.asarray(lons, dtype=float))


def _scope(user):
    """Complaints this user may include in aggregates / hotspots / nearby searches.

    Officers are limited to their department. Administrators and citizens see city-wide AGGREGATES (counts, cluster
    centres) - individual private records are still shaped by `_brief`.
    """
    if user.role == "OFFICER":
        return Complaint.objects.filter(department_id=user.department_id) if user.department_id else Complaint.objects.none()
    return Complaint.objects.all()


def _scope_label(user):
    if user.role == "OFFICER":
        return f"department {user.department.code}" if user.department_id else "no department assigned"
    return "all complaints (aggregate view)"


def _brief(c, user, **extra):
    """One complaint shaped for `user`. Imported historical records are public dataset rows; a live report belongs
    to its citizen, so anyone else (other citizens, officers of other departments) only gets anonymised facts."""
    d = {"category": c.category.name if c.category_id else None, "ward": c.ward.name if c.ward_id else None,
         "status": c.status, "priority": c.priority, "reported": c.created_at.date().isoformat(), "source": c.source}
    if c.source == Complaint.Source.HISTORICAL or services.can_view(user, c):
        d.update(ref=c.ref, title=c.title)
    else:
        d.update(ref=None, note="Another citizen's live report - details are private.")
    d.update(extra)
    return d


def _fail_form(form):
    msgs = []
    for field, errs in form.errors.items():
        label = "form" if field == "__all__" else field
        msgs.extend(f"{label}: {e}" for e in errs)
    raise BackendError("invalid_input", "; ".join(msgs) or "invalid input")


# ------------------------------------------------------------------ backend
class DjangoBackend:
    # ---- infrastructure used by the MCP layer
    def _user(self, uid):
        user = User.objects.select_related("department").filter(pk=uid, is_active=True).first()
        if user is None:
            raise BackendError("unauthenticated", "The signed-in user no longer exists or is inactive.")
        return user

    def _authorise(self, tool, uid):
        user = self._user(uid)
        if user.role not in ROLES[tool]:
            raise BackendError("forbidden", f"The {user.get_role_display()} role may not use {tool}.")
        return user

    def audit(self, uid, tool, args, outcome, detail=""):
        close_old_connections()
        try:
            user = User.objects.filter(pk=uid).first()
            services.log_audit(user, f"MCP_TOOL_{outcome.upper()}"[:60], None, f"{tool} {_redact(args)} {detail}".strip())
        finally:
            close_old_connections()

    def prepare(self, tool, uid, args):
        close_old_connections()
        try:
            user = self._authorise(tool, uid)
            return getattr(self, f"_prepare_{tool}")(user, args)
        finally:
            close_old_connections()

    def execute(self, tool, uid, args):
        close_old_connections()
        try:
            user = self._authorise(tool, uid)
            return getattr(self, f"_do_{tool}")(user, args)
        finally:
            close_old_connections()

    # ================================================================== list_wards
    def _prepare_list_wards(self, user, args):
        return {}

    def _do_list_wards(self, user, args):
        wards = Ward.objects.order_by("name").values("id", "ward_id", "name")
        return {
            "count": wards.count(),
            "wards": list(wards),
        }

    # ================================================================== submit_complaint
    def _complaint_form(self, user, args):
        ward = _resolve_ward(args.get("ward"))
        category = _resolve_category(args.get("category"))
        lat, lon = args.get("latitude"), args.get("longitude")
        if ward is None and lat is not None and lon is not None:
            est = services.nearest_ward(lat, lon)
            hint = f" The pin is closest to ward '{est['name']}' (an estimate, not an official boundary) - ask the citizen to confirm it." if est else ""
            raise BackendError("invalid_input", "ward is required: ask the citizen which ward they live in." + hint)
        if ward is None:
            raise BackendError("invalid_input", "ward is required: ask the citizen which ward (or area) this is in. Do not guess.")
        voice = args.get("input_method") == "VOICE"
        data = {"title": _text(args["title"], "title"), "description": _text(args["description"], "description"),
                "ward": ward.pk, "category": category.pk if category else "", "address": _text(args.get("address", ""), "address"),
                "severity": args.get("severity", 3), "public_impact": args.get("public_impact", 2),
                "latitude": "" if lat is None else lat, "longitude": "" if lon is None else lon,
                # the human confirmation in the CiviSense app is the review of the recognised text
                "input_method": "VOICE" if voice else "TYPED", "voice_reviewed": "on" if voice else ""}
        form = ComplaintForm(data)
        if not form.is_valid():
            _fail_form(form)
        return form, ward, category

    def _prepare_submit_complaint(self, user, args):
        recent = Complaint.objects.filter(citizen=user, source="LIVE", created_at__gte=timezone.now() - timedelta(hours=1)).count()
        if recent >= MAX_SUBMISSIONS_PER_HOUR:
            raise BackendError("rate_limited", f"At most {MAX_SUBMISSIONS_PER_HOUR} complaints per hour.")
        form, ward, category = self._complaint_form(user, args)
        d = form.cleaned_data
        text = f"{d['title']} {d['description']}"
        gloss = args.get("english_gloss") or ""
        ready = services.models_ready()
        preds = services.suggest_categories(f"{text} {gloss}", 3) if ready["classifier"] else []
        chosen = category or (preds[0][0] if preds else None)
        similar = services.find_similar(f"{text} {gloss}", d.get("latitude"), d.get("longitude"), top_n=3) if ready["duplicate_index"] else []
        prio = services.priority_preview(chosen, d["severity"], d["public_impact"], len(similar))
        return {
            "will_store_exactly": {"title": d["title"], "description": d["description"], "address": d.get("address", ""),
                                   "language": d["original_language"]},
            "ward": ward.name, "coordinates": None if d.get("latitude") is None else [d["latitude"], d["longitude"]],
            "severity": d["severity"], "public_impact": d["public_impact"],
            "category": {"name": chosen.name if chosen else None,
                         "source": "chosen by citizen" if category else ("predicted" if preds else "unavailable"),
                         "confidence": round(preds[0][1], 3) if preds and not category else None,
                         "department": chosen.default_department.code if chosen and chosen.default_department else None},
            "recommended_priority": {"level": prio["level"], "score": prio["score"]},
            "similar_existing_reports": [_brief(c, user, similarity=round(m["text_similarity"], 3),
                                                distance_m=None if m["distance_m"] is None else round(m["distance_m"]))
                                         for c, m in similar],
            "input_method": "voice (speech-to-text)" if args.get("input_method") == "VOICE" else "typed",
            "warnings": ([] if ready["classifier"] else ["Classifier not trained: category will be left unset."]),
        }

    def _do_submit_complaint(self, user, args):
        # `_prepare` re-runs every validation and the rate limit, so a stale/forged approval cannot skip them.
        recent = Complaint.objects.filter(citizen=user, source="LIVE", created_at__gte=timezone.now() - timedelta(hours=1)).count()
        if recent >= MAX_SUBMISSIONS_PER_HOUR:
            raise BackendError("rate_limited", f"At most {MAX_SUBMISSIONS_PER_HOUR} complaints per hour.")
        form, _ward, _cat = self._complaint_form(user, args)
        c = form.save(commit=False)
        c.input_method = Complaint.InputMethod.VOICE if args.get("input_method") == "VOICE" else Complaint.InputMethod.ASSISTANT
        c, matches = services.register_complaint(c, user, via=VIA)
        return {"ref": c.ref, "status": c.status, "category": c.category.name if c.category_id else None,
                "department": c.department.code if c.department_id else None,
                "priority": c.priority, "priority_score": c.priority_score, "related_reports_found": len(matches),
                "message": f"Complaint {c.ref} was submitted."}

    # ================================================================== check_complaint_status
    def _prepare_check_complaint_status(self, user, args):
        return {}

    def _do_check_complaint_status(self, user, args):
        c = Complaint.objects.select_related("category", "ward", "department", "assigned_officer").filter(ref__iexact=args["ref"]).first()
        # Same answer for "does not exist" and "not yours" so references cannot be probed.
        if c is None or not services.can_view(user, c):
            raise BackendError("not_found", f"No complaint {args['ref']} that you are allowed to view.")
        history = []
        for h in c.history.select_related("changed_by"):
            row = {"at": h.created_at.isoformat(timespec="minutes"), "from": h.old_status or None, "to": h.new_status, "remarks": h.remarks}
            if not user.is_citizen:
                row["by"] = h.changed_by.username if h.changed_by_id else None
            history.append(row)
        out = {"ref": c.ref, "title": c.title, "status": c.status, "priority": c.priority, "priority_score": c.priority_score,
               "category": c.category.name if c.category_id else None, "department": c.department.code if c.department_id else None,
               "ward": c.ward.name if c.ward_id else None, "address": c.address, "reported": c.created_at.isoformat(timespec="minutes"),
               "resolved_at": c.resolved_at.isoformat(timespec="minutes") if c.resolved_at else None,
               "resolution_details": c.resolution_details, "has_coordinates": c.has_geo, "related_reports": c.related_count,
               "assigned": bool(c.assigned_officer_id), "history": history}
        if not user.is_citizen and c.assigned_officer_id:
            out["assigned_officer"] = c.assigned_officer.username
        return out

    # ================================================================== classify_complaint
    def _prepare_classify_complaint(self, user, args):
        return {}

    def _do_classify_complaint(self, user, args):
        text, gloss = _text(args["text"], "text"), _text(args.get("english_gloss") or "", "english_gloss")
        if not services.models_ready()["classifier"]:
            raise BackendError("unavailable", "The classifier has not been trained yet. Run: python manage.py train_models")
        preds = services.suggest_categories(f"{text} {gloss}", 3)
        lang = detect_language(text)
        note = None
        if not preds:
            note = "No recognisable keywords. For Kannada text pass an English rendering in english_gloss."
        elif lang == "kn" and not gloss:
            note = ("Kannada text was classified through a small keyword lexicon only - confidence is limited. "
                    "Provide english_gloss for a better prediction.")
        return {"language": lang, "used_english_gloss": bool(gloss),
                "predictions": [{"category": c.name, "probability": round(p, 3),
                                 "department": c.default_department.code if c.default_department else None} for c, p in preds],
                "model": "TF-IDF (1-2 grams) + Logistic Regression", "note": note}

    # ================================================================== calculate_priority
    def _prepare_calculate_priority(self, user, args):
        return {}

    def _do_calculate_priority(self, user, args):
        if args.get("ref"):
            c = Complaint.objects.select_related("category").filter(ref__iexact=args["ref"]).first()
            if c is None or not services.can_view(user, c):
                raise BackendError("not_found", f"No complaint {args['ref']} that you are allowed to view.")
            return {"ref": c.ref, "score": c.priority_score, "recommended": c.recommended_priority,
                    "effective_priority": c.priority, "overridden": bool(c.override_priority),
                    "override_reason": c.override_reason or None, "factors": c.priority_factors,
                    "note": "Stored score for this complaint. Triage aid only - not a prediction of danger."}
        cat = _resolve_category(args.get("category"))
        r = services.priority_preview(cat, args.get("severity") or 3, args.get("public_impact") or 2, args.get("related_count", 0))
        return {"estimate_for_new_complaint": True, "category": cat.name if cat else None, "score": r["score"], "level": r["level"],
                "factors": r["factors"],
                "note": "Waiting-time points start at 0 and grow while the complaint stays open. Triage aid only."}

    # ================================================================== find_duplicate_complaints
    def _prepare_find_duplicate_complaints(self, user, args):
        return {}

    def _do_find_duplicate_complaints(self, user, args):
        if not services.models_ready()["duplicate_index"]:
            raise BackendError("unavailable", "The duplicate index has not been built. Run: python manage.py train_models")
        text = _text(args["text"], "text")
        gloss = _text(args.get("english_gloss") or "", "english_gloss")
        found = services.find_similar(f"{text} {gloss}", args.get("latitude"), args.get("longitude"), top_n=args.get("limit", 5))
        return {"matches": [_brief(c, user, text_similarity=round(m["text_similarity"], 3), combined_score=round(m["combined"], 3),
                                   distance_m=None if m["distance_m"] is None else round(m["distance_m"]),
                                   days_apart=None if m["days_apart"] is None else round(m["days_apart"]))
                            for c, m in found],
                "method": "TF-IDF + cosine similarity, scaled by distance and date proximity",
                "note": "Suggestions only - nothing is merged or deleted." + ("" if args.get("latitude") is not None else
                                                                             " No location given, so distance could not be used.")}

    # ================================================================== get_nearby_complaints
    def _prepare_get_nearby_complaints(self, user, args):
        return {}

    def _do_get_nearby_complaints(self, user, args):
        lat, lon, radius = args["latitude"], args["longitude"], args.get("radius_m", 500)
        dlat = radius / 111_320.0
        dlon = radius / (111_320.0 * max(math.cos(math.radians(lat)), 0.2))
        qs = _scope(user).filter(latitude__range=(lat - dlat, lat + dlat), longitude__range=(lon - dlon, lon + dlon))
        cat = _resolve_category(args.get("category"))
        if cat:
            qs = qs.filter(category=cat)
        rows = list(qs.values_list("pk", "latitude", "longitude")[:20000])
        if not rows:
            return {"count_within_radius": 0, "complaints": [], "scope": _scope_label(user), "radius_m": radius}
        pk, la, lo = zip(*rows)
        d = _haversine_rows(lat, lon, la, lo)
        order = np.argsort(d)
        inside = [i for i in order if d[i] <= radius]
        top = inside[: args.get("limit", 10)]
        objs = {c.pk: c for c in Complaint.objects.filter(pk__in=[pk[i] for i in top]).select_related("category", "ward")}
        return {"count_within_radius": len(inside), "radius_m": radius, "scope": _scope_label(user),
                "complaints": [_brief(objs[pk[i]], user, distance_m=round(float(d[i]))) for i in top if pk[i] in objs]}

    # ================================================================== get_civic_hotspots
    def _prepare_get_civic_hotspots(self, user, args):
        return {}

    def _do_get_civic_hotspots(self, user, args):
        eps, ms = args.get("eps_m", 300), args.get("min_samples", 15)
        cat, ward = _resolve_category(args.get("category")), _resolve_ward(args.get("ward"))
        qs = _scope(user).exclude(latitude=None).exclude(longitude=None)
        if cat:
            qs = qs.filter(category=cat)
        if ward:
            qs = qs.filter(ward=ward)
        key = f"mcp:hot:{user.role}:{user.department_id}:{cat and cat.pk}:{ward and ward.pk}:{eps}:{ms}:{services_version()}"
        res = cache.get(key)
        if res is None:
            rows = list(qs.values_list("pk", "latitude", "longitude", "category__name", "ward__name")[:30000])
            df = pd.DataFrame(rows, columns=["pk", "latitude", "longitude", "category", "ward"]).fillna("Unknown")
            res = mining.run_dbscan(df, eps, ms) if len(df) else {"n_clusters": 0, "noise_points": 0, "clusters": [], "n_points": 0}
            res.pop("labels", None)
            cache.set(key, res, 600)
        return {"scope": _scope_label(user), "algorithm": "DBSCAN (haversine)", "eps_m": eps, "min_samples": ms,
                "complaints_with_coordinates": res.get("n_points", 0), "n_hotspots": res.get("n_clusters", 0),
                "noise_points": res.get("noise_points", 0), "hotspots": res.get("clusters", [])[: args.get("limit", 10)],
                "note": "Computed from real complaint coordinates; complaints without valid coordinates are excluded."}

    # ================================================================== get_ward_analytics
    def _prepare_get_ward_analytics(self, user, args):
        return {}

    def _do_get_ward_analytics(self, user, args):
        ward = _resolve_ward(args.get("ward"), required=True)
        cat = _resolve_category(args.get("category"))
        base = _scope(user)
        if cat:
            base = base.filter(category=cat)
        qs = base.filter(ward=ward)
        total = qs.count()
        active_q = Q(status__in=Complaint.ACTIVE_STATUSES)
        by_status = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}
        active = qs.filter(active_q)
        city_total, city_active = base.count(), base.filter(active_q).count()
        finished = sum(by_status.get(s, 0) for s in ("Resolved", "Closed", "Rejected"))
        return {"ward": ward.name, "category": cat.name if cat else "all", "scope": _scope_label(user),
                "total": total, "by_status": by_status, "active": active.count(),
                "backlog_rate": round(1 - finished / total, 3) if total else None,
                "city_backlog_rate": round(city_active / city_total, 3) if city_total else None,
                "active_by_priority": {lv: active.filter(services.effective_priority_q([lv])).count() for lv in ("Critical", "High", "Medium", "Low")},
                "live_submissions": qs.filter(source="LIVE").count(),
                "top_categories": list(qs.values("category__name").annotate(n=Count("id")).order_by("-n")[:5]),
                "note": "Counts from the CiviSense database. Historical records are dataset rows up to Jul 2022."}

    # ================================================================== get_officer_tasks
    def _prepare_get_officer_tasks(self, user, args):
        return {}

    def _do_get_officer_tasks(self, user, args):
        status = args.get("status", "active")
        if user.role == "OFFICER":
            if args.get("officer_username") and args["officer_username"] != user.username:
                raise BackendError("forbidden", "Officers can only list their own tasks.")
            qs, who = Complaint.objects.filter(assigned_officer=user), user.username
        elif args.get("officer_username"):
            officer = User.objects.filter(username=args["officer_username"], role="OFFICER").first()
            if officer is None:
                raise BackendError("not_found", f"No officer '{args['officer_username']}'.")
            qs, who = Complaint.objects.filter(assigned_officer=officer), officer.username
        else:
            qs, who = Complaint.objects.filter(assigned_officer=None, source="LIVE"), "unassigned live complaints"
        if status == "active":
            qs = qs.filter(status__in=Complaint.ACTIVE_STATUSES)
        elif status != "all":
            qs = qs.filter(status=status)
        total = qs.count()
        items = qs.select_related("category", "ward").order_by("-priority_score")[: args.get("limit", 20)]
        now = timezone.now()
        return {"tasks_for": who, "status_filter": status, "total": total, "returned": min(total, args.get("limit", 20)),
                "tasks": [{"ref": c.ref, "title": c.title, "category": c.category.name if c.category_id else None,
                           "ward": c.ward.name if c.ward_id else None, "status": c.status, "priority": c.priority,
                           "priority_score": c.priority_score, "days_open": (now - c.created_at).days if c.is_active else None,
                           "related_reports": c.related_count, "has_coordinates": c.has_geo} for c in items]}

    # ================================================================== update_complaint_status
    def _checked_complaint(self, user, args):
        c = Complaint.objects.select_related("category", "ward", "department", "citizen").filter(ref__iexact=args["ref"]).first()
        if c is None or not services.can_view(user, c):
            raise BackendError("not_found", f"No complaint {args['ref']} that you are allowed to update.")
        f = StatusForm({"status": args["new_status"], "remarks": _text(args.get("remarks", ""), "remarks"),
                        "resolution_details": _text(args.get("resolution_details", ""), "resolution_details")})
        if not f.is_valid():
            _fail_form(f)
        return c, f.cleaned_data

    def _prepare_update_complaint_status(self, user, args):
        c, d = self._checked_complaint(user, args)
        return {"ref": c.ref, "title": c.title, "current_status": c.status, "new_status": d["status"],
                "remarks": d["remarks"], "resolution_details": d["resolution_details"],
                "citizen_will_be_notified": bool(c.citizen_id)}

    def _do_update_complaint_status(self, user, args):
        c, d = self._checked_complaint(user, args)
        old = services.apply_status_update(c, user, d["status"], d["remarks"], d["resolution_details"], via=VIA)
        return {"ref": c.ref, "previous_status": old, "status": c.status, "priority": c.priority,
                "message": f"{c.ref}: {old} -> {c.status}"}


def services_version():
    """Cache-busting stamp that changes whenever any complaint changes."""
    from django.db.models import Max
    a = Complaint.objects.aggregate(n=Count("id"), m=Max("updated_at"))
    return f"{a['n']}-{int(a['m'].timestamp()) if a['m'] else 0}"
