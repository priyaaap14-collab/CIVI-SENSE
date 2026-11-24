"""Start the CiviSense MCP server (Django must already be configured)."""
import os


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    from django.conf import settings

    from .backend import DjangoBackend
    from .server import build_server

    # stdio: the identity token is handed to the process by whoever launched it (CiviSense web app, Claude Desktop,
    # MCP Inspector). HTTP: every request must carry its own `Authorization: Bearer <identity token>` header.
    token = os.environ.get("CIVISENSE_MCP_TOKEN") or None
    server = build_server(DjangoBackend(), settings.MCP_SECRET, static_token=token)
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)
