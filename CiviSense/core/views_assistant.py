"""Web endpoints for the AI assistant and the voice-complaint review step.

Security notes
* Identity: the signed-in Django session user. Nothing about identity or role is read from the request body.
* The Gemini key lives only in settings / environment; it is never rendered into a page or sent to the browser.
* Gemini reaches CiviSense data ONLY through the MCP server (separate process, signed identity token, human approval
  for sensitive actions) - see core/assistant.py and civisense_mcp/.
"""
import asyncio
import json
import os
import queue
import sys
import threading

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from . import services
from .assistant import Assistant, AgentConfig, new_state, trim_history
from .extract import extract_fields
from .models import Complaint, Ward
from .views import role_required

SESSION_KEY = "assistant_state"
MAX_MESSAGE_CHARS = 4000


def _json_body(request):
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _mcp_factory(token):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    # The MCP subprocess gets the database / Django settings it needs and its signed identity - NOT the Gemini key.
    env = {k: v for k, v in os.environ.items() if k.startswith(("CIVISENSE_", "DJANGO_", "PG"))}
    env.update(CIVISENSE_MCP_TOKEN=token, CIVISENSE_MCP_SECRET=settings.MCP_SECRET, PYTHONIOENCODING="utf-8")
    params = StdioServerParameters(command=sys.executable, args=["-m", "civisense_mcp"], cwd=str(settings.BASE_DIR), env=env)
    return Client(params, read_timeout_seconds=90)


def _llm():
    from google import genai
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def _config(user, language="en", voice=False):
    return AgentConfig(model=settings.GEMINI_MODEL, max_tokens=settings.ASSISTANT_MAX_TOKENS,
                       max_rounds=settings.ASSISTANT_MAX_TOOL_ROUNDS, secret=settings.MCP_SECRET, user_id=user.pk,
                       username=user.username, role=user.role, department=user.department.code if user.department_id else None,
                       language=language, voice=voice)


def _state(request):
    s = request.session.get(SESSION_KEY)
    if not isinstance(s, dict) or set(s) != {"messages", "pending", "results", "order"}:
        s = new_state()
    return s


def _configured():
    return bool(settings.GEMINI_API_KEY)


@login_required
def assistant_page(request):
    st = _state(request)
    pending = [{"pending_id": p["id"], "tool": p["tool"], "preview": p["preview"]} for p in st["pending"][:1]]
    return render(request, "assistant.html", {"configured": _configured(), "pending": pending, "model": settings.GEMINI_MODEL})


