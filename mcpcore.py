"""Dependency-free MCP server core (JSON-RPC 2.0), standard library only.

Vendored verbatim into each ArthurLegal jurisdiction MCP server so every repo
stays self-contained and installable with no ``pip install`` — the same
constraint the e-qanun / lex-scholar / resourcecontracts servers ship under.

Transports
----------
- **stdio**: line-delimited JSON-RPC on stdin/stdout (desktop MCP clients).
- **Streamable HTTP**: a single ``POST /mcp`` endpoint answered with one
  ``application/json`` JSON-RPC response. ``GET /mcp`` returns 405 (this server
  never pushes server-initiated messages). ``GET /health`` returns 200 for
  platform health checks.

There is **no authentication**. Binding to a public interface exposes every
tool to anyone who can reach the port; that is an explicit opt-in via
``--host 0.0.0.0``.

Opt-in observation hooks (``SESSION_IDS``, ``BILDIRIM_KANCASI``, ``istek_baglami``)
are inert by default: unless an embedding application turns them on, responses
and headers are exactly what they were without them.

Usage
-----
    from mcpcore import Tool, run

    TOOLS = [Tool("search", "Search things.", {...}, handler)]
    run(TOOLS, name="xx-mcp", version="1.0.0", instructions="...")
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = ["Tool", "McpError", "run", "run_stdio", "run_http", "build_parser", "istek_baglami"]

# Cap the JSON-RPC request body: without this a single Content-Length header can
# drive the process out of memory.
_MAX_BODY = 4_000_000

# Protocol revisions we can speak; we echo the client's if we recognise it.
_SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
_DEFAULT_PROTOCOL = "2025-06-18"

# --------------------------------------------------------------------------- #
# Opt-in request context and hooks — all inert unless an application sets them #
# --------------------------------------------------------------------------- #
# When True, an HTTP POST carrying an ``initialize`` request and no
# ``Mcp-Session-Id`` header is answered with a fresh id in that header. Ids are
# never validated or required afterwards: after a restart the server cannot know
# yesterday's ids, and rejecting them would break every connected client for the
# sake of a label. The id exists only so an observer can tell two clients'
# JSON-RPC ids apart (each client numbers its requests from 1).
SESSION_IDS = False

# When set, called with every notification (method present, id absent) just
# before it is dropped. Nothing else in the protocol reports that a client gave up
# on a call; ``notifications/cancelled`` arrives here or nowhere. A failing hook
# costs one stderr line, never the session.
BILDIRIM_KANCASI: Optional[Callable[[Dict[str, Any]], Any]] = None

# ThreadingHTTPServer serves each connection on its own thread and a connection
# carries its requests one after another, so a thread-local holds exactly the
# request being handled. stdio is one thread and has no session.
_baglam = threading.local()


def istek_baglami() -> Dict[str, Any]:
    """The request being handled on this thread: ``session_id`` and ``request_id``.

    ``session_id`` is the incoming ``Mcp-Session-Id`` header or the id just issued
    to an ``initialize`` (None under stdio, or when the client sent none);
    ``request_id`` is the JSON-RPC id of the message being dispatched (None for a
    notification). Meaningful inside a tool handler or a hook; read anywhere
    else it may still describe the previous request.
    """
    return {"session_id": getattr(_baglam, "session_id", None),
            "request_id": getattr(_baglam, "request_id", None)}


class McpError(Exception):
    """A tool failure that should reach the model as readable text, not a crash."""


class Tool:
    """One tool: JSON Schema in, handler out. Single source of truth for both transports."""

    __slots__ = ("name", "description", "input_schema", "handler")

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Any],
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def public(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


# --------------------------------------------------------------------------- #
# JSON-RPC plumbing                                                            #
# --------------------------------------------------------------------------- #
def _ok(msg_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


class Dispatcher:
    """Handles one MCP session's methods. Shared by both transports."""

    def __init__(
        self,
        tools: List[Tool],
        name: str,
        version: str,
        instructions: str = "",
    ) -> None:
        self.tools = tools
        self.by_name = {t.name: t for t in tools}
        self.name = name
        self.version = version
        self.instructions = instructions

    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a response dict, or None for notifications (no reply)."""
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        _baglam.request_id = msg_id

        # Notifications carry no id and get no response.
        if method is not None and msg_id is None:
            kanca = BILDIRIM_KANCASI
            if kanca is not None:
                try:
                    kanca(msg)
                except Exception as exc:  # noqa: BLE001 - an observer must not break the session
                    sys.stderr.write("mcpcore: notification hook failed (%s)\n"
                                     % type(exc).__name__)
            return None

        if method == "initialize":
            asked = (params.get("protocolVersion") or "").strip()
            protocol = asked if asked in _SUPPORTED_PROTOCOLS else _DEFAULT_PROTOCOL
            result = {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
            }
            if self.instructions:
                result["instructions"] = self.instructions
            return _ok(msg_id, result)

        if method == "ping":
            return _ok(msg_id, {})

        if method == "tools/list":
            return _ok(msg_id, {"tools": [t.public() for t in self.tools]})

        if method == "tools/call":
            return self._call(msg_id, params)

        return _err(msg_id, -32601, "Method not found: %s" % method)

    def _call(self, msg_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = self.by_name.get(name)
        if tool is None:
            return _err(msg_id, -32602, "Unknown tool: %s" % name)
        try:
            value = tool.handler(args)
            payload = _as_text(value)
            return _ok(msg_id, {"content": [{"type": "text", "text": payload}]})
        except McpError as exc:
            # A handled, explainable failure — hand it to the model as text so it
            # can recover (try another query, another tool) instead of stalling.
            return _ok(
                msg_id,
                {"content": [{"type": "text", "text": "ERROR: %s" % exc}], "isError": True},
            )
        except Exception as exc:  # noqa: BLE001 - a tool crash must not kill the session
            detail = "%s: %s" % (type(exc).__name__, exc)
            if os.environ.get("MCP_DEBUG"):
                detail += "\n" + traceback.format_exc()
            return _ok(
                msg_id,
                {"content": [{"type": "text", "text": "ERROR: %s" % detail}], "isError": True},
            )


# --------------------------------------------------------------------------- #
# stdio transport                                                              #
# --------------------------------------------------------------------------- #
def run_stdio(dispatcher: Dispatcher) -> None:
    # JSON-RPC over stdio is UTF-8 by spec. On Windows sys.stdin/sys.stdout default
    # to the locale codepage (cp1254 on a Turkish install), so every non-ASCII
    # character in a tool description or result leaves the stream as bytes the
    # client cannot decode. The HTTP transport below encodes explicitly; stdio has
    # to be told.
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # not a TextIOWrapper (tests, embedding)
            pass
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps(_err(None, -32700, "Parse error")) + "\n")
            stdout.flush()
            continue
        response = dispatcher.handle(msg)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
            stdout.flush()


# --------------------------------------------------------------------------- #
# Streamable HTTP transport                                                    #
# --------------------------------------------------------------------------- #
def _make_handler(dispatcher: Dispatcher):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "%s/%s" % (dispatcher.name, dispatcher.version)

        def log_message(self, fmt, *args):  # noqa: A003 - quieter default logging
            if os.environ.get("MCP_DEBUG"):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body: bytes, ctype: str = "application/json",
                  extra_headers: Optional[List[Tuple[str, str]]] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in extra_headers or ():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _oturum(self, msg: Any) -> Optional[List[Tuple[str, str]]]:
            """Put this POST's session on the thread; return the header to issue, if any.

            The incoming ``Mcp-Session-Id`` is taken as is, never checked. A new id is
            issued only when ``SESSION_IDS`` is on, the client sent none, and the body
            (or a batch member) is an ``initialize`` request — the one response a
            client reads the header from.
            """
            gelen = self.headers.get("Mcp-Session-Id") or None
            _baglam.session_id = gelen
            _baglam.request_id = None
            if not SESSION_IDS or gelen:
                return None
            mesajlar = msg if isinstance(msg, list) else [msg]
            if not any(isinstance(m, dict) and m.get("method") == "initialize"
                       and m.get("id") is not None for m in mesajlar):
                return None
            yeni = uuid.uuid4().hex
            _baglam.session_id = yeni
            return [("Mcp-Session-Id", yeni)]

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") in ("/health", "/healthz"):
                self._send(200, b'{"status":"ok"}')
                return
            if self.path.rstrip("/") == "":
                info = {
                    "name": dispatcher.name,
                    "version": dispatcher.version,
                    "mcp_endpoint": "/mcp",
                    "transport": "streamable-http",
                    "auth": "none",
                    "tools": [t.name for t in dispatcher.tools],
                }
                self._send(200, json.dumps(info, ensure_ascii=False).encode("utf-8"))
                return
            # Streamable HTTP allows a plain JSON response and no server-initiated
            # stream; GET /mcp therefore has nothing to open.
            self._send(405, b'{"error":"GET not supported; POST JSON-RPC to /mcp"}')

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") not in ("/mcp", ""):
                self._send(404, b'{"error":"not found"}')
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send(400, b'{"error":"bad Content-Length"}')
                return
            if length <= 0 or length > _MAX_BODY:
                self._send(400, b'{"error":"missing or oversized body"}')
                return
            raw = self.rfile.read(length)
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                body = json.dumps(_err(None, -32700, "Parse error")).encode("utf-8")
                self._send(400, body)
                return
            oturum_basligi = self._oturum(msg)

            # A batch is a list; answer each and drop notification nulls.
            if isinstance(msg, list):
                out = [r for r in (dispatcher.handle(m) for m in msg) if r is not None]
                body = json.dumps(out, ensure_ascii=False, default=str).encode("utf-8")
                self._send(200, body, extra_headers=oturum_basligi)
                return

            response = dispatcher.handle(msg)
            if response is None:
                # Notification: 202 with an empty body is the correct answer.
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(response, ensure_ascii=False, default=str).encode("utf-8")
            self._send(200, body, extra_headers=oturum_basligi)

    return Handler


def run_http(dispatcher: Dispatcher, host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), _make_handler(dispatcher))
    sys.stderr.write(
        "%s %s listening on http://%s:%d/mcp (no auth)\n"
        % (dispatcher.name, dispatcher.version, host, port)
    )
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #
def build_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="%s (no auth, stdlib only)" % prog)
    p.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="stdio (default) or http (Streamable HTTP at /mcp)",
    )
    p.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "127.0.0.1"),
        help="bind address (default 127.0.0.1; 0.0.0.0 exposes the "
             "UNAUTHENTICATED server on all interfaces — opt in deliberately)",
    )
    p.add_argument(
        "--port",
        type=int,
        # PORT is what Railway/Fly/Heroku inject; MCP_PORT wins if both are set.
        default=int(os.environ.get("MCP_PORT") or os.environ.get("PORT") or 8000),
    )
    return p


def run(
    tools: List[Tool],
    name: str,
    version: str,
    instructions: str = "",
    argv: Optional[List[str]] = None,
) -> None:
    args = build_parser(name).parse_args(argv)
    dispatcher = Dispatcher(tools, name=name, version=version, instructions=instructions)
    if args.transport == "http":
        run_http(dispatcher, args.host, args.port)
    else:
        run_stdio(dispatcher)
