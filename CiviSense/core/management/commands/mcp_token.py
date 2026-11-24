import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from civisense_mcp.security import make_approval_token, make_identity_token
from core.models import User


class Command(BaseCommand):
    help = ("Mint a signed MCP token for LOCAL testing (MCP Inspector, Claude Desktop). "
            "Default: an identity token for --user. With --approve-tool/--digest: a human-approval token for one preview.")

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="username the MCP session will act as")
        parser.add_argument("--ttl", type=int, default=3600, help="identity token lifetime in seconds (default 3600)")
        parser.add_argument("--approve-tool", choices=["submit_complaint", "update_complaint_status"])
        parser.add_argument("--digest", help="args_digest printed in the tool's confirmation_required preview")

    def handle(self, *args, **o):
        user = User.objects.filter(username=o["user"], is_active=True).first()
        if user is None:
            raise CommandError(f"No active user '{o['user']}'.")
        if o["approve_tool"]:
            if not o["digest"]:
                raise CommandError("--digest is required with --approve-tool (copy it from the preview).")
            token = make_approval_token(user.pk, o["approve_tool"], o["digest"], settings.MCP_SECRET)
            note = "Human-approval token (valid 10 minutes, for exactly that tool call). Pass it as approval_token."
        else:
            token = make_identity_token(user.pk, settings.MCP_SECRET, ttl=o["ttl"])
            note = (f"Identity token for {user.username} ({user.role}), valid {o['ttl']}s. Set CIVISENSE_MCP_TOKEN to it "
                    "or send it as 'Authorization: Bearer <token>'. Treat it like a password.")
        sys.stderr.write(note + "\n")
        self.stdout.write(token)
