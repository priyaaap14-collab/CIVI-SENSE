"""python -m civisense_mcp [--transport stdio|streamable-http] [--host 127.0.0.1] [--port 8765]

Run from the project folder (the one containing manage.py). For stdio this keeps stdout clean for the protocol.
"""
import argparse
import os
import sys
from pathlib import Path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m civisense_mcp", description="CiviSense MCP server")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()

    from .runner import serve
    serve(a.transport, a.host, a.port)


if __name__ == "__main__":
    main()
