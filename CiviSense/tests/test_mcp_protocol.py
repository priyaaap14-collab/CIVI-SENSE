"""MCP protocol-layer tests - NO Django, NO database needed.

Run:  python -m unittest discover -s tests -v

They exercise `civisense_mcp.server` with an in-memory MCP client and a recording fake backend, proving: tool discovery,
input validation, identity handling, the human-approval gate (including forgery attempts), error handling and audit
calls. They do NOT prove the Django data behaviour - that is covered by `python manage.py test core`.
"""
import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import Client  # noqa: E402

from civisense_mcp.errors import BackendError  # noqa: E402
from civisense_mcp.security import (args_digest, make_approval_token, make_identity_token)  # noqa: E402
from civisense_mcp.server import SENSITIVE_TOOLS, build_server  # noqa: E402

SECRET = "unit-test-secret"
EXPECTED_TOOLS = {"submit_complaint", "check_complaint_status", "find_duplicate_complaints", "classify_complaint",
                  "calculate_priority", "get_nearby_complaints", "get_civic_hotspots", "get_ward_analytics",
                  "get_officer_tasks", "update_complaint_status"}


class FakeBackend:
    def __init__(self):
        self.calls, self.audits, self.prepared = [], [], []

    def prepare(self, tool, uid, args):
        self.prepared.append((tool, uid, args))
        if args.get("title") == "FORBIDDEN":
            raise BackendError("forbidden", "role may not do this")
        return {"summary": f"preview of {tool}"}

    def execute(self, tool, uid, args):
        self.calls.append((tool, uid, args))
        if args.get("ref") == "CS-2026-000404":
            raise BackendError("not_found", "no such complaint")
        if args.get("ref") == "CS-2026-000500":
            raise RuntimeError("secret internal detail: db password=hunter2")
        return {"echo": tool, "uid": uid}

    def audit(self, uid, tool, args, outcome, detail=""):
        self.audits.append((uid, tool, outcome, detail))


