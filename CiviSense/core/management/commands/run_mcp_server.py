from django.core.management.base import BaseCommand

from civisense_mcp.runner import serve


class Command(BaseCommand):
    help = ("Run the CiviSense MCP server. stdio (default) is what Claude Desktop / MCP Inspector launch; "
            "streamable-http serves http://HOST:PORT/mcp and requires an 'Authorization: Bearer <token>' header.")
    requires_system_checks = []          # keep stdout clean for the stdio protocol

    def add_arguments(self, parser):
        parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8765)

    def handle(self, *args, **o):
        serve(o["transport"], o["host"], o["port"])
