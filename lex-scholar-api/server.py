#!/usr/bin/env python3
"""MCP server entry point — one endpoint over ten legal-scholarship sources.

Dependency-free: MCP is implemented in pure standard library (see
``lexscholar/mcp_server.py``), so it runs on a stock Python 3.9 — no ``mcp``
SDK, no pip installs. No authentication (every upstream is public and this
server adds none), so it connects from a client UI as "No auth".

Transports
----------
    # local stdio (default) — for a desktop MCP client config
    python3 server.py

    # remote, no-auth Streamable HTTP — connect this URL from a connector UI:
    #   http://<host>:<port>/mcp
    python3 server.py --transport http --host 0.0.0.0 --port 8000

Env fallbacks: LEXSCHOLAR_MCP_TRANSPORT, LEXSCHOLAR_MCP_HOST, LEXSCHOLAR_MCP_PORT.
Optional: OPENALEX_API_KEY raises the metered OpenAlex budget ~100x.

Tools: search_legal_scholarship, compare_jurisdictions, get_scholarship_article,
get_scholarship_fulltext, resolve_doi, list_sources.

The scholarship-prefixed names are deliberate: in a multi-MCP agent a generic
``search_articles`` collides with DergiPark's tool of the same name, and the
agent then sends one server's parameters to the other.
"""

from __future__ import annotations

import argparse
import os

from lexscholar.mcp_server import run_http, run_stdio


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="lex-scholar-api MCP server (no auth, stdlib only)")
    p.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("LEXSCHOLAR_MCP_TRANSPORT", "stdio"),
        help="stdio (default) or http (Streamable HTTP at /mcp)",
    )
    p.add_argument("--host", default=os.environ.get("LEXSCHOLAR_MCP_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("LEXSCHOLAR_MCP_PORT", "8000")))
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    if args.transport == "http":
        run_http(args.host, args.port)
    else:
        run_stdio()


if __name__ == "__main__":
    main()