def run(coro):
    return asyncio.run(coro)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.token = make_identity_token(7, SECRET)
        self.server = build_server(self.backend, SECRET, static_token=self.token)

    def call(self, name, args, server=None):
        async def go():
            async with Client(server or self.server) as c:
                return await c.call_tool(name, args)
        return run(go())

    # ---- discovery
    def test_discovers_all_ten_tools_with_schemas_and_annotations(self):
        async def go():
            async with Client(self.server) as c:
                return (await c.list_tools()).tools
        tools = run(go())
        self.assertEqual({t.name for t in tools}, EXPECTED_TOOLS)
        for t in tools:
            self.assertTrue(t.description, t.name)
            self.assertEqual(t.input_schema["type"], "object")
            self.assertNotIn("ctx", t.input_schema["properties"])
        by = {t.name: t for t in tools}
        self.assertTrue(by["classify_complaint"].annotations.read_only_hint)
        self.assertFalse(by["submit_complaint"].annotations.read_only_hint)
        self.assertIn("approval_token", by["submit_complaint"].input_schema["properties"])
        self.assertIn("title", by["submit_complaint"].input_schema["required"])

    # ---- input validation (rejected before the backend is touched)
    def test_invalid_inputs_never_reach_backend(self):
        bad = [("classify_complaint", {"text": "ab"}), ("get_nearby_complaints", {"latitude": 40.0, "longitude": 10.0}),
               ("get_nearby_complaints", {"latitude": 12.97, "longitude": 77.59, "radius_m": 999999}),
               ("check_complaint_status", {"ref": "../../etc/passwd"}),
               ("update_complaint_status", {"ref": "CS-2026-000001", "new_status": "Deleted"}),
               ("submit_complaint", {"title": "x", "description": "too short"}),
               ("get_civic_hotspots", {"min_samples": 1}), ("get_officer_tasks", {"status": "bogus"})]
        for name, args in bad:
            r = self.call(name, args)
            self.assertTrue(r.is_error, (name, args))
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.prepared, [])

    # ---- identity
    def test_no_identity_means_no_access(self):
        server = build_server(self.backend, SECRET, static_token=None)
        r = self.call("classify_complaint", {"text": "garbage near my house"}, server)
        self.assertTrue(r.is_error)
        self.assertIn("unauthenticated", r.content[0].text)
        self.assertEqual(self.backend.calls, [])

    def test_forged_or_expired_identity_rejected(self):
        for tok in ("garbage", make_identity_token(7, "other-secret"), make_identity_token(7, SECRET, ttl=-5)):
            r = self.call("classify_complaint", {"text": "garbage near my house"}, build_server(self.backend, SECRET, static_token=tok))
            self.assertTrue(r.is_error)
        self.assertEqual(self.backend.calls, [])

    def test_identity_comes_from_token_not_arguments(self):
        r = self.call("classify_complaint", {"text": "garbage near my house"})
        self.assertFalse(r.is_error)
        self.assertEqual(self.backend.calls[0][1], 7)
        # a user_id / role argument is not part of any schema, so it cannot be smuggled in
        r = self.call("classify_complaint", {"text": "garbage near my house", "user_id": 1, "role": "ADMIN"})
        for _tool, uid, args in self.backend.calls:
            self.assertEqual(uid, 7)
            self.assertNotIn("role", args)

    # ---- human approval gate
    def sensitive_args(self):
        return {"title": "Garbage overflow", "description": "Garbage is overflowing near my apartment for days"}

    def test_sensitive_tool_only_previews_without_approval(self):
        r = self.call("submit_complaint", self.sensitive_args())
        self.assertFalse(r.is_error)
        sc = r.structured_content
        self.assertEqual(sc["status"], "confirmation_required")
        self.assertTrue(sc["args_digest"])
        self.assertEqual(self.backend.calls, [], "nothing may be executed before approval")
        self.assertEqual(self.backend.prepared[0][0], "submit_complaint")

    def test_model_cannot_forge_approval(self):
        forged = [make_approval_token(7, "submit_complaint", "0" * 64, SECRET), "nonsense",
                  make_approval_token(7, "submit_complaint", "x", "wrong-secret"), make_identity_token(7, SECRET)]
        for tok in forged:
            r = self.call("submit_complaint", {**self.sensitive_args(), "approval_token": tok})
            self.assertEqual(r.structured_content["status"], "confirmation_required")
        self.assertEqual(self.backend.calls, [])

    def test_valid_approval_executes_exactly_once_for_those_args(self):
        preview = self.call("submit_complaint", self.sensitive_args()).structured_content
        tok = make_approval_token(7, "submit_complaint", preview["args_digest"], SECRET)
        r = self.call("submit_complaint", {**self.sensitive_args(), "approval_token": tok})
        self.assertEqual(r.structured_content["status"], "ok")
        self.assertEqual(len(self.backend.calls), 1)
        # approval is bound to the arguments: change the text and it is refused
        changed = {**self.sensitive_args(), "description": "Something completely different and malicious", "approval_token": tok}
        self.assertEqual(self.call("submit_complaint", changed).structured_content["status"], "confirmation_required")
        self.assertEqual(len(self.backend.calls), 1)

    def test_approval_bound_to_user_tool_and_expiry(self):
        preview = self.call("submit_complaint", self.sensitive_args()).structured_content
        d = preview["args_digest"]
        for tok in (make_approval_token(8, "submit_complaint", d, SECRET),                 # another user
                    make_approval_token(7, "update_complaint_status", d, SECRET),          # another tool
                    make_approval_token(7, "submit_complaint", d, SECRET, ttl=-1)):        # expired
            r = self.call("submit_complaint", {**self.sensitive_args(), "approval_token": tok})
            self.assertEqual(r.structured_content["status"], "confirmation_required")
        self.assertEqual(self.backend.calls, [])

    def test_update_status_is_gated_too(self):
        args = {"ref": "CS-2026-000001", "new_status": "Resolved", "resolution_details": "Cleared"}
        self.assertEqual(self.call("update_complaint_status", args).structured_content["status"], "confirmation_required")
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(SENSITIVE_TOOLS, {"submit_complaint", "update_complaint_status"})

    def test_read_tools_are_not_gated(self):
        self.assertEqual(self.call("check_complaint_status", {"ref": "CS-2026-000001"}).structured_content["status"], "ok")

    # ---- errors and audit
    def test_backend_errors_are_readable_and_audited(self):
        r = self.call("check_complaint_status", {"ref": "CS-2026-000404"})
        self.assertTrue(r.is_error)
        self.assertIn("not_found", r.content[0].text)
        r = self.call("submit_complaint", {**self.sensitive_args(), "title": "FORBIDDEN"})
        self.assertTrue(r.is_error)
        outcomes = [a[2] for a in self.backend.audits]
        self.assertIn("error", outcomes)
        self.assertIn("refused", outcomes)

    def test_unexpected_exceptions_do_not_leak_internals(self):
        r = self.call("check_complaint_status", {"ref": "CS-2026-000500"})
        self.assertTrue(r.is_error)
        self.assertNotIn("hunter2", r.content[0].text)

    def test_every_call_is_audited(self):
        self.call("classify_complaint", {"text": "garbage near my house"})
        self.call("submit_complaint", self.sensitive_args())
        self.assertEqual([(a[1], a[2]) for a in self.backend.audits],
                         [("classify_complaint", "ok"), ("submit_complaint", "confirmation_required")])


class DigestTests(unittest.TestCase):
    def test_digest_ignores_token_and_is_order_independent(self):
        a = args_digest("t", {"a": 1, "b": "ಕನ್ನಡ", "approval_token": "x"})
        b = args_digest("t", {"b": "ಕನ್ನಡ", "a": 1})
        self.assertEqual(a, b)
        self.assertNotEqual(a, args_digest("t", {"a": 2, "b": "ಕನ್ನಡ"}))
        self.assertNotEqual(a, args_digest("other", {"a": 1, "b": "ಕನ್ನಡ"}))

    def test_token_expiry_uses_clock(self):
        t = make_identity_token(1, SECRET, ttl=1, now=time.time() - 10)
        from civisense_mcp.security import TokenError, read_identity_token
        with self.assertRaises(TokenError):
            read_identity_token(t, SECRET)


if __name__ == "__main__":
    unittest.main()