def _stream_events(request, user, runner, audit_action, audit_detail):
    """Run the async agent in a worker thread and stream its events to the browser as NDJSON (one JSON per line)."""
    lock_key, state = f"assistant-busy:{user.pk}", _state(request)
    q: queue.Queue = queue.Queue()

    def worker():
        async def go():
            assistant = Assistant(runner["cfg"], _llm(), _mcp_factory)
            async for ev in runner["run"](assistant, state):
                q.put(ev)
        try:
            asyncio.run(go())
        except Exception as exc:  # noqa: BLE001
            q.put({"type": "error", "message": Assistant._friendly(exc)})
        finally:
            q.put(None)

    def gen():
        services.log_audit(user, audit_action, None, audit_detail)
        threading.Thread(target=worker, daemon=True).start()
        try:
            while True:
                ev = q.get()
                if ev is None:
                    break
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        finally:
            state["messages"] = trim_history(state["messages"], settings.ASSISTANT_MAX_HISTORY)
            request.session[SESSION_KEY] = state          # the session middleware has already run: save explicitly
            request.session.save()
            cache.delete(lock_key)

    resp = StreamingHttpResponse(gen(), content_type="application/x-ndjson; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    resp["X-Accel-Buffering"] = "no"
    return resp


def _guard(request):
    """Common checks. Returns (data, error_response)."""
    if not _configured():
        return None, JsonResponse({"error": "The AI assistant is not configured: set GEMINI_API_KEY (see README)."}, status=503)
    data = _json_body(request)
    if data is None:
        return None, JsonResponse({"error": "Invalid JSON."}, status=400)
    user = request.user
    rate_key = f"assistant-rate:{user.pk}"
    n = cache.get(rate_key, 0)
    if n >= settings.ASSISTANT_RATE_PER_MINUTE:
        return None, JsonResponse({"error": "Too many requests - please wait a minute."}, status=429)
    cache.set(rate_key, n + 1, 60)
    if not cache.add(f"assistant-busy:{user.pk}", 1, 180):
        return None, JsonResponse({"error": "The assistant is still working on your previous request."}, status=429)
    return data, None


@login_required
@require_POST
def api_assistant_message(request):
    data, err = _guard(request)
    if err:
        return err
    text = data.get("message")
    lang = data.get("language") if data.get("language") in ("en", "kn") else "en"
    voice = data.get("voice") is True
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE_CHARS:
        cache.delete(f"assistant-busy:{request.user.pk}")
        return JsonResponse({"error": f"Message must be 1-{MAX_MESSAGE_CHARS} characters."}, status=400)
    text = text.strip()
    runner = {"cfg": _config(request.user, lang, voice), "run": lambda a, st: a.turn(st, text)}
    return _stream_events(request, request.user, runner, "ASSISTANT_MESSAGE", f"lang={lang} voice={voice} chars={len(text)}")


@login_required
@require_POST
def api_assistant_decide(request):
    data, err = _guard(request)
    if err:
        return err
    pid, approve = data.get("pending_id"), data.get("approve")
    if not isinstance(pid, str) or not isinstance(approve, bool):
        cache.delete(f"assistant-busy:{request.user.pk}")
        return JsonResponse({"error": "pending_id and approve are required."}, status=400)
    st = _state(request)
    item = next((p for p in st["pending"] if p["id"] == pid), None)
    tool = item["tool"] if item else "?"
    runner = {"cfg": _config(request.user, "en", False), "run": lambda a, s: a.decide(s, pid, approve)}
    return _stream_events(request, request.user, runner, "ASSISTANT_CONFIRM" if approve else "ASSISTANT_DECLINE",
                          f"tool={tool} digest={(item or {}).get('digest', '')[:16]}")


@login_required
@require_POST
def api_assistant_reset(request):
    request.session.pop(SESSION_KEY, None)
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------- voice / typed complaint review (citizen form)
@role_required("CITIZEN")
@require_POST
def api_complaint_analyze(request):
    """Review step for the complaint form (typed OR dictated text): extract fields, classify, detect duplicates, preview
    priority - with the EXISTING CiviSense algorithms. Nothing is saved; the citizen still has to press Submit."""
    data = _json_body(request)
    if data is None or not isinstance(data.get("text"), str):
        return JsonResponse({"error": "text is required"}, status=400)
    text = data["text"].strip()[:MAX_MESSAGE_CHARS]
    if len(text) < 5:
        return JsonResponse({"error": "Please say or type a little more."}, status=400)

    def num(key, lo, hi, default=None):
        v = data.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi else default
    lat, lon = num("latitude", 12.70, 13.30), num("longitude", 77.30, 77.90)
    if (lat is None) != (lon is None):
        lat = lon = None
    severity, impact = int(num("severity", 1, 5, 3)), int(num("public_impact", 1, 3, 2))

    fields = extract_fields(text, wards=list(Ward.objects.values_list("pk", "name")))
    ward, ward_how = fields["ward"], None
    if ward is None and lat is not None:
        est = services.nearest_ward(lat, lon)
        if est:
            ward = {"id": est["id"], "name": est["name"]}
            ward_how = "Estimated from the pin you chose (nearest ward centre) - not an official boundary. Please confirm."
    elif ward:
        ward_how = ward.pop("how")
    ready = services.models_ready()
    notes = []
    preds = services.suggest_categories(text, 3) if ready["classifier"] else []
    if not ready["classifier"]:
        notes.append("The category model is not trained yet (python manage.py train_models) - please choose a category yourself.")
    if fields["language"] == "kn":
        notes.append("Kannada is matched with a small keyword list (the classifier is English-trained). Please check the suggested category.")
    similar = services.find_similar(text, lat, lon, top_n=3) if ready["duplicate_index"] else []
    prio = services.priority_preview(preds[0][0] if preds else None, severity, impact, len(similar))
    dups = []
    for c, m in similar:
        mine = c.source == Complaint.Source.HISTORICAL or services.can_view(request.user, c)
        dups.append({"ref": c.ref if mine else None, "title": c.title if mine else "Another citizen's report (private)",
                     "category": c.category.name if c.category_id else None, "ward": c.ward.name if c.ward_id else None,
                     "status": c.status, "similarity": round(m["text_similarity"], 2),
                     "distance_m": None if m["distance_m"] is None else round(m["distance_m"])})
    return JsonResponse({
        "language": fields["language"], "title": fields["title"], "description": fields["description"],
        "location_text": fields["location_text"], "ward": ward, "ward_note": ward_how,
        "suggested_severity": fields["suggested_severity"], "urgency_words": fields["urgency_words"],
        "problems": fields["problems"], "notes": notes,
        "categories": [{"id": c.pk, "name": c.name, "probability": round(p, 3),
                        "department": c.default_department.code if c.default_department else None} for c, p in preds],
        "priority": {"level": prio["level"], "score": prio["score"]}, "duplicates": dups,
    })

